# RH tail-cap — net-$/position optimization (2026-07-13)

GOAL (AxiS): get RH net-$/position higher so it's worth the trading risk. KEY
FINDING built on: the biggest net-$ lever is CUTTING THE LOSS TAIL, not selection.
This models the loss-cut config that MAXIMIZES net-$/position across all 3 regime
days, robustly, and REQUIRES the cut to help the BAD day (07-11) too.

Data: local `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (457 closed trips,
43 tokens, 07-10/11/12). Trips = scorecard `load_rh_trips()` join (sells per
`(bot,pool)`, split at `fully==True`, 1970-epoch rows scrubbed). Script:
`scratchpad/_rh_tailcap_net_analyze.py` (read-only, local ledger). Builds on
`_rh_stable3_0713.md` (the tail-cap stability thesis) and `_rh_exit_rug_0713.md`
(single-block-vs-staged rug taxonomy).

**Regime map (critical):** 07-10 is CONTROL-ONLY (the fleet started 07-11, so all
70 of 07-10's trips are `rh_young_v1`). **07-11 = the BAD day** (fleet net −$214).
**07-12 = the GOOD day** (fleet net +$252). The focus racers (demand_heavy,
deep_only) only exist on 07-11/07-12.

---

## 1. The two tails are DIFFERENT mechanisms → two different levers

| racer | overall net | its loss tail | day | savable by |
|-------|------------:|---------------|-----|------------|
| **rh_demand_heavy** | **+$36.0** | QUANT staged bleeds (−30.6/−26/−21/−16, HARD_STOP, holds 0.5–20m) + "13" | **07-11 (BAD)** | **tighter STOP** |
| **rh_deep_only** | **−$3.2** | ONE CASHCATWIF LP_DRAIN **−100.1** @ 109-min hold | 07-12 (GOOD) | **DERISK only** |

- **HARD_STOP fills gap PAST the −15 trigger** (median fill −17.9, tail to −97.7;
  59 of 63 stops filled worse than −15). So even the *current* −15 stop does not
  hold losers to −15 — the observed net already carries the gap-through cost.
- **demand_heavy's tail is STAGED** (multi-minute bleeds with small per-tick slip)
  → a price stop set tighter exits earlier, near the tighter level. This is the
  net lever for it, and it lands entirely on the BAD day.
- **deep_only's tail is a SINGLE-BLOCK LP pull** (reserves gone in one block at
  t=109 min; the sell quote is already ~−84 before any stop can read it). A price
  STOP cannot catch it — only cutting exposure BEFORE the late pull (DERISK to
  25%) reduces it: −100% on a quarter position ≈ −$6.25 vs −$25.

---

## 2. Net-$/position PER DAY, per loss-cut level

Two bounds are reported because the ledger has no intra-trip price path:
- **Idealized floor** = the stop achieves exactly the level (optimistic upper bound;
  matches the "capping" framing).
- **Slip-aware** = tighter stop only helps the gradual bleeds and keeps the observed
  gap-through overshoot on the fast/single-block fills (conservative lower bound).
  **Slip-aware at −15 reproduces the ACTUAL ledger** (demand_heavy +$35.99 vs actual
  +$36.01; fleet −$12.10 vs actual −$11.98) — so it is the trustworthy baseline; the
  truth for tighter stops sits between the two bounds.

### rh_demand_heavy (n=50) — realistic / slip-aware
| config | 07-10 | 07-11 (BAD) | 07-12 (GOOD) | **TOTAL** | /pos |
|--------|------:|-----------:|------------:|----------:|-----:|
| ACTUAL (stop −15) | 0 | −12.78 | +48.79 | **+36.01** | +0.72 |
| stop −12 | 0 | −9.04 | +48.79 | +39.74 | +0.79 |
| **stop −10** | 0 | **−5.29** | +48.79 | **+43.49** | +0.87 |
| stop −10 + derisk 5m | 0 | −1.87 | +48.79 | +46.93 | +0.94 |
| stop −8 | 0 | −0.54 | +48.79 | +48.24 | +0.96 |

Idealized bound is higher (stop −8 → +58.3, stop −10 → +53.8). **Both bounds agree:
tighter monotonically lifts total net, all of it on the BAD day, GOOD day untouched.**

### rh_deep_only (n=24) — realistic / slip-aware
| config | 07-11 (BAD) | 07-12 (GOOD) | **TOTAL** | /pos |
|--------|-----------:|------------:|----------:|-----:|
| ACTUAL (stop −15) | −1.52 | −1.64 | **−3.16** | −0.13 |
| stop −10 | +0.97 | −1.65 | −0.68 | −0.03 |
| derisk 5m @ −15 | −0.68 | **+16.25** | +15.58 | +0.65 |
| **stop −10 + derisk 5m** | +0.97 | +16.25 | **+17.22** | +0.72 |

The stop alone barely moves deep_only (+$2.5, its tail is the LP_DRAIN). **DERISK
is what flips it** (+$16 on 07-12) — but see §4: that +$16 is ONE token.

### FLEET (all 457) — realistic / slip-aware
| config | 07-10 | 07-11 (BAD) | 07-12 (GOOD) | **TOTAL** | /pos |
|--------|------:|-----------:|------------:|----------:|-----:|
| ACTUAL (stop −15) | −50.25 | −213.57 | +251.84 | **−11.98** | −0.03 |
| stop −12 | −42.72 | −182.90 | +254.77 | +29.15 | +0.06 |
| **stop −10** | −33.97 | −153.65 | +261.77 | **+74.15** | +0.16 |
| stop −10 + derisk 5m | −33.45 | −128.01 | +282.09 | +120.62 | +0.26 |
| stop −8 | −22.22 | −115.65 | +272.77 | +134.90 | +0.30 |

Idealized bound: stop −10 → +$190, stop −8 → +$237. **Every day improves at every
tighter level; the GOOD day is never worse; the BAD day's −$214 hole shrinks by
~$30 per 2pp of tightening.**

---

## 3. Winner-kill vs loser-save (measured, vs the ACTUAL ledger)

| racer / config | loser-save | **winner-kill** |
|----------------|-----------:|----------------:|
| demand_heavy stop −10 | 25 trips, +$7.54 | 0 trips of consequence, −$0.06 |
| demand_heavy stop −8 | 26 trips, +$12.29 | −$0.05 |
| deep_only stop −10 + derisk 5m | 9 trips, +$20.4 | −$0.04 |
| FLEET stop −10 | 222 trips, +$86.7 | **−$0.56** |
| FLEET stop −8 | 231 trips, +$147 | −$0.54 |

**Winner-kill ≈ $0 at every level down to −8.** This is structural, not luck: the
partial-TP ladder banks 0.75 of the position at TP1 (+6) BEFORE any drawdown, so a
tighter stop only ever clips the still-underwater remainder. The deepest observed
NON-terminal exit in the whole ledger is a POST_TP1_TRAIL at −7.0 — i.e. **no green
trip ever shows a price excursion below −8**, so no green trip is stopped out on
realized data.

**The honest caveat (why not just go to −8):** the ledger records only SELL legs, so
it is blind to a trip's price path BEFORE its first sell. A dip-buy that knifes to
−10 and then recovers to a +6 TP1 would be killed by an −8 stop, and that excursion
is INVISIBLE here. This unmeasurable pre-TP1 knife-through risk grows as the stop
tightens into the entry-noise band — and it is worst for the DEEP −25 entries
(deep_only), which routinely wiggle another ±10% after the flush. So −8 scores
highest on realized net but is withheld; **−10 keeps a 2pp buffer.**

---

## 4. Overfit / token-concentration check (MANDATORY)

Share of each config's NET improvement coming from its single biggest token:

| config | net improvement | top-token share | verdict |
|--------|----------------:|----------------:|---------|
| **FLEET stop −10** | +$86.1 | **26%** (QUANT) | **DIVERSIFIED — real** |
| FLEET stop −8 | +$147 | 21% | diversified |
| demand_heavy stop −10 | +$7.5 | 50% (QUANT, ×4 bites) | moderate; repeatable across 4 trades |
| FLEET derisk 5m | +$54.8 | 34% (3 late rugs) | acceptable |
| **deep_only derisk 5m** | +$17.9 | **100% (CASHCATWIF)** | **ONE-TOKEN — NOT projectable** |
| demand_heavy derisk 5m | +$4.4 | 100% (one 20-min QUANT) | one-token |

**The STOP lever passes the overfit test decisively:** the fleet's −10 benefit is
spread across 222 saved trades with the top token only 26% of the gain. demand_heavy's
is half-QUANT but across 4 independent re-entries the bot kept bleeding on (a
repeatable mechanical loss a stop cuts each time), not one freak trade.

**The DERISK lever's observed magnitude is one-token-fragile** — deep_only's entire
+$18 is the single CASHCATWIF LP_DRAIN. The MECHANISM is sound (it structurally caps
any late rug, at ~$0 winner-kill), so it stays as cheap insurance, but its $ size is
NOT a repeatable projection from this thin rug sample.

---

## 5. The optimal cap + what shipped

**Winner: tighten the hard stop to −10, keep the 5-min derisk-to-25%.**
- Clears the bar on ALL 3 regime days in BOTH bounds (fleet: 07-10 −50→−33, 07-11
  −214→−128, 07-12 +252→+282), never worsens the GOOD day, and cuts the BAD day.
- ~$0 measurable winner-kill; the −10 net gain is diversified (top token 26%) — real,
  not overfit.
- Chose −10 over the higher-net −8 to hold a 2pp buffer against the unmeasurable
  pre-TP1 knife-through (worst for deep −25 entries).
- Derisk stays because it is the ONLY defense against the single-block LP-pull the
  stop cannot catch — flagged as sound-mechanism / one-token-magnitude.

### Shipped (working tree, PAPER — no deploy, no push)
`scripts/rh_paper_lane.py`:
- `rh_stable_demand`: `hard_stop_pct` −15.0 → **−10.0** (derisk 5m / bites2 / group kept).
- `rh_stable_deep`: `hard_stop_pct` −15.0 → **−10.0** (derisk 5m kept; the derisk owns
  its LP-pull tail, the stop adds the staged-bleed savings).
- `rh_stable_ageddeep`: **left at −15.0** — aged holds are fat-tailed (p75 924m) with a
  20-min derisk that rides the tail; it has NO catastrophic tail to cap today, and a
  −10 stop would amputate aged dip-recoveries. Not tightened.
- Narrative "WHAT IS BAKED IN" note updated with the sweep + provenance.

### Tests
`test_rh_paper_lane` + `test_rh_paper_fleet` + `test_rh_factory_racers` +
`test_rh_aged_racers` + `test_rh_rug_signals` = **215 passed**; `test_pre_live_invariants`
+ `test_experiment_scorecard` = **21 passed**. ROSTER smoke: stable_demand/deep stop
−10, ageddeep −15, all derisk/bites/group intact (32 racers).

### Pre-registered forward watch (paper race seat, never a live seat)
Grade at n≥30 closes vs the −15 parents. If forward winner-kill stays ~0, tighten
toward −8; **−12 (= `rh_lowvar_catstop`) is the conservative fallback** if forward
knife-through appears. The derisk's catastrophe-cap magnitude re-measures forward as
more late-rug tokens accrue (today it is one-token / directional).

## Bottom line
The loss tail is TWO mechanisms: STAGED bleeds (demand_heavy's QUANT, the BAD day) →
cut by a **tighter hard stop**; and SINGLE-BLOCK LP pulls (deep_only's CASHCATWIF) →
cut only by **early derisk**. The net-maximizing, regime-robust, non-overfit,
~zero-winner-kill lever is **hard_stop −10 + derisk 5-min**: fleet realistic net
−$12 → +$121, every day better, the GOOD day intact, the BAD day's hole cut ~40%.
Shipped on the two scalp stable racers; the aged racer keeps −15. deep_only's headline
"flip to +$18" is honest but ONE-TOKEN — the stop's diversified fleet-wide save is the
durable win.
