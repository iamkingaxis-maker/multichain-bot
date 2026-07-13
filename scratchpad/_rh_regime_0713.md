# RH regime gate + OOS demand signal (2026-07-13)

AxiS goals: (1) give RH a LOOSE regime gate — crash-only, never over-gating young/small
tokens; (2) test whether an entry-time demand signal (the Solana "winners net-inflow,
losers slow-bleed" finding) separates RH winners from bleeders **and survives OOS** — with
maximal skepticism, because the Solana version OVERFIT (great in-sample, negative held-out lift).

Data: `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (local, not re-pulled). Join = the
`load_rh_trips` schema, extended to attach the OPENING buy's entry features. Scripts:
`scratchpad/_rh_regime_analysis.py`, `scratchpad/_rh_demand_stress.py`. Standing SCRUB applied
(drop ret>0 & hold<10s → 9 trips dropped). **447 closed trips (223 regime-stamped), 07-10..07-12.**
Grading = ex-top-2 token-median (tokmed_ex2); mean shown for context only.

## What RH actually captures at entry (per buy ledger row)
- **Per-token:** `dip_pct` (price structure), `liq` (depth USD), `micro.flow_confirm`
  (per-token sustained net-inflow demand turn — see below), `micro.avoid_block` (always False
  in tape), `age_h` / `band`.
- **Market-wide (feed regime stamp, 30-min window):** `buy_share_30m`, `netflow_30m_usd`,
  `n_swaps_30m`, `distinct_pools_30m`, `disc` (bot/human era), `dial` (rolling expectancy).
- The `demand_turn` entry gate (buys≥$50 & net>0) fires binary — every admitted trade passed
  it, so it isn't a gradable continuous feature. `flow_confirm` is the recorded continuous-ish
  demand corroborator (currently **shadow-stamped, not enforced** — 105 True / 357 False).

---

## PART 1 — LOOSE crash-only regime gate

### Finding: the tape contains NO crash window, so a crash gate cannot be validated on outcomes
Market-wide 30-min `buy_share` never fell below **0.76** (median 0.89, p10 0.806); `netflow_30m`
was **positive in every one of the 223 stamped trips** (min +$6.1k). There was no market-wide
sell cascade in 07-10..12, so I **cannot** show "blocked periods were actually worse" — there is
nothing bad to block. Any gate tuned to fire in this tape would be fitting noise. The honest move
is a gate whose floor sits **below the observed range** (fires only in a genuine washout) and
that **forward-grades in shadow** until a real crash lands.

### Data-backed design rule: YOUNG tokens are regime-flat/inverted → EXEMPT
Per-band split of outcome vs market buy_share (median split within band):

| band | LOW buy_share rawMean | HIGH buy_share rawMean | spread (HIGH−LOW) |
|------|----------------------:|-----------------------:|------------------:|
| **young** (n=85) | **+10.96** | +3.61 | **−7.35** (inverted) |
| mid (n=97) | +1.05 | +1.65 | +0.60 |
| aged (n=41) | +1.16 | −0.16 | −1.32 |

In the young band, LOW market buy-share trips **outperformed** — young/small tokens do not
inherit market-wide demand weakness, exactly AxiS's thesis. So the gate never touches young pools.
Mid/aged show no clean, sizable separation either (spreads ≤1.3, both directions) — consistent
with "loose, crash-only."

### The gate (shipped as SHADOW; `core/rh_regime.crash_regime_block` + `regime_stamp`)
Blocks ONLY when **both** legs fire on a **non-young** pool:
- `buy_share_30m < 0.45` (a real sell cascade; normal range 0.76–1.0), **AND**
- `netflow_30m_usd < 0` (market-wide net outflow).

Both-legs-required = deliberately loose (one weak reading never blocks), mirroring the Solana
SOL-macro crash-only gate. **Young pools, and pools of unknown age, fail OPEN** (same precedent as
`aged_hour_gate_ok`). In-sample block rate: **0/223 (0.0%)** — correct: no evidence any observed
window was bad. 85 young trips explicitly `young_exempt`.

- **Env:** `RH_CRASH_GATE` = `off` | `shadow` (default) | `enforce`. In shadow it only writes
  `crash_gate`/`crash_block`/`crash_reason` onto every entry's regime stamp; **no code path halts
  a buy on it** (even "enforce" is not wired into the entry path — promotion is a separate,
  approved step). This is the "stamps without blocking, forward-grades" pattern.
- **Promotion bar (pre-registered):** once the tape captures ≥1 genuine cascade window and the
  shadow stamps show blocked-window trips are materially worse (mid/aged only) at n≥20 blocked
  trips across ≥2 tokens, bring the enforce path to AxiS. Not before.

---

## PART 2 — Does a demand/depth signal separate winners from bleeders, and survive OOS?

Tested every recorded entry feature two ways: **odd/even trip parity** (the required minimum)
AND a second **chronological day-half** axis — because the Solana overfit came precisely from a
split (even days) that a few dominant tokens leaked across. Odd/even splits the SAME token into
both halves, so a single great token fakes survival; the chrono split is token-separating and is
the honest OOS test here.

### Odd/even (trip parity) — the naive test
| feature (favor) | even LIFT_mean / tokEx2 | odd LIFT_mean / tokEx2 | odd/even verdict |
|---|---|---|---|
| `flow_confirm`=True (per-token demand turn) | **+6.49 / +10.26** | **+5.37 / +9.56** | looks like SURVIVES |
| `liq` HIGH | +7.55 / +1.72 | +5.74 / +1.81 | SURVIVES |
| `buy_share_30m` HIGH | +2.97 / −0.76 | +1.67 / +2.56 | mean-only, tokmed flips |
| `netflow_30m_usd` HIGH | +1.13 / +3.75 | −0.89 / −8.04 | **FAILS** (sign flips) |
| `n_swaps_30m` HIGH | −0.88 / −2.82 | −1.09 / −10.56 | negative both (activity ≠ good) |
| `dip_pct` (either direction) | ~0 | flips | **FAILS** |

### The overfit trap, caught: `flow_confirm` is a ONE-TOKEN artifact
`flow_confirm=True` is **98 trips across just 11 tokens — and 49 of those 98 (half) are a single
token** (`0xfb0f…3910`, mean +9.6). In the young band, `flow_confirm=True` is **n=49 across 1
token**. Odd/even splits that one token ~24/~25 into each half, so BOTH halves inherit its win →
fake "survival." This is the identical mechanism that fooled the Solana even-day test.

The **token-separating chrono day-half** OOS strips it out:

| feature | W1 (early) LIFT tokEx2 | W2 (late) LIFT tokEx2 | honest verdict |
|---|---:|---:|---|
| `flow_confirm`=True | +2.95 (9 tok) | **+0.40** (2 tok) | **NULL** — collapses; W2 is 2 tokens |
| `liq` HIGH | **−0.18** (robust metric) | +2.74 | **FAILS robust metric** (flips on tokmed) |

- **`flow_confirm` does NOT survive** the token-honest split: its robust tokmed lift decays to
  +0.4 in the later half and is carried by a handful of tokens. Its big odd/even numbers are
  concentration, not edge — the exact Solana mistake.
- **`liq`** is robust on the top-heavy MEAN (high-liq +4–6, low-liq negative in all 4 splits) but
  **flips on the mandated robust tokmed metric** across chrono halves (−0.18 W1). It also merely
  re-confirms the EXISTING `min_liq` gate (30k), not a new demand signal.
- Market-wide `netflow`/`buy_share`: `netflow` outright flips sign; `buy_share`'s tokmed flips.

### Verdict: NULL. No demand/depth feature survives token-honest OOS. Nothing shipped as a gate.
Per the mandate, I did **not** ship an overfit demand gate. `flow_confirm` looked like the RH
analog of the Solana net-inflow signal and looked great on trip-parity — and that is exactly why
it's dangerous. On the token-separating split it is null. The one feature robust on MEAN (`liq`)
is already gated and fails the robust metric. **The Solana overfit would have repeated here had we
trusted odd/even alone.**

`flow_confirm` is already shadow-stamped in `micro`, so it continues to forward-grade for free —
recommend re-checking it at n≥30 **distinct tokens** (not trips) before any promotion; do not
build a gate on it now.

---

## What shipped (working tree only — NOT enforced/deployed/pushed)
- `core/rh_regime.py`: `crash_regime_block()` (pure, loose, young-exempt, fail-open) + `crash_gate_mode()`
  (`RH_CRASH_GATE`, default `shadow`); `regime_stamp()` now stamps `crash_gate`/`crash_block`/
  `crash_reason` on every entry. Never halts a buy.
- `tests/test_rh_regime.py`: `TestCrashRegimeGate` (8 cases: normal-tape-never-blocks, young-exempt,
  true-cascade-blocks, one-weak-leg-open, floor-below-observed, fail-open, off-mode, stamp shape).
  **Full suite 28 passed.** Lane imports clean.
- Analysis scripts: `scratchpad/_rh_regime_analysis.py`, `scratchpad/_rh_demand_stress.py`.

## Honesty ledger
- No crash in the 07-10..12 tape → the crash gate is un-validated on outcomes; shipped shadow-only,
  0% in-sample block by design. It is a forward-grading instrument, not a proven edge.
- Latency: zero added budget — both are pure functions over state `regime_stamp` already holds; no
  network, no extra RPC. (≤2s mandate untouched.)
- n is low (223 regime-stamped trips, 20 tokens; band cells 41–97). Every conclusion is framed on
  the robust tokmed_ex2 metric and cross-checked on two OOS axes. The demand-signal NULL is the
  high-confidence result (it's a *rejection*, and the rejection is what protects us).
- `flow_confirm=True` young-band = 1 token: tokmed is undefined there; reported as such, not smoothed.
