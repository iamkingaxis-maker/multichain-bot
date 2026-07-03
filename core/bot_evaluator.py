from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle
from core.ng_scorer import (
    get_scorer as get_ng_scorer, scorer_mode as ng_scorer_mode,
    log_decision as ng_log_decision,
)

logger = logging.getLogger(__name__)


# Alpha-tier triggers that warrant 1.5x sizing (matches dip_scanner.py:12937).
ALPHA_TRIGGERS = frozenset({
    "1s_capit_reversal",
    "deep_1h_dip",
    "concurrent_alpha",
    "whale_concentrated_demand",
    "whale_recent_burst",
    "whale_p90_size",
    "textbook_pullback_vol_accel",
    "textbook_pullback_big_buyer",
    # Added 2026-05-29 (trigger-mine V3 winner — G10).
    # n=188, token-dedup +$3.86/tr, WR 94.7%, both days near-identical.
    "trigger_stable_compound_quality",
})

# Sizing-tier trigger sets (mirror the legacy single-bot tiers in dip_scanner).
# 2026-05-27 audit #6: wire the previously-dead premium_runner/marginal multipliers.
_PREMIUM_RUNNER_TRIGGER = "fresh_runner_factory"     # legacy 3x tier
_MARGINAL_FOR_SIZE = frozenset({                      # legacy 0.5x risk-gate tier
    "patient_bottom", "informed_cluster", "1s_capit_reversal",
    "whale_conviction", "grad_window_dip", "alpha_buyperscold",
    "net_flow_5m_demand", "fresh_pump_retrace",
})

# Layered defender filters added 2026-05-28 (perf-diff mine).
# Opt-in only: existing bots with filters_enforced=None do NOT enforce these.
# Bots opt in by adding the filter name to their filters_enforced list.
# See .perf_diff_drafts/SCHEMA_PROPOSAL.md for held-out validation results.
DEFENDER_FILTERS = frozenset({
    "filter_falling_pump",
    "filter_fusion_floor",
    "filter_btc_overheat",
    "filter_aged_corpse",
    "filter_wynn_killer",
    "filter_consec_red",
    "filter_dead_meme_lagging_pressure",
    "filter_dead_low_demand",
    "filter_dead_volume",
    "filter_huge_wick",
    # 2026-06-04: rolling_ng never-green model as a hard ENTRY BLOCK. Opt-in so only
    # bots that list it (pool_a_candidate, pool_c_tightexit) enforce it; the rest just
    # shadow-stamp. Held-out on the pools: blocks 20 fader-entries (10% WR, 0 runners),
    # keeps all 8 runners, avoids -$127 fader-loss for $6 winner-clip. Fail-open (no model).
    "filter_rolling_ng",
    # 2026-06-05: SOL-flicker capital-preservation gate. BLOCK entries when the SOL
    # macro gate flipped clear->block >=2x in the trailing hour (acute chop/crash
    # regime; causal/past-only). Self-gates to chop (flk_1h=0 on calm days). Net loss-
    # trimmer in % (+241pp; off-06-04 +68pp, ~2:1 save:kill, 4/5 non-nuke days). Opt-in:
    # acts on def2k/pool_a/pool_c_tightexit; preserves capital so the profit-sweep banks
    # higher realized peaks. Does NOT cut the catastrophic rug tail (separate defender).
    "filter_sol_flicker",
})


def _rug_gate_mode() -> str:
    """Global fleet-wide rug-structure gate mode. Env RUG_GATE_MODE in
    {off, shadow, enforce}; default 'enforce'. Lets the gate be downgraded
    without a deploy if it ever misfires."""
    m = os.environ.get("RUG_GATE_MODE", "enforce").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "enforce"


def _entry_stack_mode() -> str:
    """Fleet-wide validated entry-stack gate mode. Env ENTRY_STACK_MODE in
    {off, shadow, enforce}; default 'enforce'. Downgradeable without a deploy."""
    m = os.environ.get("ENTRY_STACK_MODE", "enforce").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "enforce"


def _rug_bundle_mode() -> str:
    """One-shot-sniped-rug ('bundle') gate mode. Env RUG_BUNDLE_MODE in
    {off, shadow, enforce}; default 'enforce'. Downgradeable without a deploy."""
    m = os.environ.get("RUG_BUNDLE_MODE", "enforce").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "enforce"


def _rug_bundle_spread_max() -> float:
    """Max top-10 buyer time-spread (sec) that counts as 'sniped/bundled'. Env
    RUG_BUNDLE_SPREAD_MAX_SEC, default 25 (losers median 20s vs winners 83s)."""
    try:
        return float(os.environ.get("RUG_BUNDLE_SPREAD_MAX_SEC", "25"))
    except (TypeError, ValueError):
        return 25.0


# Control cohort: stays UNGATED so the gated-vs-ungated counterfactual keeps
# being measured forward (same held-out discipline as every other gate).
# Override via ENTRY_STACK_CONTROL_BOTS=csv.
_ENTRY_STACK_DEFAULT_CONTROL = frozenset({
    "baseline_v1", "no_filters", "pool_a_broad_control",
})


def _entry_stack_control_bots() -> frozenset:
    raw = os.environ.get("ENTRY_STACK_CONTROL_BOTS")
    if raw is None:
        return _ENTRY_STACK_DEFAULT_CONTROL
    return frozenset(x.strip() for x in raw.split(",") if x.strip())


def _entry_stack_violations(b: FeatureBundle) -> list:
    """Fleet-wide validated entry stack (2026-06-09 bleed-week decomposition,
    28d / 18,439 closed trades): on the 11 BLEED days the fleet lost $18,338
    while entries passing this stack lost only $395 — and violators lost 2.5x
    more PER TRADE at SMALLER median size than passers ($20 vs $30), so the
    bleed is ENTRY DECISIONS, not size. Binding gates are dip-depth (6,252
    bleed-day violations) and buy-side flow (5,827); age/mcap barely bind
    (166/136) but are kept as cheap validated bounds. Each check FAILS OPEN
    when its feature is missing/zero (coverage-safe, matches fleet convention).
    """
    fails = []
    rm = b.raw_meta or {}
    dd = rm.get("shape_90m_drawdown_from_max_pct")
    if isinstance(dd, (int, float)) and not isinstance(dd, bool) and dd > -16.0:
        fails.append(f"dip_shallow({dd:.1f}>-16)")
    nf = rm.get("net_flow_60s_usd")
    if isinstance(nf, (int, float)) and not isinstance(nf, bool) and nf < 100.0:
        fails.append(f"flow_weak({nf:.0f}<100)")
    age = b.age_hours
    if isinstance(age, (int, float)) and not isinstance(age, bool) and 0 < age < 24.0:
        fails.append(f"age_young({age:.1f}h<24)")
    mc = b.mcap_usd
    if (isinstance(mc, (int, float)) and not isinstance(mc, bool) and mc > 0
            and not (500_000 <= mc <= 10_000_000)):
        fails.append(f"mcap_out({mc/1e6:.2f}M)")
    return fails


# Throttle entry_stack INFO logs to once per (token, hour) — the gate evaluates
# for every bot x token x cycle and blocks are the COMMON case (~85% of
# candidates), so unthrottled logging would flood Railway.
_entry_stack_logged: set = set()


# Post-stack filter prune (2026-06-09). Within the 2,687 STACK-PASSING closed
# trades of the 28d window, these blockable filters either blocked WINNERS
# (harmful: blocked-trade P&L > passed-trade P&L, n>=100 blocks) or never fired
# (inert: <=10 blocks). Post-stack, fear-filters mostly veto the deep dip we
# are trying to buy — entries at -16%+ with real flow LOOK "bearish/steep/
# seller-heavy" at the bottom by definition. Applies ONLY to entry-stack-gated
# bots on the DEFAULT enforcement path; control cohort and bots with an
# explicit filters_enforced list keep their exact behavior. Verdicts are still
# computed + recorded (shadow), so the forward record continues.
# Env kill-switch: ENTRY_STACK_FILTER_PRUNE=off restores blocking w/o deploy.
POST_STACK_PRUNED_FILTERS = frozenset({
    # harmful within stack-passers (blocked trades outperformed passed, n>=100)
    "filter_1m_steep_fall",        # blk +1.10 vs ok +0.04 $/tr (n=254)
    "filter_bs_m5_weak",           # blk +1.16 vs ok -0.08 (n=482)
    "filter_chasing_bounce",       # blk +2.22 vs ok -0.14 (n=322)
    "filter_knife_catch_peak",     # blk +1.07 vs ok +0.11 (n=102)
    "filter_lp_drain",             # blk +1.87 vs ok -0.13 (n=365)
    "filter_mtf_strong_downtrend", # blk +0.58 vs ok +0.13 (n=105)
    "filter_negative_net_flow_5m", # blk +1.58 vs ok -0.02 (n=277)
    "filter_reviving_lifecycle",   # blk +4.04 vs ok -0.55 (n=396)
    "filter_seller_imbalance",     # blk +1.25 vs ok +0.08 (n=152)
    "filter_stale_h1_peak",        # blk +3.72 vs ok -0.05 (n=139)
    "filter_turn",                 # blk +0.44 vs ok -0.01 (n=899)
    # inert within stack-passers (<=10 blocks in 2,687 — stack subsumes them)
    "filter_clean_break_p90",
    "filter_fake_bounce",
    "filter_low_volatility",
    "filter_microcap_trap",
    "filter_quote_asymmetry",
    "filter_sat_eve_midliq",
    "filter_solo_decay",
})


def _entry_stack_prune_on() -> bool:
    """Env kill-switch for the post-stack filter prune (default on)."""
    return os.environ.get("ENTRY_STACK_FILTER_PRUNE", "on").strip().lower() \
        not in ("off", "0", "false")


def _globally_relaxed_filters() -> frozenset:
    """FILTERS_RELAX_LIST (comma-separated) removes named filters from
    enforcement FLEET-WIDE — both the default (filters_enforced=None) path and
    the explicit filters_enforced path. Audit basis (2026-06-30 realized stack
    audit): the listed filters each BLOCK a cohort that was realized-GREEN on
    BOTH mean and median (>50% win) — i.e. they mislabel recoverable dips as
    blowoff/chase/FOMO. Default empty = no change; reversible by unsetting."""
    raw = os.environ.get("FILTERS_RELAX_LIST", "")
    return frozenset(f.strip() for f in raw.split(",") if f.strip())


