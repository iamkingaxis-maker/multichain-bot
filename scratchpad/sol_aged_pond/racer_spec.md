# Paper racer spec — adolescent_absorb_nf15 (DO NOT PROMOTE LIVE)

Status: SPEC ONLY. Forward-confirm `aged_pond_absorb_shadow` FAVOR side to
n>=20 tok/side/half + sustained green BEFORE building this racer. AxiS decides
live promotion.

## Thesis
The 6-24h absorb pond only pays when the tape shows real buyers eating the dip
NOW. Tightening adolescent_absorb's `net_flow_15s_imbalance` gate from >=0
to >=0.4 lifts ex-top-2 token-median -2.5 -> +2.7 (n=22 tok, 85 legs, 64%
green, 3/4 OOS halves green). This is the absorb thesis sharpened, not a new axis.

## Config (clone of badday_adolescent_absorb.json, one changed line)
- bot_id: `badday_adolescent_absorb_nf15`
- display_name: "ADOLESCENT ABSORB nf15>=0.4 (aged-pond mine 2026-07-13): the
  6-24h absorb pond only pays on STRONG live absorption; tighten the
  net_flow_15s_imbalance gate 0 -> 0.4. Paper A/B mirror of adolescent_absorb."
- entry_gate: identical EXCEPT
    `["net_flow_15s_imbalance", ">=", 0.0]`  ->  `["net_flow_15s_imbalance", ">=", 0.4]`
- everything else identical (age 6-24h, pc_h1<=-30, liq>=25k, buyers>=10,
  hours 8-3, TP1 6%/0.75, TP2 12%/0.25, hard_stop -12, exclusion_pool separate).
- enabled: true, live_probe: false (paper only), paper_capital_usd: 2000.

## Judge (n>=15 distinct tok)
Ex-top-2 token-median > 0 AND >=50% tok-green vs badday_adolescent_absorb over
the same window. Volume floor: >=15 distinct tok before verdict. Expect roughly
half the fire rate of adolescent_absorb (nf15>=0.4 keeps ~48% of pond legs).

## Kill / caveat
- The lever is a VOLUME tradeoff: nf15>=0.4 = ~85/178 pond legs. If fire rate
  falls below ~1/day sustained, the +2.7 median won't accrue enough tokens to
  matter — reassess.
- chrono-late half was red (-3.9) in the mine; if the FAVOR side stays red on
  fresh tape, this is window fat-tail, not edge — kill.
