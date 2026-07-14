# BOUNCED-BUT-WE-LOST — replay quantification (built 2026-07-04)

Scope: family losing rounds since 07-01 (badday_flush, badday_allday, badday_young_absorb),
round = buy->flat per bot per token, SCRUB RULE applied (2 sells scrubbed, one winning round —
zero effect on the losing cohort). Fills: per-bot `/api/bots/{id}/trades?limit=1000`
(`_bnl_trades_*.json`). Bars: `_gt_bars/` + 56 pairs (re)fetched from GT minute OHLC
(`bnl_fetch_bars.py`, 3s pacing). Pipeline: `bnl_rounds.py` -> `bnl_replay.py` -> `bnl_agg.py`;
row-level dump `_bnl_replay.json`. ANALYSIS ONLY — nothing deployed.

**Headline: the replay INVERTS the briefed hypothesis.** The velocity-bail leg did not cost the
family money on this tape — it saved 48–294pp/week (all four replay variants, both time halves).
The real "bounced but we lost" leak is the TP1-fill/breakeven-lock cohort, not the bail.

## Q1 — Cohort size (n everywhere)

232 losing rounds since 07-01 (of 298 total; family net −481pp raw-round-sum). Bars cover
228/232 (98%). **TP1 (+6% from OUR entry fill) printed within 90min in 125/228 = 54.8% of
covered losing rounds; per-token dedup 51/87 = 58.6%** (first losing round per token).

| terminal exit class | rounds (lose) | covered | reachable | reach% | tokens | tok-reach |
|---|---|---|---|---|---|---|
| velocity_bail | 185 | 181 | 89 | 49.2% | 67 | 36 (53.7%) |
| breakeven_lock | 27 | 27 | 24 | **88.9%** | 13 | 12 (92.3%) |
| mae_floor | 12 | 12 | 8 | 66.7% (n thin) | 4 | 2 |
| timestop (never_runner) | 7 | 7 | 3 | 42.9% (n thin) | 3 | 1 |
| hard stop | 0 | — | — | — (floor/bail pre-empt it) | — | — |
| trail (post-TP1) | 1 | 1 | 1 | (n=1) | — | — |

Named-token correction to the 07-04 decode: **Martolexx (max +1.0% from our entry in 90m) and
RUSH (max −0.6%, all 3 entries) were NOT TP1-reachable from OUR fills.** The decode's "TP1
reachable" was measured from the flush-trigger price; our entries sat higher. "Bounced off
trough" ≠ "bounced above our entry".

## Q2 — Counterfactual ladder, reachable-TP1 cohort (n=125 rounds / 57 tokens)

Replay from our exact entry time/price, velocity leg OFF, pessimistic same-bar ordering
(stops before TPs; entry bar can stop, never TP; entry-bar stops fill at bar close because
its O/H/L contain pre-entry action). Two fill models bound the truth: *touch* = fill at
threshold (gap->open); *pess* = fill at min(threshold, bar close) — models poll-latency on a
crashing candle (live evidence: NEVER decision −83.65 filled −52.5; GAPLA decision −15.05
filled −17.1).

Variant A = family geometry, velocity off (never_runner floor −6 @ peak<3, giveback −6 @
peak>=4, MAE floor −7, stop −12, TP1 +6/75%, TP2 +12/25%, trail 2pp, 60m timebox) — i.e.
exactly what wickride_ab runs. Variant B = −18 floor geometry (all pre-TP1 floors −12/−18),
same TP ladder.

| variant | replay total (actual −580pp) | delta/round | token-dedup sum | replay TP1 hits |
|---|---|---|---|---|
| A pess | −503pp | +0.62pp | +117pp | 36/125 |
| A touch | −375pp | +1.64pp | +153pp | 36/125 |
| B pess | −157pp | +3.38pp | +264pp | 77/125 |
| B touch | −64pp | +4.13pp | +301pp | 77/125 |

Key mechanics: even when TP1 printed on the tape, the family-geometry floors (−6/−7) died
before the bounce in **71% (89/125)** of reachable rounds. The bounce prize mostly requires
the −18 floor (B), not just velocity-off.

## Q3 — The other side: saves (n=181 vb rounds with post-bail bars / 74 tokens)

- **SAVED: 154/181 = 85.1%** fell ≥6% below the bail price within 90m (token-dedup 62/74 = 84%).
- COST: 131/181 = 72.4% rose ≥6% above the bail within 90m (token-dedup 56/74 = 76%) —
  confirms the briefed "77% hit +6 above bail" (n=48) at larger n.
- **Whipsaw BOTH: 104/181 = 57%.** Mean post-bail 90m extremes: −26.2% / +25.1%.

