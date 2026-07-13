# RH Winner Behavior Decode — EXIT / Re-entry / Breadth (2026-07-12)

AxiS: "decode winners on robinhood to see what we're missing." The entry signature was
already decoded (`_rh_candidate_factory.md` §1: winners buy moderate −8.6% pullbacks early
in the arc on proven-volume pools). This run goes DEEPER on the un-decoded axes — how the
winners EXIT, RE-ENTER, and how BROADLY they trade — to find the behavioral lever our 24
RH racers still lack.

Method: reused the full-history loader + winner selection from `rh_history/scripts/hist_decode.py`,
then reconstructed **TRIPS** (units-based: units = volume_usd/px; a trip opens on the first buy
from flat, closes when net units fall to ≤10% of the trip's peak, re-opens on the next buy).
Cohort = the **93 audited day-robust pure-on-tape winners** (union-counted, net-positive on ≥2
UTC days; 91→93 as more recorder tapes loaded). **846 closed trips + 412 re-entry trips.**
Code + data: `scratchpad/rh_winner_behavior/{winner_behavior.py, behavior_results.json, PROGRESS.md}`.

## The headline: our fixed +6% TP1 sits ABOVE the median RH mover

The single most decisive number in this decode:

- **55.4% of winner trips NEVER peak past +6%** (max favorable excursion p50 = **+3.6%** over
  the entry price). Only 44.6% ever tag +6, only 35.5% tag +12.
- Winners still MAKE money on those sub-+6 movers because they **sell into strength, all-out,
  near the local top**: median sell price = **97.4% of the trip's peak** (they give back only
  ~2.6% from the high), **74.2% of sells fire into RISING price** (price rose in the prior 120s),
  and the **median trip is a SINGLE all-out sell** (n_sells p50 = 1; first sell banks 100% of the
  position at the median; only 12.6% of trips scale out over ≥2 sells).
- Realized per-trip multiple: p25 **−2.5%**, p50 **+3.7%**, p75 **+19.8%**, p90 **+57.4%** — a
  fast, small median win with an INTACT fat right tail because the exit is all-out on 100% of size.

Our racers do the structural opposite. The scalp ladder every green racer runs
(`_rh_deep_decode.md`: rh_deep_only / rh_bites2 / rh_f_arc_scalp) is **TP1 +6/75% → TP2 +12/25%
→ trail 3pp (arms only POST-TP1) → stop −15**. That shape loses on BOTH ends of the winner
distribution:
1. **The median mover (55% of trips) never reaches our +6 TP1.** Our racer holds through the
   +3.6% peak it should have sold, then exits on the post-fade trail or the −15 stop. The winner
   already banked the whole bag near that peak.
2. **On the runners (the +57% tail), we cap 75% of the position at +6.** The winner rides 100%
   of size to near the peak. We harvest the fat tail on only the 25% TP2 sliver.

This does NOT contradict the deep-decode finding that scalp beats `rh_wide_ladder` (fixed
+10/+20): winners don't use a fixed HIGHER target either — RH fades revert, so any fixed target
above the median mover fails. Winners use a **peak-anchored TRAILING all-out exit** that fires at
+3% on a small mover and +50% on a runner. The lever is the ARM THRESHOLD and the FRONT-LOAD, not
the target level.

Honest bound: winner MFE is an UPPER bound because winners enter EARLIER in the arc than we do
(entry decode: winners −8.6% off-high vs our later fills). We see a SMALLER remaining pop than
they do — which makes "+6 TP1 too high" WORSE for us, not better. The direction of the lever
(lower the arm, go all-out on a trail) transfers and strengthens at our later entry.

## Q1 — EXIT discipline (quantified)

| metric | winners (93, 846 closed trips) | our scalp racers |
|--------|-------------------------------|------------------|
| hold time / trip (p25/p50/p75) | **0.55m / 3.4m / 20.6m** (p90 3.4h) | TP1 fires fast; trail can hold |
| sells per trip (p50/p75/p90) | **1 / 1 / 2** — all-out single sell | partial ladder (2 legs) |
| scale-out (≥2 sells) share | **12.6%** | 100% (always 75/25 split) |
| sell into RISING price | **74.2%** | n/a — fixed thresholds |
| sell px / trip-peak (p50) | **0.974** (give back ~2.6% from top) | trail 3pp but only post-TP1 |
| realized mult (p50/p75/p90) | **+3.7% / +19.8% / +57.4%** | 75% capped at +6, 25% at +12 |
| max excursion never >+6% | **55.4% of trips** | — TP1 misses all of these |
| time-boxing (15–22m cluster) | **2.6%** — NOT time-boxed | — |

The old "19-min hold" (`_rh_history_decode.md`) was a POOL-AGGREGATE (first-buy→last-sell across
all trips in a pool, conflating re-entries). The true **per-trip** median hold is **3.4 min** — RH
winners are FAST on the median trip, with a fat hold tail on the runners.

## Q2 — RE-ENTRY (secondary, fat-tailed — do NOT build a re-entry-primary racer)

- **77.4% of winners re-enter** a token after selling; re-entry = **412 of ~1,258 trips**.
- But re-entry is a fat-TAIL edge, not a per-trip edge: **re-entry net p50 = −$0.01** (mean +$11.6);
  first-trip net p50 = +$5.61. Re-entries contribute only **23.5% of total realized profit**
  ($4.8k of $20.5k) despite being a third of trips.
