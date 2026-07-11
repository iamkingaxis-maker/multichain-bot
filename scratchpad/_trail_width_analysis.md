# Trail-Width / Fat-Tail Analysis — 2026-07-10

**Question (AxiS):** Is the exit ladder's tightness (TP1 75% @ +6, TP2 25% @ +12, 2pp trail) leaving the fat tail on the table? Prompted by two blocked-sell accidents (mogdog +244% peak, SMOLE +44.6% peak) and one opposite case (LOCKIN RH runner rode +11% back to -19% hard stop).

**Short answer:** The fat tail is real (half of TP2-exited tokens go >=+30% higher within 6h), but the evidence says a wider trail does NOT net-capture it — and the mogdog/SMOLE "accidents" are not evidence for widening. We already have a live A/B running the exact proposed fix (badday_flush_runner_ab, runner_scaled_trail=true), and across 120 paired same-token episodes it nets ~zero vs the current ladder. **Recommendation: keep the ladder as-is on the young/flush enforce bots; keep accruing the existing scaled-trail A/B; fix the RH trail-fill gap (that's the real leak found here).** All below is accrual-stage evidence (diverged n=36-41 episodes), not a decision-grade result in either direction.

---

## Data used
- Solana realized trades: local `_tr.json` (fresh today 14:11 CT, 5,000 rows, 2026-07-05 02:06 -> 07-10 19:11 UTC). Sells carry `peak_pnl_pct`, `kind`, `hold_secs` directly. **No `hold_pnl_snapshots` anywhere (0/5000 rows)** — intra-hold paths had to come from GeckoTerminal.
- GT minute OHLCV: 15 pools fetched (3.5s pacing, one 429 backoff), covering 44 focus-bot runner exits + the mogdog/SMOLE case studies. Close-based simulation (bias noted below).
- DexScreener batch: 40/53 candidate tokens still listed; **13/53 delisted/dead** (tail bound for those = the ladder saved us).
- RH paper ledger: `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (19 closed sell legs today; BILLY slice-cost row used with corrected values).
- Scrub rule applied throughout (drop pnl>0 & hold<10s); distinct-token counts reported everywhere.

---

## A. Giveback (peak - exit, pp) — final exit leg per round trip

**Solana focus bots** (badday_young_rt, young_rt_paper, young_absorb, flush; 07-05 -> 07-10):

| Exit kind | trades | tokens | giveback med / p75 | exit pnl med | peak med |
|---|---|---|---|---|---|
| TP2 (runner capped) | 49 | 33 | 2.3 / 2.5 | **+16.4** | 19.0 |
| POST_TP1_TRAIL (2pp) | 37 | 26 | **6.6 / 9.8** | +1.1 | 7.7 |
| IN_FLIGHT_FLOOR | 187 | 101 | 7.7 / 10.1 | -7.7 | 0.0 |
| BREAKEVEN_LOCK | 22 | 19 | 7.0 / 7.8 | -2.2 | 4.2 |
| HARD_STOP | 4 | 3 | 36.9 / 37.2 | -30.2 | 6.7 |

**badday_flush_runner_ab** (scaled trail live arm): POST_TP1_TRAIL n=50/33 tokens, giveback med **11.2** / p75 16.0, exit med +3.5, peak med 12.3.

**RH paper** (n=2 trail exits — accrual only): BILLY giveback 8.3pp (3pp configured trail), KITTY 18.6pp. RH trails fill 5-15pp BELOW trigger.

Key readings:
1. A "2pp trail" realizes ~6.6pp median giveback — fills land ~4.6pp below trigger on fast tapes. The scaled trail realizes ~11.2pp. Realized giveback ≈ configured giveback + ~4-6pp fill slip, on both arms.
2. TP2 is a soft cap: it fires at the tick crossing +12 and fills at market — median TP2 fill is **+16.4%**, mean +19.7% (max +29 in window). The "capped at +12-13" framing overstates the cost of TP2.

## B. Abandoned tail after TP2 exits

Post-exit 6h paths (GT minute closes), TP2-final exits: **21 episodes / 9 tokens** (n<20 tokens -> accrual-stage):
- Went >=+30% above exit within 6h: **15/21** (close-based). >=+100%: **10/21** (AVAJAK, Bullscan, PUMPLON, Shadow).
- BUT at the 6h mark: **12/21 below the exit price**; median end-vs-exit **-33%**; 7/21 dead-ish (end <=-70% or candles stop). Plus 13/53 candidate tokens already delisted from DexScreener.
- So the tail exists but is round-trip-shaped: capturing it requires a trail that survives the interim dips, and the dips routinely exceed 12pp (hard stop) from local peaks.

**Runner-slice policy sim** (close-based minute candles, from TP1 onward; 35 unscrubbed episodes / 15 tokens):

| Policy | mean | med | token-level mean |
|---|---|---|---|
| actual (TP2 + 2pp trail) | +8.3 | +6.8 | +6.3 |
| no-TP2, 2pp trail | +16.7 | +8.9 | +14.6 |
| no-TP2, scaled (5/0.2/20) | +14.4 | +6.6 | +12.2 |

**This sim is upper-bound and NOT trusted as-is:** minute closes cannot see intra-minute wiggles, so it massively under-fires a 2pp trail vs the real 2-second tick loop (real 2pp exits give back 6.6pp median — the sim's +140% AVAJAK rides would have been shaken out in ticks). The bias favors the tight-trail variant most, so the sim ordering (trail2 > scaled > actual) is unreliable.

**The trustworthy evidence — live tick-truth paired A/B** (badday_flush 2pp+TP2 vs badday_flush_runner_ab scaled, same tokens, entries within 15 min): **120 unscrubbed pairs / 64 tokens**:
- Paired blended diff (scaled - current): **mean -0.13pp, median 0.00** (85 pairs identical — never reached TP1).
- Diverged pairs (n=41): mean **-0.39pp** (SE 2.19), median -2.77pp; scaled wins only 12/41.
- The scaled arm DID catch a monster: Shadow 07-07, peak +269%, runner exited **+183.7%** vs flush's TP2 +15.5% (+75pp blended on that pair). **Excluding that single monster, scaled is -2.23pp/episode (sum -87pp over 40 episodes).** The whole windfall was paid back in wider givebacks elsewhere. Monster frequency in-window: ~1/41 diverged episodes.
- Normalized to a 75/25 split (what enabling the flag on young bots would look like): diverged diff mean **+0.27pp** incl Shadow, **-0.93pp** excl. Zero-mean either way at this n.

**Conclusion for B: at current selection, expected value of widening the trail ~ 0; the sign of the answer is decided by monster frequency, and n is far too small (1 observed monster) to price it.** This matches the greenday-winner decode: the gap vs winners is exits/size/churn discipline, not tail-riding.

## C. Winner-kill check (<=5% bar)

| Change | basis | kills |
|---|---|---|
| scaled trail, 75/25 blend (live paired) | 38 green episodes | **2/38 = 5.3% — borderline at the bar** (GAYU +4.5->-6.2, Bullscan +9.6->-13.1) |
| scaled trail, runner slice only (live paired) | 33 green runner exits | **11/33 = 33% — fails hard** |
| no-TP2 2pp trail (sim, runner slice) | 29 | 6/29 = 21% — fails (sim biased in its favor, still fails) |
| no-TP2 scaled (sim, runner slice) | 29 | 7/29 = 24% — fails |

Because TP1 (75%) is untouched, blended kills are rare — but the runner slice itself turns red in 1 of 3 green cases. No variant passes cleanly enough to enforce today.

## D. Case studies

**mogdog (badday_young_rt, entry 16:53:22 @ 2.133e-4):** blocked sells let it ride to +244% peak — and it **round-tripped to -1.0% realized**. Simulated on real candles, ALL three ladders exit identically: TP1 +6.1 (75%), runner trails out at ~-3.0 (whipsaw below any giveback ~17:00 UTC), blended **+3.8%** — BEFORE the monster run started (+271% max came ~1h later). A wider trail does not capture this token; only "can't sell" did, and "can't sell" also gave it all back.

**SMOLE (badday_young_rt, entry 17:25:54):** same shape. Peak +44.6% (blocked), realized +18.0% only because the failed trail finally filled mid-run. Working ladders (all three variants): TP1 +8.1, runner -2.7, blended **+5.4%**. The 17:32 whipsaw (SMOLE dumped to -12; young_rt_paper hard-stopped -18.4 on it) shakes out every variant before the 18:00 pump. Note other young bots re-entered the 18:00 leg and took +114%/+40% TP2s — SELECTION re-entry caught the tail, not trail width. Reinforces reachability_mission: selection is the lever.

**LOCKIN (RH paper):** TP1 +11.16 trigger (75% out at +8.81 fill), runner then fell from ~+11 peak straight through the configured 3pp trail (should have fired ~+8) to a HARD_STOP fill at **-23.67%** on the slice, 5.5 min later. Under scaled trail: trigger ~+5.8 — but the trail never got a tick between +8 and -15, so config width is moot: **this is a monitoring/fill-cadence gap on the EVM rail, not a ladder-shape problem.** Same pattern in both RH trail exits (givebacks 8.3/18.6pp on a 3pp trail). Blended LOCKIN was still +0.7% pre-fees thanks to TP1 — the 75% front-take is what saved it.

---

## Recommendation

1. **Keep the ladder as-is on the enforce bots (young_rt, young_absorb, flush).** Both accidents, worked through honestly, do not indict it: mogdog round-tripped +244->-1 with no ladder; every working variant exits both tokens at the same spot; TP2 fills at median +16.4 not +12.
2. **Do NOT enable runner_scaled_trail on the young family yet — but keep badday_flush_runner_ab running exactly as-is.** It is already the right experiment; current tally: net ~0 (one +183% monster capture fully offset by -2.2pp/episode of extra giveback, 41 diverged episodes). Decision rule for later: enforce if paired diverged diff stays >0 with >=3 monster captures OR >=100 diverged pairs positive; retire the idea if it's still <=0 at ~100 diverged pairs. If a young-lane arm is wanted, spawn a SHADOW twin of young_absorb with the flag (shadow-first per house rules — needs AxiS's go for the extra bot).
3. **Fix (highest-value item found): RH trail fill gap.** The RH rail's runner losses come from trails filling 5-15pp below trigger and LOCKIN gapping through a 3pp trail to the hard stop. Check tick cadence / price-feed staleness in the RH monitor loop before touching any ladder numbers there. n=6 closed tokens — everything RH is accrual-stage.
4. **Instrumentation (cheap, closes the B gap for good):** (a) persist `hold_pnl_snapshots` (e.g., 5s-grid pnl samples) into entry_meta on sells, and (b) record a one-shot post-exit +6h price check per closed runner exit. Then this whole analysis reruns from local data with no GT fetches and no close-basis bias.

## Honesty notes
- Diverged live pairs n=41 (64 tokens paired overall); post-exit tails n=21 episodes/9 tokens; RH n=6 closed tokens. Everything here is accrual-stage; nothing meets the n>=20-distinct-token realized bar for a config change.
- The minute-close counterfactual sim systematically flatters tight trails; where it disagrees with the live paired A/B, the live data was used.
- No $ projections anywhere per calibration rule.
- Scratch outputs: `scratchpad/_b_cands.json`, `_gt_candles.json`, `_ds_now.json`, `_sim_out.json` (session scratchpad), scripts `trail_a.py`, `trail_fetch.py`, `trail_sim.py`, `trail_paired.py`, `trail_cases.py`.
