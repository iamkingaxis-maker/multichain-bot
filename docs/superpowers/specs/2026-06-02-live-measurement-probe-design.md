# Live Measurement Probe — Design Spec (2026-06-02)

**NORTH STAR: this is a MEASUREMENT instrument, not a production deploy. Its job is to
measure whether `champion_premium_tightexit`'s paper edge survives REAL Solana execution —
nothing more. Success = trustworthy live-fill data, NOT P&L.**

## Why (the gate this answers)

The 7-round sweep (2026-06-02) established the candidate's paper edge is +$0.474/tr (n=30,
WR ~57%) but the **fidelity gate FAILED** it: break-even is only ~0.9pp/leg of added
execution cost, *inside* the documented Solana memecoin realized-slippage (3–10%) + MEV
(1–8%) range; the paper fill model assumes mid-tick fills, the dip-low on entry, and ~0%
impact — all optimistic. We literally cannot know if the edge is real until we measure
**actual per-leg fill-vs-mid slippage on live fills.** Paper mining is exhausted; the only
remaining unknown is live execution.

This is also the cleanest way to resolve the two things paper can't:
1. **Runner capture under live fills** — the strategy is positive-skew by design (the trail
   catches the rare runner that pays for the many small trades). Paper can't tell us whether
   runners are *capturable live* (you chase a moving price; MEV targets the way up). Concentration
   in a few winners is the SIGNATURE of the design working — the open question is live capture +
   whether the runner rate × payoff beats the cumulative per-trade live drag.
2. **The real size→EV curve** — paper says sizing up helps (fixed $0.10/tx fee amortizes:
   3.4% drag @ $20 → 0.9% @ $200), but only 11/30 trades had a real impact curve; the rest
   assume an optimistic flat 0.1% impact. Real impact + MEV scale with order size on thin
   pools, so the paper "$200 optimal" is untrustworthy. Size must be measured live.

## What it is

A **minimal, MEV-protected, tiny-size LIVE run of `champion_premium_tightexit` as pure data
collection.** Identical entry/exit logic to the paper candidate (so the comparison is clean),
but: real swaps, MEV-protected routing, smallest viable size, and **heavy fill instrumentation**.
It is NOT scaled capital and NOT a "go live" — it is an experiment whose output is a per-leg
slippage dataset and a verdict on whether to proceed.

## Hard preconditions (ALL required before it runs)

- **Explicit user approval** for `PAPER_MODE=false` on this probe (per feedback_live_pre_flight_required).
- **`python tests/test_pre_live_invariants.py` passes** (the live pre-flight invariant suite).
- **MEV-protected routing CONFIRMED** — Jupiter Ultra / private mempool / equivalent. MANDATORY,
  not optional; unprotected routing invalidates the measurement (sandwiches dominate the signal).
- **Capital cap**: a tiny dedicated allocation (e.g. $200–500 total), isolated; the daily-loss
  halt (Phase-1 risk-floor) ENFORCED; profit-sweep wallet configured. This can lose its whole
  allocation and it must not matter — it's tuition for the data.
- **Railway cost** stays within the $25/mo cap (no new continuous-loop egress beyond the bot).

## Instrumentation (the actual deliverable)

On every live leg (BUY, TP1, TP2, trail, stop), stamp on the trade record:
- `live_mid_price` (quote mid at decision) vs `live_fill_price` (actual executed) → **per-leg
  realized slippage % = (fill − mid)/mid**, signed (pay-up on buy, receive-less on sell).
- `live_route` (Ultra/other), `live_priority_fee_lamports`, `live_tx_sig`, fill latency
  (decision→confirmation ms), and partial-fill fraction.
- `live_entry_vs_local_low` — how far above the dip-low the entry actually filled (paper
  assumes the low; this measures the real gap).
- Reuse the existing shadow stamps (exit_guard_*, tp1_knee_*, timestop45_*, scalein_*,
  phantom_pnl_pct_1leg5_s15, phantom_pnl_pct_trail25/30) so paper-vs-live is directly comparable.

## SIZE SWEEP (added 2026-06-02 per user)

Rotate position size across the probe to measure the REAL size→EV curve (fee amortization vs
live impact/MEV — the thing paper can't see):
- Cycle sizes **$20 / $50 / $100** per entry (round-robin or randomized by entry index so each
  size sees a comparable token mix; never bias a size toward a regime).
- Per size, accumulate: mean realized per-leg slippage %, net eqw/tr, WR, and runner-capture.
- Output the **live** size→EV curve to compare against the paper curve (paper: $20=1.88% /
  $50=3.72% / $100=4.25%). Expectation to test: fee-amortization benefit is real but capped
  earlier than paper says because live impact/MEV rises with size on thin pools.
- HARD LIMIT: do not exceed $100/position in the probe (beyond pool-depth-safe for the
  measurement; larger sizing is a separate decision gated on this data).

## Success criteria (the decision gate — measure, then decide)

Proceed toward production ONLY if, after **n ≥ 50 live closed trades**:
1. **Median realized per-leg slippage < 0.6%** (the break-even headroom), MEV-protected.
2. **Net live eqw/tr > 0** with the fat-tail concentration acknowledged — i.e. the runner
   rate × payoff observed live covers the cumulative live per-trade drag.
3. **Held-out-recent live window positive** (not carried solely by one early runner).
4. The size sweep shows a **non-negative** real size→EV slope at the chosen size.

If any fail → do NOT scale; either iterate the routing/size or shelve the candidate. A clean
"live slippage is 2%+, edge is negative" is a valuable, money-saving answer.

## Files (when built — separate, approval-gated)

- A probe bot config (clone of champion_premium_tightexit) with `paper_mode=false`, the tiny
  capital cap, the daily-loss halt enforced, and the size-sweep field.
- `core/` live-fill instrumentation: capture mid+fill+route+latency per leg → stamp on the
  trade record (mirror the existing shadow-stamp pattern).
- MEV-protected routing in the live swap path (trader.py) — Jupiter Ultra / private mempool.
- `tests/test_pre_live_invariants.py` must pass; add probe-specific invariants (size cap,
  daily-loss halt, MEV-route required).
- An analysis script: live per-leg slippage distribution + live-vs-paper size→EV curve +
  the success-criteria check.

## Honest caveats

- This spends real money to buy information. The allocation is tuition; expect to possibly
  lose it. The ONLY thing that matters is the slippage/runner/size dataset.
- n=50 live fills at ~25/day ≈ 2 days of probe (if entry rate holds), but runner capture
  needs enough runners to observe — may need longer to see the positive-skew tail.
- MEV protection is the make/break variable. Unprotected, the measurement is worthless.
- Nothing here ships to production automatically. The probe produces data + a verdict; the
  production decision is a SEPARATE, explicit step.
