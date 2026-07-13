# core/bot_config.py
from __future__ import annotations
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class BotConfig:
    """Universal config schema for a single bot.

    See docs/superpowers/specs/2026-05-23-multi-bot-harness-design.md for
    semantics of each field. All thresholds are inclusive unless noted.
    """

    bot_id: str
    display_name: str
    enabled: bool = True

    # Capital & sizing
    paper_capital_usd: float = 2000.0
    base_position_usd: float = 20.0
    max_concurrent_positions: int = 3
    alpha_multiplier: float = 1.5
    macro_up_multiplier: float = 1.5
    premium_runner_multiplier: float = 3.0
    marginal_multiplier: float = 0.5

    # Macro gates (None disables)
    sol_macro_h6_block_threshold: Optional[float] = -0.3
    sol_macro_h1_block_threshold: Optional[float] = -0.7
    btc_macro_h1_block_threshold: Optional[float] = None

    # Token regime gates
    pc_h24_max: Optional[float] = None
    pc_h24_min: Optional[float] = None
    pc_h1_max: Optional[float] = None
    age_h_min: Optional[float] = None
    age_h_max: Optional[float] = None
    mcap_min: Optional[float] = None
    mcap_max: Optional[float] = None
    vol_h1_min: Optional[float] = 1000.0

    # Dead-flatline reject (2026-06-02). Block entries whose token_volatility_h24_pct is
    # below this — a token that barely moves (e.g. vRse... = 0.48% 24h vol, slow-bled across
    # 26 bots) CANNOT mechanically produce the +5-30% the strategy needs. Validated: 0
    # winners had vol<5% in 22 days (the <5% bucket is all TP1 scratches / dead). Fail-OPEN
    # when the feature is missing (coverage-safe). None = off. Set ~5.0 on production-track
    # bots; NOT young probes (24h vol is ill-defined for <6h tokens).
    min_token_volatility_h24_pct: Optional[float] = None

    # Range-floor reject (2026-06-03): block entries on tokens whose trailing 90-minute
    # high-low range is below this %. A flatlining/dead token (low range) cannot produce
    # the strategy's move. Held-out (8-Opus hunt, LOTO AUC 0.767, token-clustered null
    # p=0.009): this is a STRICT SUPERSET of the min_token_volatility_h24_pct=5 gate (blocks
    # everything it blocks + 389 more) with 0.6% dollar winner-kill; TREND (+2.8x) untouched
    # (90m range 35-95%). REPLACES the 5% vol floor. Fail-OPEN on missing (token <90m old).
    min_shape_90m_range_pct: Optional[float] = None

    # Entry-quality gate (2026-05-27, held-out validated). When True the bot
    # blocks EXTENDED entries (the falling-knife signature behind the
    # buy-into-downtrend losses): requires a REAL pullback
    # (shape_90m_drawdown_from_max_pct <= -7.5) AND live volatility
    # (token_volatility_h24_pct >= 30). Fail-OPEN if a feature is missing.
    # Opt-in per bot (default False = no change to existing bots). See
    # reference_entry_separator_mine_2026_05_27.
    require_real_pullback: bool = False

    # Real-time dip-detection trigger modes (2026-06-21 plan). Each off|shadow|
    # enforce, resolved per-bot by core.fast_watch.rt_mode (bot config wins over
    # the env default of the same name). DEFAULT "off" = legacy stale-snapshot
    # trigger, no behavior change. enforce = the bot enters off FRESH-repriced
    # pc_h1/m5 (rt_trigger_mode), re-arms every fast tick (rt_arm_mode), and
    # confirms a fresh net_flow_15s demand-turn (rt_demand_turn_mode).
    rt_trigger_mode: str = "off"
    rt_arm_mode: str = "off"
    rt_demand_turn_mode: str = "off"

    # Generic per-bot entry gate (2026-05-27, held-out-validated compound mine).
    # Optional list of [feature, op, threshold] conditions ANDed against raw_meta
    # at entry; op in {">=", "<="}. Fail-OPEN per condition when the feature is
    # missing (coverage-safe). Lets a bot enforce a mined compound (e.g.
    # pc_h1<=-8 AND 1s_green_run_end>=2 AND <orthogonal axis>) with no new code.
    # Default None = no gate. See reference_entry_separator_mine_2026_05_27.
    entry_gate: Optional[tuple] = None

    # Per-bot 15m-RSI oversold entry cap (2026-06-28, rsi_oversold_ab A/B). When set,
    # the bot only fires on tokens whose 15-min RSI (raw_meta["rsi_15m"], computed live
    # by feeds.tier2_features.compute_rsi_bb) is KNOWN and <= this cap. Missing rsi
    # FAIL-CLOSED (token skipped) so the A/B measures only tokens where the signal is
    # actually observable. The cap here is the per-bot default; env RSI_OVERSOLD_MAX
    # overrides it at runtime (affects ONLY bots that opt in via this field). Default
    # None = disabled -> every other bot's decision is byte-identical. Consumed in
    # core/bot_evaluator.py::_token_regime_passes (alongside entry_gate).
    entry_rsi_15m_max: Optional[float] = None

    # Per-trigger token-state gates (2026-06-08, 7-Opus held-out validation of AxiS's
    # "each trigger only wins in a specific token-state" thesis — see
    # reference_per_trigger_state_conditioning_2026_06_08). Optional list of
    # [trigger_name, [[feature, op, threshold], ...]] pairs. At entry a FIRED trigger
    # is DROPPED from the effective set unless ALL its conditions pass against raw_meta
    # (op in {">=","<="}; fail-OPEN per condition when the feature is missing, same as
    # entry_gate). Dropped triggers don't count toward min_triggers_to_fire, so the bot
    # only enters when a trigger fires IN the state it was validated to win in. Triggers
    # with no entry here pass through ungated. Default None = no change to existing bots.
    trigger_state_gates: Optional[tuple] = None

    # Filter set — semantics: if filters_enforced is None, the bot uses
    # the project baseline filter set MINUS anything in filters_disabled.
    # If filters_enforced is a list, that's the EXACT enforced set and
    # filters_disabled is ignored.
    filters_enforced: Optional[tuple[str, ...]] = None
    filters_disabled: tuple[str, ...] = field(default_factory=tuple)

    # Rolling never-green scorer opt-in (see core/ng_scorer.py). When True AND the
    # global env NG_SCORER_MODE is shadow/enforce, this bot's entries are scored by
    # the rolling model; in enforce mode a high never-green probability blocks the
    # buy. Default False (control bots stay ungated so the A/B stays measurable).
    ng_scorer_gate: bool = False

    # Triggers — same semantics as filters
    triggers_allowed: Optional[tuple[str, ...]] = None
    triggers_disabled: tuple[str, ...] = field(default_factory=tuple)
    min_triggers_to_fire: int = 1
    require_alpha_trigger: bool = False

    # Trigger-specific gates (evaluated after universal gates pass)
    mcap_psych_pc_h24_max: Optional[float] = 80.0

    # Exit ladder
    tp1_pct: float = 5.0
    tp1_sell_fraction: float = 0.75
    tp2_pct: float = 10.0
    tp2_sell_fraction: float = 0.25
    trail_pp: float = 3.0
    hard_stop_pct: float = -15.0
    pre_stop_bail_pnl_pct: float = -3.0
    pre_stop_bail_vol_m5_max: float = 500.0
    slow_bleed_minutes: int = 60
    slow_bleed_pnl_threshold: float = -8.0
    # Stop gap-through guards (2026-06-10, momentum_shadow: 13 hard stops filled
    # at avg -15.6% on a -12 stop). Both pre-TP1 only; None = off (default).
    # giveback floor: once peak >= giveback_floor_peak_min, exit at
    # giveback_floor_pnl_pct (8/13 gap-stops peaked +3.8..+9.9 then collapsed).
    giveback_floor_peak_min: Optional[float] = None
    giveback_floor_pnl_pct: Optional[float] = None
    # fast-dump bail: exit at this pnl at ANY volume (the vol<500 condition on
    # pre_stop_bail never fires on high-volume momentum dumps).
    fast_bail_pnl_pct: Optional[float] = None

    # Bad-day playbook (2026-06-10):
    # entry_stack_exempt: this bot carries its OWN validated entry stack
    # (e.g. the badday microcap family's rug screens) — the fleet stack's
    # 500k-10M/age>=24h pond bounds don't apply to it.
    entry_stack_exempt: bool = False
    # winner_select_entry (patient sleeve, 2026-06-26): fire ONLY on winner-selected
    # entries (median_buy_size_usd >= 34.3 — deep capitulation met by real buyer size,
    # the +tail signal). FAIL-CLOSED when gated (no signal -> skip). The entry filter
    # for the patient-hold A/B sleeve. See core.bot_evaluator.winner_select_entry_blocks.
    winner_select_entry: bool = False
    # regime_dial_exempt: the P7 dial's 0.5x bad-day defense does NOT de-size
    # this bot (bad-day vehicles fish the segment that still pays on bad days;
    # momentum_mode bots and the control cohort are auto-exempt in code).
    regime_dial_exempt: bool = False
    # rug_bundle_gate_force: opt INTO the one-shot-sniped bundle rug gate even when
    # young_token_probe=True (which otherwise exempts it). The chameleon is flagged
    # young_token_probe for the zero-buyers STRUCTURE carve-out, but is NOT a genuine
    # fresh-launch probe — it must still be blocked from sniped-no-recurring rugs.
    # Default False = the young-probe family keeps its bundle-gate exemption.
    rug_bundle_gate_force: bool = False
    # antirug_floor_exempt: skip the fleet #432 anti-rug liq floor (>=25k). ONLY for
    # tiny-size + fast-time-box rug-pocket probes (wallet-mimic): the copyable winners'
    # habitat is $9-20k liq; at ~$20 size the exit slippage is small + the fast box caps
    # rug exposure. Default False = every other bot keeps the floor.
    antirug_floor_exempt: bool = False
    # microcap_mandate: passes the badday-lane buy-gate containment for sub-$1M tokens
    # (2026-06-15). REQUIRED for any non-badday/non-probe bot hunting the deep-microcap
    # pocket (e.g. rugpocket_scalper): without it the badday-lane containment skips the buy
    # even after the lane admits the token. Per-bot mcap/dip/rug gates still contain it.
    microcap_mandate: bool = False

    # Trading window (UTC hours, half-open: [start, end))
    trading_hour_utc_start: int = 0
    trading_hour_utc_end: int = 24

    # Compounding (2026-05-23 — experimental). Position size scales with
    # cumulative realized P&L. None disables (default). Modes:
    #   "linear"       — size = base * (1 + realized_pnl / starting_balance)
    #                    grows on wins, shrinks on losses (floored at 0.25x)
    #   "winners_only" — size = base * (1 + max(0, realized) / starting_balance)
    #                    grows on wins, never shrinks below base
    #   "threshold"    — size = base + floor(max(0, realized) / step_usd) * step_amount
    #                    discrete steps: +$step_amount per $step_usd of realized profit
    # All modes are capped at compound_max_multiplier to prevent runaway growth.
    compound_mode: Optional[str] = None
    compound_threshold_step_usd: float = 100.0
    compound_step_amount_usd: float = 5.0
    compound_max_multiplier: float = 5.0

    # Drawdown freeze (2026-05-23 — Deploy C). Pause buying when realized P&L
    # drops to or below this threshold. None disables. Buying resumes
    # automatically when realized recovers above the threshold.
    drawdown_freeze_threshold_usd: Optional[float] = None

    # Macro-conditional sizing (2026-05-23 — Deploy C). Scale position size
    # gradient-style based on sol_pc_h6 (overrides binary sol_macro block).
    # None disables. "sol_h6" mode: 1.5x when sol_pc_h6 >= +0.3, 0.5x when
    # sol_pc_h6 <= -0.1, 1.0x otherwise. The bot's sol_macro_h6_block_threshold
    # should typically be relaxed (e.g. None) when this is active so the
    # gradient sizing isn't pre-empted by the binary block.
    macro_conditional_mode: Optional[str] = None

    # Conviction-scaled sizing (2026-05-25 — P-stack #2). Scale position size
    # by entry conviction. None disables. "trigger_count" mode: size grows
    # with the number of triggers that fired (confluence) —
    #   mult = min(1 + conviction_step * (n_triggers - 1), conviction_max_mult)
    # Leans on the finding that confluent signals / concurrent positions are
    # the strongest big-winner predictors. Stacks AFTER alpha/compound/macro.
    conviction_sizing_mode: Optional[str] = None
    conviction_step: float = 0.5
    conviction_max_mult: float = 2.5

    # Velocity exit (2026-05-25 — P-stack #3). Recycle capital out of FLAT
    # positions that go nowhere, freeing it for new trades (max-volume lever).
    # None disables. When set, a pre-TP1 position held >= flat_exit_minutes
    # whose pnl is inside +/- flat_exit_band_pct (neither winning nor losing)
    # is closed. Distinct from slow_bleed (loss-based) — this targets dead
    # money, not losers.
    flat_exit_minutes: Optional[int] = None
    flat_exit_band_pct: float = 3.0

    # Stall exit (2026-05-29 — 7h-watch rec #3). Opt-in (None disables). Fires
    # pre-TP1 when a position (a) peaked low (peak_pnl_pct < stall_exit_peak_max),
    # (b) has been held >= stall_exit_minutes, AND (c) is now drifting back down
    # off that peak (pnl_pct <= peak_pnl_pct - stall_exit_drift_pp). Targets the
    # never-launched corpse that bleeds capital for hours below the slow_bleed
    # loss threshold. Distinct from flat_exit (band-based, ignores peak) and
    # slow_bleed (pure loss threshold). V3 validation: 328 losers (-$1275 pool)
    # peaked<5% held>90min; winner-clip risk ~$1.35 (11 of 13 at-risk winners
    # exited via trail = riding up, stall would not fire).
    stall_exit_minutes: Optional[int] = None
    stall_exit_peak_max: float = 5.0
    stall_exit_drift_pp: float = 2.0

    # Re-entry cooldown (2026-05-25 — P-stack #4). Seconds a bot must wait
    # after fully closing a token before it may buy that token again. None or
    # 0 = immediate re-entry allowed (recycle into a re-firing runner). A
    # positive value throttles churn on the same token.
    reentry_cooldown_secs: Optional[float] = None

    # Phase-1 risk floors (2026-06-01). Per-bot, None = off. Enforced only on the
    # production-candidate config (shadow-measured fleet-wide first). See
    # docs/superpowers/specs/2026-06-01-phase1-risk-floor-design.md.
    # Both floors are enforced by the canonical Phase-1 risk-floor block in
    # dip_scanner._execute_bot_buy, gated by env RISK_FLOOR_MODE (shadow|enforce).
    # 2026-06-08 capital-preservation backstop (failure investigation): both turned
    # ON fleet-wide via these defaults + RISK_FLOOR_MODE=enforce.
    # daily_loss_limit_usd: halt NEW buys once today's realized daily_pnl_usd
    #   <= -this (sells always allowed; clears at UTC 00:00). Default -$40: normal-bad
    #   days for these $100 bots run ~-$20, so -$40 catches a clearly-bad day.
    # max_token_buys_per_day: cap re-entries into a single token per UTC day. Default 3
    #   = the size-caps agent's per-token cap AND mirrors smart-money behaviour (71% of
    #   elite wallet-token pairs are one-and-done; the death-spiral was one bot buying
    #   the same token 14-206x). Conviction via breadth, not re-piling.
    daily_loss_limit_usd: Optional[float] = 40.0
    max_token_buys_per_day: Optional[int] = 3

    # Live measurement probe (2026-06-02). Per-bot, scaffolding for the
    # paper->live probe — see docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md.
    # live_probe: when True AND USE_JUPITER_ULTRA AND a real private key is present,
    #   THIS bot's fills route through the live MEV-protected Ultra swap (the bridge,
    #   piece 1b) instead of the paper simulator. Default False = paper (the whole
    #   fleet stays paper unless a bot opts in AND the env gates are set). It can
    #   NEVER go live on flag alone — the bridge requires the env gates too.
    # (Size variants for the probe are SEPARATE fixed-size bots — probe_tightexit_live_{20,50,100}
    #  with multipliers neutralized — so each size is a clean per-token paired measurement,
    #  not a rotation-within-one-bot that entangles size with token/timing.)
    live_probe: bool = False

    # Shared no-same-token exclusion pool (2026-06-02). Bots that share the same
    # non-empty pool name may NOT hold the same token concurrently — the first to
    # open it claims it; pool siblings skip that token until it closes. This is the
    # de-concentration lever (spread a pool of bots across DISTINCT simultaneous
    # tokens instead of piling into one). None (default) = not in any pool = current
    # single-bot behavior, never blocked. See core/shared_token_registry.py and
    # the A/C design comparison (2026-06-02).
    exclusion_pool: Optional[str] = None
    # TIME-BOX exit (2026-06-12, the Dw5Vykxu archetype): full close at N
    # minutes of hold REGARDLESS of pnl. Risk boxed by time, not price —
    # red-tape chop executes price-stops at local bottoms (74% of our stops
    # recovered); a time stop is immune to wicks. None = off.
    time_stop_minutes: Optional[float] = None

    # Never-runner exit (2026-06-02 mine — convergent across 3 of 8 agents). A
    # composed loss-avoidance exit for the cohort that NEVER went meaningfully
    # green: fires when peak_pnl_pct < never_runner_peak_max AND (pnl <=
    # never_runner_loss_floor [fast-bleeder arm] OR held >= never_runner_minutes
    # [flat-liner arm]). The peak gate makes it winner-safe BY CONSTRUCTION (it can
    # never touch a position that crossed the peak threshold), so the runner trail
    # is untouched (validated 0-winner-kill, held-out by token+time). SHADOW always
    # (state_blob never_runner_* stamped for phantom parity); ACTS only when
    # never_runner_exit_enabled. Default off = no behavior change. Forward-test the
    # minutes threshold (30/45/60) on the pool bots before enforcing on production.
    never_runner_exit_enabled: bool = False
    never_runner_peak_max: float = 3.0
    never_runner_loss_floor: float = -6.0
    never_runner_minutes: int = 45

    # Scale-in / staged entry (2026-06-05, 8-intervention SOL-flicker mine). The ONLY
    # lever that cut the "born-before-crater" never-green bleed without killing the
    # dip-buy edge. The fader and the dip-before-rip are inseparable at entry (proven 8
    # ways: SOL gate/velocity, early stop, drop-velocity, volume, rug-gate all fail), so
    # instead of trying to AVOID the bad trade we commit a HALF tranche at entry and
    # complete to full size only once the position CONFIRMS (pnl >= scalein_confirm_pct).
    # Faders never get the 2nd half (loss halved); winners reach FULL size on confirmation
    # (~0.5pp cost). NOT a de-size stop (winners are full size), NOT flat de-sizing.
    # Backtest (13d real, per-bot): champion_defender_2k -$821 -> ~-$55 (recovers +$766 at
    # CONF=1); outlier-robust (ex-top-5 +$545), held-out positive 10/14 days. PAPER-ONLY:
    # the 2nd-tranche LIVE execution is wired at go-live (gated by test_pre_live_invariants);
    # in live the bot opens full size = current behavior, so enabling it cannot mis-size a
    # live bot. Default off = no behavior change.
    scalein_enabled: bool = False
    scalein_confirm_pct: float = 1.0      # add the 2nd tranche when pnl first reaches this
    scalein_first_fraction: float = 0.5   # fraction of full size deployed at entry
    # FLASH-slip de-risk within scale-in (2026-06-05, flash-signature mine). Thin EXECUTABLE
    # depth at entry (slip_buy_2000_pct >= scalein_flash_slip_pct) is the orthogonal flash-
    # crash signature (AUC 0.80 vs nominal liq which failed) — but flash tokens are two-sided
    # (they also moon), so we DON'T block: enter an even SMALLER first tranche; the runner
    # still completes to FULL on confirm. Null slip (Jupiter quote unavailable on ~44%) ->
    # default fraction. Applies only where scalein_enabled. Winner-safe (confirm-to-full).
    scalein_flash_slip_pct: float = 6.0
    scalein_flash_first_fraction: float = 0.33

    # ng_faststop acting exit (2026-06-05 drawdown-mine LEVER 1). The never-green fast-stop
    # (peak<2 AND pnl<=-4) already runs as a SHADOW; promote it to ACT. It observes the
    # shallow -4 tick that the never_runner -6 floor GAPS PAST (slow never-green bleeders
    # jump -4->-16 in one 60s poll, skipping [-6,-15] -> book the -15 hard stop). Acting on
    # the -4 tick books ~-4.7 instead of -16.4: cuts ~23% of fleet loss / ~22% maxDD for
    # 0-2 winners (save:kill ~975:1, off-06-04-durable). Winner-safe BY CONSTRUCTION (peak<2
    # can't touch a runner); EXIT-ONLY (no size change — the user will not cut size). Default
    # off = no behavior change; opt-in per bot (pool_a/pool_c_tightexit/def2k).
    ng_faststop_exit_enabled: bool = False

    # Pool sizing de-rates (2026-06-02 fleet-mine, cap-respecting positive selection).
    # When True, position size is adjusted DOWN (never up — honors the $100 cap) for the
    # cohorts the fleet mine flagged, with the smart-money compound EXEMPT (kept at full
    # size = the only "up" we can do under a cap). Applied in dip_scanner before reserve:
    #   - on-chain concentration: capped top10_holder_pct < 50 -> x0.5 (held-out, 72 tok)
    #   - concurrent>1 in a DOWN regime (sol_pc_h6<0 or btc_pc_h1<0) -> x0.5 (regime-
    #     conditional; conc>1 alpha sign-flips in a crash)
    #   - smart-money compound (smart_wallet_count_total>=1 AND 5m_red_count>=6 AND
    #     net_flow_15s_usd>0): EXEMPT from de-rates (positive selection, stays full size)
    # Default False = no change. Enabled on the pool bots (the $100-cap forward A/B);
    # the production candidate stays clean until forward data + the blended-PnL re-run
    # firm the (provisional, ~3-day) magnitudes.
    pool_sizing_derates_enabled: bool = False

    # Young-token probe (2026-06-02, #4.1). When the global YOUNG_TOKEN_PROBE env flag is
    # on, a bot with young_token_probe=True trades YOUNG tokens ONLY (age < the young
    # threshold, surfaced past the fleet min_age gate with a liquidity floor); production
    # bots (False) SKIP young tokens. Default False + env off = no change. Tests whether the
    # universe-mine's age<=2 edge survives on realized dip-buy paths. See core/young_token_probe.py.
    young_token_probe: bool = False

    # Streak-latch mode (2026-07-03 swing-latch study). Market-only sim on 216
    # traction tokens: deep-swing re-entry AFTER a won swing = +4.09 gross/swing
    # (~+1.5 net of live costs), stable across time/token splits — but the edge
    # lives in a minority of SERIAL SWINGERS (per-token mean only ~+0.5). This
    # flag implements the ride-the-streak rule that concentrates onto them:
    # after any LOSING sell leg on a token, this bot drops that token
    # permanently (in-memory; resets on redeploy — fine for the paper A/B);
    # winning legs leave re-entry open (pair with reentry_cooldown_secs=0).
    streak_latch: bool = False

    # Per-bot velocity-bail pnl threshold override (2026-07-03 current-regime
    # winner decode). The in-flight velocity pre-empt (bail at pnl<=-4 on a
    # fast never-green collapse) was 48% of family exits since 07-01 at mean
    # -5.78 = 83% of the bleed, and 77% of bailed tokens hit +6% ABOVE the
    # bail within 60m (n=48, thin) — winners sit through -7/-12 wicks. None ->
    # the -4 default. Set to -8 (below the -7 MAE floor) to effectively
    # disable the velocity leg while keeping the floor — the wickride A/B arm.
    velbail_pnl_pct: Optional[float] = None

    # FAIL-CLOSED entry gate (2026-07-04 young no-data bleed). When True, a
    # missing/non-numeric feature in ANY entry_gate clause SKIPS the token
    # (default False = fail-open per the read-as-zero rule). For demand-thesis
    # bots (young/adolescent/swing lanes): unknown demand is not a waived
    # gate, it's an unqualified entry — 07-04: 5/7 young entries fired with
    # buyers/nf15=None and all lost (-40pp day); the one observed-demand entry
    # won. Candidates are plentiful; skipping unknowns costs little.
    entry_gate_require_data: bool = False

    # HL-CONFIRM entry (2026-07-05 trough anatomy, scratchpad/
    # _trough_anatomy.md). When True the bot buys ONLY when the fast-watch
    # confirm-window state machine reads CONFIRMED (no new low >=~150s AND
    # price >= low*1.01) — we fire mid-knife otherwise (median fill +14.8%
    # above the eventual low). Study: EV -2.51 -> +1.03 pp/episode,
    # TP1-before-stop 36.9 -> 64.4%, stops halved, holds in both half-splits.
    # TRACKING/STALE/EXPIRED -> skip (the flush either just started, went
    # quiet, or is old news; candidates are plentiful).
    hl_confirm_entry: bool = False

    # LIQ-EXIT-FLOOR per-bot enforce (2026-07-06): refuse entries whose book
    # is too thin to EXIT cleanly (< LIQ_EXIT_FLOOR_USD, default $30k) even
    # while the fleet-wide gate stays shadow. Set on the live probe + its
    # paper twin (parity): thin books priced ~2.45% RT slip on the first
    # live round trip; depth is the one structural live-friction lever.
    liq_exit_floor_enforce: bool = False

    # CONDITIONAL PEEL exit (2026-07-06, TP-peel replay — elite-wallet exit
    # shape, conservatively replayed: +72pp/4.5d on flush, both halves
    # positive, loser-harm zero by construction). When the TP1 fill lands
    # BELOW peel_threshold_pct the remainder becomes an uncapped runner
    # trailed by peel_giveback_pp (TP2 skipped); wick fills (>= threshold)
    # keep the standard ladder — the unconditional peel LOSES on those.
    peel_exit: bool = False
    peel_threshold_pct: float = 12.0
    peel_giveback_pp: float = 5.0

    # LOSS-STREAK PAUSE (2026-07-06 session-discipline decode): losses cluster
    # in time — a market state, not tilt (revenge-tax dissolved within-wallet).
    # Human winners rotate away from degraded stretches; bots re-fire the same
    # signal into them. After loss_streak_n consecutive losing FULL closes
    # (position-level: sum of legs), hold new entries loss_streak_pause_secs.
    # Fleet join: +1,626pp/9d, 16/17 bots positive, 7/10 days; young lane
    # EXEMPT (-11.2pp there). Winner-kill 9% — above the 5% bar, flagged;
    # net savings dominated every cut. Env kill: LOSS_STREAK_PAUSE_MODE=off.
    loss_streak_pause: bool = False
    loss_streak_n: int = 3
    loss_streak_pause_secs: float = 3600.0

    # PEAK-SCALED RUNNER TRAIL (2026-07-06 EV model — scratchpad/_ev_model):
    # the strategy's whole edge is convex (2 runners carried 100% of the young
    # lane's gross EV; median trade is negative). A FIXED giveback (peel's 5pp)
    # cuts a +70 runner at +65 — it caps exactly the tail that pays. This trails
    # TIGHT on small gains and LOOSE on monsters: giveback = base + k*(peak-ref),
    # capped. peak +10→5pp (exit +5); +40→11pp (exit +29); +80→19pp (exit +61).
    # Protects small gains from round-trip while letting the rare monster breathe.
    # Applies post-TP1 (peel runner or plain trail). Default off = no change.
    # Env kill: RUNNER_SCALED_TRAIL_MODE=off. The fixed-trail replay (8/12pp dead
    # valleys) does NOT falsify this — those widen the trail UNIFORMLY; this only
    # widens it in proportion to how far the runner has already run.
    runner_scaled_trail: bool = False
    runner_trail_base_pp: float = 5.0
    runner_trail_peak_ref_pp: float = 10.0
    runner_trail_k: float = 0.2
    runner_trail_cap_pp: float = 20.0

    # PUMP-DIP lane (2026-07-06 decode scratchpad/_pump_dip_turn): pump-dips
    # (a dip INSIDE a pump, pc_h6>0) bounce at 47.0% vs base-flush 46.9% —
    # statistically IDENTICAL quality — and are ~7x more numerous. The fleet
    # excluded them on DIRECTION alone (pc_h6<=0 / shallow-dip / pump-retrace /
    # green-rip gates). This flag exempts a lane from those four pure-direction
    # blocks ONLY, keeping the full SELECTION funnel (buyer-size>=$34 via the
    # full-thesis buyer half, liq, wash, quote-asymmetry exit-depth, structure)
    # — because the decode showed neither population is positive UNCONDITIONALLY;
    # the edge is selection, not direction. Blow-off exclusion also dropped
    # (bigger pumps bounced BETTER). Default False = no bot changes. The A/B
    # question: does the fleet's selection edge hold on the 7x-larger set?
    pump_dip_exempt: bool = False

    # ADAPTIVE ENTRY levers (2026-07-07 entry-timing fleet + token-conditional
    # decode). adaptive_swing_size: flex position size by the token's swing
    # profile — violent+shallow (dead-cat tail) sizes DOWN, violent+deep keeps
    # most, calm full (core/adaptive_entry.swing_size_multiplier). Does NOT gate
    # -> fires exactly as often as the base. vsnap_reject_min_low_age_secs: reject
    # fast V-snaps whose recent low is younger than this many seconds (0=off);
    # the fleet's reachable held-vs-dead separator (grinds hold, V-snaps die).
    # FAIL-OPEN (unknown low-age never rejects). NEVER paired with
    # hl_confirm_entry (CONFIRMED never fires — the inert-bot trap).
    adaptive_swing_size: bool = False
    vsnap_reject_min_low_age_secs: float = 0.0

    # retrace_micro_avoid (2026-07-09 on-chain retrace-vs-top fleet): when True,
    # BLOCK entries whose on-chain trade flow shows sell-side DISTRIBUTION into the
    # low (heavy + accelerating sells = a top, not a retrace that resumes) — the
    # one CONFIRMED forward-only whale-robust survivor. Pure skip rule (worst case:
    # pass on some continuations). The net-flow-persistence corroborator is
    # SHADOW-only (logged, never blocks) regardless of this flag. Default off.
    retrace_micro_avoid: bool = False

    # HOUSE-MONEY MOONBAG exit shape (2026-07-10 paper A/B). The ladder's TP2 is
    # a full-out door, so +244%-class runners (mogdog 2026-07-10) are structurally
    # uncapturable; wide-trail variants failed the winner-kill bar (33%). When
    # moonbag_fraction > 0, TP2 sells only (remainder-after-TP1 - moonbag_fraction)
    # and KEEPS moonbag_fraction of the ORIGINAL position open as a house-money
    # moonbag: profits already banked at TP1/TP2, floor at ~entry, upside open —
    # winner-kill ~0 by construction. The moonbag closes in full when pnl_pct <=
    # moonbag_floor_pct (default 0.0 = breakeven floor) or, when moonbag_trail_pp
    # is set, when pnl_pct <= peak - moonbag_trail_pp (peak tracked as today).
    # While the moonbag rides, the tight post-TP1 trail is suppressed (the
    # moonbag's own floor/trail replace it); hard_stop and an explicitly
    # configured time_stop still apply as catastrophic backstops. Default 0.0 =
    # every existing bot byte-identical.
    moonbag_fraction: float = 0.0
    moonbag_floor_pct: float = 0.0
    moonbag_trail_pp: Optional[float] = None

    # MIN-HOLD "no-panic" FLOOR (2026-07-12 winner-behavior decode,
    # scratchpad/_sol_winner_behavior.md). The #1 young-lane leak is that we PANIC-CUT
    # winners: 48% of trips exit <2min RED at a 25-47% win rate on shallow noise dips,
    # before the absorption/mean-reversion thesis reaches the 120-300s sweet spot (56%
    # WR, +4.5% median). While a PRE-TP1 position is younger than min_hold_floor_secs,
    # SUPPRESS every soft cutter (in-flight/velocity floor, giveback floor, fast-dump
    # bail, pre-stop bail, ng_faststop, never_runner) AND the -12 hard stop, keeping ONLY
    # a hard-rug price tripwire (pnl <= min_hold_floor_rug_pct, default -25) so a real
    # liquidity pull still exits. It is a FLOOR not a longer target -- the existing upper
    # time-box (slow_bleed/never_runner 45min) resumes the instant the floor expires
    # (600s+ is the WORST bucket, 11.7% catastrophic; do NOT over-hold). TP1/TP2 gains
    # still fire during the window (winner-safe). Same-token union: holding beat cutting
    # on 73% of tokens (+10pp median); bounded replay ex-top-2 token-median -5.8 -> +2.9..
    # +4.5, GREEN 4/4. Env kill MIN_HOLD_FLOOR_MODE=off|shadow|enforce (shadow stamps the
    # would-cut counterfactual without acting). Default 0 secs = off = byte-identical.
    # PAPER A/B first; no live enforce without AxiS + forward-green.
    min_hold_floor_secs: float = 0.0
    min_hold_floor_rug_pct: float = -25.0

    # TRAILING-HEAT-GATED RUNNER LIFT (2026-07-12, scratchpad/_sol_hot_market.md). The
    # market's heat is in the right tail (reach>=30 21% recent vs 9% prior; reach>=50 7.7%
    # vs 0). A fixed +12 TP2 caps exactly the trips that now run further (given a token
    # reaches +12, 55-62% reach +20). When the trailing universe-heat regime is HIGH
    # (core.heat_regime: rolling fraction of the last 25 fleet fills reaching >=+20% is
    # >= 0.20), lift the RUNNER/TP2 target from tp2_pct to tp2_pct_hot (~+18-20). TP1 (+6)
    # and the stop are UNCHANGED -- raising TP1 LOSES, most on hot trips (the early scalp
    # is the reliable money); only the runner tranche rides. Regime-gated, NEVER blanket
    # (4-half OOS: +0.08/+0.37/+0.21/+0.20 per trip). The regime is fixed AT ENTRY (decision
    # -time knowable, past closes only, no leakage). NO size-up (exit lever only, ruin-math).
    # Env kill HEAT_REGIME_MODE=off. Default False = no change.
    regime_runner_lift: bool = False
    tp2_pct_hot: float = 18.0

    # STRENGTH-TRAIL exit (2026-07-12 RH winner-behavior decode,
    # scratchpad/_rh_winner_behavior.md). Replaces the partial TP ladder with an
    # ALL-OUT, single-leg peak-anchored trail that arms from a LOW threshold —
    # the shape the 93 audited RH winners actually run: 55.4% of their trips
    # never peak past +6% (so a fixed +6 TP1 sits ABOVE the median mover), they
    # sell 100% in a single leg (n_sells p50=1) into rising price (74.2%) near
    # the local top (median sell = 97.4% of trip peak). When True the exit engine
    # bypasses TP1/TP2/post-TP1-trail/pre-TP1 cutters and manages the position
    # with exactly two doors: the catastrophic hard_stop (still -15) and this
    # strength trail — once peak_pnl_pct >= strength_trail_arm_pct, sell the FULL
    # remainder when pnl_pct <= peak_pnl_pct - strength_trail_gap_pp. arm at +2%
    # (~breakeven+fees, NOT +6) so the sub-+6 movers our scalp misses are banked;
    # gap 3pp matches the winners' 2.6% median give-back from the peak. A
    # configured time_stop_minutes still applies as a backstop. Default False =
    # every existing bot byte-identical (the whole branch is skipped).
    strength_trail_exit: bool = False
    strength_trail_arm_pct: float = 2.0
    strength_trail_gap_pp: float = 3.0

    # lp_rug_tp1_full (2026-07-09 CLOPY autopsy, AxiS "ship it"): when True and
    # the ENTRY carried the LP-drain rug flag (lp_event_verdict=REMOVE_15MIN AND
    # lp_delta_15m_pct<=-15 — present at every doomed CLOPY -98.6% entry), TP1
    # sells 100% instead of tp1_sell_fraction. Rug INSURANCE with ~-0.3pp carry
    # that pays ~+26pp/position on a CLOPY-class hit. Exit-side only — the
    # entry-veto (56% winner-kill) and size-derate (kills fat-tail winners)
    # variants were adversarially REFUTED. Default off.
    lp_rug_tp1_full: bool = False

    # min_liquidity_usd (2026-07-09 strategy re-opt): per-bot hard entry liquidity
    # floor. The strategy fleet's #2 lever — a $40k floor lifts WR ~+10-12pp AND
    # cuts entry slippage (deeper books = less friction, the one lever that attacks
    # both edges). 0 = off. Fail-OPEN on unknown liq (young cached liq is
    # unreliable; never dark the lane on missing data). Distinct from the anti-rug
    # 25k floor (rug-pocket) and liq_exit_floor (exit-cleanliness).
    min_liquidity_usd: float = 0.0

    # Momentum-continuation mode (2026-06-02, #4.3). When True the bot uses a SEPARATE
    # entry path: it BYPASSES the dip-filter stack + dip triggers (which block 100% of
    # momentum candidates) and enters on the momentum entry_gate (e.g. pc_h1>=20 AND
    # pct_above_vwap_h24<=cap AND 1m_volume_spike>=0.40). The strongest decorrelated lead
    # from the broad sweep (+14pp WR, both regimes). Default False = the normal dip path,
    # unchanged. The trading-window + drawdown-freeze + entry_gate still apply.
    momentum_mode: bool = False

    # Low-mcap probe (2026-06-02). When LOW_MCAP_PROBE env is on, a low_mcap_probe=True bot
    # trades the [floor,$1M) mcap band ONLY (forward data: 500k-1M matches/beats the 1M-5M
    # band we trade, ~2x throughput); production bots SKIP sub-$1M tokens. Default False +
    # env off = no change. Tests lowering the global min_mcap to $500k on realized paths.
    low_mcap_probe: bool = False

    def __post_init__(self) -> None:
        # Normalize entry_gate to a hashable tuple-of-tuples (JSON yields
        # tuple-of-lists; the frozen dataclass's auto __hash__ chokes on lists).
        if self.entry_gate is not None:
            object.__setattr__(
                self, "entry_gate",
                tuple(tuple(c) for c in self.entry_gate),
            )
        # Normalize trigger_state_gates (JSON: list of [trigger, [[f,op,thr],...]])
        # to a hashable tuple-of-(trigger, tuple-of-condition-tuples). Mirrors the
        # entry_gate normalization so the frozen dataclass stays hashable.
        if self.trigger_state_gates is not None:
            object.__setattr__(
                self, "trigger_state_gates",
                tuple(
                    (str(tg), tuple(tuple(c) for c in conds))
                    for tg, conds in self.trigger_state_gates
                ),
            )
        if self.filters_enforced is not None and self.filters_disabled:
            raise ValueError(
                f"bot_id={self.bot_id}: filters_enforced is set, "
                "so filters_disabled must be empty (it is ignored when enforced is set)"
            )
        if self.triggers_allowed is not None and self.triggers_disabled:
            raise ValueError(
                f"bot_id={self.bot_id}: triggers_allowed is set, "
                "so triggers_disabled must be empty"
            )
        if self.tp1_sell_fraction + self.tp2_sell_fraction > 1.0 + 1e-9:
            raise ValueError(
                f"bot_id={self.bot_id}: tp1_sell_fraction "
                f"({self.tp1_sell_fraction}) + tp2_sell_fraction "
                f"({self.tp2_sell_fraction}) must be <= 1.0"
            )
        if self.moonbag_fraction < 0:
            raise ValueError(
                f"bot_id={self.bot_id}: moonbag_fraction "
                f"({self.moonbag_fraction}) must be >= 0"
            )
        if (self.moonbag_fraction > 0
                and self.tp1_sell_fraction + self.moonbag_fraction > 1.0 + 1e-9):
            raise ValueError(
                f"bot_id={self.bot_id}: tp1_sell_fraction "
                f"({self.tp1_sell_fraction}) + moonbag_fraction "
                f"({self.moonbag_fraction}) must be <= 1.0 "
                "(nothing left after TP1 to keep as a moonbag)"
            )