- The depth-gate ("deep re-entries pay, shallow slaughter") is WEAK here: deep re-entries (buy
  below prior sell, n=153) net p50 −$0.16 / mean +$13.5 / sum +$2.1k; chases (buy above prior
  sell, n=259) net p50 +$0.09 / mean +$10.5 / sum +$2.7k. Both are ~breakeven at the median with
  a positive tail — depth does not cleanly separate them at scale. **Takeaway: re-entry is worth a
  modest bite cap (rh_bites2's cap of 2 is well-calibrated), NOT a dedicated re-entry strategy.**

## Q3 — BREADTH (RH winning is NOT a breadth game — unlike Solana)

- Winners touch a **median of 1 distinct token/day** (p75 2, p90 3, max 12) with a **median of 2
  buys/day** in the captured universe. Median 6 trips per winner over the whole window (p90 15).
- Our racers run **max_concurrent = 2** — already inside the winner range. So the RH edge is NOT
  spray-and-cut breadth (contrast Solana's wide-universe finding). Concentration + exit shape is
  where RH winners win.
- CAVEAT: the captured set is 506→782 pools (mostly 07-10 recorder day). A winner's activity in
  un-captured pools is invisible, so breadth is a strict **LOWER bound**. Conclusion is one-sided
  and safe: "breadth is not the missing lever" — we can't rule out that some winners spray more
  broadly off-tape, but we CAN say the money we observe is concentrated + exit-driven, not sprayed.

## Q4 — The behavioral gap our racers DON'T have

**#1 (highest value): the peak-anchored ALL-OUT strength exit.** Winners exit 100% of the position
in a single sell into rising price near the local peak, capturing whatever the pop gives (median
+3.6%, tail to +57%). Our racers front-load 75% at a FIXED +6% that the median RH mover never
reaches, and cap the runner's tail at 25% of size. This is the miss.

Secondary gaps (lower priority): none of breadth, time-boxing, or scale-out is the lever — winners
DON'T time-box (2.6%), DON'T scale out (87% single-sell), and DON'T spray (median 1 token/day).

## SPEC to test — `rh_strength_trail` (PAPER SHADOW ONLY; RH probe held OFF, no live)

A shadow racer identical to the green scalp on ENTRY/universe, differing ONLY in the exit mode —
isolating the one lever:

- **Entry / universe / gates: verbatim clone of `rh_deep_only`** (dip −25 capitulation OR the
  factory moderate band, min_liq $30k, max_pool_age 24h, demand floor DEFAULT $50, honeypot +
  rt-cost ≤6% + LP-drain veto all still apply). Keeps entry constant so the test attributes any
  delta to the exit alone.
- **Exit = strength_trail (the new lever):**
  - **All-out, single leg** — NO 75/25 partial ladder, NO moonbag, NO time box.
  - **Arm the peak-trail from a LOW threshold**: arm once unrealized ≥ **+2%** (≈ breakeven+fees),
    not +6. Track the running peak from arm.
  - **Trail gap ≈ 3pp from peak** (matches winners' 2.6% median give-back from the top; sweep
    2–4pp at confirmation). Sell 100% on the trail trigger.
  - **Hard stop −15** unchanged (winner p25 realized is only −2.5%, so a −15 stop is rarely the
    exit; it's tail insurance).
  - Optional A/B knob `arm_at_pct ∈ {0, +2, +4}` and `trail_pp ∈ {2,3,4}` — but ship ONE config
    first vs the scalp control; don't grid-search the tape.
- **Bite cap = 2** (re-entry is a modest, fat-tail add — keep rh_bites2's calibration; do not add
  re-entry-primary logic).
- **No new gate/exec code needed conceptually** — it's a new exit MODE (arm-from-low, all-out
  trail) alongside the existing scalp/wide/moonbag exits in `scripts/rh_paper_lane.py`; wire as a
  config + roster spec, pin in `tests/test_rh_factory_racers.py`.

**Pre-registered confirm (backtest earns a RACE seat, never a live seat):** grade at **n≥30 CLOSED
positions** vs the scalp fleet as CONTROL, **per-token median (ex-top-2), never sums**. CONFIRM =
tokmed ex-top2 GREEN and beats the scalp control's tokmed and cat ≤1/20 and direction consistent
(strength-trail banks the sub-+6 movers the scalp misses). Else it retires to the documented-kills
list — no re-tune on the same tape. Hypothesis to falsify: an all-out low-armed trail beats the
75%@+6 ladder because 55% of RH movers never reach +6 and the winners prove those are bankable.

## Honesty ledger
- Winner MFE/exit-mult = UPPER bound (winners enter earlier than us); direction of the lever
  transfers and strengthens at our later entry, but absolute magnitudes will be smaller for us.
- Trip reconstruction skips sells with no on-tape open trip (untracked basis) — consistent with the
  pure-on-tape audited selection; 846 closed trips is a solid n. Trip close threshold = 10% of peak
  units (a re-add before full flat stays one trip).
- Breadth is a strict lower bound (captured-pool coverage), so the "not a breadth game" conclusion
  is stated one-sided.
- Re-entry median ≈ breakeven with a positive tail — reported as secondary, not as an edge to build.
- Cohort realized (token median +$8.34, ex-top-2 +$8.25, top-2 only 10.9% of the $20.5k sum) is the
  winners' OWN edge = the ceiling; our racers enter later and will realize less. Figures are the
  behavioral SHAPE to copy, not a P&L projection.
