# Entry Over-Gating Test — filter_knife_catch_peak & retrace_micro_avoid (2026-07-13)

## Question
A prior agent flagged that bots WITH `filter_knife_catch_peak` + `retrace_micro_avoid`
reach +6% on ~25% of trips vs `badday_young_absorb` (which relaxes them) at ~42% —
suggesting these two filters CUT RUNNERS. This is the OPPOSITE direction from the
81-gate ADD-a-gate mine (which produced a clean OOS null). Tested here with the same skepticism.

**VERDICT: NULL. Leave both filters enforced.** Neither relax survives the mandated
ex-top-2 four-half OOS bar. The "cuts runners" signal is REAL but only at the
**peak-touch** level, and is realizable only under a *patient* exit (young_absorb),
NOT under rt's tight 6%-TP / 2pp-trail. It is an EXIT-capture problem, not entry over-gating.
Relaxing either filter on rt would ADD LOSERS at the realized ex-top-2 level.

---

## Method
- Data: local `_trades_cache.json` (no re-pull). bot_id startswith `badday_`, window >= 2026-07-03.
- Verdict keys verified in the data: `filter_knife_catch_peak_verdict` (PASS/BLOCK) and
  `retrace_micro_avoid_block` (bool). Both computed **bot-independently** in
  `feeds/dip_scanner.py` (knife: `h24_ratio_to_peak ∈ [0.85,1.0)`; retrace: on-chain sell
  distribution). Only ENFORCEMENT is per-bot config, so the verdict is a clean cross-bot signal.
- Verdicts live on BUY rows only (sell entry_meta is trimmed). Joined sells→buys by
  (bot, address, entry_price); 863/863 buys matched, one buy = one position.
- Position realized % reconstructed from partial-exit legs: cost_leg = pnl / (pnl_pct/100),
  position ppct = 100·Σpnl / Σcost. (`usd_received` and `max_drawdown_pct`/mae are NULL in this
  cache — mae dropped from analysis; peak_pnl_pct present.)
- **SCRUB RULE** applied: dropped ppct>0 & hold<10s (33 positions, mostly instant-fill TP legs).
- **Metric**: ex-top-2 token-median realized pnl_pct (drop top 2, take median). hit6 = share
  reaching peak_pnl_pct >= 6% (the "reach +6% on a trip" definition).
- **Cross-bot clean test**: among bots that do NOT enforce a filter, split their bought
  positions by verdict (BLOCK-but-bought vs PASS). BLOCK-but-bought = tokens the filter WOULD
  have blocked. If that cohort's realized ex-top-2 runs ABOVE PASS → filter kills winners.

Enforcement confirmed in code:
- `badday_young_rt_paper` enforces `filter_knife_catch_peak` (in `filters_enforced`) AND
  `retrace_micro_avoid:true` — this is bot A (the over-gated one).
- `badday_young_absorb` enforces neither (`retrace_micro_avoid` unset) — clean bot B.
- Config toggles are clean: remove `filter_knife_catch_peak` from `filters_enforced`;
  set `retrace_micro_avoid:false`. (`core/bot_evaluator.py::_effective_filter_blocks` +
  `feeds/dip_scanner.py:2170` / `:22884`.)

---

## Cohort sizes (positions, post-scrub)
| Filter  | BLOCK-but-bought (all badday) | BLOCK (young family) |
|---------|------------------------------:|---------------------:|
| knife   | 26 (11 pump_dip_ab, 9 young_pump_dip_ab) | 9 |
| retrace | 62 | 42 |

Small — power is limited. Knife-young (n=9) is too small to quarter.

---

## Aggregate cohort metrics (ex-top-2 realized pnl_pct is the decision metric)

### KNIFE_CATCH_PEAK
| Universe | cohort | n | mean | ex-top2 | hit6(peak>=6%) | peak_med |
|---|---|--:|--:|--:|--:|--:|
| all badday | BLOCK | 26 | -3.40 | **-6.47** | 0.269 | 0.12 |
| all badday | PASS  | 804 | -3.27 | **-6.32** | 0.257 | 0.00 |
| young fam | BLOCK | 9 | +0.27 | **-6.42** | 0.444 | 0.00 |
| young fam | PASS  | 264 | -0.84 | **-6.42** | 0.360 | 0.09 |

### RETRACE_MICRO_AVOID
| Universe | cohort | n | mean | ex-top2 | hit6 | peak_med |
|---|---|--:|--:|--:|--:|--:|
| all badday | BLOCK | 62 | -1.01 | **-6.72** | 0.323 | 0.00 |
| all badday | PASS  | 768 | -3.46 | **-6.32** | 0.253 | 0.00 |
| young fam | BLOCK | 42 | +1.12 | **-6.61** | 0.429 | 0.00 |
| young fam | PASS  | 231 | -1.15 | **-6.23** | 0.351 | 0.39 |

