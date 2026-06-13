"""META SENSOR — continuous wallet-panel day-meta reader (AxiS 2026-06-12).

"Why send bad trades to test the waters ourselves?" — read the day's meta from
OTHER successful wallets' realized results, continuously, BEFORE we trade.
They pay the tuition; we read the thermometer.

Architecture (the continuous, ever-evolving version — not a snapshot study):
  PANEL    config/sensor_panel.json — address -> {archetype, status, source}.
           Bootstrapped from follow_watchlist (active seats) + follow_cuts
           (cut for COPYABILITY, still sensor-grade: quality != copyable).
           Panel evolution (recruit from discovery/harvest, retire on drift/
           silence) runs via the wallet_cycle ritual; this module just reads
           whatever the panel file says — swap-in/swap-out is a file edit.
  STREAM   PumpPortal subscribeAccountTrade (free, 0 RPC) — every panel
           wallet's buys/sells arrive parsed in real time.
  EPISODE  (wallet, mint) SOL-flow accumulation; an episode closes when no
           further trades arrive for EPISODE_IDLE_SECS after a sell -> scored
           ret = recv/spent - 1 (same convention as wallet_decode).
  BOARD    rolling per-archetype scoreboard: WR + n over 6h/24h windows, plus
           the pooled "all" row. /api/meta-sensor serves it; the meta-allocator
           shadow snapshots it hourly as additional state columns.

MEASURE-ONLY: nothing in any buy path reads this. The "send signals to bots"
stage requires the same pre-registered forward bar as every other dial
(>=14 days; sensor-keyed > flat). Winner's-curse guard: we read ARCHETYPES
(which style is collecting wins today), never "follow the hot wallet".
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_STATE_FILE = os.path.join(_DATA_DIR, "meta_sensor_state.json")
_PANEL_FILE = os.path.join("config", "sensor_panel.json")

EPISODE_IDLE_SECS = 1800.0     # no trades for 30min after a sell -> episode closed
SCORE_RETENTION_SECS = 26 * 3600
_PERSIST_EVERY_SECS = 600.0

# SURVIVORSHIP GUARD (2026-06-12 gap hunt): an episode with NO sell ever
# (wallet riding a bag) would otherwise never score — the board would silently
# overstate every archetype's WR (losers-held-forever invisible). After this
# age a sell-less episode EXPIRES: it is NOT scored as a return (we don't know
# the mark — a thesis-holder's winning hold looks identical to a bag), but it
# IS counted in the per-archetype `unresolved` health metric on the
# scoreboard, so a high-bag archetype's shiny WR arrives with its asterisk.
EPISODE_MAX_AGE_SECS = 48 * 3600.0


def load_panel(path: str = _PANEL_FILE) -> Dict[str, dict]:
    """Panel file: {address: {archetype, status, source}}. Falls back to
    bootstrapping from follow_watchlist + follow_cuts if absent."""
    try:
        p = json.load(open(path))
        if isinstance(p, dict) and p:
            return {a: (m if isinstance(m, dict) else {}) for a, m in p.items()}
    except Exception:
        pass
    panel: Dict[str, dict] = {}
    try:
        for a in json.load(open(os.path.join("config", "follow_watchlist.json"))):
            panel[a] = {"archetype": None, "status": "active", "source": "roster"}
    except Exception:
        pass
    try:
        for a in json.load(open(os.path.join("config", "follow_cuts.json"))):
            panel.setdefault(a, {"archetype": None, "status": "active",
                                 "source": "cut-sensor-grade"})
    except Exception:
        pass
    return panel


class MetaSensor:
    def __init__(self, panel: Optional[Dict[str, dict]] = None):
        self.panel = panel if panel is not None else load_panel()
        # (wallet, mint) -> {"spent","recv","last_ts","sold"}
        self._episodes: Dict[str, dict] = {}
        # scored events: deque of (ts, wallet, archetype, ret_pct, hold_secs)
        self._scores: deque = deque()
        # expired sell-less episodes: deque of (ts, archetype) — health metric
        self._unresolved: deque = deque()
        # per-wallet recent tx signatures (dual-eye dedupe, rolling 128)
        self._sigs: Dict[str, list] = {}
        # launch registry ref (P2a): the PumpPortal feed's mint->launch_ts dict,
        # shared by reference so SWEEP-sourced episodes get ages too (the sweep
        # eye carries no launch_ts of its own).
        self._launches: Optional[dict] = None
        # per-archetype BUY events (ts, arch) — the LEADING meta-death signal:
        # wallets stop ENTERING a dying meta before their losses even close.
        self._buy_events: deque = deque()
        self._last_ingest_ts: Optional[float] = None
        self._last_persist = 0.0
        self._restore()

    def set_launch_registry(self, launches: dict) -> None:
        self._launches = launches
        logger.info("[MetaSensor] panel=%d wallets (%d archetyped)",
                    len(self.panel),
                    sum(1 for m in self.panel.values() if m.get("archetype")))

    # ── ingestion (called from the PumpPortal stream) ─────────────────────
    def ingest(self, wallet: str, mint: str, side: str, sol: float, ts: float,
               launch_ts: Optional[float] = None,
               signature: Optional[str] = None,
               tokens: Optional[float] = None) -> None:
        """One parsed panel-wallet trade. Never raises. ``launch_ts`` (from the
        feed's own launch registry, free) gives token-age-at-entry — the POND
        dimension a meta lives in, not just its exit geometry.

        DUAL-EYE dedupe (2026-06-12): trades arrive from BOTH the PumpPortal
        stream and the RPC sweep (PumpPortal only carries pump.fun/pumpswap;
        the panel trades graduated Raydium tokens — venue blindness measured
        live: 0 account_trades while the sweep saw 14 buys). ``signature``
        dedupes the same tx across eyes."""
        try:
            if wallet not in self.panel or not mint or sol is None:
                return
            if signature:
                seen = self._sigs.setdefault(wallet, [])
                if signature in seen:
                    return
                seen.append(signature)
                del seen[:-128]
            if launch_ts is None and self._launches is not None:
                launch_ts = self._launches.get(mint.lower())
            k = f"{wallet}|{mint.lower()}"
            ep = self._episodes.get(k)
            if ep is None:
                if side != "buy":
                    return   # sell with no observed buy -> position predates us; skip
                ep = self._episodes[k] = {"spent": 0.0, "recv": 0.0,
                                          "tok_in": 0.0, "tok_out": 0.0,
                                          "first_ts": ts, "last_ts": ts,
                                          "sold": False,
                                          "age_h": (round((ts - launch_ts) / 3600.0, 2)
                                                    if launch_ts and ts > launch_ts
                                                    else None)}
            self._last_ingest_ts = ts
            if side == "buy":
                ep["spent"] += float(sol)
                if isinstance(tokens, (int, float)) and tokens > 0:
                    ep["tok_in"] = ep.get("tok_in", 0.0) + float(tokens)
                # buy-rate tracking (leading meta-death signal)
                arch = (self.panel.get(wallet) or {}).get("archetype") or "unlabeled"
                self._buy_events.append((ts, arch))
                cut = ts - 6 * 3600
                while self._buy_events and self._buy_events[0][0] < cut:
                    self._buy_events.popleft()
            elif side == "sell":
                ep["recv"] += float(sol)
                ep["sold"] = True
                if isinstance(tokens, (int, float)) and tokens > 0:
                    ep["tok_out"] = ep.get("tok_out", 0.0) + float(tokens)
            ep["last_ts"] = ts
            self._finalize_idle(ts)
            if ts - self._last_persist > _PERSIST_EVERY_SECS:
                self._persist()
        except Exception as e:
            logger.debug("[MetaSensor] ingest error: %s", e)

    @staticmethod
    def _fully_exited(ep: dict) -> bool:
        """Partial-exit guard (P1b, 2026-06-12): score an episode only when
        the wallet has actually sold ~all of it. A scale-out style (sells half
        at 2x, holds the rest) previously idle-closed at ~0% ret — making
        exactly the styles we respect most look systematically worse. When
        token amounts are unknown on either side, fall back to the legacy
        sold-then-idle convention (coverage-safe)."""
        tok_in = ep.get("tok_in") or 0.0
        tok_out = ep.get("tok_out") or 0.0
        if tok_in <= 0 or tok_out <= 0:
            return True   # token amounts unknown -> legacy behavior
        return tok_out >= 0.9 * tok_in

    def _finalize_idle(self, now: float) -> None:
        done = [k for k, ep in self._episodes.items()
                if ep["sold"] and now - ep["last_ts"] > EPISODE_IDLE_SECS
                and self._fully_exited(ep)]
        for k in done:
            ep = self._episodes.pop(k)
            if ep["spent"] <= 0:
                continue
            wallet = k.split("|", 1)[0]
            ret = (ep["recv"] / ep["spent"] - 1.0) * 100.0
            arch = (self.panel.get(wallet) or {}).get("archetype") or "unlabeled"
            hold = max(0.0, ep["last_ts"] - ep.get("first_ts", ep["last_ts"]))
            self._scores.append((ep["last_ts"], wallet, arch, ret, hold,
                                 ep.get("age_h")))
        # survivorship guard: expire episodes past max age that never reached
        # a scoreable state (no sell at all, OR partial-exit never finished)
        # into the `unresolved` health counter (never into the WR).
        stale = [k for k, ep in self._episodes.items()
                 if now - ep.get("first_ts", ep["last_ts"]) > EPISODE_MAX_AGE_SECS
                 and (not ep["sold"] or not self._fully_exited(ep))]
        for k in stale:
            self._episodes.pop(k)
            wallet = k.split("|", 1)[0]
            arch = (self.panel.get(wallet) or {}).get("archetype") or "unlabeled"
            self._unresolved.append((now, arch))
        cutoff = now - SCORE_RETENTION_SECS
        while self._scores and self._scores[0][0] < cutoff:
            self._scores.popleft()
        while self._unresolved and self._unresolved[0][0] < cutoff:
            self._unresolved.popleft()

    # ── the board ─────────────────────────────────────────────────────────
    def scoreboard(self, now: Optional[float] = None) -> dict:
        now = now or time.time()
        self._finalize_idle(now)
        out: dict = {"panel_size": len(self.panel),
                     "open_episodes": len(self._episodes),
                     # stream health: a silent/dead feed must be VISIBLE, not
                     # read as "no meta today" (gap hunt 2026-06-12)
                     "last_score_age_secs": (round(now - self._scores[-1][0])
                                             if self._scores else None),
                     # ingest age = the real stream-health signal (scores are
                     # rare under the full-exit rule; ingestion is constant)
                     "last_ingest_age_secs": (round(now - self._last_ingest_ts)
                                              if getattr(self, "_last_ingest_ts", None)
                                              else None),
                     "scored_24h": len(self._scores),
                     # per-wallet episode counts (24h): venue-coverage check
                     # (compare vs the wallet's chain rate from the decode
                     # probe — a big shortfall = trades on venues PumpPortal
                     # doesn't stream) AND silence detection per sensor.
                     "wallet_episodes_24h": {},
                     "windows": {}}
        _wc: Dict[str, int] = {}
        for _ts, _w, *_rest in self._scores:
            _wc[_w[:8]] = _wc.get(_w[:8], 0) + 1
        out["wallet_episodes_24h"] = dict(sorted(_wc.items(), key=lambda kv: -kv[1]))
        for label, secs in (("6h", 6 * 3600), ("24h", 24 * 3600)):
            cut = now - secs
            rows: Dict[str, list] = {}
            unres: Dict[str, int] = {}
            for ts, _w, arch, ret, *_h in self._scores:
                if ts < cut:
                    continue
                rows.setdefault(arch, []).append(ret)
                rows.setdefault("all", []).append(ret)
            for ts, arch in self._unresolved:
                if ts >= cut:
                    unres[arch] = unres.get(arch, 0) + 1
                    unres["all"] = unres.get("all", 0) + 1
            out["windows"][label] = {
                arch: {"n": len(v),
                       "wr": round(sum(1 for r in v if r > 0) / len(v), 3),
                       "med_ret_pct": round(sorted(v)[len(v) // 2], 1),
                       # bags-held-forever counter: a high-unresolved archetype's
                       # WR is overstated — consumers see the asterisk
                       "unresolved": unres.get(arch, 0)}
                for arch, v in rows.items() if v
            }
        return out

    def buy_rate(self, arch: str, now: Optional[float] = None) -> tuple:
        """(buys_last_30min, trailing_6h_avg_per_30min) for an archetype — the
        LEADING meta-death signal: panel wallets stop ENTERING a dying meta
        long before their losses close. A consumer compares recent vs norm."""
        now = now or time.time()
        recent = sum(1 for ts, a in self._buy_events if a == arch and ts >= now - 1800)
        total6 = sum(1 for ts, a in self._buy_events if a == arch and ts >= now - 21600)
        return recent, total6 / 12.0

    def archetype_geometry(self, arch: str, now: Optional[float] = None,
                           window_secs: float = 6 * 3600,
                           min_n: int = 8) -> Optional[dict]:
        """The winning style's measurable GEOMETRY over the window — what a
        dynamic bot needs to re-tune itself: hold distribution + win/loss
        return shapes. None if the sample is too thin to act on."""
        now = now or time.time()
        self._finalize_idle(now)
        cut = now - window_secs
        rets, holds, ages = [], [], []
        by_wallet: Dict[str, int] = {}
        for ts, w, a, ret, *extra in self._scores:
            if ts < cut or a != arch:
                continue
            rets.append(ret)
            by_wallet[w] = by_wallet.get(w, 0) + 1
            if extra and isinstance(extra[0], (int, float)):
                holds.append(float(extra[0]))
            if len(extra) > 1 and isinstance(extra[1], (int, float)):
                ages.append(float(extra[1]))
        if len(rets) < min_n:
            return None
        wins = sorted(r for r in rets if r > 0)
        losses = sorted(r for r in rets if r <= 0)
        hs = sorted(holds)
        ag = sorted(ages)
        return {
            "n": len(rets),
            "wr": round(len(wins) / len(rets), 3),
            "med_win_pct": round(wins[len(wins) // 2], 1) if wins else None,
            "med_loss_pct": round(losses[len(losses) // 2], 1) if losses else None,
            "med_hold_secs": round(hs[len(hs) // 2]) if hs else None,
            "p75_hold_secs": round(hs[(3 * len(hs)) // 4]) if hs else None,
            # POND (2026-06-12 gap #1): token age-at-entry of the archetype's
            # episodes (known only for mints in the feed's launch registry —
            # age_coverage says how much of the pond we could place).
            "med_age_h": round(ag[len(ag) // 2], 1) if ag else None,
            "p75_age_h": round(ag[(3 * len(ag)) // 4], 1) if ag else None,
            "age_coverage": round(len(ages) / len(rets), 2),
            # PROVENANCE (2026-06-12, AxiS: "how are we identifying which
            # wallets are sending signals?"): exactly which wallets composed
            # this geometry, and how concentrated the signal is. A consumer
            # can require multi-wallet consensus and reject one-wallet boards.
            "wallets": {w[:8]: n for w, n in
                        sorted(by_wallet.items(), key=lambda kv: -kv[1])},
            "n_wallets": len(by_wallet),
            "top_wallet_share": round(max(by_wallet.values()) / len(rets), 3),
        }

    # ── persistence (deploy-amnesia guard) ────────────────────────────────
    def _persist(self) -> None:
        try:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"episodes": self._episodes,
                           "scores": list(self._scores),
                           "unresolved": list(self._unresolved)}, f)
            self._last_persist = time.time()
        except Exception as e:
            logger.debug("[MetaSensor] persist failed: %s", e)

    def _restore(self) -> None:
        try:
            d = json.load(open(_STATE_FILE))
            self._episodes = dict(d.get("episodes") or {})
            self._scores = deque(tuple(s) for s in (d.get("scores") or []))
            self._unresolved = deque(tuple(u) for u in (d.get("unresolved") or []))
        except Exception:
            pass


# module singleton so the meta-allocator shadow (dip_scanner) can read it
_SENSOR: Optional[MetaSensor] = None


def get_sensor() -> Optional[MetaSensor]:
    return _SENSOR


def init_sensor() -> MetaSensor:
    global _SENSOR
    if _SENSOR is None:
        _SENSOR = MetaSensor()
    return _SENSOR
