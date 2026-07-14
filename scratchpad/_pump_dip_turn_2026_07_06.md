# Pump-Dip vs Base-Flush — does a confirmed turn make pumped-token dips tradeable? (2026-07-06)

**Question (operator):** the fleet excludes tokens that have PUMPED (pc_h6<=0 cohort, pump-retrace block, green-day-rip block). But a *dip inside a healthy pump* may be tradeable, different from a blow-off top that keeps bleeding. We built HL-confirm ("the turn is the edge, not the prior direction"). Does a confirmed higher-low + demand-turn separate bouncers from bleeders on PUMPED tokens the way it does for base-flushes? If yes, excluding by DIRECTION leaves money on the table and we should gate on the TURN.

**ANALYSIS ONLY. No config/code changes.**

## Data
- Cached 1m OHLC (`bars_ext/`, 94 pairs, 2026-07-05 12:00Z → 07-06 16:00Z, io.dexscreener + GT seam-merged) from the trough-validation & pond-cascade studies. Rip-heavy window → mostly pumped tokens (ideal sample). 92 pairs usable (≥80 bars). Names from `top40.json`; volume_usd per bar (no buy/sell split — absorption is a total-volume proxy, caveat).
- Scripts (session scratchpad): `pumpdip.py` (method A, discarded — see below), `pumpdip2.py` (final, causal), inline sensitivity. Results `_pumpdip2_results.json`.

## Method (and a discarded first pass that matters)
- **Method A (±2-bar local-min + forward HL-confirm) was DISCARDED.** It reproduced exactly the degeneracy the trough-validation flagged: a local-min entry *guarantees* a higher low next bar, so HL-confirm fired at **99.9%** of dips (PUMP-DIP NO-CONFIRM n=**1**). That inflates WR to ~75% by construction and leaves no bleeder control group. Unusable for the confirm-vs-noconfirm test.
- **Method B (final, causal, bias-clean):** walk each pair; a **dip trigger** = first bar whose close crosses ≤ trailing-60min-high × (1−15%) (fully real-time, no lookahead; re-arm when close recovers within 5% of trailing high). Classify by trailing-90min **rise into that prior high**: PUMP-DIP ≥+50%, BASE-FLUSH <+20%, MILD in between. Observe the next **5 bars** for the TURN: HL-confirm = a bar with low>prev-low AND close ≥ reaction-low×1.005 → **CONFIRMED**, entry = confirm bar +1; else **NO-CONFIRM** (kept bleeding, no turn) → entry = trigger+6. Forward outcome from entry over 30 min: +6% (TP1) before −7% (floor) = WIN; blended return uses +6/−7 geometry, NEITHER clamped to terminal close. This has **no ±15-bar lookahead**, so +6/−7 is not satisfied by construction.

## Episode counts
| class | episodes | pairs | CONFIRMED | NO-CONFIRM |
|---|---|---|---|---|
| PUMP-DIP (rise ≥+50%) | **502** | 88 | 479 | 23 |
| BASE-FLUSH (rise <+20%) | **68** | 40 | 64 | 4 |
| MILD (+20–50%) | 235 | 69 | 215 | 20 |

**Pump-dips outnumber base-flushes ~7:1** in this window. NO-CONFIRM is thin everywhere (n=23/4) — at 1m/60s resolution almost every dip "confirms" within 5 min, so the bleeder control is under-powered (a structural limit of bar data; only a tick-level live shadow can fully settle confirm-vs-noconfirm).

## 3-way table (the headline)
| cohort | n | pairs | WR(all) | WR(resolved) | blended +6/−7 |
|---|---|---|---|---|---|
| **PUMP-DIP CONFIRMED** | 479 | 86 | **47.0%** | 47.7% | **−0.82%** |
| PUMP-DIP NO-CONFIRM | 23 | 19 | 52.2% | 54.5% | +0.04% |
| **BASE-FLUSH CONFIRMED (benchmark)** | 64 | 39 | **46.9%** | 50.8% | −0.34% |
| BASE-FLUSH NO-CONFIRM | 4 | 4 | 75.0% | 75.0% | +2.75% (n=4 noise) |
| Direction contrast — PUMP-DIP all | 502 | 88 | 47.2% | 48.0% | −0.78% |
| Direction contrast — BASE-FLUSH all | 68 | 40 | 48.5% | 52.4% | −0.16% |

- **z(pump-dip CONF WR vs base-flush CONF WR) = 0.01** → statistically identical. Pumped-token dips bounce **exactly as well** as the base-flushes we already trade. The direction exclusion is not screening out worse trades.
- **z(pump-dip CONF vs NO-CONFIRM) = −0.49** → the confirmed turn does NOT beat entering the ongoing bleed; if anything it's slightly worse. Intuition: confirming waits for a 0.5% bounce, so you pay up for a worse entry than buying the continued dip. Corroborates the trough-validation ("60s confirm misses the winning 0-30s pocket") and the memory rule "winners buy dips / falling knives bounce more."
- **Both populations are break-even-to-slightly-negative unconditionally** at +6/−7 on this window. The fleet's edge lives in its *additional* selection (buyer-size, 0–30s timing, liq), not in the dip direction itself.

