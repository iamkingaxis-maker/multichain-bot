# YOUNG 09-13 UTC block candidate — 4-half pass — PROGRESS

## Task
07-11 mine flagged: young (<6h) 09-13 UTC wr 25.9% med -8.0 (n=108/23tok, all-data). Run the same
4-half bar (chrono W1/W2 + odd/even dom, own + universe + gated lenses) before any block ships.
3 live bots trade this band (badday_young_rt/absorb/vsnap_ab @ $22.50).

## Pre-registered verdict rules
- BLOCK only if 09-13 worse in 4/4 halves on BOTH own-trade AND universe lenses.
- Own-bad + universe-flat/better => selection problem in that window; report differing entry
  features, no block.
- Mixed => NO ACTION + what n would decide.

## Survivorship
09-13 NEVER blocked (configs block 03-08 only; trader CT window 3-17 CT covers 09-13 UTC).
Full history usable. Rulebook "09-13 shadow-block" = shadow only. Verify Railway TRADING_*_CT.

## Runs (all complete)
- [x] own_0913.py -> _own_0913_out.txt: own young 09-13-worse = wr 2/4, tokmed 3/4 (FAILS 4/4;
      the two good halves are n=3 and n=12). Block-era slice 3/4 wr, 4/4 tokmed (thin cells).
      Lane-only: 09-13 wr 30.4 vs rest 44.3 (n=79 vs 379) but 60/79 fills from 2 days.
- [x] universe_0913.py -> _uni_0913_out.txt: raw young 09-13-worse = **0/4 on wr, tokmed AND
      cat30 — 09-13 BETTER in all four halves**. Gated (lane proxy) also 0/4; gated 09-13 =
      best young cell of the day (h10/h11 tokmed +19.7/+20.3). July slice agrees (09-13 wr 58.3
      tokmed +3.2 vs rest 41.6/-14.1).
- [x] comp_0913.py: compositional decomposition — never-green 58.3% vs 37.7% (lane 64.6/44.1);
      churn top2-tok=32% of fills vs 8%; gated-pass rate lowest of day in 09-13 (4-7%);
      88.9% of fills in 25-50k liq, 0% >=100k; scare-number rests on 3 days (06-23 -$317,
      07-09 -$41, 07-11 **+$95**).
- [x] Adjacent cells: 08-09 mixed (wr 3/4, tokmed 0/4); **13-14 is the actually-weak universe
      cell** (raw wr worse 4/4, tokmed 1/4) — flagged as separate follow-up, not acted on.

## VERDICT (written to scratchpad/_sol_young_0913_pass.md)
**NO BLOCK — pre-registered rule #2: own-bad + universe-BETTER = our selection in that window,
not the clock.** Universe fails the bar 0/4 in the opposite direction; a block would forfeit the
market's best gated young window. Fixable thing = never-green churn re-entries on sparse
candidates (per-token re-entry cooldown / daily fill cap = separate A/B candidate, AxiS consent).
Own-lens re-open bar pre-registered: >=20 distinct 09-13 tokens/half across >=10 fill-days AND
fresh universe no longer better in >=2/4. NO code changes, NO commits.
