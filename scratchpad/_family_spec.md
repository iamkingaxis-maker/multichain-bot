# THE IMPROVED BADDAY FAMILY — Synthesis (spike-scrubbed, per-token honest)

## 1. The real, scrubbed edge

After removing 77 latency-spike prints (+3,805pp of fake profit) and collapsing ~18x mirror duplication, the family is 1,789 positions on just **99 tokens** over 8.5 days, running **-2.06 mean / -4.73 median / 32% win / -1.87 per-token**. No entry variant is positive; the six "variants" are 88-99% the same bot (all clauses already promoted into the bases). The edge that survives is **structural and fat-tail**: ALL positive P&L flows through one path — TP1/TP2 + POST_TP1_TRAIL (+800pp) — against one dominant sink, IN_FLIGHT_FLOOR never-green bails (57% of episodes, -1,406pp, avg peak +0.10, selling within 0.09pp of their own low, i.e. **entry-caused and exit-optimal**). The recoverable pond is concrete: winners keep making highs after TP1 (68% of TP1 positions, median +3.99pp further), trail closers give back 7.29pp vs the configured 2pp, entries taken UTC 00-11 lose on **every one of 7 days** (68% of net loss on 38% of volume), and the only per-token-positive cohort is dev_pct_remaining>=20 (+1.91 mean / 70% win, n=10 thin). Profit = trim the never-green entry cohort + stop leaking the winner tail; the median trade stays red.

## 2. Ranked improvements

**#1 — Overnight entry-hours gate (ENFORCE UTC 03-08 now; shadow full 00-11).**
Evidence: UTC 00-11 = -3.62 mean / 25% win / -485pp of the family's -719pp total, negative 7/7 days; UTC 04-07 sub-block -4.92 / 17% win, survives dropping the worst calendar day (-6.21 ex-06-24). Passes scrubbed + per-day robustness + drop-worst-day. Change: new env `ENTRY_HOURS_BLOCK_UTC=3-8` in the badday entry gate; add `0-11` as a half-size shadow arm. Expected: removes ~2/3 of net loss at 38% volume cost; pooled mean -2.06 → ~-1.09 on the retained window (+~1.0pp/token). Caveat: one week / one regime — the 03-08 core is enforce-grade, the full 00-11 block is not yet.

**#2 — Fast-watch cadence on HELD post-TP1 winners (BUILD + ENFORCE — fidelity, not a strategy bet).**
Evidence: 393 trail closers book peak-7.29 avg vs peak-2.0 config; decomposed 2.0 config + 3.42pp fired-below-line (scan-cadence, median 2.21 = systematic) + 1.87pp decision→fill. Realistic recovery ~+300-450 token-pp/8.5d = **15-20% of the entire book loss**, zero volume cost, zero selection risk. Change: route open post-TP1 positions (max ~3 at a time — cheap) onto the ~2s fast-watch loop for trail checks. KPI: fired-below-line median <1pp; TP-leg booked-vs-config median >= config. This is the same fidelity treatment entries got and aligns with the fast-fill-is-fidelity rule.

**#3 — Re-derived entry stack (SHADOW 3-5 days, then enforce): add `rsi_15m<=44` + `pc_h6<=0`, raise liq floor 15k→30k, cap `unique_buyers_n<=19`.**
Evidence: stack takes per-token -1.68/30.6% win → **-0.35 / 43.1% win** keeping 51/98 tokens; holds post-06-29 (-0.77/41.7%, n=12 tok, thin); buyers 20+ is the worst cohort (-2.42, post-era -7.73/0% n=3). Expected +1.3pp/token, +12pp token win, ~-48% volume. Shadow-first because post-era n=12 and because tonight's BE-lock + GREEN_DAY already changed the forward book. Do NOT touch pc_h1 depth or the -20/buyers>=12 floors (counterfactual censored — unfalsifiable from realized data; use allday lane to accumulate the loosening evidence).