## Pump-magnitude sub-split (pump-dip CONFIRMED) — operator's blow-off intuition is INVERTED
| pump size | n | pairs | WR | blend |
|---|---|---|---|---|
| +50–150% (moderate) | 296 | 77 | 44.9% | −1.04% |
| +150–300% | 94 | 43 | 47.9% | −0.78% |
| +300–500% | 33 | 18 | 51.5% | −0.30% |
| **+500%+ blow-off** | 56 | 24 | **53.6%** | **−0.04%** |

Bigger pumps dipped and bounced **better**, not worse. Blow-off tops (>500%) were the **best** pump-dip sub-cohort, not the worst — and this held across every sensitivity variant (DROP 15/20/25%, FWD 30/45/60, TP6-SL7 & TP8-SL10: blow-off 50–55% vs moderate 42–46%). **The specific "exclude blow-offs because they bleed" premise is not supported by this data.** (Survivorship caveat: pairs that fully rugged post-blow-off may be under-represented in a tape that keeps trading; small n=56.)

## Demand-absorption sub-split (total-volume proxy — weak)
pump-dip CONFIRMED absorbing (vol rising into low) 45.1% vs drying (vol falling) 50.0%; NO-CONFIRM absorbing 50% vs drying 57%. Direction is counterintuitive (drying slightly better) and the proxy is total-volume, not buy-specific — **treat as noise; not a usable lever without net-flow data.**

## Per-pair robustness (pump-dip CONFIRMED, ≥3 eps)
60 pairs qualify. **Median per-pair WR = 48%.** Only **30/60 ≥50%**, **9/60 ≥66%** (base). Wide fat tail: winners (trust 64%, BLACKOUT 64%, PATTYICE 67%, emilio 62%) vs bleeders (Elon 22%, 7iLsHWeU 17%, Richard 31%, HANDSEM 33%). Same fat-tail signature as the winner-selection memory — no robust standalone edge from the population; it's a pair/regime lottery around break-even.

## Sensitivity (5 grid points)
pump-dip CONF ≈ base-flush CONF at every setting; at *deeper* dips base-flush degrades faster (DROP20% bf=39.6%, DROP25% bf=35.0%) while pump-dip holds ~47% — i.e. pumped dips are equal-or-**better**, robustly. Blow-off > moderate at every setting.

## VERDICT
**Does a confirmed turn make pumped-token dips as tradeable as base-flushes? The dips are equally tradeable — but NOT because of the turn.**

1. **Direction exclusion is not protecting us.** Pump-dip CONFIRMED (47.0% WR, −0.82%) is statistically identical to base-flush CONFIRMED (46.9%, −0.34%; z=0.01), and pump-dips are the *larger* population 7:1. So the pc_h6<=0 / pump-retrace / green-rip blocks are excluding a population that behaves **the same** as the one we trade — the operator is directionally right that the blanket direction-exclusion is somewhat arbitrary. **The money on the table is opportunity VOLUME (≈7× more dips at equal quality), not a higher win rate.**
2. **But "gate on the TURN instead" is NOT supported.** HL-confirm does not separate bouncers from bleeders on pumped tokens (or base-flushes) — confirmed ≈ no-confirm, and confirmed slightly *underperforms* because it pays up for the bounce. At 1m/60s resolution the turn adds no edge; a tick-level live shadow is the only way to rescue it.
3. **Blow-off intuition inverted:** >500% pumps bounced best, not worst — no data support for excluding blow-offs by depth-of-pump.
4. **Neither population is a money-maker unconditionally** (both ≈break-even/negative at +6/−7). Admitting pump-dips only pays if run through the fleet's existing selection funnel (buyer-size ≥$34, 0–30s timing, liq/structure gates), which this study did not apply to them.

### Cleanest deployable next step (paper A/B, not a live change)
Do **not** build a turn-gated "pump-dip lane" (turn doesn't discriminate here). Instead, **relax the pure-direction blocks and route pump-dips into the SAME selection funnel base-flushes already pass** — as a shadow/paper A/B:
> admit dip if `dip ≥15% from trailing-60m high` AND `[fleet's existing buyer-size + realtime-dip + liq/structure gates]` — **regardless of prior pump sign**; do NOT special-case blow-offs (they were the best sub-cohort). Measure whether the ~7× larger opportunity set holds the fleet's realized per-trade edge.

If that shadow shows pump-dips holding the funnel's edge, the pc_h6<=0 direction gate is costing us ~7× the deployable shot count. If it degrades, the direction gate is a cheap proxy for something the funnel already captures and can stay.

## Caveats
- Single ~28h rip-heavy window, one regime; base-flush n small (64) because base-flushes were rare here.
- +6/−7 outcome from 1m bars: ≤60s entry-price staleness; method B removes the ±15-bar lookahead bias but 2-bar reaction-window smoothing remains (common-mode across cohorts).
- NO-CONFIRM cohorts under-powered (n=23/4) — bar resolution can't fully test confirm-vs-noconfirm; needs tick-level live shadow.
- Absorption = total volume, not buy-specific (no net-flow in the bar cache) → discard that split.
- Winner = raw +6/−7 path, no fleet selection applied; absolute WR is the *unconditional* dip baseline, not fleet-realized.
