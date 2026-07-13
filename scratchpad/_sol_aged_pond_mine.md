# SOL aged-pond mine — why adolescent_absorb works + siblings (2026-07-13)

Honest metric throughout: **ex-top-2 token-median** (drop each cohort's 2 best
tokens, median of the rest). "Profitable" = ex2>0 AND >=50% tokens green AND
n>=15 distinct tokens. Lifetime SUM banned. Data: realized buy->sell join of the
absorb family (young_absorb + young_absorb_live + adolescent_absorb +
adolescent_guarded) over 07-02..07-13, post-scrub (drop ret>0 & hold<10s):
**352 trips / 88 tok**. adolescent_absorb alone = **68 legs / 29 tok**. Plus the
955-trip generic young-lane set (scratchpad/sol_selection/_trips.json) as the
age-band control.

## Headline
- adolescent_absorb (wider n=29) ex2 **+0.6**, plainMed +2.2, 55% tok-green — it
  is the least-red bot but the +4.3 figure was the narrower n=19 window and its
  OWN OOS is fragile (chrono-early -5.8, odd-day -5.3). Marginal, not a fortress.
- The robust, reproducible edge is the **AGE x ABSORB-GATE interaction**, and the
  best NEW cohort inside the 6-24h pond is a **stronger live-absorption floor**:
  `net_flow_15s_imbalance >= 0.4` -> **ex2 +2.7, n=22 tok, 85 legs, 64% green,
  3/4 OOS halves green.**

## Q1 — adolescent's causal signature (vs the bleeding young_pump_dip lane)
Median entry-axis values, adolescent_absorb vs young_pump_dip_ab:

| axis            | adolescent | young_dip | reading |
|-----------------|-----------:|----------:|---------|
| lifecycle_age_h |        9.3 |       3.0 | AGED pool vs fresh launch |
| pc_h1           |      -37.7 |     -19.2 | DEEP 1h capitulation vs shallow |
| **pc_h6**       |    **-48.5** | **+204.6** | **pump is OVER vs still MID-LAUNCH** |
| liq             |      34.6k |     33.7k | ~same |
| unique_buyers_n |         47 |        45 | ~same |
| nf15_imbal      |       +0.5 |     (n/a) | live buy-side absorption |
| mean_buy_usd    |       45.8 |      42.3 | ~same |
| hidden_supply%  |       69.5 |      72.5 | ~same |

The single discriminating feature is **pc_h6**. young_pump_dip buys a token
still net **+205% over 6h** — a dip *inside an ongoing launch pump* that has far
further to dump. adolescent buys a token **-48% over 6h**: the launch pump has
fully deflated, price mean-reverted, and a settled 6-24h holder base is now
**absorbing** the flush (nf15_imbal +0.5). Age + "pump is over" + live
absorption + liq floor is the signature the young-dip lane structurally lacks.

## Q2 — is the aged pond systematically better? (ex-top-2, holding mechanics fixed)
**ABSORB FAMILY split by lifecycle_age_h (same entry mechanics, age varied):**

| age band | legs | nTok | ex2Med | plainMed | tok-green% |
|----------|-----:|-----:|-------:|---------:|-----------:|
| <2h      |   64 |   25 |  -5.1  |   -4.1   |    44 |
| 2-6h     |   88 |   32 |  -5.1  |   -4.9   |    41 |
| 6-12h    |  111 |   29 |  -2.2  |   -0.9   |    45 |
| 12-24h   |   67 |   19 |  -0.1  |   +1.9   |    53 |
| 24-48h   |    6 |    2 |   n/a  |   +2.3   |    50 (underpowered) |

**Monotonic**: older -> less red, crossing to breakeven at 12-24h. This is the
real "pond > young lane" result.

