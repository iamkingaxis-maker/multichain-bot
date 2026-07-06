# core/fast_watch.py
"""Pure logic for the fast-watch loop (no scanner/asyncio imports).

The fast-watch loop re-checks the already-watched token cohort every few
seconds and escalates fresh dips into the existing scanner evaluation, instead
of waiting for the compute-bound ~150-165s main sweep. This module holds only
the cheap, deterministic decision logic so it is trivially unit-testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

# Live pool + dip-entry heavy hitters; used when FAST_WATCH_BOT_ALLOWLIST unset.
_DEFAULT_ALLOWLIST = frozenset({
    "badday_flush", "badday_flush_conviction", "deepflush_timebox", "timebox_probe_5mgreen",
    "badday_flush_live", "badday_flush_conviction_live", "deepflush_timebox_live",
    "timebox_probe_5mgreen_live",
})


def _f(env_key: str, default: float) -> float:
    try:
        return float(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


def _i(env_key: str, default: int) -> int:
    try:
        return int(os.environ.get(env_key, "").strip())
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FastWatchConfig:
    mode: str                 # "off" | "shadow" | "enforce"
    interval_secs: float
    dip_pct: float
    rise_pct: float
    eval_cooldown_secs: float
    bot_allowlist: frozenset
    armed_max: int
    sample_window: int
    arm_band_pp: float
    hot_max: int
    full_poll_every: int

    @classmethod
    def from_env(cls) -> "FastWatchConfig":
        mode = os.environ.get("FAST_WATCH_MODE", "off").strip().lower()
        if mode not in ("off", "shadow", "enforce"):
            mode = "off"
        raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
        allow = (frozenset(b.strip() for b in raw.split(",") if b.strip())
                 if raw else _DEFAULT_ALLOWLIST)
        return cls(
            mode=mode,
            interval_secs=_f("FAST_WATCH_INTERVAL_SECS", 3.0),
            dip_pct=_f("FAST_WATCH_DIP_PCT", 3.0),
            rise_pct=_f("FAST_WATCH_RISE_PCT", 3.0),
            eval_cooldown_secs=_f("FAST_WATCH_EVAL_COOLDOWN_SECS", 60.0),
            bot_allowlist=allow,
            armed_max=_i("FAST_WATCH_ARMED_MAX", 500),
            sample_window=_i("FAST_WATCH_SAMPLE_WINDOW", 40),
            arm_band_pp=_f("FAST_WATCH_ARM_BAND_PP", 15.0),
            # TIERED POLL: hot subset (top-N armed by volume.h1) is polled EVERY
            # tick for ~3s freshness; the FULL armed set is polled every
            # full_poll_every-th tick for ~9s coverage. hot_max=50 keeps the
            # hot tier at exactly ONE Jupiter 50-id call. See _fast_watch_tick
            # for the req/min rate math.
            hot_max=_i("FAST_WATCH_HOT_MAX", 50),
            full_poll_every=max(1, _i("FAST_WATCH_FULL_POLL_EVERY", 3)),
        )


def dip_trigger(trend_pct: Optional[float], threshold_pct: float) -> bool:
    """True when the token dipped at least `threshold_pct` over the trend window.

    Deliberately a LOOSE superset signal — the real entry gates inside
    `_evaluate_pair` make the actual buy decision. None (no buffered ticks) never
    triggers, so the fast loop is best-effort and the main sweep stays the net.
    """
    if trend_pct is None:
        return False
    return trend_pct <= -abs(threshold_pct)


class ChartMemo:
    """Short-TTL, ADDRESS-keyed memo for the per-token chart_data the MAIN scan
    just assembled, so a fast-watch survivor eval can REUSE it instead of either
    cold-fetching fresh GT OHLC (429 storm) or degrading to a None chart.

    Pure + clock-injected for testability. ADDRESS-keyed only (NEVER symbol — a
    symbol-keyed chart cross-poisons same-ticker mints, the 2026-06-12 SPCX bug).
    `get` returns the memoized value only within `ttl_secs` of the `put`; after
    that it MISSes (returns the sentinel) so a stale chart never drives a buy.
    Fail-safe by construction: a MISS just means the caller takes its existing
    fallback path (cold fetch / None), so the memo can only ever HELP.
    """

    MISS = object()

    def __init__(self, ttl_secs: float):
        self.ttl = float(ttl_secs)
        self._d: dict[str, tuple[float, object]] = {}

    def put(self, address: str, chart_data, now: float) -> None:
        if not address:
            return
        self._d[address.lower()] = (float(now), chart_data)

    def get(self, address: str, now: float):
        if not address:
            return ChartMemo.MISS
        hit = self._d.get(address.lower())
        if hit is None:
            return ChartMemo.MISS
        ts, val = hit
        if (now - ts) > self.ttl:
            # Expired — drop it so the dict doesn't grow unbounded over a long run.
            self._d.pop(address.lower(), None)
            return ChartMemo.MISS
        return val

    def purge_expired(self, now: float) -> None:
        """Drop expired entries (called opportunistically to bound memory)."""
        dead = [k for k, (ts, _v) in self._d.items() if (now - ts) > self.ttl]
        for k in dead:
            self._d.pop(k, None)


def chart_memo_enabled() -> bool:
    """Whether the fast-watch eval reuses the main scan's freshly-assembled chart
    via ChartMemo (FEATURE_MEMO, default 'on'). off/0/false/no disables it (the
    fast path then degrades to None on a prefetch miss, as before)."""
    v = os.environ.get("FEATURE_MEMO", "on").strip().lower()
    return v not in ("off", "0", "false", "no")


def chart_memo_ttl_secs() -> float:
    """TTL (seconds) for ChartMemo (FEATURE_MEMO_TTL_S, default 20.0, floor 1.0).
    Short by design: a memecoin chart older than ~20s is stale for entry timing,
    so we MISS and let the caller re-derive rather than buy off a stale shape."""
    try:
        v = float(os.environ.get("FEATURE_MEMO_TTL_S", "").strip())
    except (TypeError, ValueError):
        v = 20.0
    return max(1.0, v)


class FastWatchDedup:
    """Per-token TTL guard so the fast loop doesn't re-evaluate the same token
    every tick. `now` is injected (seconds) for testability."""

    def __init__(self, ttl_secs: float):
        self.ttl = ttl_secs
        self._last: dict[str, float] = {}

    def should_eval(self, addr: str, now: float) -> bool:
        t = self._last.get(addr)
        return t is None or (now - t) >= self.ttl

    def mark(self, addr: str, now: float) -> None:
        self._last[addr] = now


def shortlist(snapshot, trigger_fn: Callable, dedup: FastWatchDedup,
              is_held_or_blocked: Callable, now: float):
    """Return [(addr, entry)] for armed tokens worth a full evaluation.
    `trigger_fn(addr)` and `is_held_or_blocked(addr)` are injected for testability.
    `trigger_fn` is a generic move detector (dip OR rise) — see `move_fires`."""
    out = []
    for addr, entry in snapshot:
        if not trigger_fn(addr):
            continue
        if not dedup.should_eval(addr, now):
            continue
        if is_held_or_blocked(addr):
            continue
        out.append((addr, entry))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# CONCURRENT-TICK PERF (2026-06-20) — pure, env-gated knobs + structural helpers.
# The fast-watch tick used to block the loop 12–109s/tick (serial per-chunk price
# GETs + a serial heavy survivor eval loop), DELAYING the very fills it should
# accelerate. These helpers let the tick run prices in a bounded gather (single
# session), cap+prioritize survivors (biggest movers first), eval concurrently
# under a bounded semaphore, and prefer warm charts to avoid GT 429 storms. All
# behind env knobs with safe defaults; all fail-safe; all address-keyed.
# ──────────────────────────────────────────────────────────────────────────────


def price_concurrency() -> int:
    """Bounded concurrency for the per-chunk Jupiter price GETs (FAST_WATCH_
    PRICE_CONCURRENCY, default 4). Floor 1. Modest by design — a big parallel
    burst self-DoSes Jupiter into 429."""
    return max(1, _i("FAST_WATCH_PRICE_CONCURRENCY", 4))


def price_timeout_secs() -> float:
    """Per-call timeout (seconds) for the fast-watch batch price GETs
    (FAST_WATCH_PRICE_TIMEOUT_S, default 4.0). Floor 1.0. SHORT by design:
    the fast loop ticks ~every 3s, so a stalled/429'd chunk must FAIL-FAST and
    skip those addrs THIS tick (next tick ~3s later retries) rather than block
    the tick near the old 8s timeout. Two slow waves at 8s = the measured ~15s
    backoff-style stall; a 4s cap bounds the worst-case tick to ~2 short waves."""
    try:
        v = float(os.environ.get("FAST_WATCH_PRICE_TIMEOUT_S", "").strip())
    except (TypeError, ValueError):
        v = 4.0
    return max(1.0, v)


def pinned_price_in_fast_path() -> bool:
    """Whether the fast-watch escalation does the extra PAIR-PINNED price fetch
    (FAST_WATCH_PINNED_PRICE, default 'off'). DEFAULT OFF (2026-06-20): the pinned
    fetch goes through trader._get_token_price, whose Axiom step uses
    loop.run_in_executor(None, ...) on the DEFAULT ThreadPoolExecutor. With many
    concurrent survivors that executor is STARVED (the main-scan sync sweep +
    ledger to_thread offload share it) — measured: 3-19 survivors ALL showing an
    IDENTICAL ~15-69s pinned_price_fetch (loop/executor starvation, not the
    network; asyncio.wait_for's timer can't even fire while the loop is blocked).
    With it OFF the fast path uses the Jupiter price/v3 AGGREGATE already in hand
    (pool-aware, fetched off a bounded aiohttp gather — no executor) so a triggered
    buy fires within a couple seconds. Set FAST_WATCH_PINNED_PRICE=on to restore
    the pinned fetch (e.g. once the main-loop executor starvation is resolved)."""
    v = os.environ.get("FAST_WATCH_PINNED_PRICE", "off").strip().lower()
    return v in ("on", "1", "true", "yes")


def pinned_price_timeout_secs() -> float:
    """Hard wall-clock timeout (seconds) for the per-survivor PAIR-PINNED price
    fetch in the fast-watch escalation path (FAST_WATCH_PINNED_TIMEOUT_S, default
    3.0, floor 0.5). trader._get_token_price cascades up to ~3 serial HTTP calls
    (DexScreener pair 5s + Jupiter v6 5s + DexScreener tokens 5s = ~15s worst
    case) — under the survivor semaphore that stacks into the measured ~14s
    survivor stall. On timeout the fast path FAILS OPEN to the Jupiter aggregate
    price already in hand (the buy still fires this tick, just off the aggregate
    rather than the pinned pool; the main scan re-pins on the next cycle)."""
    try:
        v = float(os.environ.get("FAST_WATCH_PINNED_TIMEOUT_S", "").strip())
    except (TypeError, ValueError):
        v = 3.0
    return max(0.5, v)


def eval_concurrency() -> int:
    """Bounded concurrency for the per-survivor heavy _evaluate_pair (FAST_WATCH_
    EVAL_CONCURRENCY, default 5). Floor 1. Low so concurrent chart fetches don't
    worsen the free GT 25/min budget."""
    return max(1, _i("FAST_WATCH_EVAL_CONCURRENCY", 5))


def max_survivors_per_tick() -> int:
    """Max survivors evaluated in one tick (FAST_WATCH_MAX_SURVIVORS_PER_TICK,
    default 20). The rest are picked up next tick (~3s later). 0/negative -> no
    cap (disabled)."""
    return _i("FAST_WATCH_MAX_SURVIVORS_PER_TICK", 20)


def cache_only_charts_enabled() -> bool:
    """Fast-watch eval prefers the WARM chart cache and does NOT cold-fetch fresh
    GT OHLC (cutting the per-survivor 429 storm). Gate: FAST_WATCH_CACHE_ONLY_
    CHARTS, default 'on'. Any of off/0/false/no disables it (back to cold-fetch)."""
    v = os.environ.get("FAST_WATCH_CACHE_ONLY_CHARTS", "on").strip().lower()
    return v not in ("off", "0", "false", "no")


def chunk_addrs(addrs, chunk_size: int):
    """Split `addrs` into contiguous chunks of <= chunk_size (order preserved).
    A non-positive chunk_size yields a single chunk (never an infinite loop).
    [] -> []."""
    seq = list(addrs or [])
    if not seq:
        return []
    if chunk_size is None or chunk_size <= 0:
        return [seq]
    return [seq[i:i + chunk_size] for i in range(0, len(seq), chunk_size)]


def _abs_move_pct(samples, dip_pct: float, rise_pct: float):
    """The |move| magnitude (%) for prioritization: the larger of |dip| / |rise|
    off the window extremes. None/empty -> 0.0 (sorts last)."""
    dip = rolling_dip_pct(samples or ())
    rise = rolling_rise_pct(samples or ())
    best = 0.0
    if dip is not None:
        best = max(best, abs(dip))
    if rise is not None:
        best = max(best, abs(rise))
    return best


def cap_survivors(survivors, samples_by_addr, max_n: int,
                  dip_pct: float, rise_pct: float):
    """Cap a survivor list to the `max_n` BIGGEST movers (most time-sensitive),
    leaving the rest for the next tick.

    `survivors`: list of (addr, pair). `samples_by_addr`: addr -> price samples.
    Returns (kept_survivors, was_capped). max_n <= 0 disables the cap (returns the
    input unchanged, was_capped False). When <= max_n survivors, returns them in
    ORIGINAL order unchanged (no reorder churn). When over, sorts by |price move|
    desc and keeps the top max_n. Pure + deterministic; never raises."""
    if max_n is None or max_n <= 0:
        return survivors, False
    if len(survivors) <= max_n:
        return survivors, False
    ranked = sorted(
        survivors,
        key=lambda av: _abs_move_pct((samples_by_addr or {}).get(av[0]),
                                     dip_pct, rise_pct),
        reverse=True,
    )
    return ranked[:max_n], True


def arm_subset(candidates, cfg: FastWatchConfig):
    """Select the armed token addresses for the fast loop (Rev 2.3: pc_h1-AGNOSTIC, volume-ranked).

    `candidates`: list of dicts {addr, pc_h1 (float|None), vol_h1 (float|None), in_band (bool)}.
    The ONLY inclusion test is `in_band` (lane/fleet-band membership computed in
    `dip_scanner._fast_arm_subset` — the real filter). `pc_h1` direction does NOT gate which
    tokens are armed: the fleet buys BOTH deep dips AND pumps, so a one-sided `pc_h1 ≤ band`
    ceiling structurally excluded every momentum/pump token (6 bot families were 0/38 hits) and
    dropped any token with `pc_h1 = None`. `cfg.arm_band_pp` is retained on the config for docs/
    back-compat but is no longer consulted here. Ranked by recent volume (`vol_h1` desc — the
    most-active, most-buyable tokens, dips AND pumps); returns an ordered list of addresses
    (≤ armed_max, the rate-safe ceiling). Entry-type-agnostic so it serves dip and momentum bots.
    """
    inplay = [c for c in candidates if c.get("in_band")]
    inplay.sort(key=lambda c: (c.get("vol_h1") or 0.0), reverse=True)
    return [c["addr"] for c in inplay][:cfg.armed_max]


def hot_subset(armed, hot_max: int):
    """TIERED-POLL hot tier: the top `hot_max` armed addresses ranked by recent
    volume (pair `volume.h1` desc — the most-active tokens are the most likely to
    be bought, so they get the fastest ~3s freshness). Pure in-memory sort over
    the already-armed `self._fast_armed` (addr -> pair); does NOT change which
    tokens are armed (arm_subset owns that) or the trigger/escalate logic.

    `armed`: mapping addr -> pair dict (ORIGINAL-case keys; pairs carry volume.h1).
    Returns an ordered list of ≤ hot_max addresses (original case preserved). A
    non-positive `hot_max` yields an empty hot tier.
    """
    if hot_max <= 0:
        return []
    items = list((armed or {}).items())

    def _vol(pair):
        try:
            return float((pair or {}).get("volume", {}).get("h1") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    items.sort(key=lambda kv: _vol(kv[1]), reverse=True)
    return [addr for addr, _pair in items][:hot_max]


def hot_subset_movers(armed, hot_max, samples_by_addr, dip_pct, rise_pct):
    """Hot tier prioritizing recent MOVERS (so dip/pump buy candidates get the
    fast ~2s poll instead of the ~6s full tier), then top recent volume.

    `armed`: addr -> pair. `samples_by_addr`: addr -> rolling price samples. Ranks
    a token's |rolling dip| / rolling rise (from its samples) above pure volume:
    anything moving past the trigger band sorts first (by move magnitude desc),
    the rest fill remaining slots by volume desc. Guarantees tokens about to fire a
    dip/momentum trigger are on the fast poll, collapsing buy-lag for real
    candidates to the hot-tier cadence. Returns <= hot_max addresses (original
    case). Pure; never raises. Same call-count cost as hot_subset — only the RANK
    changes, not the tier size."""
    if hot_max <= 0:
        return []
    samples_by_addr = samples_by_addr or {}

    def _vol(pair):
        try:
            return float((pair or {}).get("volume", {}).get("h1") or 0.0)
        except (TypeError, ValueError, AttributeError):
            return 0.0

    def _move(addr):
        s = samples_by_addr.get(addr)
        if not s:
            return 0.0
        d = rolling_dip_pct(s)
        r = rolling_rise_pct(s)
        return max(abs(d) if d is not None else 0.0,
                   r if r is not None else 0.0)

    try:
        _thr = min(abs(float(dip_pct)), abs(float(rise_pct)))
    except (TypeError, ValueError):
        _thr = 0.0

    def _key(kv):
        addr, pair = kv
        m = _move(addr)
        is_mover = 1 if (m > 0 and m >= _thr) else 0
        return (is_mover, m, _vol(pair))

    items = list((armed or {}).items())
    items.sort(key=_key, reverse=True)
    return [addr for addr, _pair in items][:hot_max]


def rolling_dip_pct(samples):
    """% drop of the latest sample off the window max. None if <2 valid (>0) samples.
    `samples`: iterable of prices (oldest→newest)."""
    vals = [p for p in samples if isinstance(p, (int, float)) and p > 0]
    if len(vals) < 2:
        return None
    hi = max(vals)
    if hi <= 0:
        return None
    return round((vals[-1] / hi - 1.0) * 100.0, 6)


def reprice_change_pct(snapshot_pct, snapshot_price, fresh_price):
    """Recompute a price-change % (e.g. pc_h1) using a FRESH price against the
    slow high-reference encoded in the snapshot %.

    snapshot_pct: the DexScreener priceChange % at snapshot time (percent units,
        e.g. -20.0). snapshot_price: the priceUsd at snapshot time. fresh_price:
        the live price now. Returns the repriced % (percent units), or None if
        prices are unusable. When fresh_price == snapshot_price, returns
        snapshot_pct exactly (identity / inversion fallback). Pure; never raises.
    """
    try:
        sp = float(snapshot_price)
        fp = float(fresh_price)
        pc = float(snapshot_pct)
    except (TypeError, ValueError):
        return None
    if sp <= 0 or fp <= 0:
        return None
    fresh = ((fp / sp) * (1.0 + pc / 100.0) - 1.0) * 100.0
    return round(fresh, 6)


# Horizons the entry decision gates on. All four are repriceable from a fresh
# spot price; the numerator (current price) is fully refreshed. Anchor staleness
# (the ~2-min-old snapshot reference) is negligible for h1/h6/h24 (<3.3% of the
# window) but is ~40% of the m5 window — so the repriced m5 is a DIRECTIONAL
# "is there a fresh short dip" signal, trustworthy in sign but not in exact
# magnitude. h6/h24 were previously left stale even in enforce; including them
# closes that gap (their anchor error is <1%).
REPRICE_HORIZONS = ("h1", "m5", "h6", "h24")


def reprice_all(price_change, snapshot_price, fresh_price, horizons=REPRICE_HORIZONS):
    """Reprice every horizon's % change in `price_change` off the fresh price.

    Returns a NEW dict {horizon: fresh_pct} for each horizon present with a
    non-None snapshot pct that reprices to a usable value. Pure; never raises;
    empty dict if nothing repriceable (caller falls back to the stale values)."""
    out = {}
    try:
        pc = price_change or {}
        for h in horizons:
            sp = pc.get(h)
            if sp is None:
                continue
            rp = reprice_change_pct(sp, snapshot_price, fresh_price)
            if rp is not None:
                out[h] = rp
    except Exception:
        return out
    return out


_RT_VALID = ("off", "shadow", "enforce")


def rt_mode(flag, bot_cfg=None, default="off"):
    """Resolve an off/shadow/enforce mode flag, per-bot override winning over env.

    flag: env var name (e.g. 'RT_TRIGGER_MODE'). bot_cfg: optional bot config —
    a dict or object that may carry the lowercased flag name as a per-bot
    override. default: returned when neither source has a valid value. Always
    returns one of off/shadow/enforce. Pure-ish (reads env); never raises.
    """
    key = flag.lower()
    val = None
    if bot_cfg is not None:
        if isinstance(bot_cfg, dict):
            val = bot_cfg.get(key)
        else:
            val = getattr(bot_cfg, key, None)
    if val is None:
        val = os.environ.get(flag)
    val = (str(val).strip().lower() if val is not None else default)
    return val if val in _RT_VALID else default


def demand_turn_fresh_ok(fresh_imbalance, fetch_ok):
    """Whether the FRESH demand-turn confirms (net_flow_15s_imbalance >= 0).

    fetch_ok False or fresh_imbalance None -> None (caller falls back to existing
    behavior; NEVER returns True on missing data = never fail-open). Pure; never raises."""
    if not fetch_ok or fresh_imbalance is None:
        return None
    try:
        return float(fresh_imbalance) >= 0.0
    except (TypeError, ValueError):
        return None


def should_rearm_this_tick(rt_arm_mode):
    """True when the fast tick should rebuild the armed set from the freshest
    evaluated universe (RT_ARM_MODE shadow or enforce). Pure."""
    return str(rt_arm_mode).strip().lower() in ("shadow", "enforce")


def exit_reprice_would_fire(samples, entry_price, peak_pnl_pct, secs_from_peak,
                            floor_pct=-7.0, confirm_ticks=2):
    """Exit-side fresh-reprice floor check (EXIT_REPRICE, 2026-06-28).

    The exit-side twin of BUY-REPRICE: the in-flight loss floor is evaluated only
    on the slow ~150s sweep, so a position can plunge between two slow ticks while
    a fresh ~3s Jupiter price already sits in self._fast_samples. This runs the
    SAME in-flight-floor logic on the freshest fast samples.

    samples: iterable of recent fresh prices (oldest -> newest), e.g. a deque.
    Two gates, both must hold:
      1. WICK GUARD — the ``confirm_ticks`` NEWEST samples must each be below the
         floor (pnl <= floor_pct), so a single wick print can't trip a sell.
      2. in_flight_floor_fires(fresh_pnl, peak, secs_from_peak, floor_pct) — the
         EXACT slow-tick floor function, reused.

    Returns (fires: bool, fresh_pnl: float|None, why: str). FAIL-SAFE: bad data
    (empty samples / entry<=0 / dead prices) -> (False, None, ""). Pure; never raises."""
    from core.bot_evaluator import in_flight_floor_fires
    try:
        ep = float(entry_price)
    except (TypeError, ValueError):
        return False, None, ""
    if ep <= 0 or ep != ep:
        return False, None, ""
    seq = list(samples or [])
    if not seq:
        return False, None, ""
    # freshest valid (>0) price = fresh_pnl basis
    fresh_price = None
    for s in reversed(seq):
        try:
            v = float(s)
        except (TypeError, ValueError):
            continue
        if v > 0:
            fresh_price = v
            break
    if fresh_price is None:
        return False, None, ""
    fresh_pnl = (fresh_price / ep - 1.0) * 100.0
    try:
        ct = int(confirm_ticks)
    except (TypeError, ValueError):
        ct = 2
    if ct < 1:
        ct = 1
    # wick guard: count trailing consecutive sub-floor samples
    consec = 0
    for s in reversed(seq):
        try:
            v = float(s)
        except (TypeError, ValueError):
            break
        if v <= 0:
            break
        if (v / ep - 1.0) * 100.0 <= float(floor_pct):
            consec += 1
        else:
            break
    if consec < ct:
        return False, fresh_pnl, ""
    fires, why = in_flight_floor_fires(
        fresh_pnl, peak_pnl_pct, secs_from_peak, floor_pct=float(floor_pct))
    return bool(fires), fresh_pnl, (why or "")


def rolling_rise_pct(samples):
    """% gain of the latest sample off the window min. None if <2 valid (>0) samples.
    `samples`: iterable of prices (oldest→newest)."""
    vals = [p for p in samples if isinstance(p, (int, float)) and p > 0]
    if len(vals) < 2:
        return None
    lo = min(vals)
    if lo <= 0:
        return None
    return round((vals[-1] / lo - 1.0) * 100.0, 6)


def move_fires(samples, dip_pct: float, rise_pct: float) -> bool:
    """Bidirectional move detector: True on a fresh dip (off the window max) serving dip bots,
    OR a fresh rise (off the window min) serving momentum bots. Loose superset — the per-bot gates
    in `_evaluate_pair` make the actual buy decision."""
    dip = rolling_dip_pct(samples)
    if dip is not None and dip <= -abs(dip_pct):
        return True
    rise = rolling_rise_pct(samples)
    if rise is not None and rise >= abs(rise_pct):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# FORWARD FILL-SPEED CAPTURE (shadow) — fast-entry-price vs main-sweep-entry-price
# ──────────────────────────────────────────────────────────────────────────────
# The historical counterfactual (scripts/fill_speed_pnl.py) is data-blocked:
# DexScreener doesn't retain pre-entry price trajectory for old trades. Instead we
# capture the comparison AT THE MOMENT it exists — the fast-watch loop already holds
# the fresh price it WOULD fill at (the would-fill price), the main sweep produces
# the ACTUAL fill (entry_price). We log both keyed by ADDRESS (never symbol — a
# symbol-keyed price cross-poisons same-ticker mints) so we accumulate real per-trade
# (fast-entry-price vs sweep-entry-price, same exit) deltas to judge fill-speed P&L
# at n>=30 going forward. Pure + deterministic so it is trivially unit-testable.


def _pos_num(x):
    """Return float(x) if it is a real, strictly-positive number, else None."""
    if isinstance(x, bool):  # bool is an int subclass — reject it explicitly
        return None
    if not isinstance(x, (int, float)):
        return None
    f = float(x)
    if f <= 0.0:
        return None
    return f


def fill_speed_delta_pct(fast_price, sweep_price):
    """(sweep/fast - 1) * 100 — how much DEARER the main-sweep fill was vs the fast
    would-fill price (positive = the fast entry was cheaper; negative = the fast
    entry front-ran a further drop). None on any non-positive / bad input."""
    f = _pos_num(fast_price)
    s = _pos_num(sweep_price)
    if f is None or s is None:
        return None
    return (s / f - 1.0) * 100.0


def realized_pair(fast_price, sweep_price, exit_price):
    """Given the SAME exit, the realized P&L of each entry side and the edge.

    Returns (fast_pnl_pct, sweep_pnl_pct, edge_pp) where
        fast_pnl_pct  = (exit/fast  - 1) * 100
        sweep_pnl_pct = (exit/sweep - 1) * 100
        edge_pp       = fast_pnl_pct - sweep_pnl_pct   (the decisive number)
    None if any price is missing or <= 0 (guards bad/half-recorded rows)."""
    f = _pos_num(fast_price)
    s = _pos_num(sweep_price)
    x = _pos_num(exit_price)
    if f is None or s is None or x is None:
        return None
    fast_pnl = (x / f - 1.0) * 100.0
    sweep_pnl = (x / s - 1.0) * 100.0
    return (fast_pnl, sweep_pnl, fast_pnl - sweep_pnl)


def fill_speed_record(token, bot, fast_price, fast_ts, sweep_price, sweep_ts,
                      address, exit_price=None, now_ts=None):
    """Build ONE forward fill-speed shadow record (a plain dict, JSON-safe).

    ADDRESS-keyed (`token_address`) — NEVER symbol-keyed (same-ticker mints
    cross-poison symbol-keyed price state). `token`/`symbol` is carried for human
    reading only. `lead_secs` = sweep_ts - fast_ts (how much earlier the fast loop
    saw the price). `delta_pct` = fill_speed_delta_pct (sweep vs fast). Exit P&L is
    NOT captured here — the trade lifecycle already records exit_price; the offline
    joiner (scripts/fill_speed_forward.py) fills realized P&L by joining on
    address+entry. All fields degrade to None on bad input (never raises)."""
    lead = None
    try:
        if fast_ts is not None and sweep_ts is not None:
            lead = float(sweep_ts) - float(fast_ts)
    except (TypeError, ValueError):
        lead = None
    rec = {
        "ts": (now_ts if now_ts is not None else _now_iso()),
        "token_address": address,
        "symbol": token,
        "bot": bot,
        "fast_price": fast_price,
        "fast_ts": fast_ts,
        "sweep_price": sweep_price,
        "sweep_ts": sweep_ts,
        "lead_secs": lead,
        "delta_pct": fill_speed_delta_pct(fast_price, sweep_price),
    }
    if exit_price is not None:
        rec["exit_price"] = exit_price
    return rec


def _now_iso():
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def trail_reprice_would_fire(samples, entry_price, peak_pnl_pct, trail_pp,
                             confirm_ticks=2):
    """POST-TP1 trail check on FRESH fast samples (TRAIL_REPRICE, 2026-07-01).

    Family re-mine: trail closers gave back peak-7.29pp avg vs the configured
    peak-2pp because the trail is only evaluated on the slow sweep (3.42pp
    median fired-below-line = scan-cadence latency). This runs the SAME trail
    logic every fast tick for HELD post-TP1 positions — the exit-side fidelity
    twin of exit_reprice_would_fire, for winners.

    eff_peak = max(recorded peak_pnl_pct, max pnl seen in the fresh buffer) —
    strictly MORE accurate than the slow path (can't fire against a stale peak
    that fresh samples already exceeded).
    Fires when the ``confirm_ticks`` NEWEST samples are ALL at/below
    (eff_peak - trail_pp) — a single wick print can't trip a sell.

    Returns (fires, fresh_pnl, eff_peak, why). FAIL-SAFE: bad data ->
    (False, None, None, ""). Pure; never raises."""
    try:
        ep = float(entry_price)
        tp = float(trail_pp)
        pk = float(peak_pnl_pct)
    except (TypeError, ValueError):
        return False, None, None, ""
    if ep <= 0 or ep != ep or tp <= 0:
        return False, None, None, ""
    seq = [s for s in list(samples or []) if isinstance(s, (int, float)) and s > 0]
    if len(seq) < max(int(confirm_ticks), 1):
        return False, None, None, ""
    pnls = [(s / ep - 1.0) * 100.0 for s in seq]
    eff_peak = max(pk, max(pnls))
    line = eff_peak - tp
    n = max(int(confirm_ticks), 1)
    newest = pnls[-n:]
    fresh_pnl = pnls[-1]
    if all(x <= line for x in newest):
        why = (f"trail pnl={fresh_pnl:.2f}% <= peak({eff_peak:.2f}%) - "
               f"{tp:g}pp [fresh x{n}]")
        return True, fresh_pnl, eff_peak, why
    return False, fresh_pnl, eff_peak, ""


def tp1_fastfill_would_fire(samples, entry_price, tp1_pct, confirm_ticks=2):
    """PRE-TP1 take-profit check on FRESH fast samples (TP1_FASTFILL,
    2026-07-05 bounced-but-we-lost replay).

    The exit loop evaluates TP1 on scan-cadence prices while peaks happen on
    fresh ones: 27 rounds since 07-01 peaked +7.5 mean (ABOVE the +6 TP1
    line), TP1 never filled, and they bled to -84.6pp via breakeven-lock —
    replay banks +24pp instead (~260pp/week, 88.9% reachable). This is the
    pre-TP1 fidelity twin of trail_reprice_would_fire: the strategy already
    SAYS sell at tp1_pct; the engine just misses the touch.

    Fires when the ``confirm_ticks`` NEWEST fresh samples are ALL at/above
    the TP1 line — a single wick/glitch print can't trip a 75% sell.

    Returns (fires, fresh_pnl, why). FAIL-SAFE: bad data -> (False, None, "").
    Pure; never raises."""
    try:
        ep = float(entry_price)
        t1 = float(tp1_pct)
    except (TypeError, ValueError):
        return False, None, ""
    if ep <= 0 or ep != ep or t1 <= 0:
        return False, None, ""
    seq = [s for s in list(samples or []) if isinstance(s, (int, float)) and s > 0]
    n = max(int(confirm_ticks), 1)
    if len(seq) < n:
        return False, None, ""
    pnls = [(s / ep - 1.0) * 100.0 for s in seq]
    fresh_pnl = pnls[-1]
    if all(x >= t1 for x in pnls[-n:]):
        why = f"TP1 fastfill pnl={fresh_pnl:.2f}% >= {t1:g} [fresh x{n}]"
        return True, fresh_pnl, why
    return False, fresh_pnl, ""


# ── warm tape cache (2026-07-05 structural fix for None-tape) ───────────────
# Decision-time tape fetches race a throttled client queue and lose exactly
# when the market is busiest (adverse selection into the never-green class).
# The background sampler keeps recent-trades warm for the ARMED set; these
# pure helpers define the cache contract. Fail-safe: any miss returns None
# and the caller falls through to the live fetch + retry chain.

def tape_cache_get(cache, key, now_mono, max_age_secs):
    """Return the cached trades list if fresh, else None. Pure; never raises."""
    try:
        ent = (cache or {}).get(key)
        if not ent:
            return None
        ts, trades = ent
        if not trades or (now_mono - float(ts)) > float(max_age_secs):
            return None
        return trades
    except Exception:
        return None


def tape_cache_put(cache, key, trades, now_mono, max_entries=64):
    """Store trades under key; evict oldest beyond max_entries. Mutates cache."""
    try:
        if not key or trades is None:
            return
        cache[key] = (float(now_mono), trades)
        if len(cache) > int(max_entries):
            for k in sorted(cache, key=lambda k: cache[k][0])[:len(cache) - int(max_entries)]:
                cache.pop(k, None)
    except Exception:
        pass


# ── HL-confirm entry (2026-07-05 trough anatomy study) ──────────────────────
# scratchpad/_trough_anatomy.md: our dip entries fire MID-KNIFE (median fill
# +14.8% above the eventual flush low; low still ahead in 54% of episodes).
# The confirmed higher-low trigger flips crude EV -2.51 -> +1.03 pp/episode,
# TP1-before-stop 36.9% -> 64.4%, halves stops, and fixes the never-green
# cohort from the price side (-4.76 -> +0.51). Pond resolves FAST (median
# HL 4 min after low) so the window is short. This is the pure state logic;
# the scanner feeds it fresh samples (2-3s cadence) and reads the state.

def hl_confirm_update(st, price, now_mono):
    """Update one token's confirm-window state with a fresh price sample.

    st: mutable dict {} on first call. Tracks running low + its timestamp.
    Returns st (mutated). Pure besides st; never raises on garbage price."""
    try:
        p = float(price)
        if p <= 0 or p != p:
            return st
    except (TypeError, ValueError):
        return st
    if "low" not in st or p < st["low"]:
        st["low"] = p
        st["low_ts"] = float(now_mono)
    if "armed_ts" not in st:
        st["armed_ts"] = float(now_mono)
    st["last"] = p
    st["last_ts"] = float(now_mono)
    return st


def hl_confirm_state(st, now_mono, hold_secs=150.0, bounce_frac=0.01,
                     expiry_secs=1800.0, stale_secs=30.0):
    """Read a token's confirm state -> 'TRACKING' | 'CONFIRMED' | 'EXPIRED' | 'STALE'.

    CONFIRMED = no new low for >= hold_secs AND last price >= low*(1+bounce_frac).
    EXPIRED   = armed longer than expiry_secs (flush is old news; re-arm).
    STALE     = no fresh sample within stale_secs (can't trust the low).
    Defaults from the study: hold 120-180s (150 middle), bounce +1%, 30min expiry."""
    try:
        if not st or "low" not in st or "last" not in st:
            return "TRACKING"
        if float(now_mono) - float(st.get("armed_ts", 0.0)) >= float(expiry_secs):
            return "EXPIRED"
        if float(now_mono) - float(st.get("last_ts", 0.0)) > float(stale_secs):
            return "STALE"
        no_new_low = (float(now_mono) - float(st["low_ts"])) >= float(hold_secs)
        bounced = float(st["last"]) >= float(st["low"]) * (1.0 + float(bounce_frac))
        return "CONFIRMED" if (no_new_low and bounced) else "TRACKING"
    except Exception:
        return "TRACKING"


def pump_ws_ticks(armed_addrs, price_lookup, hl_map, seen_ts, now_mono):
    """Feed NEW on-chain WS ticks into the HL-confirm states (2026-07-06).

    The poll path samples armed tokens every 2-3s; the on-chain WS feed sees
    pool-state changes at ~400-800ms. This pump consumes only ticks NEWER
    than the last one seen per address (seen_ts), so the HL low/hold clocks
    run at WS resolution — the trough study's oracle gap (+1.03 bar-level vs
    +2.66 one-minute-after-true-low) says sub-second low-tracking is where
    the remaining edge lives. price_lookup(addr) -> (usd, ts) | None.
    Mutates hl_map/seen_ts; never raises; returns ticks consumed."""
    n = 0
    try:
        armed = list(armed_addrs)
        for addr in armed:
            try:
                got = price_lookup(addr)
            except Exception:
                got = None
            if not got:
                continue
            usd, ts = got
            if not ts or ts <= seen_ts.get(addr, 0.0):
                continue
            seen_ts[addr] = ts
            hl_confirm_update(hl_map.setdefault(addr, {}), usd, now_mono)
            n += 1
        if len(seen_ts) > 512:   # prune disarmed
            keep = set(armed)
            for k in list(seen_ts):
                if k not in keep:
                    seen_ts.pop(k, None)
    except Exception:
        pass
    return n
