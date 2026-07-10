# core/runner_signal.py
"""Monster-vs-regular runner decode — SHADOW-ONLY scorer (2026-07-10 mine).

At the +5..15% decision moment, monster runners (>=+40%) are separable from
regular winners (+8..20%) via 4 tape-shape features (token-level AUC 0.84,
scratchpad/_runner_signature_report.md):

  net_ratio       (buy$-sell$)/(buy$+sell$) over the decision window
                  (monsters ~+0.15 vs regulars ~+0.02)
  bpm_accel       buys/min 2nd half / 1st half (monsters hold ~1.0+,
                  regular pops DECAY to ~0.7)
  med_buy_rel     median buy $ vs the pre-run median (monsters ~1.67:
                  buyers upsize into strength)
  new_maker_frac  window buyers unseen in the pre-run tape (~0.51 vs ~0.42)

Surprise worth restating: wallet-diversity-per-dollar is ANTI-predictive —
monsters are fewer, BIGGER buyers, not a swarm of small wallets.

Round thresholds, no ML: flow=net_ratio/0.2, accel=(bpm_accel-0.6)/0.6,
size=(med_buy_rel-1.0)/1.0, fresh=(new_frac-0.35)/0.3, each clipped 0..1;
score = mean of the AVAILABLE subscores. Missing maker data (GT fallback
strips maker) degrades to a subset score noted in reasons — NEVER to 0
(read-as-zero bug class). <20 window trades -> (None, ...) — never 0.

SHADOW FIRST: stamped as runner_score/runner_reasons on sell records; no
decision reads it until >=30 stamped exits validate against realized peak.

Trades: {kind:'buy'|'sell', volume_usd:float, ts:ISO8601|epoch, maker:str}.
Pure; never raises. HoldTape below is the pure accumulator the Solana
scanner feeds from its recent-trades polls while positions are open.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.retrace_microstructure import _epoch

WINDOW_SECS = 600.0        # decision window (last 10 min)
PRE_RUN_SECS = 600.0       # pre-run reference window (the 10 min before that)
MIN_WINDOW_TRADES = 20     # below this the tape can't be read -> None


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _vol(t: Dict[str, Any]) -> float:
    try:
        v = float(t.get("volume_usd") or 0.0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else 0.0


def runner_score(trades: Sequence[Dict[str, Any]],
                 window_start_ts, now,
                 pre_run_trades: Optional[Sequence[Dict[str, Any]]] = None,
                 ) -> Tuple[Optional[float], Dict[str, Any]]:
    """Score the decision window [window_start_ts, now] for monster-shape.

    Returns (score 0..1 | None, reasons dict). None = unreadable tape
    (thin/no-buys/bad window) — callers must treat None as "no signal",
    never as 0. reasons carries the raw features, the subscores actually
    used, n_trades, and a `degraded` list naming any dropped subscores.
    """
    ws, ref = _epoch(window_start_ts), _epoch(now)
    if ws is None or ref is None or ref <= ws:
        return None, {"reason": "bad_window", "n_trades": 0}
    win: List[Tuple[float, str, float, str]] = []
    for t in trades or ():
        e = _epoch(t.get("ts"))
        if e is None or not (ws <= e <= ref):
            continue
        win.append((e, str(t.get("kind", "")).lower(), _vol(t),
                    str(t.get("maker") or "")))
    n = len(win)
    buys = [w for w in win if w[1] == "buy"]
    sells = [w for w in win if w[1] == "sell"]
    bvol = sum(w[2] for w in buys)
    svol = sum(w[2] for w in sells)
    if n < MIN_WINDOW_TRADES or bvol <= 0:
        return None, {"reason": "thin_tape", "n_trades": n}

    degraded: List[str] = []
    half = (ws + ref) / 2.0

    # 1. flow: dollar net-ratio over the whole window
    net_ratio = (bvol - svol) / (bvol + svol)

    # 2. accel: buys/min 2nd half vs 1st half (counts; equal-length halves)
    n1 = sum(1 for w in buys if w[0] < half)
    n2 = len(buys) - n1
    bpm_accel = n2 / max(n1, 1)

    # 3. size: median window buy $ vs pre-run median buy $ (default 1.0
    #    when there is no pre-run baseline -> size subscore reads 0)
    sizes = sorted(w[2] for w in buys)
    med_buy = sizes[len(sizes) // 2]
    pre_buy_sizes = sorted(
        _vol(t) for t in (pre_run_trades or ())
        if str(t.get("kind", "")).lower() == "buy" and _vol(t) > 0)
    if pre_buy_sizes:
        med_buy_rel = med_buy / max(pre_buy_sizes[len(pre_buy_sizes) // 2], 1e-9)
    else:
        med_buy_rel = 1.0
        degraded.append("no_prerun_size_baseline")

    # 4. fresh: window buy-makers unseen in the pre-run tape. Needs BOTH
    #    window maker data AND a non-empty pre-run maker baseline; either
    #    missing -> drop the subscore (subset mean), never fabricate 0.
    new_maker_frac: Optional[float] = None
    win_makers = {w[3] for w in buys if w[3]}
    pre_makers = {str(t.get("maker") or "") for t in (pre_run_trades or ())}
    pre_makers.discard("")
    if not win_makers:
        degraded.append("no_maker_data")
    elif not pre_makers:
        degraded.append("no_prerun_makers")
    else:
        new_maker_frac = len(win_makers - pre_makers) / len(win_makers)

    subs: Dict[str, float] = {
        "flow": _clip01(net_ratio / 0.2),
        "accel": _clip01((bpm_accel - 0.6) / 0.6),
        "size": _clip01((med_buy_rel - 1.0) / 1.0),
    }
    if new_maker_frac is not None:
        subs["fresh"] = _clip01((new_maker_frac - 0.35) / 0.3)
    score = sum(subs.values()) / len(subs)
    reasons: Dict[str, Any] = {
        "n_trades": n,
        "net_ratio": round(net_ratio, 3),
        "bpm_accel": round(bpm_accel, 2),
        "med_buy_rel": round(med_buy_rel, 2),
        "new_maker_frac": (round(new_maker_frac, 3)
                           if new_maker_frac is not None else None),
        "subs": {k: round(v, 3) for k, v in subs.items()},
        "degraded": degraded,
    }
    return round(score, 3), reasons


def score_at_exit(trades: Sequence[Dict[str, Any]], now,
                  window_secs: float = WINDOW_SECS,
                  pre_secs: float = PRE_RUN_SECS,
                  ) -> Tuple[Optional[float], Dict[str, Any]]:
    """Convenience wrapper for the exit-time stamp: decision window = the
    last `window_secs` of `trades`, pre-run = the `pre_secs` before that
    (when any of it exists in the buffer). Pure; never raises."""
    ref = _epoch(now)
    if ref is None:
        return None, {"reason": "bad_window", "n_trades": 0}
    ws = ref - float(window_secs)
    pre = []
    for t in trades or ():
        e = _epoch(t.get("ts"))
        if e is not None and (ws - float(pre_secs)) <= e < ws:
            pre.append(t)
    return runner_score(trades, ws, ref, pre_run_trades=pre or None)


class HoldTape:
    """Bounded per-token trade-tape accumulator for OPEN positions.

    Pure logic (no I/O, no clock reads): the scanner polls recent trades
    for every held pair (~45s) and feeds them here; runner_score reads the
    buffer at exit time. Semantics:
      - dedupe by (ts, maker, volume_usd) — overlapping polls are expected
      - per-key row cap (drop oldest) bounds memory on hot tokens
      - retention: a key survives until `retain_secs` after sync_open()
        first sees it closed (so the exit-time stamp and any trailing exit
        legs still have tape); re-opening clears the countdown
    """

    def __init__(self, cap_rows: int = 2000, retain_secs: float = 1800.0):
        self.cap = int(cap_rows)
        self.retain = float(retain_secs)
        self._buf: Dict[str, List[Dict[str, Any]]] = {}
        self._seen: Dict[str, set] = {}
        self._closed_ts: Dict[str, float] = {}

    @staticmethod
    def _key(t: Dict[str, Any]) -> tuple:
        return (str(t.get("ts")), str(t.get("maker") or ""),
                round(_vol(t), 6))

    def add(self, key, trades: Sequence[Dict[str, Any]], now=None) -> int:
        """Merge a poll's trades into `key`'s buffer. Returns rows added."""
        k = str(key)
        buf = self._buf.setdefault(k, [])
        seen = self._seen.setdefault(k, set())
        added = 0
        for t in trades or ():
            if not isinstance(t, dict):
                continue
            dk = self._key(t)
            if dk in seen:
                continue
            seen.add(dk)
            buf.append({"kind": t.get("kind"), "volume_usd": _vol(t),
                        "ts": t.get("ts"), "maker": str(t.get("maker") or "")})
            added += 1
        if len(buf) > self.cap:
            del buf[: len(buf) - self.cap]
            self._seen[k] = {self._key(t) for t in buf}
        return added

    def get(self, key) -> List[Dict[str, Any]]:
        return list(self._buf.get(str(key)) or ())

    def keys(self) -> List[str]:
        return list(self._buf)

    def sync_open(self, open_keys, now) -> None:
        """Reconcile with the CURRENT open-position set: open keys get their
        close-countdown cleared; tracked keys no longer open start (or
        continue) theirs; anything closed >= retain_secs ago is dropped."""
        now = float(now)
        opened = {str(x) for x in (open_keys or ())}
        for k in opened:
            self._closed_ts.pop(k, None)
        for k in list(self._buf):
            if k not in opened and k not in self._closed_ts:
                self._closed_ts[k] = now
        for k, ts in list(self._closed_ts.items()):
            if now - ts >= self.retain:
                self._buf.pop(k, None)
                self._seen.pop(k, None)
                self._closed_ts.pop(k, None)