**GENERIC young lane (955 trips) split by the SAME age bands = FLAT** (all bands
ex2 -4.4..-6.3). Coarse aged>=6h on the young lane is ex2 **-5.6** (all 4 OOS
halves red); pc_h6<0 alone -5.4; pc_h6<0 & age>=6h -4.6. **So age alone is NOT
the lever** — it only monetizes UNDER the absorb gate (deep pc_h1 capitulation +
net_flow_15s absorption + liq floor + buyers>=10). The edge is the interaction,
not either factor alone.

## Q3 — green aged cohorts inside the 6-24h absorb pond (base ex2 -2.5, n=42)
Single-axis screens that clear the bar (ex2>0, tok-green>=50, n>=15):

| cohort (6-24h absorb pond)  | legs | nTok | ex2Med | tok-green% | OOS (4 halves) |
|-----------------------------|-----:|-----:|-------:|-----------:|----------------|
| **net_flow_15s_imbal>=0.4** |   85 |   22 | **+2.7** |     64 | +2.1 / -3.9 / +1.0 / +2.6  (3/4 green) |
| net_flow_15s_imbal>=0.2     |  113 |   29 |  +1.9  |     55 | +2.1 / -6.8 / -5.3 / +2.6  (2/4) |
| entry_vol_h24>=1M           |  130 |   30 |  +1.8  |     57 | -2.7 / +2.4 / +1.7 / -0.9  (2/4) |
| liq>=35k                    |   85 |   23 |  +1.7  |     57 | -2.2 / +2.4 / +1.7 / -2.2  (2/4) |
| buyers>=50                  |   64 |   16 |  +0.6  |     56 | -2.4 / +2.1 / -0.8 / -2.7  (1/4) |
| liq>=45k                    |   35 |   11 |  +1.9  |     64 | (underpowered n=11) |

2-way combos with pc_h6<0 (post-pump) confirm nf15 as the driver:
`pc_h6<0 & nf15_imbal>=0.4` ex2 +2.0 (n=20, 60% green) and `pc_h6<0 & liq>=35k`
+1.7 (n=19) — both green but smaller n and weaker OOS than pure nf15>=0.4.
pc_h6<0 is ~86% of the pond so it is not the discriminating lever *within* the
pond (it is the between-bot lever vs young-dip). Axes that INVERT/fail on the
pond: buy_pressure_60s>=0.6 (-4.9), pct_above_support>=10 (-5.6), top10<25
(-4.8), lower_wick_5m>=0.5 (-2.5) — chasing strength/late structure is a trap
here too, consistent with the young-lane mine.

## Verdict / best new cohort
**net_flow_15s_imbal >= 0.4 on the 6-24h absorb pond**: ex2 **+2.7**, n=22 tok,
85 legs, 64% tok-green, 3/4 OOS halves green (only chrono-late red). It is the
absorb thesis sharpened — an aged pool pays only when buyers are *eating the dip
right now*, not merely neutral flow (the bot's current gate is >=0). Caveat:
chrono-late red + n~22 sits at the powered floor. MEASURE-ONLY forward; no live
enforce.

## Shipped (working tree, no commits, no live enforce)
- SHADOW stamp `aged_pond_absorb_shadow` in feeds/dip_scanner.py (FAVOR when
  6-24h AND net_flow_15s_imbalance>=0.4; also records `aged_pond_absorb_postpump`
  = pc_h6<0 for the forward join). Fail-open on missing (isinstance guard).
  py_compile OK.
- tests/test_aged_pond_absorb_shadow.py (6 tests, all pass) — threshold contract
  + fail-open + postpump-flag-not-gated.
- Paper racer spec (do NOT promote live — AxiS decides after forward confirm):
  scratchpad/sol_aged_pond/racer_spec.md.

## Next (forward)
Grade `aged_pond_absorb_shadow` FAVOR side forward to n>=20 tok/side/half on
fresh tape; confirm the chrono-late red was window noise, not decay. If it holds
green, promote the racer spec to a paper A/B (nf15>=0.4 mirror of
adolescent_absorb). Then enforce-or-kill.
