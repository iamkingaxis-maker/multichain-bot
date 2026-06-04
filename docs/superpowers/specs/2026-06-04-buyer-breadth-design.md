# Buyer-Breadth Entry Signal (C + A) — Design

**Date:** 2026-06-04
**Status:** Design approved (user picked C+A). Enforcement of C is gated on the grad-mine n≥100 incremental test.

## Motivation

Two months of work proved entry-instant features are **non-predictive of trade outcome out-of-token on AGED tokens** (~10 hard gates falsified). The 2026-06-03 3-thread hunt + the per-wallet HHI mine surfaced the first entry-side signal to clear the fleet's full discipline (held-out-by-token + token-clustered null + BH): **on FRESH tokens, distributed buying → continuation; whale-dominated buying → bleed.**

### Evidence
- **Grad mine** (n=50 fresh grads = 50 distinct tokens): `buyer_hhi` AUC 0.063, `n_buyers` 0.874, `buyer_top1_share` 0.045 — all survive BH (p≤0.001), out-of-token by construction.
- **Fleet fresh trades** (n=279, ~12 tokens): same direction; `large_buyer_volume_pct` Cohen d=**−0.80** (whale-dominated buying → loss). Bucket: lbv=0 → 64% WR; 0–0.5 → 78% WR; **≥0.5 → 9% WR / −14.6% mean**. Held-out-by-token, buyer structure adds **+0.22 AUC over order-flow**.
- **Washes out on aged >7d** (all |d|≤0.19) → the signal is **fresh-cohort-specific** (consistent with the entry-non-predictive-on-aged wall).

### Open questions (gate enforcement on these)
1. **Cross-token generalization** — both samples are token-limited (the fleet "kill-ratio 0.10" gate was a single-token, 10m, mirage). The grad mine at **n≥100 (= 100 distinct tokens)** is the decisive cross-token incremental-over-net-flow test. Banking now.
2. **Incrementality over net-flow** — `large_buyer_volume_pct` is moderately collinear with the known order-flow edge (~0.4–0.5). Favors using it as an **AND-condition alongside** `net_flow_60s`, not as a standalone gate.

## Components

### A — Shadow flag on fleet entries (ship NOW, zero risk)
A MEASURE-ONLY `buyer_concentration_shadow` verdict stamped into `entry_meta` on every buy (verdict-only, no blocking), mirroring the existing `filter_no_demand_entry` / `watchlist_bypass_downtrend_shadow` pattern in `dip_scanner.py`. Accrues forward parity data so we can confirm the signal holds **live, cross-token, post-crash** before any enforcement spreads.

- **Verdict logic** (`core/buyer_concentration.py`, pure): `BLOCK` if `large_buyer_volume_pct >= 0.5` (whale-dominated buying), `PASS` if below, `NEUTRAL` if the feature is absent (fail-open).
- Stamped + counted + logged; **never appends to `_filters_block`** (no live effect).
- **Phantom parity:** add a `buyer_concentration_block` predicate to `scripts/live_forward_test.py` (required by the phantom-parity rule for any new shadow).

### C — 2nd entry-gate condition on `momentum_grad_probe` (READY, flip on n≥100 confirm)
Add a buyer-breadth condition to the probe's `entry_gate` so it enters only when buying is **broad AND showing order-flow momentum**:

```
entry_gate: [
  ["net_flow_60s_imbalance", ">=", 0.3],
  ["1m_volume_spike",        ">=", 0.4],
  ["large_buyer_volume_pct", "<=", 0.5]   // NEW: reject whale-dominated buying
]
```

**Operator constraint (verified in `core/bot_evaluator.py:282-297`):** the gate evaluator implements ONLY `>=` and `<=` (it silently ignores `<`/`>`). The condition MUST be `<=` (rejects entry when `large_buyer_volume_pct > 0.5`). Fail-open per condition when the feature is missing.

**Scope:** `momentum_grad_probe` only — it is already fresh-scoped (`age_h_max=3.0`), experimental ($2k paper), and purpose-built to test pre-peak fresh-graduation entry. This is exactly the cohort where the signal lives.

## Scope guardrails
- **Fresh tokens only.** Never apply this as a fleet-wide gate on aged tokens (signal is absent there).
- C touches only the probe config. A is shadow-only fleet-wide (measurement, no behavior change).
- No change to any production/champion bot's live behavior in this work.

## Validation gates before C is enforced (deployed live)
1. Grad mine **n≥100**: `large_buyer_volume_pct` / `buyer_hhi` incremental-over-net-flow held-out AUC lift, winner-kill ≪1 across ≥8–10 distinct tokens, token-clustered null + BH.
2. A's forward-shadow shows the same direction on live fleet fresh entries post-crash.

If (1) fails cross-token, C is NOT flipped on; A remains as a measurement shadow and we revisit.

## Risks
- **Collinearity with net-flow** → C as an AND-condition is safe even if marginal; a standalone gate would be the questionable use (not what we're doing).
- **Concentration** → only enforce after cross-token (n≥100) confirmation.
- **Crash-regime** → current evidence is crash-period; A's forward shadow gathers post-crash data before enforcement spreads.
- **Feature availability** → C relies on `large_buyer_volume_pct` being present in `raw_meta` at gate-eval time; fail-open means a missing value never blocks (degrades to current behavior). A task verifies population rate.

## Files
- **Create:** `core/buyer_concentration.py` (pure verdict helper), `tests/test_buyer_concentration.py`.
- **Modify:** `feeds/dip_scanner.py` (A: stamp shadow verdict), `scripts/live_forward_test.py` (A: phantom parity), `config/bots/momentum_grad_probe.json` (C: entry_gate condition), `tests/test_bot_evaluator.py` (C: gate test).
