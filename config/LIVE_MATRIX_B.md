# MATRIX B — THE live configuration (2026-07-01, 4-agent diagnosis)

**Rule: NEVER flip `PAPER_MODE=false` under the paper/learning config (Matrix A).
This file is the checklist. Every line below must be set/verified FIRST.**

The running Railway env is Matrix A (paper data collection): loose SOL gate,
cohorts in shadow, regime gate in shadow, wide universe. That configuration
exists to LEARN. Live capital must only ever trade Matrix B.

## Matrix B env (set each before go-live)

| var | live value | why (measured) |
|---|---|---|
| SOL_MACRO_GATE_MODE | strict | loose-admits were -3.86pp / 17.9% win — ~all of the 06-30/07-01 bleed |
| FULL_THESIS_COHORT_MODE | enforce | IN +3.45/+2.47med/42.5% (n=523) vs OUT -1.38/-4.1/30.0 — re-verify SCRUBBED first |
| OVERSOLD_HELD_MODE | enforce | best slice but a LOSS-REDUCER ex-spike (-1.09 vs -3.01) — layer, don't lean |
| REGIME_BUY_GATE_MODE | enforce | crash protection; 06-30 proved cohorts alone don't save red-SOL days |
| GREEN_DAY_MODE | enforce (after shadow bar) | blocked cohort -$1093 / 25.9% win / negative all 4 firing days |
| BUY_GATE_SOL_H24_OFF | unset (default -1) | the -4 relax admitted n=5 mean -5.67 |
| FILTERS_RELAX_LIST | unset/none | admitted 2 trades, both losers |
| PAPER_FIDELITY_MODE / BUY_REPRICE_MODE / EXIT_REPRICE_MODE | enforce (permanent) | the honest book |
| GAP_THROUGH_HAIRCUT_PCT | 5 | default; the quiet cut to 1 was unjustified |
| EXIT_SLIP_LIQ_MODE | enforce at flip | sellability modeling on exits |
| DIP_POSITION_USD | 5 (25 after slip probe) | ruin math; $25 kills the 0.7pp fixed-fee drag |
| PROBE_AGG_DAILY_KILL_USD | 10 | 2 losers halts the day on a small bankroll |
| WORKING_CAPITAL_FLOOR_USD | ACTUAL starting capital | currently 500 vs real ~10-25 — sweep misbehaves if wrong |

## Roster + invariants
1. Live roster = `badday_fill_probe_live` ONLY (it is enabled + live_probe=true RIGHT NOW —
   the key is in the env and `PAPER_MODE=true` is the single barrier).
   Confirm no other bot carries live_probe.
2. `STRATEGY_ALLOWLIST=dip_buy` (fail-closed).
3. `python -m pytest tests/test_pre_live_invariants.py` -> 13/13 before flip.
4. Restore live-fidelity machinery: ONCHAIN_WS_MODE=on, FAST_WATCH_INTERVAL_SECS=2.

## The go-live BAR (from the honest scoreboard)
Live resumes ONLY when `scripts/honest_book.py` (SCRUBBED, per-token) shows the
Matrix-B cohort at **mean >= +2pp over >= 300 trades / >= 50 distinct tokens /
>= 5 days** at restored volume. As of 2026-07-01 it does NOT (pooled scrubbed
-2.06 mean / 32% win). First live act = n>=30 fill probe at $5 purely to measure
real slippage vs the PROBE_ULTRA 250/600bps guesses; set PAPER_LIVE_SLIP_PCT to
the measured value; re-run the ruin sim on scrubbed cohort numbers before $25.

**Expected live economics: live = scrubbed paper − ~1.0–1.5pp/trade at $5
(−~0.5pp at $25). Parity is already real; make the scrubbed paper number worth
matching first.**