**Read:** On the mandated ex-top-2 median, the BLOCK-but-bought cohort is EQUAL-TO-WORSE
than PASS for both filters, in both universes. The BLOCK cohorts DO show higher `hit6`
(peak touches +6% more often) and slightly higher MEAN — the fat right tail the prior agent
saw — but the median trip still bails at the ~-6.4% giveback floor, and the realized ex-top-2
does not improve. Relaxing = trading tokens whose realized median is the stop floor.

---

## Four-half OOS (chrono early/late × odd/even interleave) — YOUNG family
Decision rule: a relax must improve BLOCK ex-top2 over PASS in a MAJORITY (>=3/4) of quarters.

### KNIFE
| quarter | BLOCK ex-top2 (n, hit6) | PASS ex-top2 (n, hit6) | verdict |
|---|---|---|---|
| Q1 early-odd  | -6.42 (3, .667) | -2.95 (65, .477) | protective |
| Q2 early-even | -6.42 (3, .667) | -3.93 (65, .492) | protective |
| Q3 late-odd   | n<3 (1)          | -7.63 (67, .239) | degenerate |
| Q4 late-even  | n<3 (2)          | -7.43 (67, .239) | degenerate |

**BLOCK beats PASS ex-top2 in 0/2 valid quarters.** Underpowered + negative. No relax lever.

### RETRACE
| quarter | BLOCK ex-top2 (n, hit6) | PASS ex-top2 (n, hit6) | verdict |
|---|---|---|---|
| Q1 early-odd  | -6.66 (7, .429)  | -2.95 (61, .492) | protective |
| Q2 early-even | -9.46 (12, .500) | -3.44 (56, .500) | protective |
| Q3 late-odd   | -5.47 (10, .600) | -7.92 (58, .172) | BLOCK>PASS (kill) |
| Q4 late-even  | -7.64 (13, .231) | -7.23 (56, .232) | protective |

**BLOCK beats PASS ex-top2 in 1/4 quarters — fails the majority bar.** The single "kill"
quarter (Q3) is the window where `young_absorb`'s patient exit was active (see below).

---

## Why the peak signal is REAL but not an entry lever — within-bot `young_absorb` (retrace)
| cohort | n | mean | ex-top2 | hit6 | **peak_med** |
|---|--:|--:|--:|--:|--:|
| BLOCK | 10 | +0.09 | -5.28 | 0.60 | **11.90** |
| PASS  | 27 | +0.53 | -5.77 | 0.44 | **5.34** |

Under `young_absorb`'s PATIENT exit, the retrace-blocked cohort peaks at a median +11.9%
and its ex-top2 (-5.28) edges PASS (-5.77). This is the ONLY place the blocked cohort looks
better realized — and it is driven entirely by the exit capturing the peak. Under
`badday_young_rt`'s exit (tp1 6% / 0.75 sell, 2pp trail, -6% giveback, -9% fast-bail) the same
peak is given back: rt-lane BLOCK ex-top2 stays at the -6.6% floor. The lever that turns the
higher peak-touch into realized $ is the EXIT shape, not removing the entry filter.

---

## Conclusion & recommendation
1. **Do NOT relax `filter_knife_catch_peak`.** 0/2 valid OOS quarters; BLOCK cohort ex-top2
   (-6.4) is no better than PASS. n=9 is also too small to conclude a lever exists. Leave enforced.
2. **Do NOT relax `retrace_micro_avoid`.** 1/4 OOS quarters — fails majority. Aggregate BLOCK
   ex-top2 (-6.61) is WORSE than PASS (-6.23). Relaxing adds losers at the realized level.
3. **No relax A/B config shipped** (per the task's conditional — survive-OOS gate not met).
   Shipping one would ADD LOSERS, exactly the risk the task flagged.
4. **The real, separable lever is EXIT capture.** Both blocked cohorts genuinely touch +6% more
   often (knife-young hit6 0.44 vs 0.36; retrace-young 0.43 vs 0.35), and `young_absorb`'s patient
   exit converts that into a better realized median while rt's tight exit gives it back. A
   heat-runner / patient-sleeve EXIT A/B on the young lane is the follow-up worth mining — NOT
   an entry-filter relaxation. This is consistent with the standing "we exit wrong" finding and
   the 81-gate OOS null (entry gating is roughly neutral in both directions).

## Caveats
- Cross-bot outcomes carry each bot's exit logic; the within-bot splits (young_absorb, pump_dip
  variants) control for that but at tiny n. Knife-young n=9 cannot be quartered.
- mae/`max_drawdown_pct` and `usd_received` are NULL in this cache snapshot; realized % was
  reconstructed from pnl/pnl_pct legs (exact for the cost basis) and peak from peak_pnl_pct.
- Window 2026-07-03 → 2026-07-11 only. If more badday buy tape accrues, re-run
  `scratchpad/_join.py` + `_oos.py` — the null should be re-checked at higher n before any
  entry relax is entertained, but the exit-capture lever is the higher-EV direction regardless.

Artifacts: `scratchpad/_overgating_buys.jsonl`, `_overgating_sells.jsonl`,
`_overgating_pos.json`, `_join.py`, `_analyze.py`, `_oos.py`.
