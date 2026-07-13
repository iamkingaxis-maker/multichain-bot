# SOL Young/Live-Lane Green Cohort Sweep — 2026-07-12

**Goal:** find MORE profitable entry options than deep(pc_h1<=-45)+liq>=30k (green +4.6 ex2, ~19% vol), ideally **higher volume**.

**Data:** `scratchpad/sol_selection/_trips.json` — 955 trips (07-02..07-12), entry_meta axes, post-scrub, top-2 tokens (Hoppy, mogdog) dropped from every median.

**Metric (banked, no fat-tail promotions):** ex-top-2 **token-median** (drop each cohort's 2 best tokens). GREEN = ex2 median > 0 AND >=50% tokens green. OOS = green in >=3/4 halves (CH1/CH2 chrono x ODD/EVEN parity). n>=15 distinct tokens or UNDERPOWERED. p90 reported (winner-preserving — must not clip the tail).

**Baseline (all fills):** ex2 token-median **-5.8**, 35.6% tokens green. Cohorts below are genuine selection, not luck.

---

## 2-axis sweep — GREEN cells ranked by VOLUME-SHARE (624 cells evaluated, 22 green)

| cell | vol% | ex2-med | grn% | ntok | p90 | 4-half |
|---|---:|---:|---:|---:|---:|:--:|
| **deep(h1<=-45) & liq>=30k** (REF) | **19.1%** | **+4.6** | 59% | 34 | +32.5 | 3/4 |
| deep(h1<=-45) & evol>=1M | 16.0% | +1.7 | 52% | 33 | +34.0 | 2/4 |
| deep(h1<=-45) & nf60>=150 | 14.2% | +7.0 | 55% | 22 | +26.4 | 3/4 |
| liq>=35k & ubuy>=50 | 13.9% | +2.1 | 57% | 35 | +30.6 | 3/4 |
| liq>=45k & ubuy>=45 | 13.6% | +0.8 | 53% | 30 | +31.2 | 2/4 |
| deep(h1<=-45) & liq>=35k | 13.4% | +6.0 | 62% | 24 | +33.4 | **4/4** |
| liq>=45k & nf15>=150 | 12.9% | +2.0 | 54% | 28 | +31.2 | 2/4 |
| liq>=45k & bsh1>=1.35 | 12.9% | +1.6 | 54% | 24 | +29.8 | 3/4 |
| liq>=45k & nf60>=150 | 12.6% | +1.9 | 52% | 27 | +31.2 | 2/4 |
| **liq>=45k & bsh1>=1.6** | 10.2% | +2.1 | 59% | 17 | +30.6 | **4/4** |
| deep(h1<=-45) & evol>=1.5M | 10.1% | +5.3 | 55% | 22 | +38.6 | **4/4** |
| deep(h1<=-45) & mbuy>=60 | 9.8% | +0.1 | 50% | 16 | +39.3 | 2/4 |
| ubuy>=50 & mtf<=-1 | 9.2% | +2.9 | 57% | 21 | +40.3 | 2/4 |
| mid(-45..-30) & mbuy<=28 | 9.0% | +8.1 | 53% | 15 | +40.3 | 3/4 |
| vdeep(h1<=-55) & liq>=30k | 8.9% | +8.2 | 59% | 17 | +38.7 | 2/4 |
| mtf<=-1 & bp60<0.52 | 8.5% | +6.9 | 53% | 17 | +40.3 | 3/4 |
| liq>=45k & mtf<=-1 | 7.9% | +2.1 | 58% | 19 | +33.4 | 3/4 |
| vdeep(h1<=-55) & liq>=35k | 6.4% | +8.2 | 53% | 15 | +45.6 | 2/4 |

(remaining green cells <8% vol omitted; full list in `scratchpad/sol_green_sweep/_green_cells.json`)

**Key structural fact:** deep+liq is *already* the single highest-volume green cell (19.1%). No single 2-axis cell keeps strictly more volume AND stays green. So the lever for "more volume" is **ORTHOGONAL green cells whose UNION with deep+liq expands total coverage while the union stays green.**

---

## The real answer — UNION expansion (measure what adding a cohort does)

For each candidate: incremental slice = `candidate \ base` (the NEW fills it adds); union = `base OR candidate` (total coverage).

| construction | vol% | ex2-med | grn% | ntok | p90 | 4-half |
|---|---:|---:|---:|---:|---:|:--:|
| deep+liq>=30k (BASE) | 19.1% | +4.6 | 59% | 34 | +32.5 | 3/4 |
| **BASE ∪ (liq>=45k & bs_h1>=1.6)** | **28.1%** | **+4.9** | 61% | 41 | +31.2 | **4/4** |
| BASE ∪ (liq>=35k & ubuy>=50) | 30.7% | +2.5 | 55% | 58 | +31.2 | **4/4** |
| BASE ∪ (liq>=45k & bsh1>=1.35) | 30.1% | +3.6 | 61% | 46 | +31.2 | 3/4 |
| BASE ∪ (ubuy>=50 & mtf<=-1) | 25.4% | +3.6 | 57% | 46 | +33.4 | 3/4 |
| BASE ∪ (mtf<=-1 & bp60<0.52) | 25.0% | +3.9 | 56% | 41 | +34.0 | **4/4** |

**Incremental slices (the net-new volume, standalone):**
- `liq>=45k & bs_h1>=1.6 \ base` → **+2.1 ex2, 56% grn, 4/4 halves, 9.0% net-new vol** — genuinely GREEN orthogonal volume.
- `liq>=35k & ubuy>=50 \ base` → -1.4 ex2, 48% grn, 1/4 — the new fills are marginally RED; the union only stays green because base carries it.
- `mtf<=-1 & bp60<0.52 \ base` → -5.4, 0/4 — overlaps base's edge; nothing new.

---

## Verdict — the top 2 higher-volume green cohorts

### #1 (SHIP as shadow) — cohort A: `liq>=45k AND bs_h1>=1.6`
- **UNION with base = 28.1% volume (+9pp over base's 19.1%) at +4.9 ex2-med — EDGE-PRESERVING** (base's +4.6 does not degrade; it slightly improves).
- Lifts OOS from 3/4 → **4/4 halves**. p90 32.5→31.2 (no winner-clip).
- Its incremental slice (net-new 9.0% of fills) is itself green +2.1 / 4-of-4 → this is real orthogonal edge, not dilution.
- **Caveat:** the incremental slice's CH2/EVEN halves are thin (2, 1 tokens) so those +33/+54 medians are fat-tail-driven; the **union** halves are well-powered (32/9/31/12 tok) and robust. Note bs_h1 is a 1h *structural* buy-skew, not the *momentary* demand that inverts (prior guardrail) — and it only holds gated by liq>=45k.

### #2 (SHIP as shadow) — cohort B: `liq>=35k AND unique_buyers_n>=50`
- **UNION with base = 30.7% volume (+11.6pp, the MOST volume) at +2.5 ex2-med**, 4/4 halves, n=58 tokens (best-powered), p90 31.2.
- **Trade-off:** buys the most volume but DILUTES edge (+4.6 → +2.5); its net-new fills are breakeven-to-red standalone. Use only if throughput matters more than per-fill edge.

**deep+liq remains the highest-edge, but it is NOT the only green cell** — cohort A adds ~9% net-new green volume with no edge loss (the clean win), and cohort B adds max volume at a documented edge haircut.

---

## Shipped (working tree, NO commits, NO live enforce)
- `core/bot_evaluator.py` — pure `green_cohort_membership(pc_h1, liq, bs_h1, unique_buyers_n)` classifier (base / liq_bsh1 / liq_ubuy / ''), env-tunable thresholds, never raises.
- `feeds/dip_scanner.py` — GREEN-COHORT positive selector stamp after the GREEN_DAY gate. `GREEN_COHORT_MODE=shadow(default)|off`. Measure-forward: records `green_cohort` verdict (PASS=in-cohort / BLOCK=out) once per token via filter_shadow_recorder, fail-open. **Enforce spec written-but-OFF** (the `return` that would restrict entries to green-cohort members is commented out; guarded on an undocumented `enforce` mode).
- `tests/test_green_cohort_membership.py` — 8 pure tests (boundaries, overlap→highest-edge, missing/NaN/bool→unclassified, env override). All pass; full evaluator suite 107 passed.

## Enforce bar (before flipping GREEN_COHORT_MODE, needs AxiS)
Forward realized on the UNION (base ∪ cohort A): n>=15 distinct tokens, ex2-med>0, green in 3/4 halves, p90 not below base's — validated on untuned forward fills, not this in-sample set.
