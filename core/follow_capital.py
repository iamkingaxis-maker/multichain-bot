"""Smart-wallet capital pool + per-pool profit-sweep floor (2026-06-11, AxiS).

smart_follow* gets its OWN pool, isolated from the legacy shared book (the
scalp-pool precedent) — and critically, its OWN sweep floor that starts CLEAN
TODAY. The lifetime sweep sim showed why: the strategy's launch-day tuition
(-$787 since 06-08) would block every sweep forever under a shared floor
("losers block winners' sweeps"). The rebuilt system starts at $0 realized
and banks from its own waterline.

Paper semantics: sweeps are VIRTUAL — hourly, excess-above-floor, $5 minimum,
appended to DATA_DIR/follow_sweeps.jsonl and visible at /api/follow-capital.
This proves the cadence + amounts pre-live. At go-live the same ledger's
excess is what the real profit_sweeper banks for this pool.

State persists across restarts (DATA_DIR/follow_capital.json): realized and
swept totals survive deploys; open-exposure tracking is in-memory (restored
positions re-enter accounting as they close — same accepted gap as scalp).

Env: SMART_FOLLOW_POOL_USD (default 1000), SMART_FOLLOW_FLOOR_USD (default =
pool), SMART_FOLLOW_SWEEP_MIN_USD (default 5).
"""
from __future__ import annotations
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_STATE_FILE = os.path.join(_DATA_DIR, "follow_capital.json")
_SWEEPS_FILE = os.path.join(_DATA_DIR, "follow_sweeps.jsonl")


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


