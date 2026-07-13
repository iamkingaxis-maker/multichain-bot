# SOL Hot-Market Opportunity — is the heat in OUR universe, and are exits too tight?

Date: 2026-07-12. Data: `scratchpad/sol_selection/_trips.json` (955 trips, 2026-07-02..07-12, 10 bots).
Honest metric: ex-top-2 token-median; claims validated in 4 chronological halves (OOS).
Cohorts: **recent** = 07-10..07-12 (n=464), **prior** = 07-06..07-09 (n=412).
Scope: analysis only. NO commits, NO live changes. SHADOW-stamped, not routed.

---

## Q1 — IS the market hotter in our data? YES, in the TAIL (not the median).

The ex-top-2 token-**median** peak is 0.0 in BOTH windows — the typical token still never goes
green, and median realized ret is red in both (recent -6.6, prior -6.3). Median liquidity, vol_h24,
and mcap are all flat-to-slightly-LOWER recent. So the heat is NOT "bigger/more-liquid tokens."

The heat is entirely in the **right tail** — the fraction of tokens that run:

| token-level reach | recent (07-10..12) | prior (07-06..09) |
|---|---|---|
| peak ≥ +10 | **50.0%** | 37.1% |
| peak ≥ +20 | **28.8%** | 17.1% |
| peak ≥ +30 | **21.2%** | 8.6% |
| peak ≥ +50 | **7.7%** | 0.0% |

Fill volume up too: 154.7 trips/day recent vs 103/day prior. Trip-level peak p90 ~26–29 both;
p95 29 vs 35. **Verdict: the market is materially hotter — reach-30 ~2.5x and reach-50 went
0 → 7.7%.** The extra money is in a fatter bounce tail, so a fixed low TP caps exactly the trips
that now run further. AxiS is right that the heat is real AND that a tight-exit fleet under-captures it.

## Q2 — TP-target gap: how much bounce we leave after TP2 (+12).

Of all trips that reach +12 (24% of trips, 33% reach +6), the eventual peak distribution:

| window | trips ≥+12 | peak of those p50 / p75 / p90 | bounce LEFT past +12 (p50) |
|---|---|---|---|
| recent | 24.8% | **21.2 / 27.5 / 43.1** | +9.2 |
| prior | 23.1% | **28.5 / 33.7 / 42.1** | +16.5 |
| all | 24.0% | 23.8 / 31.3 / 43.4 | +11.8 |

Conditional continuation — **given a token reaches +12, P(reach +20) = 55–62%, P(+30) = 20–32%.**
A hard +12 TP2 captures barely half the available bounce on the tokens that do run.
Caveat: current *realized* exits already drift past +12 (TP2-reachers realize a median +15.9),
so the truly un-captured median is ~+5pp, wider (~+12pp) in the hot/prior windows and in the p75+ tail.

**Do NOT read this as "raise TP1."** TP1 (+6) is the reliable scalp; see Q4 — raising it *loses*.

## Q3 — Hot-regime signal (decision-time, 4-half OOS). CLEAN SIGNAL FOUND.

Signal: **trailing universe-heat** = rolling fraction of the last K=25 fleet fills whose realized
peak ≥ +20 (computed strictly from trips BEFORE the current one — no leakage). HIGH = heat ≥ 0.20.
This is knowable at decision time and is a *realized-universe-quality* measure, NOT a time-of-day
proxy (the standing prior that time-of-day is a composition artifact is respected).

Whole sample, HIGH vs LOW trailing-heat forward outcomes:
- peak p75: **13.5 vs 6.5** · reach≥20: **19.2% vs 5.5%** · reach≥30: 8.2% vs 2.7% · mean ret +3.55 vs -2.12.

4-half OOS — HIGH-heat beats LOW-heat on bounce availability in **all 4 quarters**:

| quarter | reach20 HIGH−LOW spread |
|---|---|
| Q1 | +10.8 |
| Q2 | +11.7 |
| Q3 | +20.7 |
| Q4 | +3.5 |

Holds 4/4 for peak availability. (Realized mean-ret under *current* exits inverts in Q2 only —
i.e. the fatter tail is present every half; monetizing it depends on the exit, which is Q4.)

Rejected conditioners: `chart_mtf_align` bullish labels are the WORST (strong_bull peak-p75 = 1.4,
bull = 1.1 — chasing tops); "flat" is best. SOL price not in trip data (heartbeat ~$77, flat) —
not the driver. Heat is a universe-realized-quality regime, not a SOL-macro or chart-label regime.

## Q4 — The ONE regime-conditional change.

Synthetic exit model (TP1 half + TP2 half, −10 stop), comparing policies vs current (TP1 6 / TP2 12):

| policy | Δ mean ret/trip | HOT Δ | COLD Δ |
|---|---|---|---|
| raise TP1 6→10, blanket | **−0.21** | **−1.33** | +0.31 |
| raise TP1 6→10, regime | −0.42 | — | — |
| **raise TP2 12→20, regime-gated (HIGH only)** | **+0.22** | +0.69 | untouched |
| raise TP2 12→20, blanket | +0.41 | +0.69 | +0.28 |

Raising TP1 (holding the first tranche longer) **loses, and loses most on hot trips** (−1.33) — the
early scalp is the reliable money; don't touch it. Raising **TP2** captures the tail and is positive.

**#1 lever: when trailing universe-heat is HIGH (rolling reach20 ≥ 0.20), lift the runner-tranche
target from +12 to ~+18–20 (keep TP1 +6 and the stop unchanged). Cold tape keeps the current
tight +12.** Regime-gated version holds in **all 4 halves**: Δ = +0.08 / +0.37 / +0.21 / +0.20 per trip.
Blanket is slightly larger in-sample but the gated version is the safe, prior-respecting choice
(never blanket) and never harms the TP1 scalp or cold-tape trips.

Equivalent trailing formulation also works but needs a TIGHT giveback (≤5pp; giveback 8–12 gives back
more than current exits already keep and turns negative) — the hard-TP2→18/20 form is cleaner and more robust.

Sizing note: size-up is defensible in HIGH-heat (mean ret +3.55 vs -2.12, positive expectancy) but
is second-order vs the exit lever and carries ruin-math exposure — recommend exit change first,
regime size-up only after the exit change is validated live.

---

### Bottom line
- Market hotter in OUR universe: **YES** — reach≥30 21.2% vs 8.6%, reach≥50 7.7% vs 0.0% (recent vs prior).
- Bounce left after +12 TP2: given +12, **55–62% reach +20**; TP2-reacher peak p50 ~21–28 vs a +12 cap
  (~+5pp un-captured at median, ~+12pp in the hot tail).
- Top regime-conditional lever: **trailing-heat-gated TP2 lift +12 → +18–20 (TP1 unchanged), holds 4/4.**
