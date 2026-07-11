# core/rh_regime.py
"""RH-chain REGIME layer v1 (2026-07-11) — pure + cheap, computed entirely
from in-memory state the paper lane already holds.

Design rules (Solana lessons, hard constraints):
  1. STAMPS fleet-wide, always: every entry ledger row carries the full
     regime snapshot so any future rule can be graded retroactively.
  2. ENFORCEMENT only for rules that held in BOTH disjoint history windows
     (chrono halves 07-01..05 vs 07-06..11 AND odd/even-day parity), and only
     as per-racer opt-in flags — never a blanket fleet block.
  3. The rolling-expectancy DIAL is a STAMP (offense/defense signal recorded
     per entry). It never halts paper buys — paper = data; its record grades
     it for the eventual live lane.
  4. Axes: hour-of-day + DEMAND COMPOSITION + pool-discovery rate (the axes
     that ran the Solana market), pool-AGE interaction (AxiS hypothesis:
     young tokens regime-flat), ETH move as documented candidate.

Mining provenance: scratchpad/rh_regime/{mine_regimes.py, analyze_regimes.py,
rulebook_v1_tables.json} over the full-history sweep (10.36M swaps, all WETH
pools, 2026-07-01..11) — synthetic dip trips (the lane's own trigger replayed
maker-less) resolved at +20m, rug = trough <= 0.2x entry within 60m.
Headline numbers are cited next to each constant below.
"""
from collections import deque
from typing import Optional

# ── pool age bands (the required interaction axis) ──────────────────────────
YOUNG_MAX_AGE_H = 6.0     # matches AGED_MIN_POOL_AGE_H in the paper lane
MID_MAX_AGE_H = 24.0      # matches the feed's historical MAX_AGE_H ceiling


def age_band(age_h) -> Optional[str]:
    """Pool age -> 'young' (<6h) / 'mid' (6-24h) / 'aged' (>=24h); None when
    age is unknown (gate consumers must fail OPEN on None)."""
    if age_h is None:
        return None
    if age_h < YOUNG_MAX_AGE_H:
        return "young"
    if age_h < MID_MAX_AGE_H:
        return "mid"
    return "aged"


# ── hour blocks (analysis/rulebook unit; stamped hour stays raw) ─────────────
HOUR_BLOCKS = {"22-01": (22, 23, 0, 1), "02-07": (2, 3, 4, 5, 6, 7),
               "08-10": (8, 9, 10), "11-13": (11, 12, 13),
               "14-18": (14, 15, 16, 17, 18), "19-21": (19, 20, 21)}


def hour_block(hour_utc: int) -> Optional[str]:
    for k, hs in HOUR_BLOCKS.items():
        if int(hour_utc) in hs:
            return k
    return None


# ── ENFORCED v1 gate: aged-band 19-21 UTC block ─────────────────────────────
# The ONE hour rule that passed the two-window bar (rulebook v1, 39,132 mined
# dip trips): >24h pools underperform in 19-21 UTC in ALL FOUR halves —
# chrono W1 -7.3pp (n=340) / W2 -1.3pp (n=1421) / even -0.7pp (n=776) /
# odd -3.6pp (n=985) vs band base, median ret negative in each. Held in both
# eras -> NOT era-conditional. Everything else on the hour axis either failed
# a half (young 19-21, 22-01 "dead zone", 14-18) or is favorable (young+aged
# 02-07/08-10 — favorable blocks are scheduling guidance, never gates).
# Per-racer opt-in (LaneBot.regime_hours); fails OPEN on unknown age/hour.
AGED_BLOCK_HOURS_UTC = (19, 20, 21)


def aged_hour_gate_ok(hour_utc, age_h) -> bool:
    """v1 regime hour gate: False (block) ONLY for an aged-band (>24h) pool
    in 19-21 UTC. Young/mid pools and unknown ages always pass — the mined
    mid band showed NO consistent hour rule and the young band's only
    consistent hour signal is favorable (02-07), which is not a block."""
    if hour_utc is None or age_band(age_h) != "aged":
        return True
    return int(hour_utc) not in AGED_BLOCK_HOURS_UTC


# ── discovery-rate regime (bot era vs human era) ─────────────────────────────
# decode chain facts (_rh_history_decode.md): human era 800-2,600 pools/day
# (33-108/h) vs bot era 14k-20k/day (583-833/h); 200/h splits the gap. The
# paper lane's REGIME_BOT_ERA_POOLS_H aliases this constant.
DISC_BOT_ERA_POOLS_H = 200.0


def discovery_regime(new_pools_per_hour,
                     bot_era_rate: float = DISC_BOT_ERA_POOLS_H
                     ) -> Optional[str]:
    """Observed pool-discovery rate -> 'bot' / 'human'; None during warm-up
    (rate unknown). Consumers fail OPEN on None — the chain's CURRENT era is
    the bot era."""
    if new_pools_per_hour is None:
        return None
    return "bot" if new_pools_per_hour >= bot_era_rate else "human"