class FollowCapitalManager:
    def __init__(self):
        self.pool = _env_f("SMART_FOLLOW_POOL_USD", 1000.0)
        self.floor = _env_f("SMART_FOLLOW_FLOOR_USD", self.pool)
        self.sweep_min = _env_f("SMART_FOLLOW_SWEEP_MIN_USD", 5.0)
        self.realized = 0.0       # cumulative realized P&L since pool epoch
        self.swept_total = 0.0    # virtually banked to cold
        self.epoch = datetime.now(timezone.utc).isoformat()
        self._open: dict[str, float] = {}   # addr -> deployed usd (in-memory)
        self._last_sweep_check = 0.0
        # per-token realized P&L today (UTC) — feeds smart_follow's
        # won-today re-fire veto (persisted; resets on day roll)
        self.token_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.token_pnl_today: dict[str, float] = {}
        # rolling close P&Ls (last 40) -> the COPY-REGIME DIAL (2026-06-12,
        # AxiS: "so we can detect a bad copy trading regime?"). Persisted.
        self.recent_closes: list[float] = []
        self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self):
        try:
            with open(_STATE_FILE) as f:
                d = json.load(f)
            self.realized = float(d.get("realized", 0.0))
            self.swept_total = float(d.get("swept_total", 0.0))
            self.epoch = d.get("epoch") or self.epoch
            if d.get("token_day") == self.token_day:
                self.token_pnl_today = {k: float(v) for k, v in
                                        (d.get("token_pnl_today") or {}).items()}
            self.recent_closes = [float(x) for x in (d.get("recent_closes") or [])][-40:]
        except Exception:
            pass  # fresh pool

    def _save(self):
        try:
            tmp = _STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"realized": round(self.realized, 6),
                           "swept_total": round(self.swept_total, 6),
                           "epoch": self.epoch,
                           "token_day": self.token_day,
                           "recent_closes": [round(x, 4) for x in self.recent_closes[-40:]],
                           "token_pnl_today": {k: round(v, 4) for k, v in
                                               self.token_pnl_today.items()}}, f)
            os.replace(tmp, _STATE_FILE)
        except Exception as e:
            logger.warning(f"[FollowCapital] save failed: {e}")

    # ── accounting ──────────────────────────────────────────────────────────
    def equity(self) -> float:
        """Hot-pool equity: pool + realized − already-swept (open P&L excluded)."""
        return self.pool + self.realized - self.swept_total

    def deployed(self) -> float:
        return sum(self._open.values())

    def available(self) -> float:
        return self.equity() - self.deployed()

    def can_open(self, usd: float) -> bool:
        return usd <= self.available()

    def record_open(self, addr: str, usd: float):
        self._open[(addr or "").lower()] = usd

    def record_close(self, addr: str, pct: float, pnl_usd: float):
        """pct = fraction of the ORIGINAL position sold in this exit leg."""
        a = (addr or "").lower()
        if a in self._open:
            if pct >= 0.999:
                self._open.pop(a, None)
            else:
                self._open[a] = max(0.0, self._open[a] * (1 - pct))
        self.realized += pnl_usd
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.token_day:
            self.token_day, self.token_pnl_today = today, {}
        self.token_pnl_today[a] = self.token_pnl_today.get(a, 0.0) + pnl_usd
        self.recent_closes.append(pnl_usd)
        del self.recent_closes[:-40]
        self._save()
        self.maybe_sweep()

    # ── the per-pool sweep (virtual in paper) ───────────────────────────────
    def maybe_sweep(self) -> float:
        now = time.monotonic()
        if now - self._last_sweep_check < 3600:   # hourly cadence
            return 0.0
        self._last_sweep_check = now
        excess = self.equity() - self.floor
        if excess < self.sweep_min:
            return 0.0
        self.swept_total += excess
        self._save()
        evt = {"ts": datetime.now(timezone.utc).isoformat(),
               "swept_usd": round(excess, 2),
               "swept_total": round(self.swept_total, 2),
               "equity_after": round(self.equity(), 2)}
        try:
            with open(_SWEEPS_FILE, "a") as f:
                f.write(json.dumps(evt) + "\n")
        except Exception:
            pass
        logger.info(f"[FollowCapital] 🏦 SWEEP ${excess:+.2f} -> cold "
                    f"(total banked ${self.swept_total:.2f}, hot back to floor "
                    f"${self.floor:.0f}) [paper-virtual]")
        return excess

    def day_realized(self) -> float:
        """Today's (UTC) realized P&L across the pool."""
        if datetime.now(timezone.utc).strftime("%Y-%m-%d") != self.token_day:
            return 0.0
        return sum(self.token_pnl_today.values())

    def daily_floor_hit(self) -> bool:
        """Pool circuit breaker (2026-06-12): the fleet candidates all carry
        daily floors, but smart_follow bypasses the bot evaluator — the pool
        could grind down all night with nothing stopping it (overnight
        2026-06-11: ~115 fires/6h from night-active recruits, -$13.66 drip).
        When today's realized <= -floor, the strategy stops FIRING (open
        positions still manage/exit normally). Env SMART_FOLLOW_DAILY_FLOOR_USD
        (default 40), 0/off disables."""
        raw = os.environ.get("SMART_FOLLOW_DAILY_FLOOR_USD", "40").strip().lower()
        if raw in ("0", "off", "false", ""):
            return False
        try:
            floor = float(raw)
        except Exception:
            floor = 40.0
        return self.day_realized() <= -abs(floor)

    def copy_dial(self) -> dict:
        """Copy-regime dial: rolling-20 expectancy of the follow book.
        bad = exp < -$1/close at n>=12 (the overnight grind read -$2 to -$3
        by 01:30); good = exp > +$0.5. Mirrors the P7 dial design — the
        record itself is the regime signal, no clock excuses."""
        last = self.recent_closes[-20:]
        if len(last) < 12:
            return {"state": "warming", "exp": None, "n": len(last)}
        exp = sum(last) / len(last)
        state = "bad" if exp < -1.0 else ("good" if exp > 0.5 else "neutral")
        return {"state": state, "exp": round(exp, 2), "n": len(last)}

    def won_today(self, addr: str) -> bool:
        """True if this token's realized P&L today is positive (and it's today)."""
        if datetime.now(timezone.utc).strftime("%Y-%m-%d") != self.token_day:
            return False
        return self.token_pnl_today.get((addr or "").lower(), 0.0) > 0

    def status(self) -> dict:
        return {
            "pool_usd": self.pool,
            "floor_usd": self.floor,
            "epoch": self.epoch,
            "realized_since_epoch": round(self.realized, 2),
            "swept_total": round(self.swept_total, 2),
            "hot_equity": round(self.equity(), 2),
            "deployed": round(self.deployed(), 2),
            "available": round(self.available(), 2),
            "open_positions": len(self._open),
            "day_realized": round(self.day_realized(), 2),
            "daily_floor_hit": self.daily_floor_hit(),
            "copy_dial": self.copy_dial(),
            "mode": "paper-virtual (real transfers at go-live via profit_sweeper)",
        }