def _rug_structure_blocks(b: FeatureBundle,
                          allow_zero_buyers: bool = False) -> tuple[bool, str]:
    """Fleet-wide catastrophic-rug guard (2026-06-08). The STANDARD rug
    fundamentals (rugcheck_score, lp_locked_pct, lp_burned) do NOT catch the
    single-sided / no-demand rug: the GO -99.95% LP-pull (38s after entry) had a
    CLEAN rugcheck + 100% locked + burned LP. The real signature is LIQUIDITY
    STRUCTURE — a one-sided LP with zero real buyers is the dev's own liquidity,
    primed to pull (a lock is worthless if there's no counter-liquidity). Held-out
    across weeks of fleet history: `lp_single_sided OR unique_buyers_n==0` catches
    5/6 rugs (83%, incl GO) for ~5% survivor-kill — and those survivors are
    low-quality no-demand entries anyway. Fail-OPEN when both features are missing
    (coverage-safe). See the rug-separation analysis 2026-06-08.

    `allow_zero_buyers` exempts the unique_buyers_n==0 branch ONLY (2026-06-13):
    for young_token_probe bots the thesis is buying fresh (<2h) tokens BEFORE
    buyers accumulate, so 0 unique buyers is the EXPECTED entry state, not a
    no-demand rug. This silenced the entire young family in enforce mode once
    unique_buyers_n began populating 0 for fresh tokens. lp_single_sided (the
    real one-sided-LP rug signature that caught GO) still applies to every bot."""
    ss = b.raw_meta.get("lp_single_sided")
    ub = b.raw_meta.get("unique_buyers_n")
    if ss is True:
        return True, "lp_single_sided=True (one-sided LP)"
    if (not allow_zero_buyers and isinstance(ub, (int, float))
            and not isinstance(ub, bool) and ub == 0):
        return True, "unique_buyers_n=0 (no real buyers)"
    return False, ""


def _rug_bundle_blocks(b: "FeatureBundle") -> tuple[bool, str]:
    """Surgical 'one-shot sniped launch' rug gate (2026-06-14). Catches the rug
    class that PASSES _rug_structure_blocks AND shows a clean rugcheck / 100%-locked
    LP — the pump-then-dump that looks IDENTICAL to a winner on every standard rug
    feature. Held-out separation (n=41 deep losers <=-40% vs 248 winners >=+15%):
    liquidity_usd (~$26k), lp_locked_pct (100%), unique_buyers_n, rugcheck_score (1)
    were statistically identical between rugs and winners; only the COMBINATION of
    [0 recurring buyers] AND [top-10 buyers bundled within ~25s] separated them —
    catches 49% of rugs for 12% winner-kill / 7% buy-volume. Requires BOTH (recur0
    alone = 45% winner-kill; sniped alone = 25%). Fail-OPEN when either feature is
    missing (coverage-safe). See the rug-separation analysis 2026-06-14."""
    m = b.raw_meta or {}
    rb = m.get("n_recurring_buyers_3plus")
    sp = m.get("top10_buyer_time_spread_sec")
    no_repeat = isinstance(rb, (int, float)) and not isinstance(rb, bool) and rb == 0
    sniped = (isinstance(sp, (int, float)) and not isinstance(sp, bool)
              and sp <= _rug_bundle_spread_max())
    if no_repeat and sniped:
        return True, (f"one-shot sniped rug (0 recurring buyers + top10 spread "
                      f"{sp:.0f}s<={_rug_bundle_spread_max():.0f}s)")
    return False, ""


def _terminal_collapse_h6_pct() -> float:
    """pc_h6 floor for the terminal-collapse gate (default -60.0%). Below this,
    the 6-hour price has collapsed so far the token is a death-spiral corpse, not
    a buyable dip. Tunable without redeploy via TERMINAL_COLLAPSE_H6_PCT."""
    try:
        return float(os.environ.get("TERMINAL_COLLAPSE_H6_PCT", "-60.0"))
    except (TypeError, ValueError):
        return -60.0


def terminal_collapse_blocks(pc_h6, threshold: float | None = None) -> tuple[bool, str]:
    """Block buying a token in TERMINAL multi-hour collapse (2026-06-22, QAI -$55).

    A real dip is a recent flush (deep pc_h1) on a token still ALIVE over 6h; a
    corpse has collapsed across the whole 6h window and keeps dying. Live-data
    separation (n=22 labelled live buys): the QAI rug entered at pc_h6=-85.3% and
    lost -55.7%, while the NEXT-deepest trade was -46.3% and EVERY winner was
    >= -32% pc_h6 — a 39-point gap. A pc_h6 <= -60% floor catches the QAI class
    with a wide margin and clips ZERO winners in the live set. Distinct from
    filter_post_pump_corpse (pumped + calm) — this is the bled-to-death end.

    Pure. Fail-OPEN on missing/garbage pc_h6 (never blocks on absent data)."""
    thr = _terminal_collapse_h6_pct() if threshold is None else threshold
    try:
        v = float(pc_h6)
    except (TypeError, ValueError):
        return False, ""
    if v <= thr:
        return True, f"pc_h6={v:.1f}%<={thr:.0f}% (terminal 6h collapse — corpse, not a dip)"
    return False, ""


def in_flight_floor_fires(pnl_pct, peak_pnl_pct, secs_from_peak,
                          floor_pct: float = -7.0, velbail_pps: float = 0.012,
                          velbail_peak_max: float = 2.0,
                          velbail_pnl: float = -4.0) -> tuple[bool, str]:
    """In-flight loss-floor for PRE-TP1 legs (badday gap audit 2026-06-22).

    Fires a full-close when EITHER:
      * MAE floor: live intratrade pnl_pct <= floor_pct (default -7.0). The badday
        loss-tail rode the -9 fast-bail/-12 hard-stop down to a mean -12.3%; a -7
        floor exits ~5pp earlier with ZERO winner-kill (worst-winner MAE -5.85% vs
        nearest loser -6.01% = empty 1.15pp band; n=89 losers / 0 winners).
      * Velocity pre-empt: a never-green fast collapse — peak_pnl_pct<velbail_peak_max
        AND pnl_pct<=velbail_pnl AND drop_velocity>=velbail_pps — bails at the fire
        point BEFORE -7 (drop_velocity=(peak-pnl)/secs_from_peak pp/s). Refines the
        already-built ng_faststop shadow: the velocity gate flips it from
        value-destroying to value-saving (its only winner-kills were one token).

    Returns (fires, why). Pure, FAIL-SAFE: non-numeric/NaN inputs -> (False, "")
    (never fire spuriously on bad data). Caller gates on not-tp1_hit + mode + scope."""
    try:
        p = float(pnl_pct)
        pk = float(peak_pnl_pct)
    except (TypeError, ValueError):
        return False, ""
    if p != p or pk != pk:  # NaN guard
        return False, ""
    # velocity pre-empt (fast never-green collapse) — checked first (fires shallower)
    if pk < velbail_peak_max and p <= velbail_pnl:
        try:
            sfp = max(float(secs_from_peak), 1.0)
            vel = (pk - p) / sfp
            if vel >= velbail_pps:
                return True, f"velocity-bail pnl={p:.2f}% vel={vel:.4f}pp/s"
        except (TypeError, ValueError):
            pass
    if p <= float(floor_pct):
        return True, f"MAE-floor pnl={p:.2f}%<={float(floor_pct):.0f}%"
    return False, ""


def breakeven_lock_fires(peak_pnl_pct, pnl_pct, tp1_hit,
                         peak_min: float = 7.0) -> tuple[bool, str]:
    """Peak-anchored breakeven-arm for PRE-TP1 legs (winner-comparison 2026-06-26).

    Fires a full-close when a leg that CONFIRMED green (peak_pnl_pct >= peak_min,
    default +7%) round-trips back to breakeven (pnl_pct <= 0) before TP1 — locking
    ~0 instead of riding the give-back down to the -7 floor / hard stop.

    Validated path-aware on the give-back cohort (n=82 fires at peak>=7): 70 saves /
    12 winner-kills = +349pp net, winner-kill 0.15 (vs 0.30 at peak>=3). The +7 anchor
    is what separates round-trip losers from V-recoverers. NOTE: PAPER over-states this
    (deep stops gap THROUGH live) — keep shadow until forward/live-confirmed.

    Returns (fires, why). Pure, FAIL-SAFE: non-numeric/NaN -> (False, "") (never fire
    on bad data). Caller gates on mode + scope; tp1_hit guards post-TP1 (trail owns it)."""
    if tp1_hit:
        return False, ""
    try:
        p = float(pnl_pct)
        pk = float(peak_pnl_pct)
        pm = float(peak_min)
    except (TypeError, ValueError):
        return False, ""
    if p != p or pk != pk:  # NaN guard
        return False, ""
    if pk >= pm and p <= 0.0:
        return True, f"breakeven-lock peak={pk:+.1f}%>={pm:.0f}% pnl={p:+.2f}%<=0"
    return False, ""


def winner_select_entry_blocks(median_buy_size_usd, gate_on, threshold=None) -> tuple[bool, str]:
    """Entry gate for the patient sleeve (winner-comparison 2026-06-26): when gate_on,
    ALLOW only winner-selected entries (median_buy_size_usd >= threshold, default 34.3 —
    deep capitulation met by real buyer size, the +tail signal). FAIL-CLOSED: a missing/
    garbage signal while gated -> BLOCK (the sleeve holds ONLY qualified +tail entries).
    gate_on False -> never blocks (every other bot is unaffected). Returns (block, why)."""
    if not gate_on:
        return False, ""
    sel, why = winner_demand_selected(median_buy_size_usd, threshold=threshold)
    if sel:
        return False, why
    return True, "winner_select_entry: not a qualified +tail entry"


def _structure_edge_liq_floor() -> float:
    """Liquidity floor (USD) for the structure-edge gate (default 48000 — the p75
    of the badday cohort; deeper book = better fills + out of the rug pocket).
    Tunable via STRUCTURE_EDGE_LIQ_FLOOR."""
    try:
        return float(os.environ.get("STRUCTURE_EDGE_LIQ_FLOOR", "48000"))
    except (TypeError, ValueError):
        return 48000.0


