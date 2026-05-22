# Multichain Bot — Session Handoff

**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode**: PAPER (PAPER_TRADING=true), $20/position base, max 3 concurrent, dip_buy strategy
**Trading window**: 24/7 (TRADING_START_HOUR_CT=0, TRADING_END_HOUR_CT=24)

---

## 2026-05-22 — MEGA SESSION: 21 new entry triggers + 1 filter + race-fix + dashboard

### Top-level numbers

**Live HEAD:** `eb6e8b8`

**Ships this session (chronological):**

| Commit | What | Type |
|---|---|---|
| `d64a37b` | vol_breakout_flat trigger | trigger |
| `e45577a` | vol_drying + wick_rejection post-TP1 exits | exits |
| `85925a5` | calm_buyer_demand + calm_at_support triggers | triggers |
| `9fe8366` | filter_sol_macro_down ENFORCED | filter |
| `4228a36` | Dashboard SOL gate indicator | UI |
| `5ab8e74` | SOL fetch race-condition fix (cycle scope) | bug |
| `734bab5` | Dashboard sol_price key fix | bug |
| `f15ed43` | Ban toxic combo + retire 2 + alpha tier 1.5x | cleanup |
| `960a354` | 3 alpha triggers (demand_burst, 1s_demand, two_pattern_demand) | triggers |
| `6368ed4` | concurrent_alpha (highest $/tr ever mined) | trigger |
| `f6b9113` | 10 new alpha-quality entry triggers | triggers |
| `eb6e8b8` | 5 round-3 triggers (CNN cluster, slip, hot streak) | triggers |

**21 new entry triggers + 1 filter + 2 exit rules + 1 dashboard + 2 bug fixes shipped in one session.**

### Alpha-tier (1.5x sizing) — 8 triggers

`1s_capit_reversal`, `deep_1h_dip`, `concurrent_alpha`, `whale_concentrated_demand`, `whale_recent_burst`, `whale_p90_size`, `textbook_pullback_vol_accel`, `textbook_pullback_big_buyer`

### Top meta-discoveries