Both prior stats were true; the missing piece was sequencing. The tape whipsaws, the trough
runs deeper than any floor in the −6..−18 band on half these tokens, so a floor-based stack
eats the downside before the bounce. NEVER (−83.65% in 113s) is the tail this leg exists for —
and replay still books −14.7 there only because bars flatter the no-velocity fill.

## Q4 — Net verdict (ALL covered velocity-bail rounds, n=181 / 74 tokens, window 2.9d)

Delta = replay(velocity off) − actual. Negative = the velocity leg was SAVING us.

| variant | raw round-sum | /week | token-dedup sum | /week | halves (H1/H2) |
|---|---|---|---|---|---|
| A pess | −294pp | −716 | −120pp | −294 | −149 / −145 |
| A touch | −107pp | −260 | −48pp | −116 | −13 / −94 |
| B pess | −477pp | −1163 | −138pp | −336 | −196 / −281 |
| B touch | −325pp | −791 | −79pp | −193 | −100 / −225 |

Distribution (A pess): p10 −8.6 / med −1.5 / p90 +5.3. Split by reachability: reachable vb
rounds ≈ breakeven under A (−41..+30pp raw) and clearly positive under B (+215..+273pp);
NON-reachable vb rounds are a bath (A −253pp, B −692pp raw). The prize exists only
conditioned on the bounce — a SELECTION problem, which no floor geometry can see ex ante.

**Live corroboration (thin):** same-window scrubbed net since 07-03 14:00 — wickride_ab
−$14.8 (24 tokens) vs badday_flush −$7.4 (23 tokens); 07-04 day: wickride +4.48 vs flush
+5.16. Sign-consistent with replay in both windows.

## The REAL bounced-but-we-lost leak: breakeven_lock (TP-side fill mechanics)

27 losing rounds locked ~breakeven after peaking mean **+7.5%** live (median +7.4) without a
TP1 fill at +6 — realized −84.6pp. Bar replay turns them into +24pp (19/27 TP1 hits): a
**~+109pp/2.9d (~+260pp/week) swing sitting in TP1 fill mechanics on already-peaked legs**,
bigger than the entire wickride prize under family geometry. This matches the queued
post-TP1/fast-watch fill build (family_remine 07-01) and is where "the flush bounced and we
still lost" is literally true — the token printed +7.5 and we banked ≤0.

## Ship recommendation

1. **Do NOT ship velbail_pnl_pct=−8 family-wide.** Replay says the leg saves 116–336pp/week
   (dedup) on this tape, robust across both fill models and both time halves; day-1 live A/B
   agrees in sign.
2. **Keep wickride_ab running to its stated bar** (n≥30 distinct tokens, scrubbed, per-token,
   judged vs badday_flush). It accrues ~24 tokens/day, so the bar lands in ~1–2 days. Given
   replay now points the other way, RAISE the ship bar: wickride must BEAT flush per-token at
   n≥30 (not merely tie) before any family-wide velocity-off; if it ties or lags, retire the
   wickride hypothesis and keep the leg.
3. **Do not arm the −18 floor family-wide** on this evidence: on the full vb cohort it is the
   worst variant (−79..−138pp dedup). It only pays behind selection that already knows the
   flush bounces (decode's own caveat: gated entries only).
4. **Point the next build at TP1 fill mechanics / breakeven_lock cohort** (~260pp/week
   replay-grade, 88.9% of that cohort TP1-reachable, and it needs no new market thesis).

## Honesty ledger
- Replay-grade numbers, not realized: bar replay fills TP1 at touch (live TP1 demonstrably
  fails to fill on these — that is what breakeven_lock IS), and floor fills between touch and
  bar-close bound live latency. Enforce decisions on REALIZED A/B only (standing rule).
- Entries held fixed: velocity-off changes bail-cooldowns/slot-freeing, so live round counts
  would differ (55/88 tokens have >1 losing round; PEACE/SUPERMAN 8 each). Only the live A/B
  captures the full loop.
- badday_allday (120/232 losing rounds) actually runs TP1 +13 (not +6); replay used the
  task-specified +6/75 ladder for all rounds — its counterfactual is flush-geometry, not
  allday's own. Direction of Q4 unchanged (allday's wider TP1 would only lower the
  no-velocity replay further).
- 4/232 rounds uncovered (GT returns nothing for 2 pairs; 2 partial windows); window is 2.9d
  — /week scaling is a x2.4 extrapolation; thin flags: mae_floor n=12, timestop n=7,
  reachable-vb token n=43, live A/B n=1 day.
- Q3 save/cost thresholds are symmetric ±6% off the bail fill within 90m; whipsaw overlap 57%
  means those columns do not sum to 100%.