def structure_edge_blocks(pc_h6, liquidity_usd, liq_floor=None) -> tuple[bool, str]:
    """Fire badday dip entries ONLY when the STRUCTURE is favorable — `pc_h6 >= 0`
    (the dip is a pullback within a 6h-reclaimed structure, not a falling knife) OR
    `liquidity_usd >= floor` (deep book → good fills, out of the rug pocket). BLOCK
    only when BOTH fail: a falling-knife (pc_h6<0) in a thin book (liq<floor).

    The verified +EV gate (2026-06-24 TRUE-edge decomposition, fraction-weighted +
    haircut-corrected): INSIDE the gate +2.6% median / 61% win vs the -1.65%/49%
    breakeven baseline, keeping 61% of volume; both arms independently +EV, robust
    across all 4 days and 35 tokens. The edge is STRUCTURE, not demand-flow.

    Pure. FAIL-OPEN: blocks ONLY when BOTH features are present AND both fail — any
    missing/NaN value means the OR-of-passes can't be disproven, so do NOT block."""
    floor = _structure_edge_liq_floor() if liq_floor is None else float(liq_floor)
    try:
        a = float(pc_h6) if pc_h6 is not None else None
        l = float(liquidity_usd) if liquidity_usd is not None else None
    except (TypeError, ValueError):
        return False, ""
    if a is not None and a != a:  # NaN
        a = None
    if l is not None and l != l:
        l = None
    if (a is not None and a < 0.0) and (l is not None and l < floor):
        return True, (f"falling-knife thin-book (pc_h6={a:.0f}%<0 AND "
                      f"liq=${l:.0f}<${floor:.0f}) — no structure edge")
    return False, ""


def _liquidity_exit_floor_usd() -> float:
    """Liquidity floor (USD) for the exit-tail gate (default 30000 — conservative;
    a human raises it from Part 2's measured exit-slip-by-liquidity table at the
    Part 4 bar). Tunable via LIQ_EXIT_FLOOR_USD."""
    try:
        return float(os.environ.get("LIQ_EXIT_FLOOR_USD", "30000"))
    except (TypeError, ValueError):
        return 30000.0


def liquidity_exit_floor_blocks(liquidity_usd, floor_usd=None) -> tuple[bool, str]:
    """Refuse a badday entry into a book too thin to EXIT cleanly — the
    liquidity-conditional exit-tail lever (2026-06-24 design).

    "Don't enter what you can't exit." A live exit into a sub-floor book gaps
    through 20-30%+ past mid (the ANT/$CWIF tail), un-fixable with exit logic
    because the FILL itself gaps past the price. Block when ENTRY ``liquidity_usd``
    is a finite number AND below ``floor_usd``.

    Pure. FAIL-OPEN: a None/NaN/non-finite liquidity can't disprove exitability,
    so do NOT block (telemetry-gap safety). Never raises."""
    floor = _liquidity_exit_floor_usd() if floor_usd is None else float(floor_usd)
    try:
        l = float(liquidity_usd) if liquidity_usd is not None else None
    except (TypeError, ValueError):
        return False, ""
    if l is None or l != l or l in (float("inf"), float("-inf")):  # None/NaN/inf
        return False, ""
    if l < floor:
        return True, (f"liq=${l:.0f} below exit-floor ${floor:.0f} "
                      f"(too thin to exit cleanly — gap-through tail risk)")
    return False, ""


def _consec_red_knife_threshold() -> int:
    """1m_consec_red threshold for the no-bounce-knife gate (default 3). Tunable
    via CONSEC_RED_KNIFE_THRESHOLD."""
    try:
        return int(float(os.environ.get("CONSEC_RED_KNIFE_THRESHOLD", "3")))
    except (TypeError, ValueError):
        return 3


def consec_red_knife_blocks(consec_red_1m, threshold=None) -> tuple[bool, str]:
    """Refuse a badday dip entry that is buying into N+ consecutive red 1-minute
    candles — the token is STILL falling at entry (a no-bounce knife), not a
    demand-turn.

    28-agent bounce-vs-knife study (2026-06-25, 1130 joined badday pairs, held-out
    by token via clustered bootstrap + by time): at threshold >=3, bounce rate is
    38.1% inside vs 20.2% in the blocked cohort (18pp separation), blocked cohort
    68% knives, winner-kill ratio 0.295 (~1 bounce per 3.4 knives) — the only rule
    that passed the winner-kill bar in BOTH time halves and never inverted. (A
    main-scan filter_consec_red already exists; this places the same cut on the
    entry/fast path where structure_edge/liq-exit-floor live, to close the leak.)

    Pure. FAIL-OPEN: None/NaN consec_red can't prove a knife -> do NOT block
    (telemetry-gap safety; the fast path sometimes lacks 1m features). Never raises."""
    thr = _consec_red_knife_threshold() if threshold is None else int(threshold)
    try:
        c = float(consec_red_1m) if consec_red_1m is not None else None
    except (TypeError, ValueError):
        return False, ""
    if c is None or c != c:  # None / NaN
        return False, ""
    if c >= thr:
        return True, (f"1m_consec_red={int(c)}>={thr} "
                      f"(still falling at entry — no-bounce knife, not a demand-turn)")
    return False, ""


def falling_knife_blocks(mtf_score, last_close_pct, mtf_max=-1.0) -> tuple[bool, str]:
    """Refuse a badday dip entry that is a falling knife: multi-timeframe trend is
    bearish (chart_mtf_score <= -1) AND the most recent 1m bar is still red
    (1m_last_close_pct < 0) — there is no green confirmation candle yet, so the
    "bottom" has not formed (the RAGEGUY 2026-05-15 pattern: 4 triggers stacked,
    mtf=-1, last 1m -0.83%, kept falling another -8.5% post-entry).

    A main-scan filter_falling_knife already computes this (dip_scanner ~12793,
    SHADOW since the 2026-05-16 small-n revert); this places the SAME cut on the
    entry/fast path (where structure_edge / consec_red_knife / liq-exit-floor live)
    so it can be measured and, on AxiS's go, enforced. Fresh trade-join (n=1121
    positions, 2026-06-26) flips the small-n May revert: knife-BLOCK cohort mean
    -3.80%/22%WR vs PASS -2.71%/30%WR (newest half -3.02 vs -0.51), and the blocked
    cohort is 53% never-green vs 45% — it removes a higher fraction of doomed
    entries (the confirmed entry leak).

    Pure. FAIL-OPEN: either feature None/NaN -> do NOT block (telemetry-gap safety;
    the fast path sometimes lacks 1m / chart features). Never raises."""
    try:
        m = float(mtf_score) if mtf_score is not None else None
        lc = float(last_close_pct) if last_close_pct is not None else None
    except (TypeError, ValueError):
        return False, ""
    if m is None or m != m or lc is None or lc != lc:  # None / NaN -> fail-open
        return False, ""
    if m <= float(mtf_max) and lc < 0.0:
        return True, (f"mtf_score={m:.1f}<={mtf_max:.0f} AND 1m_last_close={lc:+.2f}%<0 "
                      f"(falling knife — no green confirmation candle yet)")
    return False, ""


def gate_blocks(verdict, mode, default_mode="enforce") -> bool:
    """Reusable MODE-flag arbiter for a computed filter verdict. A gate ACTUALLY
    blocks only when its verdict is BLOCK and its env MODE resolves to 'enforce'.
    'shadow'/'off'/anything-else -> measure-only (no block). Lets a hard-enforced
    filter be demoted to shadow via a single env flag without touching its verdict/
    counter/log (so the forward scoreboard keeps measuring it). FAIL-SAFE: a bad/empty
    mode falls back to default_mode (default 'enforce' = behavior-preserving for a
    gate that was hard-enforced before the flag existed)."""
    m = (mode or default_mode).strip().lower() if isinstance(mode, str) else default_mode
    return verdict == "BLOCK" and m == "enforce"


