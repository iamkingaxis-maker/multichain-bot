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
        # scored events: deque of (ts, wallet, archetype, ret_pct)
        self._scores: deque = deque()
        self._last_persist = 0.0
        self._restore()
        logger.info("[MetaSensor] panel=%d wallets (%d archetyped)",
                    len(self.panel),
                    sum(1 for m in self.panel.values() if m.get("archetype")))

    # ── ingestion (called from the PumpPortal stream) ─────────────────────
    def ingest(self, wallet: str, mint: str, side: str, sol: float, ts: float) -> None:
        """One parsed panel-wallet trade. Never raises."""
        try:
            if wallet not in self.panel or not mint or sol is None:
                return
            k = f"{wallet}|{mint.lower()}"
            ep = self._episodes.get(k)
            if ep is None:
                if side != "buy":
                    return   # sell with no observed buy -> position predates us; skip
                ep = self._episodes[k] = {"spent": 0.0, "recv": 0.0,
                                          "first_ts": ts, "last_ts": ts,
                                          "sold": False}
            if side == "buy":
                ep["spent"] += float(sol)
            elif side == "sell":
                ep["recv"] += float(sol)
                ep["sold"] = True
            ep["last_ts"] = ts
            self._finalize_idle(ts)
            if ts - self._last_persist > _PERSIST_EVERY_SECS:
                self._persist()
        except Exception as e:
            logger.debug("[MetaSensor] ingest error: %s", e)

    def _finalize_idle(self, now: float) -> None:
        done = [k for k, ep in self._episodes.items()
                if ep["sold"] and now - ep["last_ts"] > EPISODE_IDLE_SECS]
        for k in done:
            ep = self._episodes.pop(k)
            if ep["spent"] <= 0:
                continue
            wallet = k.split("|", 1)[0]
            ret = (ep["recv"] / ep["spent"] - 1.0) * 100.0
            arch = (self.panel.get(wallet) or {}).get("archetype") or "unlabeled"
            hold = max(0.0, ep["last_ts"] - ep.get("first_ts", ep["last_ts"]))
            self._scores.append((ep["last_ts"], wallet, arch, ret, hold))
        cutoff = now - SCORE_RETENTION_SECS
        while self._scores and self._scores[0][0] < cutoff:
            self._scores.popleft()

    # ── the board ─────────────────────────────────────────────────────────
    def scoreboard(self, now: Optional[float] = None) -> dict:
        now = now or time.time()
        self._finalize_idle(now)
        out: dict = {"panel_size": len(self.panel),
                     "open_episodes": len(self._episodes),
                     "windows": {}}
        for label, secs in (("6h", 6 * 3600), ("24h", 24 * 3600)):
            cut = now - secs
            rows: Dict[str, list] = {}
            for ts, _w, arch, ret, *_h in self._scores:
                if ts < cut:
                    continue
                rows.setdefault(arch, []).append(ret)
                rows.setdefault("all", []).append(ret)
            out["windows"][label] = {
                arch: {"n": len(v),
                       "wr": round(sum(1 for r in v if r > 0) / len(v), 3),
                       "med_ret_pct": round(sorted(v)[len(v) // 2], 1)}
                for arch, v in rows.items() if v
            }
        return out

    def archetype_geometry(self, arch: str, now: Optional[float] = None,
                           window_secs: float = 6 * 3600,
                           min_n: int = 8) -> Optional[dict]:
        """The winning style's measurable GEOMETRY over the window — what a
        dynamic bot needs to re-tune itself: hold distribution + win/loss
        return shapes. None if the sample is too thin to act on."""
        now = now or time.time()
        self._finalize_idle(now)
        cut = now - window_secs
        rets, holds = [], []
        for ts, _w, a, ret, *h in self._scores:
            if ts < cut or a != arch:
                continue
            rets.append(ret)
            if h and isinstance(h[0], (int, float)):
                holds.append(float(h[0]))
        if len(rets) < min_n:
            return None
        wins = sorted(r for r in rets if r > 0)
        losses = sorted(r for r in rets if r <= 0)
        hs = sorted(holds)
        return {
            "n": len(rets),
            "wr": round(len(wins) / len(rets), 3),
            "med_win_pct": round(wins[len(wins) // 2], 1) if wins else None,
            "med_loss_pct": round(losses[len(losses) // 2], 1) if losses else None,
            "med_hold_secs": round(hs[len(hs) // 2]) if hs else None,
            "p75_hold_secs": round(hs[(3 * len(hs)) // 4]) if hs else None,
        }

    # ── persistence (deploy-amnesia guard) ────────────────────────────────
    def _persist(self) -> None:
        try:
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"episodes": self._episodes,
                           "scores": list(self._scores)}, f)
            self._last_persist = time.time()
        except Exception as e:
            logger.debug("[MetaSensor] persist failed: %s", e)

    def _restore(self) -> None:
        try:
            d = json.load(open(_STATE_FILE))
            self._episodes = dict(d.get("episodes") or {})
            self._scores = deque(tuple(s) for s in (d.get("scores") or []))
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
