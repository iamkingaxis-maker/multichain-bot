# Project Bug Audit — Findings (2026-05-27)

5 parallel review agents (persistence, price-feed, config-enforcement, resource-growth,
metrics). Deduped + ranked. Severity = impact on money/decisions/cost. Already fixed
today (excluded): GIGA/EURC phantoms, position orphaning, open-count over-count, sticky bloat.

## TIER 1 — correctness bugs that corrupt money or bot-promotion decisions

1. **compare_bots.py uses a STALE cutoff** — `CUTOFF="2026-05-23T15:40"` vs canonical
   `MIN_TRADE_TIMESTAMP="2026-05-25T21:25"` (sp4_common). Every pairwise verdict mixes
   ~2 days of pre-reset (wrong-semantics) trades → corrupts the $/trade + WR + significance
   used to pick live bots. FIX: import MIN_TRADE_TIMESTAMP. (compare_bots.py:34) [conf 100]

2. **Realtime stop/TP/trail callbacks bypass the phantom guard** — `axiom_price_feed.py:481-484`
   + `solana_rpc_price_feed.py:326-329` call `position_manager.check_*_realtime()` with a RAW
   price; the new symmetric `guarded_exit_price` is only wired into the MULTI-bot path
   (dip_scanner:801). The single-bot/legacy path (baseline_v1 + scalp/MC) uses the weaker
   ±20% gate → a phantom tick can still fire a stop/TP there. Also `state.peak_price` is set
   from the raw tick (position_manager:2619/2854/3003) → a glitched high poisons trail math.
   FIX: route the realtime checks through guarded_exit_price (shared guard dict). [conf 90]

3. **Persistence gaps in the just-shipped position fix** (fold into it):
   a. `_last_close_time` (reentry cooldown) NOT in to_state_list/load_state_list → lost every
      restart → `reentry_cooldown_secs` is dead post-restart. Invalidates champ_reentry_throttle,
      reentry_30m, reentry_60m. (per_bot_position_manager.py:59) [conf 100, 2 agents]
   b. `state_blob` (slip_pct stashed at buy) NOT persisted → restored positions sell with the
      WRONG slippage fallback → fleet-wide realized-P&L error after every deploy.
      (per_bot_position_manager.py:109-124; dip_scanner sell path) [conf 97]

4. **entry_price<=0 → inf P&L / never-stops** — per_bot_position_manager `tick()`/`close_position`
   compute `price/entry` with no guard; a corrupted/missing entry loads as 0.0 → pnl_pct=inf →
   all TPs fire at once OR ZeroDivision, and the hard stop never fires (inf <= -15 is False).
   FIX: guard entry_price>0 in tick()/load_state_list. (per_bot_position_manager.py:166,189) [conf 83]

## TIER 2 — invalidated experiments / metric skew

5. **btc_macro_h1_block_threshold is DEAD** — gate code is correct but `btc_pc_h1` is hardcoded
   `None` in the FeatureBundle (dip_scanner:13742) though it's fetched into _cycle_sol_features.
   regime_aware_bullish's BTC gate never fires. FIX: wire btc_pc_h1 into FeatureBundle. [conf 100]

6. **Dead sizing multipliers** — macro_up_multiplier / premium_runner_multiplier /
   marginal_multiplier are never read in BotEvaluator._size_for (only alpha_multiplier is).
   Any bot setting them gets nothing. (cap2k flat-sizing still works incidentally because the
   multi-bot path only has alpha/standard tiers and cap2k set alpha_mult=1.0.) FIX: wire or remove. [conf 100]

7. **Win-rate / $/trade counted per-SELL not per-POSITION** — dashboard `_build_bot_rows`
   (web_dashboard.py:3530) + compare_bots count raw sells; partial TP1+TP2 = 2-3 sells per
   position → inflates trade count, deflates $/trade, skews WR. Attribution scripts (sp4) do it
   right (pair_buys_sells); the dashboard/leaderboard don't. [conf 88]

8. **Sell records missing address/pair_address** — _execute_bot_sell (dip_scanner:863-878) omits
   them (buys have them) → sell→buy joins in postmortem/attribution/compare fail on partial sells. [conf 95]

## TIER 3 — resource growth / egress (volume + $25/mo cap)

9. **Unbounded shadow .jsonl appenders, no rotation** (~50-350 MB/day combined):
   signal_event_recorder.py, uptrend_scanner.py (uptrend_shadow), filter_shadow_recorder.py
   (filter_shadow_log), axiom_trending_scanner.py (pre_gate_events), trader.py (stop_recovery_log).
   FIX: size-cap rotation (same pattern as the recorder fix shipped today).

10. **Unbounded in-memory token-key growth** — `_h24_history` (dip_scanner:173) and
    LiquidityFlowTracker `_history` (liquidity_flow.py:66) prune deque CONTENTS by age but never
    delete the empty token KEY → dict + on-disk JSON grow forever with the widened universe.
    Also GT/DexScreener client caches have no eviction. FIX: `del key` when its deque empties.

11. **Egress amplifiers** (drive the bill): Jupiter slippage curve = up to 8-11 API calls per
    qualifying token per 30s cycle, UNCACHED (dip_scanner:2059-2165); assemble_chart_data runs 5
    candle fetches per candidate BEFORE the fast mcap/vol/age filters (dip_scanner:1424). FIX:
    cache Jupiter slip per token ~90s; pre-filter before chart assembly.

## TIER 4 — lower / latent

12. closed_positions.csv never receives the GIGA/EURC scrub corrections → /api/closed-positions
    still shows the phantoms. (web_dashboard.py:2757)
13. reconcile_positions hook would zero REAL open_positions if its sentinel is ever absent
    (disaster-recovery edge) — guard: skip bots that already have a non-empty persisted book.
14. trades_multi.json written via direct write_text (not temp+os.replace) → crash mid-write
    truncates the whole ledger → next boot falls back to [] and overwrites empty.
15. Phantom-parity gaps: filter_sol_macro_down + filter_topping have no live_forward_test combo;
    overnight-trigger combos evaluate datetime.now() at RESOLVE time not snapshot time.
16. PerBotCapital.in_flight can drift slightly negative over many partial sells (float); cosmetic.