def post_pump_corpse_blocks(pc_h1, pc_h24, buys_per_min_recent) -> tuple[bool, str]:
    """Refuse an entry into a post-pump corpse: a token that just had an extreme
    pump and is now mean-reverting / dying. Either (a) pc_h1>=+500% (extreme
    single-hour pump, always followed by reversion — the SPCX/PAC class) OR
    (b) pc_h24>=+200% AND buys_per_min_recent<=2 (recently pumped + now calm).

    This is the SAME predicate as the main-scan filter_post_pump_corpse (ENFORCED
    fleet-wide 2026-05-16), ported to the entry/fast path because that filter LEAKS
    there: 145 flagged positions traded in the 06-21..26 window (NEW BLOCK -3.37%
    vs PASS -1.12%, the scoreboard's #2 enforce-ready leaker). Mirrors consec_red_
    knife / falling_knife. Pure. FAIL-OPEN: missing/NaN features -> do NOT block.
    Never raises."""
    reasons = []
    try:
        h1 = float(pc_h1) if pc_h1 is not None else None
        h24 = float(pc_h24) if pc_h24 is not None else None
        bpm = float(buys_per_min_recent) if buys_per_min_recent is not None else None
    except (TypeError, ValueError):
        return False, ""
    if h1 is not None and h1 == h1 and h1 >= 500.0:
        reasons.append(f"pc_h1={h1:.0f}%>=500 (extreme single-hour pump)")
    if (h24 is not None and h24 == h24 and h24 >= 200.0
            and bpm is not None and bpm == bpm and bpm <= 2.0):
        reasons.append(f"pc_h24={h24:.0f}%>=200 AND buys_per_min_recent={bpm:.0f}<=2 "
                       f"(post-pump corpse: pumped + currently calm)")
    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def _nd_env(name: str, default: float) -> float:
    """Float env override with default for the not-dipping gate."""
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _nd_num(v):
    """None/NaN-safe float coercion (bool excluded)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x  # drop NaN


def not_dipping_blocks(macro30_pct, ma50_dist_pct, slope_30m, pct_in_1h_range,
                       macro30_floor=None, ma50_floor=None,
                       slope_floor=None, range_ceil=None) -> tuple[bool, str]:
    """Block a badday dip entry when the token is NOT actually dipping at decision
    time — the dip-buyer fires but the token is flat/green on its 30m macro, above
    its MA50, has a non-falling 30m regression slope, OR is sitting near its 1h
    high. There is no real dip to catch, so it slow-bleeds to the -7 floor.

    THE slow-bleeder no-bounce signature (2026-06-25 24-agent mine, gate-passing
    cohort 1m_consec_red<3 AND (pc_h6>=0 OR liq>=48k)): blocks 90.4% never-green
    vs 48.7% kept (41.7pp), winner-kill 0.107, +52.5pp WITHIN-token separation,
    survives dropping STARMIND+CASHBACK+$CWIF together, both time halves. Catches
    STARMIND/$CWIF (1h-range arm) + CASHBACK's trend slice. Orthogonal to cr3 (1m
    freefall) and structure_edge (thin book).

    RULE = NOT_DIPPING_TREND OR HIGH_IN_1H_RANGE, where
      NOT_DIPPING_TREND = macro30>=-4.83 OR ma50_dist>=-5.463 OR slope30m>=-0.1816
      HIGH_IN_1H_RANGE  = pct_in_1h_range>=0.198

    Pure. FAIL-OPEN: each term contributes ONLY when its signal is present; if
    every signal is missing/NaN the OR is False -> do NOT block. Never raises."""
    mc = _nd_num(macro30_pct)
    ma = _nd_num(ma50_dist_pct)
    sl = _nd_num(slope_30m)
    rg = _nd_num(pct_in_1h_range)
    mcf = _nd_env("NOT_DIPPING_MACRO30_FLOOR", -4.83) if macro30_floor is None else float(macro30_floor)
    maf = _nd_env("NOT_DIPPING_MA50_FLOOR", -5.463) if ma50_floor is None else float(ma50_floor)
    slf = _nd_env("NOT_DIPPING_SLOPE_FLOOR", -0.1816) if slope_floor is None else float(slope_floor)
    rgc = _nd_env("NOT_DIPPING_RANGE_CEIL", 0.198) if range_ceil is None else float(range_ceil)
    reasons = []
    if mc is not None and mc >= mcf:
        reasons.append(f"macro30={mc:.2f}>={mcf:g}")
    if ma is not None and ma >= maf:
        reasons.append(f"ma50_dist={ma:.2f}>={maf:g}")
    if sl is not None and sl >= slf:
        reasons.append(f"slope30m={sl:.3f}>={slf:g}")
    if rg is not None and rg >= rgc:
        reasons.append(f"in_1h_range={rg:.3f}>={rgc:g} (near 1h high)")
    if reasons:
        return True, "not dipping (no real dip to catch): " + ", ".join(reasons)
    return False, ""


def _winner_size_threshold() -> float:
    """median_buy_size_usd threshold for the winner size-up selector (default
    34.3). Tunable via WINNER_SIZE_MEDIAN_BUY_USD."""
    try:
        return float(os.environ.get("WINNER_SIZE_MEDIAN_BUY_USD", "34.3"))
    except (TypeError, ValueError):
        return 34.3


def winner_demand_selected(median_buy_size_usd, threshold=None) -> tuple[bool, str]:
    """POSITIVE winner-selection signal (2026-06-25 37-agent winner mine) — a
    big-winner badday entry is a DEEP flush MET BY GENUINE BUYER SIZE. The single
    most robust, ADDITIVE selector is median_buy_size_usd >= 34.3 (big-winner rate
    15.7% vs 4.1%, realized +6.74pp, keeps ~20% volume, held-out by token+time,
    RECOVERS runners structure_edge would block).

    This is a FIRE-ON / SIZE-UP signal, NOT a filter — and a FAT-TAIL one: it lifts
    the runner RATE and the MEAN but NOT the median (selected median stays ~-3..-5%),
    so it is a size-up BIAS toward the +EV tail, never a hard profitability gate.

    Pure. FAIL-OPEN: missing/NaN -> (False, '') = not selected (no size change).
    Never raises."""
    thr = _winner_size_threshold() if threshold is None else float(threshold)
    try:
        v = float(median_buy_size_usd) if median_buy_size_usd is not None else None
    except (TypeError, ValueError):
        return False, ""
    if v is None or v != v:  # None / NaN
        return False, ""
    if v >= thr:
        return True, (f"median_buy_size=${v:.0f}>=${thr:g} "
                      f"(big buyers stepping in — runner-tail selector)")
    return False, ""


def full_thesis_cohort_eval(pc_h6, median_buy_size_usd,
                            buyer_threshold=None) -> tuple[bool, bool, str]:
    """Full-thesis cohort (coverage audit 2026-06-29): a profitable badday dip =
    a GENUINE 6h decliner (pc_h6 <= 0, a real decline not a pump-retrace) MET BY
    real buyer size (median_buy_size_usd >= ~34.3, reusing the validated winner
    selector). Returns (selected, blocked, why):
      selected = both signals present AND in-cohort  (PASS / would-keep)
      blocked  = a signal is PRESENT and CONFIRMED out-of-cohort (pc_h6>0 pump-retrace,
                 or buyer<threshold low-buyer)  -> the ONLY case enforce should block
      neither  = data missing/NaN -> (False, False, 'unknown ...')  -> enforce ALLOWS (fail-open)
    NEVER returns blocked=True on missing data — the median_buy_size_usd FeatureBundle
    gap on fast-watch entries would re-create the 24h dark-fleet outage. Composes the
    buyer half from winner_demand_selected (+ its threshold helper) — no hardcoded 34.3.
    Pure. Never raises (any coercion error -> that signal is treated as missing)."""
    thr = _winner_size_threshold() if buyer_threshold is None else float(buyer_threshold)
    # ── decline half: parse pc_h6 safely (None/bool/str/NaN -> not present) ──
    try:
        h6 = None if (pc_h6 is None or isinstance(pc_h6, bool)) else float(pc_h6)
    except (TypeError, ValueError):
        h6 = None
    if h6 is not None and h6 != h6:  # NaN
        h6 = None
    decline_present = h6 is not None
    decline_ok = decline_present and h6 <= 0.0
    # ── buyer half: presence coerced here, ok delegated to winner_demand_selected ──
    try:
        bv = None if (median_buy_size_usd is None or isinstance(median_buy_size_usd, bool)) \
            else float(median_buy_size_usd)
    except (TypeError, ValueError):
        bv = None
    if bv is not None and bv != bv:  # NaN
        bv = None
    buyer_present = bv is not None
    buyer_ok, _ = winner_demand_selected(median_buy_size_usd, threshold=buyer_threshold)
    buyer_ok = bool(buyer_ok)

    selected = decline_present and decline_ok and buyer_present and buyer_ok
    blocked = (decline_present and not decline_ok) or (buyer_present and not buyer_ok)

    if blocked:
        reasons = []
        if decline_present and not decline_ok:
            reasons.append(f"pc_h6={h6:+.1f}>0 pump-retrace")
        if buyer_present and not buyer_ok:
            reasons.append(f"buyer${bv:.1f}<{thr:g}")
        return False, True, "BLOCK: " + ", ".join(reasons)
    if selected:
        return True, False, f"PASS: pc_h6={h6:+.1f}<=0 & buyer${bv:.1f}>={thr:g}"
    # fail-open: at least one signal missing and nothing present-and-failing
    parts = ["pc_h6 present" if decline_present else "pc_h6 missing",
             "buyer present" if buyer_present else "buyer missing"]
    return False, False, "unknown: " + ", ".join(parts) + " -> allow"


def stale_knife_blocks(stale_watch_verdict, mtf_verdicts) -> tuple[bool, str]:
    """STALE-KNIFE entry gate (fresh-fill-fork validation 2026-06-30). The clean
    -EV cohort in the realized-outcome study (33 distinct tokens) was the
    INTERSECTION: a STALE watch candidate (filter_stale_watch_verdict == 'BLOCK',
    i.e. seen >15 cycles — "fresh dips beat stale ones") AND an ALL-BEAR multi-
    timeframe chart (1m AND 5m AND 15m all 'bear'). That slice ran -3.13% mean /
    -4.70% median / 2-of-11 token win (18%). Neither signal alone gates cleanly
    (stale-alone is ~breakeven; 2-bear is the BEST cohort and 3-bear-alone is fat-
    tailed +mean/-median) — it is specifically the AND that isolates the dead-cat
    knife we fill on the bounce.

    Returns (blocked, why). blocked=True ONLY when BOTH conditions are CONFIRMED
    present-and-true. FAIL-OPEN: any missing/unparseable signal -> (False, ...) =
    allow (never dark the fleet on absent chart/stale meta — same discipline as
    full_thesis_cohort_eval). Pure; never raises.

    mtf_verdicts: the chart_mtf_verdicts dict, e.g. {'1m':'bear','5m':'bear',
    '15m':'bear','1h':'flat'} (1h intentionally ignored — the 1m/5m/15m triple is
    the validated knife signature)."""
    stale = str(stale_watch_verdict).upper() if stale_watch_verdict is not None else ""
    stale_block = (stale == "BLOCK")
    if not stale_block:
        return False, f"not stale (stale_watch={stale or 'missing'}) -> allow"
    # all-bear half: need 1m AND 5m AND 15m ALL == 'bear', all present
    if not isinstance(mtf_verdicts, dict) or not mtf_verdicts:
        return False, "mtf missing -> allow"
    tfs = ("1m", "5m", "15m")
    vals = {tf: str(mtf_verdicts.get(tf, "")).lower() for tf in tfs}
    if any(vals[tf] == "" for tf in tfs):
        return False, "mtf incomplete -> allow"
    all_bear = all(vals[tf] == "bear" for tf in tfs)
    if stale_block and all_bear:
        return True, ("BLOCK: stale-watch + all-bear MTF "
                      "(1m/5m/15m bear) = dead-cat knife")
    return False, (f"stale but not all-bear (mtf={vals['1m']}/{vals['5m']}/{vals['15m']})"
                   " -> allow")


def _dev_not_dumped_min_pct() -> float:
    """dev_pct_remaining floor for the dev-not-dumped gate (default 20.0). The
    reachability/MAE selection mine (2026-06-30, n=152 tokens) found the deep-MAE
    losers we keep eating are largely DEV DUMPS: tokens where the dev has sold most
    of their bag then crater. dev_pct_remaining>=20 (dev still holds >=20%) lifted
    token-mean -1.55%->+8.46% / win 31%->56%; <20 (dev dumped 80%+) is the knife.
    Tunable via DEV_NOT_DUMPED_MIN_PCT."""
    try:
        return float(os.environ.get("DEV_NOT_DUMPED_MIN_PCT", "20.0"))
    except (TypeError, ValueError):
        return 20.0


def dev_not_dumped_blocks(dev_pct_remaining, min_pct=None) -> tuple[bool, str]:
    """DEV-NOT-DUMPED entry gate (MAE selection mine 2026-06-30). Block ONLY a
    CONFIRMED dev-dump: dev_pct_remaining is PRESENT and < min_pct (dev has sold
    down to <min_pct of their original bag = abandoning/rugging = the deep-MAE
    knife cohort). Returns (blocked, why).

    FAIL-OPEN: missing/NaN dev_pct_remaining -> (False, ...) = allow (never dark the
    fleet on absent dev data — same discipline as full_thesis_cohort/stale_knife).
    Pure; never raises. NOTE: ~86% of our dev-known tokens sit below 20 (we are
    mostly buying dev-abandoned tokens) so this gate is HIGH-VOLUME — shadow-first
    + tune the threshold on realized outcomes before enforce."""
    thr = _dev_not_dumped_min_pct() if min_pct is None else float(min_pct)
    try:
        v = None if (dev_pct_remaining is None or isinstance(dev_pct_remaining, bool)) \
            else float(dev_pct_remaining)
    except (TypeError, ValueError):
        return False, "dev_pct_remaining unparseable -> allow"
    if v is None or v != v:  # None / NaN
        return False, "dev_pct_remaining missing -> allow"
    if v < thr:
        return True, f"BLOCK: dev_pct_remaining={v:.1f}<{thr:g} (dev dumped {100 - v:.0f}% — abandon/rug risk)"
    return False, f"dev holds {v:.1f}%>={thr:g} -> allow"


def _shallow_dip_depth_max() -> float:
    """pc_h6 depth REQUIREMENT for the shallow-dip gate (default -30.0). The
    solve-it army's honest-fill band analysis (2026-06-30, token-level, full
    sample) found decline DEPTH — not sign — separates winners from losers:
    pc_h6>0 (pump-retrace) -1.94%/35%win, (-15,0] mild -2.09%/24%win,
    (-30,-15] moderate -2.00%/35%win, BUT (-45,-30] deep +3.29%/51%win and
    <=-45 crater +11.10%/52%win. So a genuine *deep* decline is the edge; a
    shallow dip is the trash. full_thesis_cohort only checks pc_h6<=0 (SIGN),
    so it admits the shallow losers. Require pc_h6 <= this. Tunable via
    SHALLOW_DIP_DEPTH_MAX. (NOTE: opposite of a 'too-deep floor' — deeper is
    BETTER here; the cohort is fat-tail, so pair with exit/size to harvest it.)"""
    try:
        return float(os.environ.get("SHALLOW_DIP_DEPTH_MAX", "-30.0"))
    except (TypeError, ValueError):
        return -30.0


def shallow_dip_blocks(pc_h6, depth_max=None) -> tuple[bool, str]:
    """SHALLOW-DIP entry gate (solve-it band analysis 2026-06-30). Block a dip
    that is NOT DEEP ENOUGH: pc_h6 is PRESENT and > depth_max (e.g. > -30 =
    shallow/pump-retrace = the -2%/24%-win loser cohort). Deep declines
    (pc_h6 <= depth_max) are the +3..+11% fat-tail winners and PASS.

    Returns (blocked, why). FAIL-OPEN: missing/NaN pc_h6 -> (False, ...) = allow
    (same discipline as full_thesis_cohort/dev_not_dumped). Pure; never raises.
    Fat-tail caveat: this keeps the winner POOL (mean+) but the median stays ~0,
    so it is the ENTRY half — exit/size must harvest the tail."""
    thr = _shallow_dip_depth_max() if depth_max is None else float(depth_max)
    try:
        v = None if (pc_h6 is None or isinstance(pc_h6, bool)) else float(pc_h6)
    except (TypeError, ValueError):
        return False, "pc_h6 unparseable -> allow"
    if v is None or v != v:
        return False, "pc_h6 missing -> allow"
    if v > thr:
        return True, f"BLOCK: pc_h6={v:+.1f} > {thr:g} (shallow dip / pump-retrace — not a deep decline)"
    return False, f"deep decline pc_h6={v:+.1f}<={thr:g} -> allow"


def _oversold_held_thresholds() -> tuple[float, float]:
    """(rsi_max, dev_min) for the oversold-held positive selector. The ONLY
    config that survived the solve-it army's held-out + leave-one-out backtest
    (2026-06-30): rsi_15m<=44 AND dev_pct_remaining>=10 = token-mean +4.30%
    /med +0.54%/53% win (vs baseline -1.55/-0.90/31%), POSITIVE in BOTH time
    halves (train +0.81, test +7.10), survives dropping the top 5 tokens (+0.26).
    Median-positive (not pure fat-tail). Keeps ~30% of volume. Tunable via
    OVERSOLD_HELD_RSI_MAX / OVERSOLD_HELD_DEV_MIN."""
    try:
        rsi_max = float(os.environ.get("OVERSOLD_HELD_RSI_MAX", "44"))
    except (TypeError, ValueError):
        rsi_max = 44.0
    try:
        dev_min = float(os.environ.get("OVERSOLD_HELD_DEV_MIN", "10"))
    except (TypeError, ValueError):
        dev_min = 10.0
    return rsi_max, dev_min


def nf5m_toxic_zone_blocks(net_flow_5m_usd, lo=0.0, hi=300.0) -> tuple[bool, str]:
    """NF5M TOXIC-ZONE gate (wallet-flow mine 2026-07-02). The 'weak bounce
    already started' band: entries with net_flow_5m_usd in [0, +300) were the
    single most robust losing cell in the study — full book n=27 tokens,
    mean -3.37 / 11% win / 33% never-green, holds BOTH time halves AND
    drop-top-token; additive on the re-derived entry stack (+0.71pp/token,
    +8.2pp win, -6.3pp never-green at 18% volume cost). Interpretation: a
    small positive 5m inflow at a deep dip = the weak bounce ALREADY happened
    and fizzled — we're buying the failed recovery, not the turn. STRONG
    outflow (still capitulating) or REAL inflow (>=$300) are both fine.
    FAIL-OPEN on missing/garbage. Do NOT ship the stricter 'require nf<0'
    variant — it failed the early time-half. Pure; never raises."""
    try:
        v = None if (net_flow_5m_usd is None or isinstance(net_flow_5m_usd, bool)) \
            else float(net_flow_5m_usd)
    except (TypeError, ValueError):
        return False, "nf5m missing -> allow"
    if v is None or v != v:
        return False, "nf5m missing -> allow"
    if lo <= v < hi:
        return True, (f"nf5m_toxic_zone: net_flow_5m_usd=${v:+.0f} in "
                      f"[{lo:g},{hi:g}) (weak bounce already fizzled)")
    return False, f"nf5m=${v:+.0f} outside toxic zone -> allow"


def green_day_blocks(sol_pc_h6, sol_pc_h1, pc_h6, rsi_15m,
                     dev_pct_remaining) -> tuple[bool, str]:
    """GREEN-DAY regime gate (measured 2026-07-01, 892-position honest book):
    73.7% of gross losses came from SOL-green entries. Rules (R5+R7, the best
    measured combo — kept cohort +$700-1090 over baseline on the 6-day book;
    blocked cohort -$1093, 25.9% win, negative on all 4 days it fired):

      sol_pc_h1 > 1       -> BLOCK regardless (SOL ripping RIGHT NOW: blocked
                             cohort -$220, 29% win, 0/2 days positive)
      sol_pc_h6 <= 0      -> PASS (the dip bot's home turf)
      0 < sol_pc_h6 <=1.5 -> require genuine capitulation pc_h6 <= -25
                             (that cell: +4.57%/55.6% win vs pump-retrace -5.39%)
      sol_pc_h6 > 1.5     -> require oversold_held (rsi<=44 AND dev>=10)
                             (best rip-day slice: 49.6% win, 4/5 days positive;
                             everything else: -1.71%/31% win, the reliable bleed)

    FAIL-OPEN: missing sol fields -> PASS (matches fleet null-handling).
    Exits/size unchanged by design (green-day bounces measured SMALLER — p90
    peak 22.6 rip vs 90.1 red — do NOT uncap on green days). Pure; never raises."""
    def _num(x):
        try:
            v = None if (x is None or isinstance(x, bool)) else float(x)
        except (TypeError, ValueError):
            return None
        return None if (v is None or v != v) else v
    s6 = _num(sol_pc_h6)
    s1 = _num(sol_pc_h1)
    if s1 is not None and s1 > 1.0:
        return True, f"greenday_h1_spike: sol_pc_h1={s1:+.2f}>1 (SOL ripping now)"
    if s6 is None:
        return False, "greenday: sol_pc_h6 missing -> pass (fail-open)"
    if s6 <= 0:
        return False, f"greenday: sol_pc_h6={s6:+.2f}<=0 home turf -> pass"
    p6 = _num(pc_h6)
    if s6 <= 1.5:
        if p6 is not None and p6 <= -25.0:
            return False, (f"greenday mild ({s6:+.2f}): genuine capitulation "
                           f"pc_h6={p6:+.1f}<=-25 -> pass")
        return True, (f"greenday_mild_no_capit: sol_pc_h6={s6:+.2f} in (0,1.5] "
                      f"and pc_h6={'missing' if p6 is None else f'{p6:+.1f}'}"
                      f">-25 (not a capitulation)")
    osh_block, _ = oversold_held_blocks(rsi_15m, dev_pct_remaining)
    if not osh_block:
        return False, f"greenday rip ({s6:+.2f}): oversold_held -> pass"
    return True, (f"greenday_rip_not_oversold: sol_pc_h6={s6:+.2f}>1.5 and not "
                  f"oversold_held (the reliable rip-day bleed)")


def oversold_held_blocks(rsi_15m, dev_pct_remaining,
                         rsi_max=None, dev_min=None) -> tuple[bool, str]:
    """OVERSOLD-HELD positive selector (solve-it backtest 2026-06-30). The cohort
    that held up out-of-sample: rsi_15m <= rsi_max (oversold) AND
    dev_pct_remaining >= dev_min (dev hasn't dumped). Returns (blocked, why).

    This is a POSITIVE selector, so it is FAIL-CLOSED by construction: block
    UNLESS the token is confirmed oversold-AND-dev-held (both signals present and
    passing). A missing rsi or dev -> not selected -> blocked. That is the
    validated cohort (require both), BUT it darks any token missing those features
    -> HIGH-VOLUME (~30% kept) -> SHADOW-FIRST + verify coverage before enforce
    (don't repeat the arm_only dark-fleet). Pure; never raises (coercion error on
    a signal -> that signal treated as absent -> blocked)."""
    rmax, dmin = _oversold_held_thresholds()
    if rsi_max is not None:
        rmax = float(rsi_max)
    if dev_min is not None:
        dmin = float(dev_min)
    def _num(x):
        try:
            v = None if (x is None or isinstance(x, bool)) else float(x)
        except (TypeError, ValueError):
            return None
        return None if (v is None or v != v) else v
    rsi = _num(rsi_15m)
    dev = _num(dev_pct_remaining)
    rsi_ok = rsi is not None and rsi <= rmax
    dev_ok = dev is not None and dev >= dmin
    if rsi_ok and dev_ok:
        return False, f"oversold-held: rsi_15m={rsi:.0f}<={rmax:g} & dev={dev:.0f}>={dmin:g} -> keep"
    why = []
    why.append(f"rsi_15m={rsi:.0f}" if rsi is not None else "rsi missing")
    why.append(f"dev={dev:.0f}" if dev is not None else "dev missing")
    return True, "BLOCK (not oversold-held): " + ", ".join(why) + f" (need rsi<={rmax:g} & dev>={dmin:g})"


def _falling_day_flush_h1_max() -> float:
    """pc_h1 ceiling for the falling-day-flush gate (default -35.0%). At/below this
    extreme flush, combined with a down day, the token is in freefall. Tunable via
    FALLING_DAY_FLUSH_H1_MAX."""
    try:
        return float(os.environ.get("FALLING_DAY_FLUSH_H1_MAX", "-35.0"))
    except (TypeError, ValueError):
        return -35.0


def falling_day_flush_blocks(pc_h24, pc_h1, h24_max: float = 0.0,
                             h1_max: float | None = None) -> tuple[bool, str]:
    """Block a 'dying token in freefall' entry: DOWN on the day AND in an extreme
    h1 flush (2026-06-22 loss-tail decomposition).

    The pc_h24 SIGN is the state-switch the base pc_h1<=-20 gate cannot see: a deep
    h1 flush is a buyable PULLBACK when the token is UP on the day, but a structural
    COLLAPSE when it is DOWN on the day. Mined on the 4-bot badday family (n=420):
    the 8-trade loss-tail (1.9% of trades, 21% of all negative P&L, incl the two
    -55% catastrophes) ALL share pc_h24<0 AND pc_h1<=-35 — 8/8 losers, 0 winners
    clipped, kept-mean +21%. Single conditions each kill many winners; only the
    INTERSECTION is surgical.

    Pure. Fail-OPEN: if either input is non-numeric/NaN, returns False (don't
    block on absent data)."""
    hi = _falling_day_flush_h1_max() if h1_max is None else h1_max
    try:
        a = float(pc_h24)
        b = float(pc_h1)
    except (TypeError, ValueError):
        return False, ""
    if a != a or b != b:  # NaN guard
        return False, ""
    if a < float(h24_max) and b <= float(hi):
        return True, (f"pc_h24={a:.0f}%<0 AND pc_h1={b:.0f}%<={hi:.0f}% "
                      f"(dying-token freefall, not a pullback)")
    return False, ""


def _pump_retrace_h6_min() -> float:
    try:
        return float(os.environ.get("PUMP_RETRACE_H6_MIN", "50"))
    except (TypeError, ValueError):
        return 50.0


def pump_retrace_blocks(pc_h6, h6_min: float | None = None) -> tuple[bool, str]:
    """Block a 'retrace of a fresh pump' entry: token still UP > h6_min (default
    +50%) on the 6h window at fire time (2026-07-03 evening-bleed autopsy).

    A dip on a token that pumped hard in the last 6h is distribution, not
    capitulation — the dip machine keeps catching the unwind (TATE entered at
    pc_h6=+286, Goofreck +73 on the 07-02 evening bleed). Scrubbed per-token
    realized on badday_flush (16 days, n=50 blocked at +50): BLOCK cohort
    -7.80 EARLY / -4.74 LATE (negative BOTH time halves, WR 34%/19%) vs PASS
    +0.45/-4.17; forfeits 5 winners (~+80pp) against ~-326pp of blocked losses.
    Corroborates the coverage audit (pump-retraces = 54% of admissions, the
    never-profitable slice), green-day track B (losers = pump-retraces) and the
    full-thesis cohort (pc_h6<=0 arm).

    NOT for the young lane: a young token's post-launch-pump retrace IS the
    young_absorb setup — callers must exempt young-probe bots.

    Pure. Fail-OPEN on non-numeric/bool/NaN (never block on absent data)."""
    lo = _pump_retrace_h6_min() if h6_min is None else h6_min
    if isinstance(pc_h6, bool):
        return False, ""
    try:
        a = float(pc_h6)
    except (TypeError, ValueError):
        return False, ""
    if a != a:  # NaN guard
        return False, ""
    if a > float(lo):
        return True, (f"pc_h6={a:.0f}%>+{lo:.0f}% "
                      f"(fresh-pump retrace/distribution, not a capitulation dip)")
    return False, ""


@dataclass
class BuyDecision:
    bot_id: str
    token: str
    address: str
    pair_address: str
    entry_price: float
    size_usd: float
    size_tier: str
    triggers_fired: tuple[str, ...]
    reason_summary: str


class BotEvaluator:
    """Per-bot decision engine.

    Pure function of (BotConfig, FeatureBundle) -> Optional[BuyDecision].
    No I/O. Safe to call N times per cycle.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config

    def evaluate(self, b: FeatureBundle,
                 realized_pnl_usd: float = 0.0) -> Optional[BuyDecision]:
        # Momentum-continuation mode (#4.3) — a SEPARATE entry path that bypasses the dip
        # filter stack + dip triggers. Default off -> the dip path below is unchanged.
        if getattr(self.config, "momentum_mode", False):
            return self._evaluate_momentum(b, realized_pnl_usd)
        if self._trading_window_blocks(b):
            return None
        if self._rug_gate_blocks(b):
            return None
        if self._entry_stack_blocks(b):
            return None
        if self._drawdown_freeze_blocks(realized_pnl_usd):
            return None
        if self._sol_macro_blocks(b):
            return None
        if self._btc_macro_blocks(b):
            return None
        if not self._token_regime_passes(b):
            return None
        if self._effective_filter_blocks(b):
            return None

        effective_triggers = self._effective_triggers(b)
        if len(effective_triggers) < self.config.min_triggers_to_fire:
            return None
        if self.config.require_alpha_trigger:
            if not (set(effective_triggers) & ALPHA_TRIGGERS):
                return None

        # Rolling never-green scorer gate (opt-in per bot via ng_scorer_gate +
        # global env NG_SCORER_MODE). Fail-open: a missing model/feature never
        # blocks. Logs every decision in BOTH shadow and enforce so we keep the
        # forward record even while enforcing. See core/ng_scorer.py.
        if self.config.ng_scorer_gate:
            mode = ng_scorer_mode()
            if mode in ("shadow", "enforce"):
                try:
                    block, proba = get_ng_scorer().should_block(b.raw_meta)
                except Exception:
                    block, proba = False, None
                if proba is not None:
                    enforced_block = bool(block and mode == "enforce")
                    logger.info(
                        f"[ng_scorer] bot={self.config.bot_id} token={b.token} "
                        f"p_nevergreen={proba:.3f} thr={get_ng_scorer().threshold:.3f} "
                        f"mode={mode} block={enforced_block} "
                        f"triggers={','.join(effective_triggers)}"
                    )
                    # Persist the decision so the enforced gate is observable forward
                    # (blocks leave no trade record; Railway logs evaporate in ~30min).
                    ng_log_decision({
                        "t": datetime.now(timezone.utc).isoformat(),
                        "bot": self.config.bot_id, "token": b.token,
                        "addr": getattr(b, "address", None),
                        "p": round(proba, 4), "thr": round(get_ng_scorer().threshold, 4),
                        "blocked": enforced_block, "mode": mode,
                        "triggers": list(effective_triggers),
                    })
                    if enforced_block:
                        return None

        size_usd, size_tier = self._size_for(effective_triggers, b, realized_pnl_usd)

        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier=size_tier,
            triggers_fired=effective_triggers,
            reason_summary=f"triggers={','.join(effective_triggers)} tier={size_tier}",
        )

    def _evaluate_momentum(self, b: FeatureBundle,
                           realized_pnl_usd: float = 0.0) -> Optional[BuyDecision]:
        """Momentum-continuation entry path (#4.3). Bypasses the dip filter stack + dip
        triggers; enters on the momentum entry_gate (evaluated via _token_regime_passes,
        which includes the entry_gate AND-conditions). Keeps the trading-window +
        drawdown-freeze lifecycle gates. Emits a single 'momentum_continuation' trigger."""
        if self._trading_window_blocks(b):
            return None
        if self._rug_gate_blocks(b):
            return None
        if self._drawdown_freeze_blocks(realized_pnl_usd):
            return None
        if not self._token_regime_passes(b):   # = the momentum entry_gate conditions
            return None
        size_usd, _ = self._size_for(("momentum_continuation",), b, realized_pnl_usd)
        return BuyDecision(
            bot_id=self.config.bot_id,
            token=b.token,
            address=b.address,
            pair_address=b.pair_address,
            entry_price=b.price_usd,
            size_usd=size_usd,
            size_tier="momentum",
            triggers_fired=("momentum_continuation",),
            reason_summary="momentum_continuation",
        )

    def _drawdown_freeze_blocks(self, realized_pnl_usd: float) -> bool:
        """Pause buying when realized P&L is at or below the freeze threshold.

        Default threshold is None (disabled). When set, the bot stops opening
        new positions until realized P&L recovers above the threshold. Open
        positions are unaffected — only NEW buys are gated.
        """
        c = self.config
        if c.drawdown_freeze_threshold_usd is None:
            return False
        return realized_pnl_usd <= c.drawdown_freeze_threshold_usd

    def _trading_window_blocks(self, b: FeatureBundle) -> bool:
        """Block if FeatureBundle snapshot is outside the configured UTC window.

        Half-open interval [start, end). The default 0..24 always passes
        (fast-path). Wrap-around windows (start > end, e.g. 22..2 meaning
        22,23,0,1) are supported.

        Bug fix 2026-05-23: this method was missing entirely. trading_hour_utc_*
        was defined on BotConfig but never enforced, so the 4 tod_* bots
        had 100% token overlap with each other. See
        [[project_tod_bot_bug_2026_05_23]].
        """
        c = self.config
        if c.trading_hour_utc_start == 0 and c.trading_hour_utc_end == 24:
            return False
        if b.snapshot_ts is None:
            return False  # fail-open if no timestamp on the bundle
        hour = datetime.fromtimestamp(b.snapshot_ts, tz=timezone.utc).hour
        if c.trading_hour_utc_start <= c.trading_hour_utc_end:
            return not (c.trading_hour_utc_start <= hour < c.trading_hour_utc_end)
        # Wrap-around (start > end). Hour is in window if >= start OR < end.
        return not (hour >= c.trading_hour_utc_start or hour < c.trading_hour_utc_end)

    def _sol_macro_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if (c.sol_macro_h6_block_threshold is not None
                and b.sol_pc_h6 is not None
                and b.sol_pc_h6 < c.sol_macro_h6_block_threshold):
            return True
        if (c.sol_macro_h1_block_threshold is not None
                and b.sol_pc_h1 is not None
                and b.sol_pc_h1 < c.sol_macro_h1_block_threshold):
            return True
        return False

    def _btc_macro_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if (c.btc_macro_h1_block_threshold is not None
                and b.btc_pc_h1 is not None
                and b.btc_pc_h1 < c.btc_macro_h1_block_threshold):
            return True
        return False

    def _token_regime_passes(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.pc_h24_max is not None and b.pc_h24 is not None and b.pc_h24 > c.pc_h24_max:
            return False
        if c.pc_h24_min is not None and b.pc_h24 is not None and b.pc_h24 < c.pc_h24_min:
            return False
        if c.pc_h1_max is not None and b.pc_h1 is not None and b.pc_h1 > c.pc_h1_max:
            return False
        if c.age_h_min is not None and b.age_hours < c.age_h_min:
            return False
        if c.age_h_max is not None and b.age_hours > c.age_h_max:
            return False
        if c.mcap_min is not None and b.mcap_usd < c.mcap_min:
            return False
        if c.mcap_max is not None and b.mcap_usd > c.mcap_max:
            return False
        if c.vol_h1_min is not None and (b.vol_h1_usd or 0) < c.vol_h1_min:
            return False
        # Dead-flatline reject (2026-06-02): a token below min_token_volatility_h24_pct
        # cannot MECHANICALLY produce the strategy's needed move (validated: 0 winners had
        # vol<5% in 22 days). Fail-OPEN when the feature is missing (coverage-safe; protects
        # young tokens lacking a 24h window).
        if c.min_token_volatility_h24_pct is not None:
            _vol = (b.raw_meta or {}).get("token_volatility_h24_pct")
            if (isinstance(_vol, (int, float)) and not isinstance(_vol, bool)
                    and _vol < c.min_token_volatility_h24_pct):
                return False
        # Range-floor reject (2026-06-03): trailing-90m high-low range below the floor =
        # flatlining/dead token. Held-out LOTO AUC 0.767; strict superset of the 5% vol
        # gate; 0.6% dollar winner-kill; TREND (90m range 35-95%) untouched. Fail-OPEN when
        # the feature is missing (token <90m old). REPLACES min_token_volatility_h24_pct.
        if c.min_shape_90m_range_pct is not None:
            _rng = (b.raw_meta or {}).get("shape_90m_range_pct")
            if (isinstance(_rng, (int, float)) and not isinstance(_rng, bool)
                    and _rng < c.min_shape_90m_range_pct):
                return False
        if c.require_real_pullback:
            # Held-out-validated entry-quality gate (2026-05-27): block EXTENDED
            # entries (the falling-knife signature behind the buy-into-downtrend
            # losses). Greens are bought after a REAL pullback on a LIVE token;
            # knives near the top of dead ones. Block if drawdown-from-90m-max
            # isn't deep enough OR h24 volatility is too low. Fail-OPEN when a
            # feature is missing (coverage-safe). Isolated to opt-in bots.
            _dd = b.raw_meta.get("shape_90m_drawdown_from_max_pct")
            _vol = b.raw_meta.get("token_volatility_h24_pct")
            if isinstance(_dd, (int, float)) and _dd > -7.5:
                return False
            if isinstance(_vol, (int, float)) and _vol < 30.0:
                return False
        if c.entry_gate:
            # Generic mined-compound gate: AND of [feature, op, threshold]
            # against raw_meta. Fail-OPEN per condition when feature missing.
            # Alias map (2026-06-12): raw_meta's age key is lifecycle_age_hours
            # — gates written as entry_age_hours silently failed open and the
            # young BAND probes collapsed into one overlapping pool on their
            # first fires (all three bands bought CPX). Same defensive chain
            # the young-probe gate uses.
            _GATE_ALIASES = {"entry_age_hours": ("lifecycle_age_hours", "age_hours"),
                             "liquidity_usd": ("entry_liquidity_usd", "liq_usd"),
                             "entry_market_cap_usd": ("mcap", "market_cap_usd")}
            for _cond in c.entry_gate:
                try:
                    _f, _op, _thr = _cond[0], _cond[1], float(_cond[2])
                except (TypeError, ValueError, IndexError):
                    continue
                _v = b.raw_meta.get(_f)
                if not isinstance(_v, (int, float)):
                    for _alt in _GATE_ALIASES.get(_f, ()):
                        _v = b.raw_meta.get(_alt)
                        if isinstance(_v, (int, float)):
                            break
                if not isinstance(_v, (int, float)):
                    continue
                if _op == ">=" and _v < _thr:
                    return False
                if _op == "<=" and _v > _thr:
                    return False
        # Per-bot 15m-RSI oversold gate (2026-06-28, rsi_oversold_ab A/B). Default
        # None = disabled (every other bot byte-identical — this branch is skipped).
        # When set, the bot fires only on tokens whose 15m RSI is KNOWN and <= the cap.
        # FAIL-CLOSED: a missing/non-numeric rsi_15m skips the token, so the A/B
        # measures only tokens where the signal is observable. Threshold = env
        # RSI_OVERSOLD_MAX if set+parseable, else the per-bot config default.
        _rsi_cap = getattr(c, "entry_rsi_15m_max", None)
        if _rsi_cap is not None:
            try:
                _rsi_cap = float(os.environ.get("RSI_OVERSOLD_MAX", _rsi_cap))
            except (TypeError, ValueError):
                _rsi_cap = float(_rsi_cap)
            _rsi_v = (b.raw_meta or {}).get("rsi_15m")
            if not isinstance(_rsi_v, (int, float)) or isinstance(_rsi_v, bool):
                return False  # fail-CLOSED: rsi unknown -> skip
            if float(_rsi_v) > _rsi_cap:
                return False
        return True

    def _rug_gate_blocks(self, b: FeatureBundle) -> bool:
        """Fleet-wide catastrophic-rug guard (2026-06-08). Applies to EVERY bot
        (not opt-in like the filter stack) — gated only by env RUG_GATE_MODE
        (off|shadow|enforce, default enforce). Logs every flagged entry in both
        shadow and enforce so blocks stay observable forward (Railway logs evaporate
        ~30min and a block leaves no trade record). See _rug_structure_blocks."""
        allow_zero_buyers = bool(getattr(self.config, "young_token_probe", False))
        # (1) LP-structure / no-demand instant-pull gate (RUG_GATE_MODE).
        mode = _rug_gate_mode()
        if mode != "off":
            blocked, why = _rug_structure_blocks(b, allow_zero_buyers=allow_zero_buyers)
            if blocked:
                enforced = (mode == "enforce")
                logger.info(
                    f"[rug_gate] bot={self.config.bot_id} token={b.token} {why} "
                    f"mode={mode} block={enforced}"
                )
                if enforced:
                    return True
        # (2) One-shot-sniped 'bundle' rug gate (2026-06-14, RUG_BUNDLE_MODE) — the
        # pump-then-dump class that passes (1) with a clean rugcheck. Exempt
        # young_token_probe bots: buying FRESH (<2h) tokens before buyers accumulate
        # makes 0 recurring buyers + a sniped top-10 the EXPECTED entry state, not a
        # rug (mirrors allow_zero_buyers; the young/microcap family carries its own
        # rug screen).
        bmode = _rug_bundle_mode()
        if bmode != "off" and (not allow_zero_buyers
                               or getattr(self.config, "rug_bundle_gate_force", False)):
            bblocked, bwhy = _rug_bundle_blocks(b)
            if bblocked:
                benforced = (bmode == "enforce")
                logger.info(
                    f"[rug_bundle] bot={self.config.bot_id} token={b.token} {bwhy} "
                    f"mode={bmode} block={benforced}"
                )
                if benforced:
                    return True
        return False

    def _entry_stack_blocks(self, b: FeatureBundle) -> bool:
        """Fleet-wide validated entry-stack gate (2026-06-09). Applies to every
        bot on the dip path EXCEPT the control cohort (which stays ungated so
        the gated-vs-ungated counterfactual keeps being measured forward).
        Momentum-mode bots use a separate entry path and are not gated — the
        stack was validated on dip-style entries only.

        Evidence: bleed-week decomposition (28d, 18,439 closed) — gate-passing
        entries lost $395 across 11 bleed days vs fleet -$18,338; entry
        discipline alone removes ~98% of the bleed. See _entry_stack_violations.
        Env: ENTRY_STACK_MODE=off|shadow|enforce (default enforce),
        ENTRY_STACK_CONTROL_BOTS=csv (default baseline_v1,no_filters,
        pool_a_broad_control)."""
        mode = _entry_stack_mode()
        if mode == "off":
            return False
        if self.config.bot_id in _entry_stack_control_bots():
            return False
        # badday microcap family (2026-06-10): carries its own validated
        # rug-screen stack; the pond bounds (500k-10M, age>=24h) are exactly
        # what it must NOT inherit (its prey lives at 50-500k).
        if getattr(self.config, "entry_stack_exempt", False):
            return False
        fails = _entry_stack_violations(b)
        if not fails:
            return False
        enforced = (mode == "enforce")
        # Throttled observability: one INFO per (token, hour) fleet-wide.
        try:
            key = (b.token, int((b.snapshot_ts or 0) // 3600))
            if key not in _entry_stack_logged:
                if len(_entry_stack_logged) > 8000:
                    _entry_stack_logged.clear()
                _entry_stack_logged.add(key)
                logger.info(
                    f"[entry_stack] token={b.token} {';'.join(fails)} "
                    f"mode={mode} block={enforced} (1/token/hr)"
                )
        except Exception:
            pass
        return enforced

    def _effective_filter_blocks(self, b: FeatureBundle) -> bool:
        c = self.config
        if c.filters_enforced is None:
            disabled = set(c.filters_disabled)
            # Defender filters are OPT-IN only — excluded from default enforcement.
            # Existing bots with filters_enforced=None are unaffected by their addition.
            # Post-stack prune (2026-06-09): for entry-stack-GATED bots, the
            # POST_STACK_PRUNED_FILTERS no longer block on the default path —
            # within stack-passers they blocked winners or never fired. Control
            # cohort (ungated) keeps the full filter set, preserving the
            # counterfactual. See POST_STACK_PRUNED_FILTERS.
            pruned = frozenset()
            if (_entry_stack_prune_on()
                    and _entry_stack_mode() != "off"
                    and c.bot_id not in _entry_stack_control_bots()):
                pruned = POST_STACK_PRUNED_FILTERS
            relaxed = _globally_relaxed_filters()
            return any(
                f not in disabled and f not in DEFENDER_FILTERS and f not in pruned
                and f not in relaxed
                for f in b.filters_block
            )
        enforced = set(c.filters_enforced)
        relaxed = _globally_relaxed_filters()
        return any(f in enforced and f not in relaxed for f in b.filters_block)

    def _effective_triggers(self, b: FeatureBundle) -> tuple[str, ...]:
        c = self.config
        result = list(b.triggers_fired)

        # mcap_psych_level pc_h24 gate
        if (c.mcap_psych_pc_h24_max is not None
                and "mcap_psych_level" in result
                and b.pc_h24 is not None
                and b.pc_h24 >= c.mcap_psych_pc_h24_max):
            result = [t for t in result if t != "mcap_psych_level"]

        if c.triggers_allowed is not None:
            allow = set(c.triggers_allowed)
            result = [t for t in result if t in allow]

        if c.triggers_disabled:
            block = set(c.triggers_disabled)
            result = [t for t in result if t not in block]

        # Per-trigger TOKEN-STATE enforcement (2026-06-12, DORMANT until
        # TRIGGER_STATE_ENFORCE is set): drop a fired trigger when it fired
        # outside its mined state (scorecard sec.5 crossed pre-reg n>=50 on 4
        # gates). Controls exempt — clean counterfactual. Fail-open.
        try:
            from core.trigger_state_gates import enforce_set, should_drop_trigger
            if (enforce_set()
                    and c.bot_id not in _entry_stack_control_bots()):
                result = [t for t in result
                          if not should_drop_trigger(t, b.raw_meta)]
        except Exception:
            pass

        # Per-trigger token-state gates (2026-06-08): drop a FIRED trigger unless its
        # token-state conditions all pass against raw_meta. Fail-OPEN per condition when
        # the feature is missing (same convention as entry_gate, line ~302). Triggers
        # with no gate pass through ungated. A dropped trigger no longer counts toward
        # min_triggers_to_fire -> the bot only enters when a trigger fires IN the state
        # it was validated to win in. See reference_per_trigger_state_conditioning_2026_06_08.
        if c.trigger_state_gates:
            gates = {tg: conds for tg, conds in c.trigger_state_gates}
            kept = []
            for t in result:
                conds = gates.get(t)
                ok = True
                if conds:
                    for _cond in conds:
                        try:
                            _f, _op, _thr = _cond[0], _cond[1], float(_cond[2])
                        except (TypeError, ValueError, IndexError):
                            continue
                        _v = b.raw_meta.get(_f)
                        if not isinstance(_v, (int, float)):
                            continue  # fail-OPEN on missing feature
                        if _op == ">=" and _v < _thr:
                            ok = False
                            break
                        if _op == "<=" and _v > _thr:
                            ok = False
                            break
                if ok:
                    kept.append(t)
            result = kept

        return tuple(result)

    def _size_for(self, triggers: tuple[str, ...], b: FeatureBundle,
                  realized_pnl_usd: float = 0.0) -> tuple[float, str]:
        c = self.config
        is_alpha = bool(set(triggers) & ALPHA_TRIGGERS)
        # 1s_capit_reversal demoted from alpha at pc_h24 >= 80 (commit 9840ffe)
        if (
            "1s_capit_reversal" in triggers
            and b.pc_h24 is not None
            and b.pc_h24 >= 80.0
            and not (set(triggers) - {"1s_capit_reversal"}) & ALPHA_TRIGGERS
        ):
            is_alpha = False
        if is_alpha:
            base = c.base_position_usd * c.alpha_multiplier
            tier = "alpha_trigger"
        else:
            base = c.base_position_usd
            tier = "standard"
        # Honor premium_runner / marginal multipliers (2026-05-27 audit #6 — these
        # BotConfig fields were defined but never applied). Trigger-set based, so
        # impact is contained; bots that set them to 1.0 (e.g. cap2k) stay flat.
        # macro_up_multiplier is intentionally NOT wired: its legacy condition needs
        # sol_pc_m1, which the FeatureBundle doesn't carry — left explicit-N/A rather
        # than blindly applied to ~half the fleet's trades.
        if not is_alpha and _PREMIUM_RUNNER_TRIGGER in triggers:
            base *= c.premium_runner_multiplier
            tier = "premium_runner"
        elif not is_alpha and triggers and all(t in _MARGINAL_FOR_SIZE for t in triggers):
            base *= c.marginal_multiplier
            tier = "marginal"
        if c.compound_mode is not None:
            base = self._apply_compound(base, realized_pnl_usd)
            tier = f"{tier}+compound_{c.compound_mode}"
        if c.macro_conditional_mode is not None:
            base, macro_tag = self._apply_macro_conditional(base, b)
            tier = f"{tier}+{macro_tag}"
        if c.conviction_sizing_mode is not None:
            base, conv_tag = self._apply_conviction(base, triggers, b)
            tier = f"{tier}+{conv_tag}"
        # P7 regime dial (2026-06-10; OFFENSE unlocked 2026-06-11): on dial-bad
        # days, halve dip-pond size (everyone). On dial-good days, the 1.5x
        # upsize applies ONLY to walk-forward LIVE-SET members — bots already
        # net-positive trailing-7d BEFORE today (they earned size; the
        # size-is-the-bleed disaster was size on UNqualified bots). Env
        # REGIME_DIAL_OFFENSE=live_set(default)|off. Exempt: momentum-mode,
        # entry-stack controls, regime_dial_exempt. Fail-soft.
        try:
            if (not getattr(c, "regime_dial_exempt", False)
                    and not c.momentum_mode
                    and c.bot_id not in _entry_stack_control_bots()):
                from core.regime_dial import get_dial
                import os as _os
                _m = get_dial().defense_multiplier()
                if (_os.environ.get("REGIME_DIAL_OFFENSE", "live_set").lower()
                        == "live_set"):
                    try:
                        from core.live_set import get_live_set
                        if c.bot_id in get_live_set().members():
                            _full = float((get_dial().current() or {})
                                          .get("mult_full") or 1.0)
                            _m = max(_m, _full)   # offense only lifts, never
                                                  # weakens defense floor
                    except Exception:
                        pass
                if _m != 1.0:
                    base *= _m
                    tier = f"{tier}+dial{_m:g}"
        except Exception:
            pass
        return base, tier

    def _apply_conviction(self, base: float, triggers: tuple[str, ...],
                          b=None) -> tuple[float, str]:
        """Scale size by entry conviction. 'trigger_count' mode: more
        confluent triggers → bigger size, capped at conviction_max_mult.

        SOL-RED DOWN-SIZE (badday gap audit 2026-06-22, ENFORCE): conviction
        up-sizing while SOL itself is falling is the conviction-drawdown amplifier
        — don't 2x into a falling tape. When the conviction mult would up-size
        (>1x) AND b.sol_pc_h1 <= CONVICTION_SOLRED_H1_MAX (default -0.3), cap to 1x.
        SIZE-DOWN ONLY — never blocks a trade, never adds risk. Gated
        CONVICTION_SOLRED_MODE (enforce default; off disables)."""
        c = self.config
        if c.conviction_sizing_mode == "trigger_count":
            n = len(triggers)
            mult = min(1.0 + c.conviction_step * max(0, n - 1), c.conviction_max_mult)
            if mult > 1.0 and os.environ.get(
                    "CONVICTION_SOLRED_MODE", "enforce").strip().lower() != "off":
                _sh1 = getattr(b, "sol_pc_h1", None) if b is not None else None
                try:
                    _thr = float(os.environ.get("CONVICTION_SOLRED_H1_MAX", "-0.3"))
                except (TypeError, ValueError):
                    _thr = -0.3
                if isinstance(_sh1, (int, float)) and _sh1 == _sh1 and _sh1 <= _thr:
                    return base, f"conviction_x1.00_solred(sol_h1={_sh1:.2f})"
            return base * mult, f"conviction_x{mult:.2f}"
        return base, "conviction_off"

    def _apply_macro_conditional(self, base: float, b: FeatureBundle) -> tuple[float, str]:
        """Gradient sizing based on macro state. Currently supports 'sol_h6' mode:
        1.5x when sol_pc_h6 >= +0.3, 0.5x when sol_pc_h6 <= -0.1, 1.0x else.
        Other modes can be added later (btc, multi-asset, etc.)."""
        c = self.config
        if c.macro_conditional_mode == "sol_h6":
            sol = b.sol_pc_h6
            if sol is None:
                return base, "macro_neutral"
            if sol >= 0.3:
                return base * 1.5, "macro_bull"
            if sol <= -0.1:
                return base * 0.5, "macro_bear"
            return base, "macro_neutral"
        # Unknown mode → no-op
        return base, "macro_off"

    def _apply_compound(self, base: float, realized_pnl_usd: float) -> float:
        """Apply compounding multiplier per the bot's compound_mode.

        All modes are floored at 0.25x (never size below 25% of base, even
        on a brutal drawdown — lets the bot recover if it's right going
        forward) and capped at compound_max_multiplier (default 5x, prevents
        runaway growth from a single fluky win streak).
        """
        c = self.config
        starting = c.paper_capital_usd or 2000.0
        if c.compound_mode == "linear":
            mult = 1.0 + (realized_pnl_usd / starting)
        elif c.compound_mode == "winners_only":
            mult = 1.0 + (max(0.0, realized_pnl_usd) / starting)
        elif c.compound_mode == "threshold":
            # Step-additive: mult is computed against `base` so the formula
            # output stays in the same units the caller expects.
            steps = int(max(0.0, realized_pnl_usd) // c.compound_threshold_step_usd)
            if base <= 0:
                return base
            mult = 1.0 + (steps * c.compound_step_amount_usd) / base
        else:
            return base
        mult = max(0.25, min(mult, c.compound_max_multiplier))
        return base * mult