# ── demand-composition window (feed-wide rolling flow) ───────────────────────
COMP_WINDOW_S = 1800.0    # 30-min window, the mined unit


class CompositionTracker:
    """Rolling feed-wide flow composition over the last COMP_WINDOW_S.

    ingest() is O(1) amortized (called from the tape drain — hot path);
    snapshot() is O(1). Distinct-pool count is maintained incrementally via
    per-pool row counts. Pure in-memory; no network, no clock reads (caller
    passes `now` — the same feed clock the lane uses everywhere)."""

    def __init__(self, window_s: float = COMP_WINDOW_S):
        self.window_s = float(window_s)
        self._rows = deque()          # (ts, pool, is_buy, usd)
        self._pool_n = {}             # pool -> in-window row count
        self._buy_usd = 0.0
        self._sell_usd = 0.0
        self._n_buys = 0
        self._n_sells = 0

    def ingest(self, ts: float, pool: str, kind: str, usd) -> None:
        usd = float(usd or 0.0)
        is_buy = kind == "buy"
        self._rows.append((ts, pool, is_buy, usd))
        self._pool_n[pool] = self._pool_n.get(pool, 0) + 1
        if is_buy:
            self._buy_usd += usd
            self._n_buys += 1
        else:
            self._sell_usd += usd
            self._n_sells += 1
        self._prune(ts)

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_s
        rows = self._rows
        while rows and rows[0][0] < cutoff:
            ts, pool, is_buy, usd = rows.popleft()
            n = self._pool_n.get(pool, 0) - 1
            if n <= 0:
                self._pool_n.pop(pool, None)
            else:
                self._pool_n[pool] = n
            if is_buy:
                self._buy_usd -= usd
                self._n_buys -= 1
            else:
                self._sell_usd -= usd
                self._n_sells -= 1

    def snapshot(self, now: float) -> dict:
        self._prune(now)
        tot = self._buy_usd + self._sell_usd
        return {
            "buy_usd": round(self._buy_usd, 2),
            "sell_usd": round(self._sell_usd, 2),
            "netflow_usd": round(self._buy_usd - self._sell_usd, 2),
            "buy_share": (round(self._buy_usd / tot, 4) if tot > 0 else None),
            "n_buys": self._n_buys,
            "n_sells": self._n_sells,
            "distinct_pools": len(self._pool_n),
        }


# ── rolling-expectancy dial (STAMP ONLY — never a paper buy-halt) ───────────
DIAL_MIN_N = 10           # below this the dial reads None (no signal)
DIAL_WINDOW_N = 20        # judge the last N closed positions


def expectancy_dial(recent_pnls, min_n: int = DIAL_MIN_N,
                    window_n: int = DIAL_WINDOW_N) -> dict:
    """Last-N closed-position realized P&L -> offense/defense STAMP.
    {'state': 'offense'|'defense'|None, 'exp_usd': mean|None, 'n': int}.
    Recorded on every entry so the dial's own record grades it (promotion to
    a live-lane control needs that record at n>=30 — see the pre-registered
    grading plan in scratchpad/_rh_regime_system.md)."""
    pnls = list(recent_pnls)[-window_n:]
    if len(pnls) < min_n:
        return {"state": None, "exp_usd": None, "n": len(pnls)}
    exp = sum(pnls) / len(pnls)
    return {"state": "offense" if exp > 0 else "defense",
            "exp_usd": round(exp, 2), "n": len(pnls)}


# ── the per-entry stamp (fleet-wide, always) ─────────────────────────────────
def regime_stamp(hour_utc: int, new_pools_per_hour, comp: dict,
                 dial: Optional[dict] = None, eth_usd=None,
                 age_h=None) -> dict:
    """Assemble the regime STAMP for one entry ledger row. Pure dict shaping;
    every field is decision-time observable (no forward peeking)."""
    npph = (round(float(new_pools_per_hour), 1)
            if new_pools_per_hour is not None else None)
    return {
        "hour_utc": int(hour_utc),
        "npph": npph,
        "disc": discovery_regime(new_pools_per_hour),
        "band": age_band(age_h),
        "buy_share_30m": comp.get("buy_share"),
        "netflow_30m_usd": comp.get("netflow_usd"),
        "distinct_pools_30m": comp.get("distinct_pools"),
        "n_swaps_30m": comp.get("n_buys", 0) + comp.get("n_sells", 0),
        "dial": (dial or {}).get("state"),
        "dial_exp_usd": (dial or {}).get("exp_usd"),
        "eth_usd": (round(float(eth_usd), 2) if eth_usd else None),
    }
