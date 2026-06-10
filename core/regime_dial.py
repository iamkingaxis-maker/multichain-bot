"""P7 regime dial (2026-06-10) — day-level size discipline, defense-first.

Walk-forward study (analysis/2026-06/_regime_beat.py, 9 days / 1,263 candidate
closes, thresholds set a priori from the 06-08 day-level mine): the consensus
dial min(yesterday-WR, first-quarter-WR, rolling-20-expectancy) removed ~63%
of the bleed (-$677 -> -$250) while keeping the one good day's upside.

ENFORCEMENT IS ASYMMETRIC (AxiS-approved 2026-06-10):
  - defense (multiplier < 1.0) ENFORCES on dip-pond sizing immediately
  - offense (1.5x upsize) is SHADOW-ONLY until its forward forecast record
    earns it (size amplification is the historically dangerous direction)

PRE-REGISTERED FORECAST (judge via scripts/badday_scorecard.py):
  on days the dial reads bad (<1.0) the dip-pond cohort's per-close loss
  should be materially smaller than the same cohort's unscaled P&L; judge at
  >=5 dial-bad days. Kill: if the dial's bad-day calls are <50% accurate
  against realized day sign at n>=10 forecasts, demote to shadow.

Live deviations from the study (documented, not hidden):
  - signals computed over ALL store sells (fleet) rather than the candidate
    subset — broader and stabler server-side; direction identical.
  - "first quarter of the day" is approximated walk-forward as the first
    max(10, yesterday_n//4) closes (the study's quarter used the day's final
    count, which isn't knowable intraday).

Env: REGIME_DIAL_MODE=enforce|shadow|off (default enforce).
"""
from __future__ import annotations
import logging
import os
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_CACHE_SEC = 120.0


def _dial_mode() -> str:
    m = os.environ.get("REGIME_DIAL_MODE", "enforce").strip().lower()
    return m if m in ("enforce", "shadow", "off") else "enforce"


def _ct_day(iso: str) -> str | None:
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
    except Exception:
        return None


class RegimeDial:
    def __init__(self):
        self._provider = None      # () -> list[trade dict]
        self._cached = None
        self._cached_at = 0.0

    def set_trades_provider(self, fn):
        self._provider = fn

    def _compute(self) -> dict:
        out = {"mult_full": 1.0, "mult_defense": 1.0, "signals": {}, "mode": _dial_mode()}
        if self._provider is None:
            out["signals"]["error"] = "no provider"
            return out
        try:
            trades = self._provider() or []
        except Exception as e:
            out["signals"]["error"] = f"provider: {e}"
            return out
        sells = []
        for t in trades:
            if t.get("type") != "sell":
                continue
            if "cancelled on restart" in (t.get("reason") or "").lower():
                continue
            d = _ct_day(t.get("time"))
            if d:
                sells.append((t.get("time"), d, float(t.get("pnl") or 0)))
        sells.sort()
        today = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")
        y_sells = [p for _, d, p in sells if d < today]
        y_days = sorted({d for _, d, _ in sells if d < today})
        m1 = m2 = m3 = 1.0
        # 1) yesterday fleet WR
        if y_days:
            yd = y_days[-1]
            yp = [p for _, d, p in sells if d == yd]
            if len(yp) >= 20:
                wr = sum(1 for p in yp if p > 0) / len(yp)
                m1 = 0.5 if wr < 0.55 else (1.5 if wr > 0.65 else 1.0)
                out["signals"]["yesterday_wr"] = round(wr, 3)
                out["signals"]["yesterday_n"] = len(yp)
        # 2) first-quarter-of-day WR (walk-forward approximation)
        tp = [p for _, d, p in sells if d == today]
        if y_days:
            yd = y_days[-1]
            yn = sum(1 for _, d, _ in sells if d == yd)
            q = max(10, yn // 4)
            if len(tp) >= q:
                qw = sum(1 for p in tp[:q] if p > 0) / q
                m2 = 0.5 if qw < 0.5 else (1.5 if qw > 0.65 else 1.0)
                out["signals"]["quarter_wr"] = round(qw, 3)
                out["signals"]["quarter_n"] = q
        # 3) rolling-20 expectancy (cross-day, catches loss-size days WR misses)
        last20 = [p for _, _, p in sells[-20:]]
        if len(last20) >= 20:
            ev = sum(last20) / 20
            m3 = 0.5 if ev < -1.0 else (1.5 if ev > 1.5 else 1.0)
            out["signals"]["rolling20_ev"] = round(ev, 3)
        full = min(m1, m2, m3)
        out["signals"]["m_yesterday"] = m1
        out["signals"]["m_quarter"] = m2
        out["signals"]["m_rolling"] = m3
        out["mult_full"] = full
        out["mult_defense"] = min(full, 1.0)
        return out

    def current(self) -> dict:
        now = time.monotonic()
        if self._cached is None or now - self._cached_at > _CACHE_SEC:
            prev = (self._cached or {}).get("mult_full")
            self._cached = self._compute()
            self._cached_at = now
            if self._cached.get("mult_full") != prev:
                logger.info(f"[RegimeDial] mult={self._cached['mult_full']:g} "
                            f"(defense={self._cached['mult_defense']:g}) "
                            f"signals={self._cached['signals']}")
        return self._cached

    def defense_multiplier(self) -> float:
        if _dial_mode() != "enforce":
            return 1.0
        return self.current()["mult_defense"]


_DIAL = RegimeDial()


def get_dial() -> RegimeDial:
    return _DIAL