1. **`concurrent_positions_at_entry > 1` is strongest big-winner predictor (Cohen's d=+1.02).** Hot streaks have real persistence. Could justify max_concurrent=4-5 (currently 3). See `[reference_concurrent_positions_alpha]`.
2. **"Few buyers" universal alpha** — `top_buy_makers_n < 9` surfaced across 6 mining angles. 5+ shipped triggers anchor on this. See `[reference_few_buyers_alpha]`.
3. **BTC h1 stronger than SOL h1** in current regime (d=+0.41 vs +0.33). Both gates complementary, +0.41 correlation, catch different trades.
4. **Race condition fix:** SOL fetch was inside per-token loop, causing some tokens to silently bypass filter_sol_macro_down (WORLDCUP 01:14:42). Lifted to `_fetch_cycle_sol_features` called once per cycle. Cut 5-30x GT calls per cycle as a bonus.
5. **Mining "exhaustion" is scope-dependent.** Every prior "exhausted" claim turned out wrong-at-different-cohort. Pivoting cohort/threshold/independence-constraint always surfaces new signal.

### 13 distinct entry-trigger signal families

1. Concentrated whale demand (top_buy_makers_n<9 variants)
2. Textbook pullback compounds (mtf_textbook_pullback flag)
3. Support + buyer confirmation (chart_sr_5m_at_support)
4. BTC macro alignment (btc_pc_h4 + bs_h1)
5. Mean-reversion chart_score (high chart + quiet flow)
6. CNN cluster individual (clusters 10, 13, 16)
7. Slip asymmetry (low buy slippage)
8. Bot-state momentum (hot streak persistence)
9. Concurrent positions hot streak (concurrent_alpha)
10. 1s capitulation + variants
11. Deep 1h dip (workhorse)
12. Vol-breakout flat base
13. Calm pattern + demand

### Banned combos + retired triggers

- **BANNED:** `chart_quality_bottom + net_flow_5m_demand` (0% WR n=6, $/tr=$-1.29). Stripped from triggers_fired when both fire; if no other trigger, entry blocked.
- **RETIRED:** `grad_window_dip` (-$0.62/tr, 30% WR) and `controlled_greens_5m` (-$0.61/tr, 40% WR). Match flag stamped to entry_meta for forensic analysis but not added to triggers_fired.

### Pending followup (PRIORITY)

1. **PHANTOM PARITY OWED** on EVERY new trigger + filter this session — `scripts/live_forward_test.py` not updated. Violates `[feedback_phantom_parity]`. **Backfill ASAP.**
2. **Forward-validate all 21 triggers** for 24-48h. Most have n=8-25 — small samples. Watch for triggers that over-fire vs mining frequencies.
3. **Re-mine SOL gate with 500+ post-restoration trades** — current n=188 was thin.
4. **Test max_concurrent=4-5** — concurrent_positions>1 was strongest big-winner predictor. Currently capped at 3.
5. **Trigger attribution audit** after 50+ new-trigger fires — measure forward $/tr vs mined $/tr.
6. **Promote `low_buy_slip`, `support_with_60s_flow`, `btc_safe_bs_h1` to alpha-tier?** Their $/tr ($0.40-$0.58) is borderline; standard 0.5x might be leaving alpha on table.

### Known issues

- GT 25-req/min budget shared across full scanner; SOL fetch occasionally 429s. 300s cache absorbs. Fail-CLOSED safety added (commits 5ab8e74) — if SOL feed went stale within 10min, block as safety.
- Phantom parity gap (see above) — when bot makes a phantom-paper trade in `live_forward_test.py`, the new triggers won't fire there. Phantom dashboard will UNDER-count signals.
- Railway 503s during deploy mid-session (intermittent). Retry pattern works.

### Caveats

- 21 triggers in one session is aggressive. Some will under-perform forward (regime shift, threshold drift).
- Most mined samples are n=8-25. Statistical confidence low; mining $/tr ≠ forward $/tr.
- Alpha-tier 1.5x sizing on 8 triggers means up to 8x more capital deployed when multiple alpha triggers fire simultaneously (each is independent — could fire together).

---

## 2026-05-21 PM — 5 production ships, 9/9 WR since deploy, bot near production-ready

### Session P&L

**Since first deploy of the day (11:32 UTC):**
- 9 closed paired wins, 0 losses (**100% WR**)
- NET **+$7.09**
- Today total (00:00 CT → now): 43 trades, 77% WR, +$1.57

**Best trades:** TYGR +$2.81 (runner trail rode peak +45.8%), UFO +$1.67 + $1.57 (2 manual sells — user chart-pattern instincts validated), VIRL +$1.12, PAC +$0.87, MORI +$0.88.

### Production version

**Live (deployed):** commit **`9fe8366`** — filter_sol_macro_down ENFORCED.

### Commits shipped + DEPLOYED today (4)

1. **`d64a37b`** — `trigger_vol_breakout_flat` (held overnight, deployed AM). First production fire on MORI at 15:00 UTC, won $0.88. Validates the per-token forensic mine.
2. **`e45577a`** — `vol_drying` + `wick_rejection` post-TP1 exits ENFORCED. Mined from 43 winners; user UFO instincts validated (vol_m5=0 + wick rejection = $1.01/$0.18 per trade lift respectively). See `[reference_exit_signal_mining_2026_05_21]`.
3. **`85925a5`** — `trigger_calm_buyer_demand` (T1) + `trigger_calm_at_support` (T2) ENFORCED. From today's-9/9-winner mine. T1 broad coverage (8/9 today match); T2 highest precision (93% WR mined). Awaiting first fires.
4. **`9fe8366`** — `filter_sol_macro_down` ENFORCED (h6<-0.3 OR h1<-0.7). REVERSES prior FALSIFIED finding via outlier cleanup + filter-regime stratification. Cohen's d climbed from 0.04 lifetime → 0.33 post-restoration. Expected lift $/tr -$0.13 → +$0.26 on bigger n=1373 sample. See `[reference_sol_gate_2026_05_21]`.

### Pending followup (next session priorities)

1. **PHANTOM PARITY OWED** — none of the 4 today-commits wired into `scripts/live_forward_test.py`. Violates `[feedback_phantom_parity]`. Backfill ASAP.
2. **Forward-validate 24-48h** — all 5 new pieces (1 filter, 3 triggers, 2 exit rules) need WR + $/tr confirmation. Watch for:
   - `wick_rejection` clipping runners mid-pump (rule is precision-tuned but bar-completion may not be enough buffer)
   - `vol_drying` early-exits on noise (low-vol minute mid-uptrend)
   - `filter_sol_macro_down` overblocking when SOL is in a healthy range-bound regime (h6=-0.4 + h1=+0.5 = blocked but not actually a downtrend)
3. **Re-mine SOL with bigger window** — current n=188 post-restoration was thin. Wait for 500+ trades.
4. **Sample mid-cap cohort** ($5-20M) — yesterday's session-compare flagged only n=33 trades. Need more data on this tier.
5. **Investigate scanner ingestion gap** — 9 watchlist tokens (HENRY, ATTENTION, MANIFEST, ROUTER, Ebola, SCAM, PENGUIN, HODL, MAGA) had real runs but 0 trades in 9d.

### Key methodology learnings

- **Always stratify by filter regime when mining.** SOL signal hidden in lifetime data (d=0.04) but strong post-restoration (d=0.33). Don't trust single-window mines.
- **Always clean outliers before mining $/tr.** 20 trades with |pnl|>$100 (one was $409,471) polluted the SOL mine first pass.
- **`/api/trades?full=1`** is the source of truth for entry_meta + trigger attribution. See `[reference_trades_api_full_param]`. Railway log retention is only ~30min — DO NOT rely on logs for historical trigger attribution.
- **Coinbase Exchange API** works for SOL price (US-friendly, unlike Binance HTTP 451).

### Caveats

- All 4 deploys were ENFORCED without held-out validation per user direction. Forward-monitor closely.
- Position sizing wasn't touched today (sizing audit pending from prior session).

---

## 2026-05-20 → 2026-05-21 — Rough session, 8 deploys, forensic mine surfaced flat-base alpha gap

### Session P&L

Since 2026-05-19 14:00 UTC ("we were up $4"): **NET -$12.54** across 65 paired trades (33W/32L, WR 49%).
- Avg win: +$1.02. Avg loss: -$1.37 (1.34x larger than wins).
- 3 catastrophic -15% stops cost $11.21: TYGR -$7.36, WR26 -$2.20, TOLYBOT -$1.65.
- 7 pre-stop bail-outs (dying vol) cost $11.09; 9 slow-bleed exits cost $10.28.

Critically: tonight's per-token forensic showed **6% capture rate** — 142 missed +15% runs in 24h vs 9 captured. The bot is bleeding *opportunity* even more than dollars.

### Production version

**Live (deployed):** commit `c406a6f` (hot_runner_calm_5m + hot_runner_shallow_1h)
**Queued (pushed, NOT deployed):** `d64a37b` — trigger_vol_breakout_flat. Deploy tomorrow morning: `MSYS_NO_PATHCONV=1 railway up --detach`.

### Commits shipped + DEPLOYED today (8)

1. **caa8f5c → 8b4ba89** — sizing rebalance + macro_up 2.0x→1.5x rollback (after macro_up amplified Digi -$3.89, TYGR -$7.36, HERMES -$3.63, https -$3.43, BP -$1.65 on $40 sizing).
2. **e94015b → 9559007** — `filter_1m_steep_fall` PROMOTED to ENFORCED. Driver: TYGR -$7.36 — filter flagged BLOCK at entry (1m_cum3=-9.65%) but was SHADOW. 11 SHADOWs flagged BLOCK; bot bought anyway.
3. **36bd228** — pre-TP1 panic exit (5s confirm on 6pp drop).
4. **1c9437d** — pre-TP1 trail carve-out (`pnl ≤ -2%` gate). Prevents false-bottom trails (AMERICA/VIRL pattern).
5. **b6118ff** — `filter_1m_dead_vol` SHADOW + `filter_dead_vol_chart` SHADOW (compound carve-out preserves all winners).
6. **8b2071a** — `filter_premium_shallow_dip` ENFORCED. Block premium-tier when pc_h1 > -10%. Zero winner cost.
7. **9713459** — `filter_zero_winner_compound` ENFORCED (6 OR-rules, zero-winner-block, +$79.17 projected).
8. **29a75fb** — Retired `trigger_deep_dip_bottom` (-$1.03/trade phantom). Added `trigger_deep_1h_dip` (Rule A) + `trigger_power_dip_runner` (Rule D). Shipped `filter_lazy_fade_buy` (Rule B) — block bs_m5∈[1.5,3.0) AND pc_h1>-8% (saves $8.27 of today's $12.31 retroactively).
9. **c406a6f** — `trigger_hot_runner_calm_5m` (n=56/61% WR mining) + `trigger_hot_runner_shallow_1h` (n=48/**67% WR**).

### Queued — NOT YET DEPLOYED

**d64a37b** — `trigger_vol_breakout_flat` (Trigger A from per-token forensic):
- Conditions: `1m_volume_spike ≥ 2.0` AND `shape_30m_chg_pct ∈ [-5%, +5%]` AND `pc_h24 > -10%`
- Mining: 14 hits/24h across watchlist, mean run +27%
- Captures: HENRY +122%, ATTENTION +24%, DEGEN +21%, BABYTROLL +21%, VIRL +18%, PENGUIN +16%, UFO +16%, Goblin +16%, Digi +17%
- Mechanism: flat-base breakout precursor. Existing dip_buy requires pc_h1<0 which excludes this entire regime.

### Mining work products

- `.deep_mine_findings.md` — 49-token universe analysis (Rules A, B, D)
- `.per_token_forensic.md` — per-token forensic, all 50 watchlist (481 lines, 42 live + 8 dead). Surfaced Trigger A + 142-vs-9 capture rate.
- `.rugcheck_v2.json` — on-chain rugcheck for 35 tokens
- `.mined_v3.json`, `.mined_v4.json`, `.mined_10_winners_v2.json` — compound mining results
- `.recurring_signature_cohort.json` — A/B cohort of recurring losers vs winners

### Critical infrastructure event: Railway 4-hour outage

Railway suffered a major outage 22:17 UTC May 19 → ~02:00 UTC May 20 (Google Cloud blocked their account). During:
- 5+ min of `PriceFeed Poll batch HTTP 429` flooded logs 22:08-22:13 UTC pre-outage
- Bot logs went silent at 22:13:34 UTC
- 10 trades completed during window: WR 30%, NET -$5.60
- HERMES "trail confirmed 705s" anomaly (price-feed lag)
- **Clean window (excluding Railway): 15 trades, 67% WR, +$3.12**

Lesson: Railway-window trades are noisy. Exclude from forward audits.

### Late-night spot-check (00:00 UTC May 21)

After c406a6f deploy:
- WORLDCUP buy 02:06, vol_death exit -$0.47 (vol_m5 → $0 mid-hold, bail-out at -3.6%). Sizing standard $10 limited damage. Not a regression — vol died on us post-entry.
- BP open at +$0.08 (67min hold).

### Key research findings

**Top loser pattern (recurring):** "FOMO on non-runner" — losers have median pc_h24=+9% + bs_m5=+2.43 vs winners pc_h24=+71% / bs_m5=+1.51. Drove Rule B (filter_lazy_fade_buy).

**Top winner archetype:** pc_h1 ≤ -22% + big_size signature → PAC R1/R2/R3 generated +$7+ today.

**Mcap stratification — bot bleeds in micro-cap:**
| Tier | n | WR | NET |
|---|---|---|---|
| Micro (<$1M) | 184 | 35% | **-$95.52** |
| Small ($1-5M) | 124 | 42% | -$23.28 |
| Mid ($5-20M) | 33 | 33% | -$6.86 |
| Large (>$20M) | 2 | 50% | -$0.68 |

**On-chain features predictive but post-rugcheck only:** top1_pct<21%, top10_pct<43%, totalHolders<13407, lp_locked_pct≥100. 8 of top-10 mined compound rules use these but can't fire as triggers (only as trader.py filters post-rugcheck — deferred).

**Phantom is RUNNING** (was wrongly called "offline" mid-session). `.live_forward_test/_cron.log` updated through 2026-05-20 01:06 UTC. `^C` at log end suggests it may have been manually killed. Check at next session.

### Triggers retired

- `trigger_deep_dip_bottom` — phantom WR 31%, -$1.03/trade. Fire suppressed; match still stamped for forensics.

### Pending work — for tomorrow / next session

1. **Deploy d64a37b** — `trigger_vol_breakout_flat`. Biggest near-term alpha unlock.
2. **Scanner ingestion gap** — 9 watchlist tokens (HENRY, ATTENTION, MANIFEST, ROUTER, Ebola, SCAM, PENGUIN, HODL, MAGA) had real runs but 0 trades in 9d. Pool-discovery filters likely excluding them. Investigate.
3. **Loosen filter_dumping** — over-blocks Trigger C drawdown-reset bounces (~6 hits/day on Ebola/WIZARD/PAC/SPCX/HERMES cohort).
4. **Ship filter_extended_runner_block** — block pc_h24 > +150% to kill TOLYBOT-class cohort (4/6 losses had pc_h24 > 100%).
5. **Ship 8 on-chain rules in trader.py post-rugcheck filter** — bigger change, deferred. Specifically `oc_top1<21%` + `oc_holders<13407` + `oc_lp>=100` as quality gates.
6. **Restart phantom cron** if dead.
7. **Sample mid-cap cohort** ($5-20M, only 33 trades) for more data — currently can't surface mid-cap-specific rules.

### Memory updates this session

- Confirmed `feedback_no_bandaids` — rejected cooldown-on-loss recommendation. Right fix is upstream entry signal.
- Confirmed `feedback_complete_task_fully` — forensic must cover all 50 tokens, no sampling.
- Confirmed `feedback_time_chart_over_dow` — dropped time-of-day from trigger mining mid-session.

### Caveats / things to watch

- **Heavy ship today (8 commits, 6 ENFORCED filters/triggers)** — projected ~17% combined volume cut. Watch WR/volume forward 24-48h.
- **Mcap micro-cap is structurally losing** — even with new filters, $500k-$1M tokens net -$0.52/trade. Long-term fix needed (mcap floor?).
- **Per-token forensic: 6% capture rate** — even good entries are missing 94% of available alpha. Trigger A is one fix; more needed.

---

## 2026-05-19 — Runner-tilt validation + 4-commit deploy queue (sizing/SHADOW/panic-exit/phantom-parity)

### Session P&L (rough)

~22:00 UTC May 18 → 02:30 UTC May 19. Net session: **~+$8-12 realized**:
- **Winners**: RKC R1 (+$1.20), DEGEN R1 (+$2.52 blended), DEGEN R2 (+$2.45 TP1+TP2, then user manually closed runner at +21.75% peak +33.13%), Buttcoin (+$0.70), VIRL R1 (+~$3.20, ~16% blended on $20), PAC R1 (+$0.07), **PAC R2 (+$3.47, full ladder)**, RKC R2 TP1 partial (+$0.16)
- **Losers**: Goblin -$1.58 (130min dud), FAHHHH v1 -$0.89, FAHHHH v2 -$0.84, TripleT -$0.41
- **VIRL was the biggest single win**: +29.9% peak runner farmed perfectly by runner-tilt ladder (TP1 +5.4% then trail +23.6%)

### Commits made tonight (4 — all pushed, NONE deployed yet)

Held pending close of all positions.

1. **e94015b** — `feat(filter_1m_steep_fall): SHADOW — block 1m_cum_3min < -1.5% entries`
   - Stamps `filter_1m_steep_fall_verdict` into entry_meta for forward validation
   - Validated NET +$12.26 over 4d lifetime backfill
2. **36bd228** — `feat(pre-tp1-trail): panic exit on catastrophic drop (>=6pp from peak)`
   - Tightens existing 60s confirmation to 5s when drop >= 6pp
   - Lifetime data: only 1 case (memecoins -$1.93), saves ~$1.50. Surgical
3. **9499987** — `chore(phantom-parity): wire filter_1m_steep_fall into live_forward_test`
4. **caa8f5c** — `tune(sizing): rebalance tiers — macro_up 1.5x→2x, premium 2x→1x, standard 1x→0.5x`
   - Lifetime + recent data showed sizing inverted relative to outcome
   - Tonight retro: +$2.53 over 6 closed trades

### Key research findings

**Confirmed strong signals:**
- **macro_up sizing cohort** (sol_pc_m1 >= +0.01): WR 45.5%, edge +1.37%. Only profitable cohort. Robust.
- **1m_vol_spike < 0.20**: would save **+$34/4d** at lifetime scale. **Not yet shipped — top candidate for next SHADOW.**
- **1m_cum_3min TWO-sided gate needed**: bot has both mid-fall (3min < -1.5%) AND mid-pump (3min > +3%) entry problems. SHADOW catches first; second is still unhandled.
- **Post-TP1 trail is tight already** (5s confirm). Pre-TP1 was the 60s problem fixed by panic exit.

**Confirmed non-signals:**
- **CNN outcome_prob is NOT predictive at lifetime scale**: winners 0.174 mean vs losers 0.170. Don't gate on CNN.
- **Fusion has small edge but cuts too much volume** as primary gate (≥0.70 = 65% volume cut).
- **SOL macro at entry is weak in recent** (winners sol_pc_m1 median +0.034%, losers 0.000%).

**Trigger combo audit (n=137 closed):**
- **2-compound is worst** (avg -$0.79). 3-compound is sweet spot.
- All individual triggers net-negative except chart_channel_strong, swing_structure_rsi.
- 0% WR combos: `patient_bottom_informed_cluster` (0/4, -$7.99), `clean_break` solo, `whale_conviction` solo.

### Sizing inversion (lifetime + recent confirmed)

| Tier | Old | New (shipped) | WR | Edge |
|---|---|---|---|---|
| premium_runner | 3x | 3x | (n=0) | — |
| **premium** | 2x | **1x** | 28.6% | -2.06% ⚠ |
| **macro_up** | 1.5x | **2x** ✓ | 45.5% | +1.37% ✓ |
| **standard** | 1x | **0.5x** | 14.8% recent | -3.69% ⚠ |
| marginal | 0.5x | 0.5x | 33.3% | -0.39% |

**Premium tier fires from 3 conditions** (not just v_bottom_body): big-trade-size signature (ats≥116, lv≥135, p90≥153), chart_quality_bottom+chart_score_reversal pair, or v_bottom_body. The big-trade-size cohort dominates premium trades.

### Runner-tilt is validated

Tonight 5 separate +10%+ runners farmed: RKC +12.8% peak, DEGEN R1 +11.5%, DEGEN R2 +33.1%, VIRL +29.9%, PAC R2 +16.9%. The TP1 +5% / TP2 +10% / 34% trail ladder caught all of them. **Don't break this.**

### What to do next session

1. **Deploy 4 pending commits** once positions close. (`MSYS_NO_PATHCONV=1 railway up --detach`)
2. **Reset dashboard** after deploy.
3. **Watch SHADOW data** — log `filter_1m_steep_fall_block` count; if "would-block" trade outcomes confirm forward, promote to ENFORCED.
4. **Ship the 1m_vol_spike < 0.20 SHADOW next** — highest-leverage unshipped finding ($34/4d projected).
5. **Consider two-sided 3min gate** — add `1m_cum_3min > +3%` block to catch mid-pump entries.
6. **Watch standard-tier volume** — new 0.5x sizing means trades happen at $10 base. Will WR improve?
7. **Trigger demotion candidates**: `patient_bottom_informed_cluster`, `clean_break` solo, `whale_conviction` solo all 0% WR.

### Memory updates

- Added `reference_cnn_vs_fusion_separator.md`
- Existing constraint: `feedback_railway_cost_cap.md` (under $25/month)

### Caveats

- **Recent WR (25.5%) is WORSE than lifetime (36.4%).** Volume push (filter_quad demote, etc.) is hurting entry quality. Sizing rebalance is partial offset.
- **1m_cum_3min signal flipped between eras**: lifetime had losers more negative; recent has winners more negative. SHADOW still NET-positive on both eras.
- **My local data file is stale (20:36 UTC)** — when re-running backfills, refetch with curl first.
- **DEGEN manual close** at +21.75% (peak +33.13%) — user's choice. Locked $2.85 instead of waiting for trail.

### Open positions at handoff end (2)

- **VIRL R2** (new buy 02:29:40 UTC): $0.001215, currently +~2%
- **RKC R2** (new buy 02:34:42 UTC): $0.003941, TP1 fired +4.7%, 67% trail at +6%

---

## Earlier session notes (preserved)

(Previous handoffs retained below for context.)