# ---------------------------------------------------------------------------
# JSON serialization helpers
# ---------------------------------------------------------------------------

def _to_json_safe(value):
    """Convert tuples to lists for JSON; recurse into structures."""
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    return value


def _from_json_safe(field_type, value):
    """Coerce JSON-deserialized lists back to tuples for tuple-typed fields.

    ``field_type`` is the string annotation (from __future__ annotations),
    e.g. ``"Optional[tuple[str, ...]]"`` or ``"tuple[str, ...]"``.
    """
    if value is None:
        return None
    type_str = str(field_type)
    if "tuple" in type_str and isinstance(value, list):
        return tuple(value)
    return value


def _add_json_methods(cls):
    def to_json(self, path):
        path = Path(path)
        data = {f.name: _to_json_safe(getattr(self, f.name))
                for f in dataclasses.fields(self)}
        path.write_text(json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def from_json(cls_, path):
        path = Path(path)
        text = path.read_text()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in {path}: {e}") from e
        known = {f.name: f for f in dataclasses.fields(cls_)}
        unknown = set(data.keys()) - set(known.keys())
        if unknown:
            raise ValueError(
                f"Unknown field(s) in {path.name}: {sorted(unknown)}"
            )
        coerced = {
            name: _from_json_safe(known[name].type, val)
            for name, val in data.items()
        }
        try:
            return cls_(**coerced)
        except TypeError as e:
            raise ValueError(
                f"Missing or invalid field in {path.name}: {e}"
            ) from e

    cls.to_json = to_json
    cls.from_json = from_json
    return cls


BotConfig = _add_json_methods(BotConfig)