**#4 — dev_pct_remaining>=20 as CONVICTION-SIZING tilt (SHADOW, replaces trigger-count sizing).**
Evidence: only per-token-POSITIVE condition (+1.91/70% win, n=10; +6.61/100% stacked n=6, directional; survives post-era n=2). Too thin as a hard gate (~10 tok/9 days). Change: size multiplier keys on devrem>=20 instead of trigger_count. Accrue dev_not_dumped shadow to n>=30 tokens before enforcing size.

**#5 — Read the already-running A/Bs before touching exits (HOLD).**
wideexit_ab (tp1 +13 / sell 30%) is independently validated by the TP1-continuation finding (68% peak higher post-TP1) — judge at n>=30 tokens on post-06-29 data. BE-lock: keep enforced, verify realized vs +2.46/fire at n>=30 bel_shadow fires; then **retire GIVEBACK_FLOOR** (all 25 of its closers had peak>=3 — fully redundant with the lock).

**#6 — Feed-gap/stale-fill guard (BUILD, medium priority).**
42 episodes closed below the -12 hard stop (-722 pos-pp / ~-331 token-pp, ~16% of net token loss, worst -29.2). Threshold-immune; bail on feed-resume while in stop zone. Flagged 06-22, still unbuilt.

**#7 — Shadow-block `demand_bottom_compound` trigger.**
-3.61 mean / 19% win / green on 0 of 6 days it fired (n=27 eps/15 tok). Shadow-first; watch active_dip (-2.81, 2/8 days) behind it.

## 3. STOP doing (post-measurement-week)

- **Retire the clones**: badday_flush_nf15, nf15_live, conviction_live, and one of flush/flush_live (88-99% token-identical, paired diffs 0.02-0.23pp, all t<1.1). Delete the disabled conviction_demand config. Same information at ~1/3 the volume/cost.
- **Retire badday_flush_convex**: the only statistically significant knob and it is NEGATIVE (-1.99pp/token vs base, t=3.3, n=80; peak-capture 20.5% vs 49.4%; maxDD -873). The +107% tail it's priced for never arrives (p90 peak ~+22).
- **Kill trigger-count conviction sizing**: -$3.44/token vs flat at equal base (it upsizes the deep flushes that die: -$6.02/token on loser tokens vs +$1.91 on winners). Flat-size conviction bots; do NOT carry it into badday_greencapit_conviction.
- **Retire median_buy_size_usd>=34.3** as a selector: doesn't survive the scrub per-token (34-80 band -2.49, worse than sub-$10 at -1.77); the 06-25 flagship number was position-weighted/spike-inflated. Re-test only at >=80.
- **Stop tuning loser-side exits**: ng_faststop = +0.03pp/fire wash; timestop45 wash; floors sell AT the low. Don't extend nf15>=0 (degrades the rsi/h6 stack), don't move tp1 fraction 0.75 (local optimum, 50/50 measured -0.14pp/pos), don't move the -12 stop level, don't act on hot_streak_early_day (100% single-day artifact) or day-of-week (1-2 calendar days per weekday).
- **Don't call volume restoration a recovery**: post-06-29 entry mix is WORSE (-2.45/token vs -1.69, exit composition unchanged) — the arm_only fix restored the same negative book.

## 4. Single highest-leverage change

**Stop entering overnight: enforce the UTC 03-08 block (shadow the full 00-11) — one env flag, ~2/3 of the family's net loss, negative on every single day observed, robust to dropping the worst day, and fully orthogonal to the entry stack and tonight's exit changes.** It converts a -2.06 book to a ~-1.09 book by itself; stacked with #2 (winner-trail fidelity, +300-450 token-pp with no volume cost) and #3 (entry stack to -0.35/token), it is the shortest measured path to the family's first genuinely per-token-positive configuration. Honest bound: all of this is one week of one regime on 99 tokens — enforce #1/#2 now, hold #3-#7 to shadow/A-B discipline, and re-run the full attribution on post-06-29 data at n>=300 token-fills.