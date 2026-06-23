# Fleet Research Manifest

Generated 2026-06-23. Total bots: 123. The paper fleet was trimmed to the **badday family** for cost (Railway memory/CPU); every other bot is `enabled:false` but its config file is PRESERVED here and in git — re-enable (`"enabled": true`) to resume its research. This manifest records WHAT EACH BOT WAS FOR so the research intent is never lost.

## badday

### `badday_flush` — KEPT-ENABLED (badday family)
BAD-DAY MICROCAP: deep-flush rider (50-500k mcap, age>=6h, rug-screened). Entry = pc_h1<=-20 (the flush state holds 56-59% win10 on bad days universe-wide; MAITIU case: four +12..+19% flushes in 22min, all blocked by the pond stack). DEMAND-TURN PROMOTED 2026-06-23 (net_flow_15s_imbalance>=0): the nf15 A/B lifted $/tr $0.57->$1.07 AND cut max-drawdown -$141->-$96 at n>=321 vs this control, so the clause graduates from A/B into the base — require buyers to have stepped in (15s flow non-negative), NOT still net-selling. Overturns the original 'capitulation IS negative flow' thesis (the demand turn beats raw capitulation on both $/tr and drawdown). PRE-REGISTERED (2026-06-10, badday scorecard judges): >= +$2/tr realized at n>=30 closes on dial-bad days AND catastrophe rate (<=-35% fills) < 10%, else RETIRE. Rug screens from the 3,959-event microcap catastrophe mine: age>=6h cuts 79% of catastrophes (rugs are YOUNG + FRANTIC, not quiet); composite screen 20%->6% catastrophe rate.  [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [06-12: young_token_probe flag REMOVED — vestigial from pre-lane creation; it restricted the family to young tokens while the envelope wants age>=6h. Lane admission + badday_ prefix mandate are the real access path.]

### `badday_flush_convex` — KEPT-ENABLED (badday family)
BAD-DAY MICROCAP: deep-flush rider (50-500k mcap, age>=6h, rug-screened). Entry = pc_h1<=-20 (the flush state holds 56-59% win10 on bad days universe-wide; MAITIU case: four +12..+19% flushes in 22min, all blocked by the pond stack). NO positive-flow requirement — capitulation IS negative flow. PRE-REGISTERED (2026-06-10, badday scorecard judges): >= +$2/tr realized at n>=30 closes on dial-bad days AND catastrophe rate (<=-35% fills) < 10%, else RETIRE. Rug screens from the 3,959-event microcap catastrophe mine: age>=6h cuts 79% of catastrophes (rugs are YOUNG + FRANTIC, not quiet); composite screen 20%->6% catastrophe rate.  FLEET-CONVEX EXPERIMENT (2026-06-11, AxiS: our entries + the elite payoff curve). Same entries as the parent, payoff reshaped to the decoded elite math (51 pct WR, p90 +107 tails, fast cuts): $25 probes, TP1 +5 sells only 10 pct, TP2 +25 sells 20 pct, 70 pct rides the trail, -15 cut (their median loser), -9 fast bail. PRE-REG: judge vs parent head-to-head on $/tr at n>=25 closes each; convex must beat parent or retire. The smart wallets are the existence proof. [DAILY FLOOR $15 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [06-12: young_token_probe flag REMOVED — vestigial from pre-lane creation; it restricted the family to young tokens while the envelope wants age>=6h. Lane admission + badday_ prefix mandate are the real access path.]

### `badday_flush_conviction` — KEPT-ENABLED (badday family)
CONVICTION A/B of badday_flush (2026-06-12 roster decode: ALL 10 profitable wallets size with conviction — dmuX bets 10x the table; our fleet bets flat, every bot). Single variable vs parent: trigger-count sizing (1 + 0.5*(n-1), cap 2x). PRE-REG: judge vs parent $/tr at n>=30 closes each; loser retires. 48h burn-in.

### `badday_flush_conviction_demand` — KEPT-ENABLED (badday family)
DEMAND-GATE A/B of badday_flush_conviction (2026-06-15 win-signature mine). ONE added entry clause: net_flow_15s_imbalance >= 0. From the 442-entry signature, winners enter at +0.63 vs losers -0.10 (buyers stepping in vs still net-selling) -- the strongest single separator (sep 0.91). Tests whether gating OUT still-falling-knife entries (negative 15s flow) cuts losers while keeping the winners. Control = badday_flush_conviction (identical except this clause). PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30 closes; promote the gate to the live baddays ONLY if it lifts $/tr. paper, 48h burn-in.

### `badday_flush_conviction_live` — KEPT-ENABLED (badday family)
CONVICTION A/B of badday_flush (2026-06-12 roster decode: ALL 10 profitable wallets size with conviction — dmuX bets 10x the table; our fleet bets flat, every bot). Single variable vs parent: trigger-count sizing (1 + 0.5*(n-1), cap 2x). PRE-REG: judge vs parent $/tr at n>=30 closes each; loser retires. 48h burn-in.

### `badday_flush_live` — KEPT-ENABLED (badday family)
BAD-DAY MICROCAP: deep-flush rider (50-500k mcap, age>=6h, rug-screened). Entry = pc_h1<=-20 (the flush state holds 56-59% win10 on bad days universe-wide; MAITIU case: four +12..+19% flushes in 22min, all blocked by the pond stack). NO positive-flow requirement — capitulation IS negative flow. PRE-REGISTERED (2026-06-10, badday scorecard judges): >= +$2/tr realized at n>=30 closes on dial-bad days AND catastrophe rate (<=-35% fills) < 10%, else RETIRE. Rug screens from the 3,959-event microcap catastrophe mine: age>=6h cuts 79% of catastrophes (rugs are YOUNG + FRANTIC, not quiet); composite screen 20%->6% catastrophe rate.  [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [06-12: young_token_probe flag REMOVED — vestigial from pre-lane creation; it restricted the family to young tokens while the envelope wants age>=6h. Lane admission + badday_ prefix mandate are the real access path.]

### `badday_flush_nf15` — KEPT-ENABLED (badday family)
WIN-SIG A/B of badday_flush (2026-06-16 campaign). +1 entry clause: net_flow_15s_imbalance >= 0. net_flow_15s>=0 = demand turned (the proven badday find, recurs in 3 bots). Expect: cut ~50% losers / keep 66% winners, WR 56->63%. Control=badday_flush. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to badday_flush only if it lifts $/tr. paper, single-variable A/B.

### `badday_flush_nf15_dense` — KEPT-ENABLED (badday family)
THIN-TAPE-FLOOR A/B of badday_flush_nf15 (2026-06-20 decode). Adds ONE entry clause: net_flow_15s_n >= 3 (min 3 trades in the 15s demand window). Drops the saturated net_flow_15s_imbalance==1.0 thin-tape trap (median 1 trade = noise, 45% WR / -1.88%). Empirically nf15>=0 AND net_flow_15s_n>=3 = 67% WR / +3.58% median vs 55%/+1.85% for plain nf15>=0, keeping 59% of entries. Control = badday_flush_nf15 (identical except this clause; flat sizing, no conviction). PRE-REG: forward-judge per-trade pnl_pct at n>=30 vs badday_flush_nf15; promote the clause to badday_flush_nf15 only if it lifts $/tr. paper, single-variable A/B.

### `badday_flush_nf15_live` — KEPT-ENABLED (badday family)
LIVE bot (chosen 2026-06-20) = badday_flush_nf15 (FLAT $100 + net_flow_15s_imbalance >= 0 demand gate). Decode of today's drawdown profile: nf15-flat has the BEST drawdown of the badday family (worst trade -$18, INSIDE AxiS's -$21 pain line; ~half the give-back of the base bots) AND its $/tr edge is REAL SELECTION, not leverage. FLAT sizing only â€” NO conviction sizing. Replaces badday_flush_conviction_live (whose trigger-count conviction sizing reintroduced ~1.5-1.6x deeper $ drawdown). Routes real money ONLY under the existing PAPER_MODE=false + live_probe + key state. Reversible: flip live_probe=false to retire.

### `badday_momo` — badday (disabled in config)
BAD-DAY MICROCAP: momentum-wave rider (50-500k mcap, age>=6h, rug-screened). Entry = pc_h1>=+30 already-running (63% win10 on bad days) + moderate buy pressure bs_m5 1.2-2.0 (54% — the frantic >2 band is the rug signature). PRE-REGISTERED (2026-06-10, badday scorecard judges): >= +$2/tr realized at n>=30 closes on dial-bad days AND catastrophe rate (<=-35% fills) < 10%, else RETIRE. Rug screens from the 3,959-event microcap catastrophe mine: age>=6h cuts 79% of catastrophes (rugs are YOUNG + FRANTIC, not quiet); composite screen 20%->6% catastrophe rate.  [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [06-12: young_token_probe flag REMOVED — vestigial from pre-lane creation; it restricted the family to young tokens while the envelope wants age>=6h. Lane admission + badday_ prefix mandate are the real access path.] [RETIRED 2026-06-12 by AxiS: FAILED its pre-reg — 35 closes at -$2.34/tr vs the >=+$2/tr bar. The momo envelope side (pc_h1>=+30 chasing) loses; the flush side carries the family.]

## baseline

### `baseline_v1` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Baseline (current production)

## champion

### `champion_defender_2k` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
$2k Defended Turnover — cap2k_turnover spine ($650x3 flat, TP1 75%@+5 / 25%@+10, trail3, stop-12, bleed45, flat-exit-30m) PLUS the 8-filter layered defender + stall-exit(90m). 7h-watch rec #1: cap2k was the biggest loss source (-$757, mostly the TOESCOIN correlated cluster) yet carries real size; defenders had 0 losses but only $20. This puts the protection on the size-carrying spine. cap2k_turnover stays as the unfiltered control for the A/B. [2026-06-08 BACKSTOP: base_position_usd 650->100 â€” the 8-agent failure audit found this bot's size was 29-72% of the entire fleet dollar bleed; capping to fleet-typical $100 removes ~$388 of bleed at near-zero winner cost.]

### `champion_defender_2k_trendbreak5m` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG RESCUE A/B of champion_defender_2k (2026-06-16 campaign C3, a LOSING control). +1 entry clause: chart_trendline_5m_breakdown >= 1. require a confirmed 5m trendline breakdown (real flush); cd_2k bleeds buying mid-trend. RESCUE: kept -1.125->+0.965 (n463, -$593 biggest loser). >=1 gates True-vs-False; ~5% None fail-open. Control=champion_defender_2k. PRE-REG: forward-judge kept-subset $/tr vs control at clone n>=30; promote clause to champion_defender_2k only if it flips $/tr positive with no catastrophe regression. paper, single-variable A/B.

### `champion_defender_btc` — already-disabled
Defender BTC overheat (btc_pc_h4>+0.5)

### `champion_defender_falling_pump` — already-disabled
Defender G1 only (pc_h6>=10 AND pc_h1<=-5 AND age>24h)

### `champion_defender_fusion` — already-disabled
Defender fusion floor (fusion_constrained_score_shadow<0.40)

### `champion_defender_v3` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Layered defender (6 filters) — production-successor candidate, held-out 4x lift

### `champion_defender_v3_pch1le5` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG RESCUE A/B of champion_defender_v3 (2026-06-16 campaign C3, a LOSING control). +1 entry clause: pc_h1_lookback <= 5. anti-euphoria: h1 flat-to-down, not chasing pumps. RESCUE: -0.07->+4.18; n38 thin -> shadow lead. Control=champion_defender_v3. PRE-REG: forward-judge kept-subset $/tr vs control at clone n>=30; promote clause to champion_defender_v3 only if it flips $/tr positive with no catastrophe regression. paper, single-variable A/B.

### `champion_defender_v4` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Defender v4 (WR-preserving) — v3's full 9-filter stack + vol_accel, but NO stall_exit/flat_exit. A/B vs v3: tests whether dropping the corpse-booking recycle exits recovers btc-like WR (75%+) now that vol_accel filters most never-green corpses at ENTRY. v3 keeps stall_exit as the control.

### `champion_defender_v4_trend60r020` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG RESCUE A/B of champion_defender_v4 (2026-06-16 campaign C3, a LOSING control). +1 entry clause: trend_60m_r_squared >= 0.2. structured 60m trend (same axis as no_filters). window-positive purification (weaker evidence, lifetime bleed outside window); forward-judge. Control=champion_defender_v4. PRE-REG: forward-judge kept-subset $/tr vs control at clone n>=30; promote clause to champion_defender_v4 only if it flips $/tr positive with no catastrophe regression. paper, single-variable A/B.

### `champion_defender_volaccel` — already-disabled
Defender vol-accel (filter_dead_volume only) — single-filter isolation probe for the held-out entry gate vol_h1_accel_vs_h6>=0.70. Starts at a clean zero so its $/trade vs baseline measures the filter's standalone contribution.

### `champion_minimal` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
MINIMAL +EV candidate (2026-05-31) — isolates the TWO components that carry the fleet's edge: filter_dead_volume (the volume-freshness gate shared by EVERY +EV defender bot — volaccel/btc/falling_pump/post_peak/v3 all enforce it) + entry_gate time_since_h24_peak_secs>=14400 (enter only >=4h after the 24h peak — anti-top timing, the constructive form of filter_extended_chase). NOTHING redundant: no other defender filters (btc_overheat/falling_pump were INERT this regime), no ng_scorer, no stall — a clean test of whether the 2 carrying gates alone reproduce post_peak's +EV (post_peak's full 10-filter stack +$6.27 does NOT beat single-filter volaccel +$7.97, so the extra filters add ~nothing). Default ladder (swept-optimal), $20 base (sizing only amplifies, never tested up). JUDGE at n>=50 via /api/leaderboard realized_pnl_total_usd (NOT /api/trades). Control = baseline_v1.

### `champion_minimal_avgbuy80` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of champion_minimal (2026-06-16 campaign C2). +1 entry clause: rt_avg_buy_usd <= 80. froth filter: small recent avg buy size (wins quiet, loses in froth). Expect: kept WR68->84.6; n=19 shadow. Control=champion_minimal. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to champion_minimal only if it lifts $/tr. paper, single-variable A/B.

### `champion_post_peak` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Post-peak positive-selection — champion_defender_v4's EXACT stack (10-filter defender + WR-preserving exits, no stall), single variable = entry_gate time_since_h24_peak_secs>=14400 (only enter a token >=4h AFTER its 24h peak — well past the top, into the post-peak base; NOT a near-top extended runner). Directly targets the recurring 'bot buys extended runners near local tops' complaint. Broad held-out+worst-day feature scan survivor (2026-05-29): the relationship is NON-monotonic (mid-range Q3 ~1.7-4.2h since peak is a TEST disaster, 27% WR) but the >=4h cohort is uniformly strong and +$/tr-positive in BOTH regimes: TRAIN 64% WR +$0.20/tr, TEST 76% WR +$0.78/tr, and on the TRUE worst fleet day 05-27 (fleet 23% WR) 43% (+20pp) [correction: an earlier draft mislabeled the worst day as 05-28/87%; 05-28 was actually an easy 58% day]. Robust across the 4-5h threshold plateau. ~48% overlaps the top_buy_makers_n<9 whale gate, so a genuinely orthogonal axis. triggers open so the post-peak gate is the isolated variable. A/B control = champion_defender_v4. Judge n>=50 --unrealized.

### `champion_premium` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Premium positive-selection  -  champion_defender_v4's EXACT stack (10-filter defender + WR-preserving exits, no stall) with ONE variable changed: triggers_allowed restricted to 4 BAD-DAY-ROBUST premium triggers. Selection method: systematic per-trigger held-out scan (train 05-16..24 / test 05-27..29) kept only triggers beating fleet WR in BOTH windows, then a per-day stability gate kept only those that ALSO beat fleet on the TRUE worst fleet day 05-27 (fleet 23% WR) [correction: 05-28 was mislabeled as worst; it was an easy 58% day]. Survivors: power_dip_runner (87/72 WR across windows; robust on the true worst day 05-27), chart_quality_bottom (34%; big vol; +$2.65/tr test), deep_1h_dip (32%; 76/55 WR), pullback_in_uptrend (60%; 91/59). REJECTED as good-day artifacts (flashy test-$/tr but cratered on the brutal day): demand_burst_no_crash 14%, liq_velocity 12%, 1s_capit_reversal 6%, net_flow_5m_demand 17%. Converting the WR edge to +$/tr is the defender stack + tight exits' job, measured FORWARD. A/B control = champion_defender_v4. Judge n>=50 --unrealized.

### `champion_premium_dip90m` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of champion_premium (2026-06-16 campaign). +1 entry clause: shape_90m_drawdown_from_max_pct <= -16.0. deeper dip off the 90m high (n=411, monotonic). Expect: WR 56->63.9%, keeps 61% winners. Control=champion_premium. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to champion_premium only if it lifts $/tr. paper, single-variable A/B.

### `champion_premium_fresh` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Premium positive-selection + FRESHNESS gate. Exact clone of champion_premium (v4 10-filter stack + 4 held-out triggers: deep_1h_dip, pullback_in_uptrend, power_dip_runner, chart_quality_bottom) with ONE addition: entry_gate 1m_volume_spike>=0.40 AND 1m_cum_3min_pct>=-3 (the standing lagging-feature freshness rule the premium triggers never adopted). Held-out 2026-05-30 across all 4 triggers (n=1060): PASS-cohort never-green 25%->14%, $/tr +0.52->+3.82, fade-regime TEST flips -0.53->+5.80. Cuts ~46% volume (selectivity). A/B: champion_premium = pure control (no freshness); this isolates the freshness gate's marginal effect. Diagnosed from the ATTENTION never-green loss (vol_spike 0.19, cum_3min -8.53 - would be blocked) vs LOA winner (6.43/-2.22 - passes).

### `champion_premium_tightexit` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Tight-exit A/B of champion_premium_fresh (IDENTICAL stack+freshness gate+ng_scorer_gate) with ONE change: tighter exit to plug the trailing-stop leak. trail_pp 3.0->1.5, tp2_pct 10->7 (lock the trailing portion before the trail gives it back). Calibrated 2026-05-31: live trail exits peak 6.6%->realized 0.7% = 5.3pp give-back, 11% capture (n=480 clean). Runner cost tiny (3/613 trail exits peaked >=15%). A/B vs champion_premium_fresh isolates exit aggressiveness -> tests whether capturing the leak beats the whipsaw cost (unquantifiable offline). Forward-judge avg_win + EV at n>=50.

### `champion_premium_tightexit_reaccum` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of champion_premium_tightexit (2026-06-16 campaign). +1 entry clause: chart_reaccum_drawdown_pct <= 16.0. dont buy a re-accum already bled >16% from trough. Expect: n=13 UNDERPOWERED - directional shadow, judge n>=20-30. Control=champion_premium_tightexit. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to champion_premium_tightexit only if it lifts $/tr. paper, single-variable A/B.

### `champion_proposal` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
Champion Proposal (synthesis v1)

### `champion_whale_buyers` — already-disabled
Concentrated-whale positive-selection — champion_defender_v4's EXACT stack (10-filter defender + WR-preserving exits, no stall), single variable = entry_gate top_buy_makers_n<=8 (concentrated whales, NOT a FOMO crowd). Strongest held-out signal of the 2026-05-29 feature-gate scan: beats fleet WR in BOTH windows AND on the brutal 05-28 day, AND is +$/tr-positive in both regimes (the only signal that held $/tr through the bad regime). top_buy_makers_n<9: TRAIN 69% WR +$1.17/tr, TEST 63% WR +$1.82/tr, and on the TRUE worst fleet day 05-27 (fleet 23% WR) ~43% (+20pp) [correction: an earlier draft mislabeled the worst day as 05-28/78%; 05-28 was actually an easy 58% day]. The >=9 FOMO crowd is the loser (53/43 WR, -$4.97/-$3.13). concurrent_positions>=2 'hot streak' thesis was REJECTED (collapsed to TEST 48%/-$5.93). triggers open so the whale gate is the isolated variable. A/B control = champion_defender_v4. Judge n>=50 --unrealized.

## deepflush

### `deepflush_timebox` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
DEEP-FLUSH TIME-BOX (wallet-decode build B, 2026-06-13): encodes the DaxfeJKe drawdown-winner signature (the 'Dw5' 6-min time-boxer; recurred = hit 2 of the period's runners). Agent-decoded entry: DEEP-FLUSH CAPITULATION buy on micro-liq young tokens â€” dd90 median -30% (gate -16, deeper passes), vol_spike median 7.5x (gate >=3.0), liq median $22k (admitted via trigger_volume_burst_runner's liq<=$21.3k micro-liq lane), age ~19h. The EDGE IS THE EXIT: it buys the same deep-flush state every time and lets a ~6-min TIME-BOX carry it (dip-depth does NOT separate its own winners from losers; loss-median tight -7.9% because the box caps bleeders). CONFIRMS+EXTENDS our deep-dip edge at a deeper threshold; this is a CAPITULATION-reversal entry (sellers winning + volume burst), the OPPOSITE timing philosophy to bs_m5 demand-confirmation. young_token_probe=true for the rug-gate unique_buyers_n==0 exemption (fresh micro-liq) + young admission; entry_stack_exempt (the stack's age>=24h/mcap>=500k is the structural opposite of this pond). time_stop_minutes=6 IS the mechanism. Forward-judge per-trade % at n>=30; expect low WR + tight losses + fast turnover.

### `deepflush_timebox_bottom1s` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of deepflush_timebox (2026-06-16 campaign). +1 entry clause: 1s_bottom_score >= 20. aligns with the bot's native bottom trigger (winner 35 vs loser 10). Expect: WR-inside 88 vs 69; n=29 so shadow-judge at n>=30. Control=deepflush_timebox. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to deepflush_timebox only if it lifts $/tr. paper, single-variable A/B.

### `deepflush_timebox_h6peak` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of deepflush_timebox (2026-06-16 campaign). +1 entry clause: time_since_h6_peak_secs <= 9000. recent-h6-peak flushes recover, stale ones bleed (parallel deepflush arm). Expect: WR-inside 79 vs 69, higher winner-retention; n=29 shadow. Control=deepflush_timebox. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to deepflush_timebox only if it lifts $/tr. paper, single-variable A/B.

### `deepflush_timebox_live` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
DEEP-FLUSH TIME-BOX (wallet-decode build B, 2026-06-13): encodes the DaxfeJKe drawdown-winner signature (the 'Dw5' 6-min time-boxer; recurred = hit 2 of the period's runners). Agent-decoded entry: DEEP-FLUSH CAPITULATION buy on micro-liq young tokens â€” dd90 median -30% (gate -16, deeper passes), vol_spike median 7.5x (gate >=3.0), liq median $22k (admitted via trigger_volume_burst_runner's liq<=$21.3k micro-liq lane), age ~19h. The EDGE IS THE EXIT: it buys the same deep-flush state every time and lets a ~6-min TIME-BOX carry it (dip-depth does NOT separate its own winners from losers; loss-median tight -7.9% because the box caps bleeders). CONFIRMS+EXTENDS our deep-dip edge at a deeper threshold; this is a CAPITULATION-reversal entry (sellers winning + volume burst), the OPPOSITE timing philosophy to bs_m5 demand-confirmation. young_token_probe=true for the rug-gate unique_buyers_n==0 exemption (fresh micro-liq) + young admission; entry_stack_exempt (the stack's age>=24h/mcap>=500k is the structural opposite of this pond). time_stop_minutes=6 IS the mechanism. Forward-judge per-trade % at n>=30; expect low WR + tight losses + fast turnover.

## meta

### `meta_chameleon` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
META CHAMELEON (2026-06-12, AxiS): the fixed DYNAMIC bot — its three exit-geometry dials (time_stop_minutes / tp1_pct / hard_stop_pct) retune automatically to the day's winning wallet ARCHETYPE measured by the meta sensor (panel of 17 decoded wallets, free PumpPortal stream). Quiesce: tunes apply only on a flat book; cadence max 1/6h; clamps ts[10,780]m tp1[8,60]% stop[-60,-10]%; size/lanes/filters FROZEN — it changes shape, not exposure. Defaults = the Dw5 timebox geometry until the sensor's first qualifying read. PRE-REG: judge vs timebox_probe at n>=30 closes; 48h burn-in label. [2026-06-13 watch: liq floor 15k->25k — copies gap/slip in thin books (smart_follow held-out lesson); targets the -17/-20% gap-through-stop losses on first conviction trades] [A: tp1 banks 60% at meta-win, trail 8pp rides 40% for the tail (2026-06-13)]

## momentum

### `momentum_grad_probe` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
FRESH-GRADUATION MOMENTUM pool (2026-06-03) — pool_a-style 10-slot no-same-token pool. Tests the pre-peak-capture thesis (VERIFIED: the fleet buys ~97% of pumps POST-peak / catches the fade; the rare pre-peak catches = TREND/SPCX/GACHA = the entire edge). momentum_mode (bypasses the dip filter+trigger stack) + young_token_probe (age < YOUNG_TOKEN_MAX_AGE_H) + a DATA-CALIBRATED order-flow entry_gate = enter the RISING leg of fresh pumps, NOT the post-peak dip the dip-stack waits for. CALIBRATION (5-agent fresh-grad deep dive 2026-06-03): the dominant pumper-vs-dumper separator is net_flow_60s_imbalance (Cohen d=+0.98, GOOD med +0.51 vs BAD +0.03); the originally-GUESSED candle gate was WRONG (1m_cum_3min_pct d=-0.30, BACKWARDS sign — retired). Age tightened 6h->3h: known pumpers surge in hour 0 (median 2.9x) and 12/14 drop >=20% off peak within 1 HOUR; the runnable window is ~hours 0-3, after which you enter the fade. 10 concurrent slots (exclusion_pool=momentum_grad, no two slots on the same token) = max fresh-graduation capture throughput (pumps are abundant; fills/data-accrual is the bottleneck). DATA-INFORMED exits (base-rate deep dive wf_62b8f392): ~70% of grads RUG to near-zero (median retains 2-3% of peak), so SURVIVAL is an exit problem -> dominant lever is TIME-based downside protection (stall_exit 40min/peak<12, never_runner 35min, slow_bleed 35min) to bail the rug fast; TP ladder kept wide-ish (tp1 20/0.4, tp2 60/0.3, trail 8 on 0.3) because the tail is fat (~17% hit 5x, ~8% hit 10x) and tight TP would cap the EV. EXACT exit numbers are PRELIMINARY defaults (directional from base rate); refine from the probe's own forward fills. $2k paper, $100/slot ($1k max deployed). OHLCV_CAPTURE_SIDECAR records the first-minute paths. Needs YOUNG_TOKEN_PROBE=1. FORWARD-JUDGE: (1) do its entries land PRE-peak (vs the fleet's 97% post-peak), (2) does early momentum predict continued pump, (3) realized vs the dip fleet.

### `momentum_pump_tight` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
MOMENTUM-PUMP-TIGHT (2026-06-14, regime-entry-hunt): the ONLY positive entry that survived a gate-independent forward test in the live SOL-pump tape â€” tight momentum-CONTINUATION (pc_h1 20-60 = committed but PRE-parabolic, liq>=30k HARD floor [the 20-30k band was the RUG pocket WR 0.44/-22%], mcap 80k-3M [winners cluster 140-600k], 1m volume surge). momentum_mode bypasses the dip-filter stack because our dip-buying INVERTS + bleeds in euphoria (dip entries WR 0.42 / ~-$341 net = the window bleed). THIN: n=11 fills / ~6 distinct winner tokens (AIELON = half), single 6.5h pump day â€” PAPER shadow, judge at n>=30 DISTINCT tokens vs momentum_shadow, promote only if >=+$2/tr AND catastrophe(<=-35%) rate <10%, NO $ projection. CORRECTED 2026-06-14 (0-fire diagnosis): the BINDER was age_h_max=24 (0/3 live momentum candidates passed) + mcap_max=3M (only 1/3, candidates median $4.7M). The n=11 hunt sample was over-fit to a microcap pump day; the REAL momentum-continuation pond (verified vs live momentum_shadow candidates) is MATURE (>24h) + bigger-cap (median mcap $4.7M, median liq $322k), NOT the $100-160k thin-liq RUG pond the chameleon green-default keeps buying (SPIRPIX $142k->$3.5k). Fix: age_h_max 24->null, mcap_max 3M->10M; kept liq>=30k + mcap_min 80k (both 100%-pass). CAVEAT: no in-config SOL-regime gate yet (fires every regime; the SOL-pump-conditional version = chameleon GREEN mode). $50 fixed paper, daily $30.

### `momentum_shadow` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
MOMENTUM-continuation shadow (#4.3): the strongest DECORRELATED lead from the broad sweep (+14pp WR, both regimes; 100% of momentum candidates are blocked by the dip-filter stack). momentum_mode bypasses the dip filters+triggers and enters on pc_h1>=20 AND pct_above_vwap_h24<=20 (no blow-off chase) AND 1m_volume_spike>=0.40 (freshness). Wider exits than the dip ladder to capture continuation asymmetry. $100 fixed, paper. Forward-judge WR + decorrelation vs the dip fleet. [GAP-THROUGH GUARDS 2026-06-10: 13 hard stops filled avg -15.6% on the -12 stop. giveback floor (peak>=+4 -> exit -6, pre-TP1) + fast-dump bail (-9 any volume, pre-TP1). Judge: avg loss on stopped positions should compress toward -7..-10; watch for TP1 winner-kill (positions exited -6..-9 that would have TP1d).] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

## no_filters

### `no_filters` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
No filters enforced

### `no_filters_trend60r040` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG RESCUE A/B of no_filters (2026-06-16 campaign C3, a LOSING control). +1 entry clause: trend_60m_r_squared >= 0.4. clean linear 60m trend (most recurrent rescue axis, 4/8 bots). RESCUE: -2.97->+0.51, WR43->61, 100% cov. Control=no_filters. PRE-REG: forward-judge kept-subset $/tr vs control at clone n>=30; promote clause to no_filters only if it flips $/tr positive with no catastrophe regression. paper, single-variable A/B.

## other

### `champ_conviction` — already-disabled
Champ Conviction

### `champ_reentry_throttle` — already-disabled
Champ Reentry Throttle

### `champ_regime_rider` — already-disabled
Champion C - Regime Rider

### `champ_runner` — already-disabled
Champion E - Runner

### `champ_sniper` — already-disabled
Champion A - Sniper

### `champ_specialist` — already-disabled
Champion D - Specialist

### `champ_velocity` — already-disabled
Champ Velocity

### `champ_workhorse` — already-disabled
Champion B - Workhorse

### `compound_winners_only` — already-disabled
Compounding (winners only, no shrink)

### `deep_dip_only` — already-disabled
Deep dip only (retrace + buyer return triggers)

### `drawdown_freeze` — already-disabled
Drawdown freeze (pause when realized <= -$100)

### `flow_only` — already-disabled
Flow only (5m fresh-demand triggers)

### `macro_conditional` — already-disabled
Macro-conditional sizing (sol_pc_h6 gradient)

### `no_sol_gate` — already-disabled
No SOL macro gate

### `one_sec_only` — already-disabled
1s cascade only (ultra-fast reversal triggers)

### `psych_h24_100` — already-disabled
mcap_psych pc_h24 max 100

### `regime_aware_bullish` — already-disabled
Regime-aware bullish (sol+btc h1>=0)

### `runner_tilt_aggressive` — already-disabled
Runner tilt aggressive (TP1+8/33, TP2+20/33, trail 4pp)

### `sol_h6_loose` — already-disabled
sol_h6 sweep loose (-0.1)

### `stop_8` — already-disabled
Stop -8% (tighter than tight_stop)

### `tod_morning` — already-disabled
Morning UTC (6-12 — EU morning / US pre-open)

### `tp_runner` — already-disabled
TP runner (8% TP1, 20% TP2)

### `vol_min_500` — already-disabled
vol_h1_min sweep 500

### `vol_min_5k` — already-disabled
vol_h1_min sweep 5k

### `wide_stop` — already-disabled
Wide stop (-20%)

## pond

### `pond_bb_mtf` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO #5 (capacity, mission 2026-06-09): bb_low + mtf_neg (bb_pos_15m<=0.452 AND chart_mtf_score<=-0.01) ON TOP of fleet entry stack. Token-day held-out: first half +4.86%/tok 82% WR, second +3.20%/66% (n=76 token-days, ~5.4 tok/day = ~+$22/day @ $100). Widest validated unshipped Pareto point; shipped to close the $100/day capacity gap (3-clone pond capacity was only ~$46/day). Trade-level test was diluted by pile-ons (+$0.29/tr) — token-level is what a 1-position/token clone experiences. Judge vs pool_a_stack + pond family at n>=50 distinct tokens. [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_flow_thin` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO #6 (capacity, mission 2026-06-09): flow_imbal + thin_book pair (net_flow_60s_imbalance>=0.365 AND slip_buy_2000_pct>=1.709) ON TOP of fleet entry stack. Token-day held-out: first half +0.55%/60% (weak but positive), second +3.85%/68% (~4.4 tok/day = ~+$10/day @ $100). WEAKEST evidence of the pond family — explicitly a capacity experiment; cut fast if forward WR <60% at n>=30 tokens. Judge vs pond_settled_flow_thin (its superset). [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_settled_flow` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO: settled_dip + flow_imbal (shape_30m_mins_since_max>=23 AND net_flow_60s_imbalance>=0.365) ON TOP of the fleet entry stack. 2026-06-09 in-pond mine, held-out TEST: WR 80%, +$2.05/tr, n=217 across 26 DISTINCT tokens (~16 tr/day) — the diversity-ROBUST anchor of the pond family (26 tokens vs the triple's 14). Thesis: settled flush + buy-flow dominance. Forward-judge vs pond_settled_flow_thin (does thin_book earn its throughput cost?) and pool_a_stack control. [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_settled_flow_solcap` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO + SOL-GREEN CAP: pond_settled_flow (settled_dip + flow_imbal) PLUS sol_pc_h1<=0.3 — direct A/B vs pond_settled_flow to measure the green-side cap. 2026-06-09 in-pond SOL audit: sol_h1>+0.3 was the pond's WORST cohort in BOTH halves (train 46% WR/-$2.27/tr, test 62%/-$0.25) but magnitude is unstable (saves +$1074 train vs +$110 test) -> A/B, not fleet enforce. Modestly-red SOL (-0.7..-0.3) = best band (79% WR +$2.32, confirms day-level regime at trade level). Existing red-side gate (h1<-0.7) left untouched. Judge vs pond_settled_flow at n>=50 distinct tokens; if the cap wins, roll into the other pond clones; if marginal (test-half truth), drop. [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_settled_flow_thin` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO: settled_dip + flow_imbal + thin_book (shape_30m_mins_since_max>=23 AND net_flow_60s_imbalance>=0.365 AND slip_buy_2000_pct>=1.709) ON TOP of the fleet entry stack. 2026-06-09 in-pond mine, held-out TEST: WR 86% (+21pp vs pond 65%), +$2.94/tr (23x pond +$0.13), n=153 across 14 tokens (~11 tr/day fleet-wide). CAUTION: 14-token diversity = pseudo-replication risk; judge vs pond_settled_flow (the robustness anchor). Thesis: buy the SETTLED flush (peak >23min old) with buy-flow DOMINANCE into a THIN book (slip>1.7% = real capitulation). Forward-judge per-trade % + $ vs pool_a_stack control at n>=50 distinct tokens. [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_sweep_deep_thin` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO: sweep_reclaim + deep_60m + thin_book (chart_sweep_5m_low_candles_ago>=1.01 AND shape_60m_chg_pct<=-12.11 AND slip_buy_2000_pct>=1.709) ON TOP of the fleet entry stack. 2026-06-10 frontier re-pull, held-out TEST: WR 82% (train 89%), +$3.85/tr (BEST dollar density on the frontier), n=60 across 11 tokens (~4 tr/day fleet-wide). CAUTION: 11-token diversity = pseudo-replication risk - fast-cut if forward WR<60% by n=25 closes or net negative at n>=15 distinct tokens. Thesis: swept-and-reclaimed low + deep 60m leg + thin book = maximal capitulation. triggers_allowed=ALL. [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_sweep_flow` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POND COMBO: sweep_reclaim + flow_imbal (chart_sweep_5m_low_candles_ago>=1.01 AND net_flow_60s_imbalance>=0.365) ON TOP of the fleet entry stack. 2026-06-10 frontier re-pull, held-out TEST: WR 84% (train 90%), +$1.11/tr, n=93 across 15 tokens (~7 tr/day fleet-wide). NEW AXIS: sweep-reclaim (5m low swept >1 candle ago and reclaimed = the flush already happened) with buy-flow dominance. Forward-judge vs pool_a_stack at n>=50 distinct tokens. triggers_allowed=ALL. [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pond_ugly_mtf` — already-disabled
POND COMBO: ugly_chart + mtf_neg (chart_score<=49.9 AND chart_mtf_score<=-0.01) ON TOP of the fleet entry stack. 2026-06-09 in-pond mine, held-out TEST: WR 81%, +$1.79/tr, n=161 across 22 tokens (~12 tr/day). ORTHOGONAL family to settled_flow: the 'good entries look BAD at the bottom' theme — low chart score + negative MTF at a deep-dip+flow entry marks the real bottom (same finding that made fear-filters harmful post-stack). Forward-judge vs the settled_flow pair + pool_a_stack control. [2026-06-09 mission fix: triggers_allowed=ALL — combos were mined/validated on ALL stack-passing entries; the inherited 4-trigger whitelist cut reach to 62% of the validated cohort.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [RETIRED 2026-06-12 by AxiS: 'they cant prove themselves' — 1 fire/48h each; held-out-validated combos too narrow to ever reach their n>=50 pre-reg. A validated gate that fires monthly is a museum piece.]

### `pond_ugly_rsi` — already-disabled
POND COMBO: ugly_chart + rsi15_os (chart_score<=49.9 AND rsi_15m<=48.6) ON TOP of the fleet entry stack. 2026-06-10 frontier re-pull of the 06-09 in-pond mine, held-out TEST: WR 82% (train 84%), +$2.40/tr, n=135 across 17 tokens (~10 tr/day fleet-wide). NEW AXIS vs shipped clones: 15m RSI oversold. Thesis: the ugly-chart paradox (low chart_score = capitulation, not weakness) confirmed by 15m RSI oversold. Forward-judge vs pool_a_stack + pond_ugly_mtf at n>=50 distinct tokens. triggers_allowed=ALL (combos mined on all stack-passing entries). [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [RETIRED 2026-06-12 by AxiS: 'they cant prove themselves' — 1 fire/48h each; held-out-validated combos too narrow to ever reach their n>=50 pre-reg. A validated gate that fires monthly is a museum piece.]

## pool_a

### `pool_a_aged_scalper` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
AGED-SCALPER (wallet-decode build A, 2026-06-13): encodes the C3zPgDxqJ drawdown-winner signature (73% WR, ~3.6h discretion-held scalper, +11.6% win-median / -16% loss-median). Agent-decoded entry (100% coverage, 20 winners): buys AGED survivors (17/17 winners >24h, median ~10 DAYS old) on a -13 to -16% dip off the 90m HIGH (winners -15.8% vs its own losers -11.8%; split vanishes at 60m -> validates the 90m window), in the $100k-$1M mcap band. EXTENDS pool_a_dipgate: its -16 gate REJECTS half C3zP's winners (cluster -13..-16), so this variant uses dd90<=-13 + an age>=24h floor + mcap 100k-1M. entry_stack_exempt because the stack's mcap>=500k floor would silence the $100k-$500k part of the band. Exits widened to C3zP's geometry (hard -16, tp +10/+15, ~3.6h holds). Forward-judge per-trade % vs pool_a_dipgate (control, -16 no-floor). In-sample thresholds optimistic; the A/B is the proof.

### `pool_a_broad_control` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A BROAD CONTROL (2026-06-08): pool_a_candidate spine, SAME broad 40-trigger universe as pool_a_broad_trigstate but NO token-state gates (fires on any allowed trigger, one-size-fits-all). Matched ungated baseline for the broad per-trigger-state A/B. Own exclusion_pool. Expected to bleed more / win less if AxiS's thesis holds. See reference_per_trigger_state_conditioning_2026_06_08.

### `pool_a_broad_control_nf60imb0` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG RESCUE A/B of pool_a_broad_control (2026-06-16 campaign C3, a LOSING control). +1 entry clause: net_flow_60s_imbalance >= 0. non-negative 60s buy-flow imbalance (recurring family). RESCUE: -1.466->+0.973, WR36->54, 100% cov. Control=pool_a_broad_control. PRE-REG: forward-judge kept-subset $/tr vs control at clone n>=30; promote clause to pool_a_broad_control only if it flips $/tr positive with no catastrophe regression. paper, single-variable A/B.

### `pool_a_broad_trigstate` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A BROAD TRIGGER-STATE (2026-06-08): pool_a_candidate spine, broad trigger universe (40 triggers) WITH per-trigger token-state gates. 40 archetype triggers, each held-out-validated in BOTH date folds (May26-31 & Jun1-8) over 1560 entries / 7-Opus mine. Excludes phantom-$ (low_buy_slip, demand_bottom_compound = SIZE lever not entry gate) + dormant/under-sampled (beta_retailfresh, concurrent_alpha, etc). Gate feature coverage >=97% so every gate ACTS (keep-rate 10-59%). See reference_per_trigger_state_conditioning_2026_06_08. A/B against pool_a_broad_control (SAME triggers, NO gates) -> isolates the gating effect across the full archetype set. Own exclusion_pool (independent eval). Forward-judge n>=50 compare_bots --unrealized.

### `pool_a_bsm5` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A + BUY-SIDE-FLOW GATE (A/B treatment of pool_a_candidate). IDENTICAL to pool_a_candidate except entry_gate adds bs_m5>=1.5 (5m buy/sell trade-count ratio = buy-side demand confirmation). Encodes the 2026-06-07 held-out entry-signature mine's STRONGEST separator: bs_m5 TEST AUC 0.661 core / 0.600 fleet, ~98% coverage, survives both passes; alone flips core family -2.11%->~break-even (+1.9pp), keeps 54% of volume. Pairs with pool_a_dipgate (dip-depth) for parallel single-variable entry A/Bs vs pool_a_candidate control. Own exclusion_pool=pool_a_bsm5. entry_gate fail-OPEN if bs_m5 missing (~2%). Forward-judge per-trade % vs pool_a_candidate; in-sample lift optimistic (short test window), forward A/B is the proof.

### `pool_a_candidate` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A shadow (Design A): candidate (premium_tightexit) spine, $100 fixed, 7 selective slots (slot-sweep 2026-06-05: capital efficiency + per-trade quality degrade past ~7; 7 = sweet spot for the profit-sweep float). Tests the user's original idea â€” ONE strategy, many no-same-token slots (de-concentration). Multipliers neutralized to 1.0 so every position is exactly $100 (hard cap). exclusion_pool=pool_a. Forward-judge eqw/day + stability vs pool_c. [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pool_a_candidate_shape30dd` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_a_candidate (2026-06-16 campaign C2). +1 entry clause: shape_30m_drawdown_from_max_pct <= -10. deeper 30m dip (n=230 monotonic, the ONLY powered c2 case). Expect: KEEP WR57.3 vs REJECT 31.7; ENFORCE-track. Control=pool_a_candidate. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to pool_a_candidate only if it lifts $/tr. paper, single-variable A/B.

### `pool_a_dipgate` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A + DIP-DEPTH GATE (A/B treatment of pool_a_candidate). IDENTICAL to pool_a_candidate except entry_gate adds shape_90m_drawdown_from_max_pct<=-16.0 (require entry to be >=16% below the 90-min recent high). Encodes the 2026-06-06 smart-money finding (n=248 elite buys: median dip -16% off recent high, buy weakness not breakouts) AND our own validation (core-family backtest: -16% gate lifts net +1.19%->+2.30%/tr, WR 62%->69%, keeps 55% of volume; winners entered -23% vs losers -14%). The gate also auto-neutralizes shallow-buying triggers (chart_quality_bottom/pullback_in_uptrend too shallow to clear it). Own exclusion_pool=pool_a_dipgate. Forward-judge per-trade % vs pool_a_candidate (control). In-sample threshold is optimistic; forward A/B is the real proof.

### `pool_a_dipgate_cool1h` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A DIPGATE + COOL-1H CEILING (2026-06-15 A/B). EXACT clone of pool_a_dipgate, ONE change: pc_h1_max=40.0 (anti-parabolic ceiling - reject a dip entry when 1h momentum is HOT >+40%). From the 8zkgFGVZ copyable-winner entry-mine: on a DIP entry, hot 1h momentum is a CONTRA signal (buying a shallow pullback inside a parabola = the top). Its winners entered dips with COOL 1h (pc_h1 med ~+18%); its 2 worst trades chased parabolas (pc_h1 +69/+143%). Realized on that wallet: dip<=-15 + pc_h1<=+40 + age>=10 = 82% WR / +9.5% med (n=11), monotone gate ladder. The dip/age elements restate pool_a_dipgate/goodpond; the pc_h1<=+40 ceiling on a DIP is genuinely NEW (nothing bounds 1h momentum from above; momentum_pump_tight does the opposite). Forward-judge per-trade %/WR vs pool_a_dipgate (control) at n>=30 closes. paper.

### `pool_a_dipgate_cool1h_tight` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A DIPGATE + TIGHT COOL-1H (2026-06-15 A/B arm 2). Clone of pool_a_dipgate, ONE change: pc_h1_max=0.0 (require NON-positive 1h momentum on the dip = a REAL pullback, not a bounce-chase). The cool-1h edge is TRIANGULATED: (1) 8zkgFGVZ copyable winner (winners cool pc_h1, losers chase +69/+143% parabolas), (2) the in-bot #435 regime miner on the FLEET'S OWN trades = pc_h1 is the #1 separator, rel_gap -1.0: winner med -10.3% vs loser med +3.4%. The +40 arm (pool_a_dipgate_cool1h) is too loose for the fleet (losers sit at +3.4, far under +40 -> ~no-op); this arm cuts at the SIGN boundary where the fleet's winners/losers actually split. 3-point sweep vs control (pool_a_dipgate) / +40 (cool1h) / 0 (this). Forward-judge per-trade %/WR at n>=30. paper.

### `pool_a_dipgate_deep` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A DIPGATE DEEP (2026-06-15 A/B). Clone of pool_a_dipgate, ONE change: shape_90m_drawdown_from_max_pct <= -25 (was -16) â€” tests whether a DEEPER dip gate beats our -16 on the tradeable pond. From the mission wallet-mine: copyable winners enter DEEPER dips than our gate (7BNaxx winners median -43%, smart-money median -23% vs our -16%); their richest edge is rug-pocket microcaps we avoid, so this tests the transferable DEPTH lever on our own pond. Forward-judge per-trade %/WR vs pool_a_dipgate (-16 control) at n>=30 closes. paper.

### `pool_a_dipgate_vwap1h` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_a_dipgate (2026-06-16 campaign). +1 entry clause: pct_above_vwap_1h <= -4.0. buy the discount below the 1h VWAP (deep-dip family). Expect: WR 45.6->63% on data-present subset; re-measure on fresh pull. Control=pool_a_dipgate. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to pool_a_dipgate only if it lifts $/tr. paper, single-variable A/B.

### `pool_a_goodpond` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A GOOD-POND (2026-06-08 entry-quality investigation synthesis). Clone of pool_a_candidate with the ONLY held-out-surviving entry levers from the 8-agent investigation: (1) net_flow_60s_usd>=100 (LIVE buy-side flow at entry â€” on-chain agent: WR 0.688/+2.09%per-tr held-out all folds, the headline lever), (2) age_h_min=24h (cut the <2h fresh-micro 0%-WR lane; good-pond + 23d agents agree, +18-22pp WR held-out), (3) mcap 500k-10M (our true edge band; sub-500k is WORST at 29-32% â€” so this REPLACES the backwards <=500k mcap clones). Kills dip-depth (didn't survive) and holder/smart-money features (dead OOS). Edge = enter aged mid-mcap tokens with live net buying. Own exclusion_pool. Forward-judge vs pool_a_candidate control. CAVEAT: 3.5d data, net_flow has mild recency decay (latest fold 0.63 vs 0.70) â€” forward A/B is the proof.

### `pool_a_goodpond_reaccum15` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_a_goodpond (2026-06-16 campaign C2). +1 entry clause: chart_reaccum_drawdown_pct <= 15. same recurring re-accum lever (cross-bot corroboration). Expect: n=11 PERFECT split=overfit; SHADOW only. Control=pool_a_goodpond. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to pool_a_goodpond only if it lifts $/tr. paper, single-variable A/B.

### `pool_a_mcap1m` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A + MCAP GATE <=$1M (A/B treatment #2 of pool_a_candidate). IDENTICAL to pool_a_candidate except mcap_max=1000000. Pairs with pool_a_mcapgate (<=$500k) and pool_a_candidate (no gate) for a clean 3-way band sweep on the same base: control / $500k / $1M. ~2x the $500k clone's volume (~12/day) so it reaches n>=50 faster; tests whether the $500k-1M band is worth keeping (low_mcap_probe note: 500k-1M ~= 1M-5M, never sub-split before). Own exclusion_pool=pool_a_mcap1m. Forward-judge per-trade % vs the other two.

### `pool_a_mcapgate` — already-disabled
POOL-A + MCAP GATE (A/B treatment of pool_a_candidate). IDENTICAL to pool_a_candidate except mcap_max=500000 (block entries >$500k). Tests the 2026-06-07 validated finding: >$500k band is break-even/negative (-0.38% mean, 69% of fleet volume) while $50-500k is +4%; gating >$500k ~4x'd fleet expectancy (+1.07%->+4.27%) at low winner-cost (kept 93% of +30% winners, 100% of +50%). Own exclusion_pool=pool_a_mcap so it runs independently of the control. Forward-judge per-trade % vs pool_a_candidate. [STACK-EXEMPT + microcap mandate 2026-06-11: this bot deliberately tests the sub-500k band; the fleet stack (mcap>=500k) made it structurally dead since 06-09 enforcement. Own mcap bounds are its validated stack.] [RETIRED 2026-06-12 by AxiS: question answered — sub-500k with mid-cap dip gates = toxic water (-$47 across the pair in 5h of freedom; confirms the failure-mine's 'sub-500k is backwards').]

### `pool_a_nopullback` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A MINUS pullback_in_uptrend trigger (A/B treatment of pool_a_candidate). IDENTICAL except triggers_allowed drops pullback_in_uptrend. The 2026-06-07 archetype mine flagged pullback_in_uptrend as the ONE clean CUT candidate: 54% never-green rate (highest), negative mean net% even WITH positive buy-side flow, and it buys shallow/near-high (the weakest entry timing). Tests whether removing the worst trigger lifts per-trade %. Keeps deep_1h_dip/power_dip_runner (the +2.1% edge) + chart_quality_bottom. Own exclusion_pool=pool_a_nopullback. Forward-judge vs pool_a_candidate control.

### `pool_a_solmacro` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A SOL-MACRO RELAX (2026-06-08): exact clone of pool_a_candidate, ONLY change = sol_macro thresholds relaxed h1 -0.7->-2.5, h6 -0.3->-1.5 (block only DEEP SOL crashes). Tests the v2 missed-winner finding: sol_macro was the #1 config gate blocking missed held-winners (97% of gate-blocked), AND it contradicts the day-regime edge (SOL modestly-red -1..-3% is our BEST dip-buy market, but the -0.7 gate vetoed exactly those days). Own exclusion_pool -> independent A/B vs pool_a_candidate (control). Judge realized n>=50 compare_bots --unrealized. See reference_day_level_regime_real_2026_06_08.

### `pool_a_solmacro_reaccum20` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_a_solmacro (2026-06-16 campaign C2). +1 entry clause: chart_reaccum_drawdown_pct <= 20. tight re-accum, not still-bleeding (chart_reaccum recurs on 3 bots). Expect: kept WR23.5->50; n=17 shadow. Control=pool_a_solmacro. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to pool_a_solmacro only if it lifts $/tr. paper, single-variable A/B.

### `pool_a_stack` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A + STACKED ENTRY GATE (bs_m5>=1.5 AND shape_90m_drawdown<=-16). Combines the two held-out entry levers: buy-side-flow confirmation + deep-dip. The 2026-06-07 signature mine's best held-out gate (core WR 0.39->0.62, ~21% throughput). Tests whether stacking BOTH levers beats each alone (pool_a_bsm5 / pool_a_dipgate). Lower volume, highest selectivity. Own exclusion_pool=pool_a_stack. entry_gate fail-OPEN per missing feature. Forward-judge per-trade % vs pool_a_candidate control + the single-lever clones.

### `pool_a_stack_5mred2` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_a_stack (2026-06-16 campaign C2). +1 entry clause: 5m_consec_red >= 2. require a confirmed >=2-candle 5m red flush (sep 2.01). Expect: kept WR71 vs rejected 19; n=34 forward-judge. Control=pool_a_stack. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to pool_a_stack only if it lifts $/tr. paper, single-variable A/B.

### `pool_a_trigstate` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-A TRIGGER-STATE (2026-06-08, 7-Opus held-out validation of AxiS's thesis: each trigger only WINS in a specific token-state; we've been firing them one-size-fits-all). Exact clone of pool_a_candidate (same spine/triggers/exits) + the ONE change: per-trigger token-state gates on its 4 triggers, each validated in BOTH date folds (May26-31 & Jun1-8): deep_1h_dip needs a washed-out chart (chart_score<=40.9, WR .54->.68); pullback_in_uptrend needs a FRESH peak (minutes_since_peak<=22, .40->.68 +28pp); power_dip_runner needs a real 5m flush (pc_m5<=-2.43, .53->.66, the n=100 rescue); chart_quality_bottom needs a live vol burst (vol_5m_burst_vs_h1>=1.42, .49->.62). Own exclusion_pool so it evaluates the SAME tokens independently of pool_a_candidate (the control). Fail-open on missing feature. Forward-judge vs pool_a_candidate at n>=50 (compare_bots --unrealized). See reference_per_trigger_state_conditioning_2026_06_08.

## pool_c

### `pool_c_mcapgate` — already-disabled
POOL-C + MCAP GATE (A/B treatment of pool_c_tightexit). IDENTICAL to pool_c_tightexit except mcap_max=500000 (block entries >$500k). Tests the 2026-06-07 validated finding: >$500k band break-even/negative, $50-500k +4%; gating >$500k ~4x'd expectancy at low winner-cost. Own exclusion_pool=pool_c_mcap so it runs independently of the control. Forward-judge per-trade % vs pool_c_tightexit. [STACK-EXEMPT + microcap mandate 2026-06-11: this bot deliberately tests the sub-500k band; the fleet stack (mcap>=500k) made it structurally dead since 06-09 enforcement. Own mcap bounds are its validated stack.] [RETIRED 2026-06-12 by AxiS: same verdict as pool_a_mcapgate.]

### `pool_c_post_peak` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-C shadow (Design C member): post_peak (>=4h after 24h peak gate), $100 fixed, max 3 slots, no-same-token across pool_c. mults=1.0 -> $100/position. [RE-ENABLED 2026-06-10: 06-05 disable was the fleet bleed regime (twin tightexit shows identical decay shape and was kept); full record 63% WR/35 tok/+$0.39/tr beats the enabled twin. Now trades through the entry stack. Judge at n>=30 post-stack closes; fast-cut if net-negative at n>=25.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pool_c_post_peak_chl1m` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_c_post_peak (2026-06-16 campaign). +1 entry clause: consec_higher_lows_1m >= 1. momentum confirmation: at least one 1m higher-low. Expect: WR 51.9->64.7% (+12.8pp), keeps 33/40 winners. Control=pool_c_post_peak. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to pool_c_post_peak only if it lifts $/tr. paper, single-variable A/B.

### `pool_c_tightexit` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
POOL-C shadow (Design C member): premium_tightexit, $100 fixed, max 3 slots, no-same-token across pool_c. Diverse-pool de-concentration test. mults=1.0 -> $100/position. [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.]

### `pool_c_tightexit_h24peak55` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of pool_c_tightexit (2026-06-16 campaign C2). +1 entry clause: h24_ratio_to_peak <= 0.55. deep off the 24h peak, not a chase (deep-pullback thesis). Expect: n=8 noise; hypothesis-rank/shadow only. Control=pool_c_tightexit. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote clause to pool_c_tightexit only if it lifts $/tr. paper, single-variable A/B.

## probe

### `probe_swing` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
OVERNIGHT SWING PROBE (2026-06-12, the 4TeTtW1G archetype — green through BOTH red windows, 86% scanner-visible universe): 615min median holds (p75 13h), 75% WR, +15.6 med win. Geometry: ride the ESTABLISHED tape's swings — 13h time-box, NO price exits except a -45% catastrophe stop, sell 75% at +15 / rest at +30. THE EXPERIMENT: our mid-cap stack pond read 16% won10 at FAST exits — was it dead water, or fished at the wrong timescale? Same validated stack entries, swing horizon. PRE-REG: n>=30 closes, >=+$1.50/tr promotes; negative retires. 48h burn-in; slow accrual expected (13h holds x 4 slots).

### `probe_tightexit_live_100` — already-disabled
LIVE MEASUREMENT PROBE $100 (DORMANT: enabled=false). Fixed-size ($100, multipliers neutralized to 1.0) clone of champion_premium_tightexit for the paper->live fidelity probe. live_probe=true -> when ENABLED + USE_JUPITER_ULTRA + a real private key, fills route through MEV-protected Ultra swaps with per-leg fill instrumentation. Run all three ($20/$50/$100) together: they buy the SAME token at the SAME tick at three sizes = a clean per-token PAIRED size->slippage measurement (not a rotation that entangles size with token/timing). Own daily-loss halt -$75, max_concurrent 1, max 2 buys/token/day. Deliverable = live slippage dataset, NOT P&L. Do NOT enable without approval + test_pre_live_invariants + MEV routing. Spec: docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md

### `probe_tightexit_live_20` — already-disabled
LIVE MEASUREMENT PROBE $20 (DORMANT: enabled=false). Fixed-size ($20, multipliers neutralized to 1.0) clone of champion_premium_tightexit for the paper->live fidelity probe. live_probe=true -> when ENABLED + USE_JUPITER_ULTRA + a real private key, fills route through MEV-protected Ultra swaps with per-leg fill instrumentation. Run all three ($20/$50/$100) together: they buy the SAME token at the SAME tick at three sizes = a clean per-token PAIRED size->slippage measurement (not a rotation that entangles size with token/timing). Own daily-loss halt -$30, max_concurrent 2, max 2 buys/token/day. Deliverable = live slippage dataset, NOT P&L. Do NOT enable without approval + test_pre_live_invariants + MEV routing. Spec: docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md

### `probe_tightexit_live_50` — already-disabled
LIVE MEASUREMENT PROBE $50 (DORMANT: enabled=false). Fixed-size ($50, multipliers neutralized to 1.0) clone of champion_premium_tightexit for the paper->live fidelity probe. live_probe=true -> when ENABLED + USE_JUPITER_ULTRA + a real private key, fills route through MEV-protected Ultra swaps with per-leg fill instrumentation. Run all three ($20/$50/$100) together: they buy the SAME token at the SAME tick at three sizes = a clean per-token PAIRED size->slippage measurement (not a rotation that entangles size with token/timing). Own daily-loss halt -$50, max_concurrent 2, max 2 buys/token/day. Deliverable = live slippage dataset, NOT P&L. Do NOT enable without approval + test_pre_live_invariants + MEV routing. Spec: docs/superpowers/specs/2026-06-02-live-measurement-probe-design.md

## rugpocket

### `rugpocket_scalper` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
RUG-POCKET SCALPER (2026-06-15, wallet-mimic). Faithful mimic of the 3 copyable pond winners (8zkg/1eveYY/7BNaxx) = deep-dip FAST-time-box scalpers in the DEEP-MICROCAP rug pocket they profit in but we normally avoid. Edge (mission wallet-mine): deep flush (shape_90m_drawdown<=-30) in microcaps (mcap 15k-200k, liq>=12k) + FAST 6-min time-box (their survival mechanism: 79% of losers flushed at ~5min) + tight fast_bail -10. RUG-SAFETY = TINY $20 size (low slippage even at $9-20k liq = why antirug_floor_exempt is safe here) + the fleet RUG_BUNDLE sniped-rug gate + lp-structure gate STILL apply + max 3 concurrent. PAPER test of whether the copyable rug-pocket edge is capturable safely. Forward-judge realized %/WR at n>=30; promote only if +EV AND catastrophe(<=-35%) rate <10%. NO $ projection.

## timebox

### `timebox_probe` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
TIME-BOX PROBE (2026-06-12, the Dw5Vykxu archetype — decoded from the only copyable wallet green through BOTH red windows): fixed $75 probes, -25% stop + never-runner cut at 20min (TIGHTENED 2026-06-15: 16-Opus consistency mine FALSIFIED the no-price-stop thesis -- the -60 rug-guard was 60% of all variance, WR is stop-INVARIANT, and 0 winners ever peaked past +20 TP1 so 0 are killed), hard 240min TIME stop, sell-ALL on strength at +20%. Risk boxed by time, not price — red-tape chop executes price-stops at local bottoms (74% of our stops recovered; ANTH: our -15% stop at 12:59 was ITS +124% entry at 13:06). Near-indiscriminate entries by design (the edge is the geometry, not selection). PRE-REG: n>=30 closes, >=+$1.50/tr -> promote; negative -> retire. 48h burn-in label.

### `timebox_probe_5mgreen` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
WIN-SIG A/B of timebox_probe (2026-06-16 campaign). +1 entry clause: 5m_consec_green >= 1. momentum confirm: a 5m green streak (monotonic dose-response). Expect: WR 58.4->61.9%, blocks the stall cohort. Control=timebox_probe. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to timebox_probe only if it lifts $/tr. paper, single-variable A/B.

### `timebox_probe_5mgreen_live` — already-disabled
WIN-SIG A/B of timebox_probe (2026-06-16 campaign). +1 entry clause: 5m_consec_green >= 1. momentum confirm: a 5m green streak (monotonic dose-response). Expect: WR 58.4->61.9%, blocks the stall cohort. Control=timebox_probe. PRE-REG: judge loser-cut vs winner-keep + $/tr vs control at n>=30; promote the clause to timebox_probe only if it lifts $/tr. paper, single-variable A/B.

### `timebox_probe_mcap` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
TIME-BOX PROBE + MCAP FLOOR (2026-06-14 A/B): EXACT clone of timebox_probe with the ONE change mcap_min=50000 â€” isolates whether a mid-mcap floor fixes the micro-cap RUG exposure that dropped timebox_probe -$326 from peak (FableTrump/Pixelands -99% etc. were ~$10k micro-caps). Red-winner mine: winners' median mcap ~$124k, losers' ~$10k. timebox_probe stays the untouched control. PRE-REG: judge vs timebox_probe at n>=30 closes â€” does the floor cut the deep-rug tail without killing the edge?

## young

### `young_momo_launch` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
YOUNG-MOMENTUM LAUNCH (wallet-decode build C, 2026-06-13): the SECOND lane DaxfeJKe runs (decoded alongside its deep-flush lane). On ultra-young <1h launches it buys the OPPOSITE shape â€” m5 +53% to +65% (ripping UP), shallow drawdown, i.e. a young-launch MOMENTUM/breakout, the 'Dw5 -> firehose' lane our memory flagged but we lacked a clean trigger for. Points the just-un-silenced young family (rug-gate unique_buyers_n==0 exemption shipped 2026-06-13) at the momentum lane: trigger_volume_burst_runner admits young micro-liq volume-burst tokens; entry_gate requires age<=1h + RISING momentum (1m_cum_3min_pct>=3, the opposite of the dip gates) + a real volume spike. Paired with Dax's fast time-box (time_stop_minutes=8, a touch longer than the deep-flush 6 to let a launch run) + trail for the runners. RUG RISK is real on <1h launches (Dax ate a -96% at 0.04h) -> lp_single_sided rug-gate STILL applies (only the no-buyers branch is exempted), tight hard stop, fusion/wynn/huge_wick filters on. entry_stack_exempt + regime_dial_exempt (young pond). Forward-judge per-trade %; this is the most speculative of the three.

### `young_probe_baseflow` — already-disabled
YOUNG-PROBE CLONE: confirmed base + hourly buy dominance (1s_base_confirmed_at_entry>=0.5 AND bs_h1>=1.41) on the young_probe_light template. 2026-06-10 mine: held-out TEST WR 95% +$3.39/tr n=21; FULL 97% +$4.59/tr n=33 across 8 tokens. THESIS: a 1s-confirmed base with buyers dominating the hour = the young pond entry that sticks. CAUTION: 8-token diversity = pseudo-replication risk. FAST-CUT: retire if forward WR<60% by n=20 closes or net-negative at n>=12 distinct tokens. [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [THROUGHPUT 2026-06-11: shared young_pond exclusion pool (siblings spread across DISTINCT tokens instead of duplicating - light/candidate closed the same 2 tokens today) + max_concurrent raised. Same entries, more at-bats; $100/day needs shots.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.]

### `young_probe_baseflow_convex` — already-disabled
YOUNG-PROBE CLONE: confirmed base + hourly buy dominance (1s_base_confirmed_at_entry>=0.5 AND bs_h1>=1.41) on the young_probe_light template. 2026-06-10 mine: held-out TEST WR 95% +$3.39/tr n=21; FULL 97% +$4.59/tr n=33 across 8 tokens. THESIS: a 1s-confirmed base with buyers dominating the hour = the young pond entry that sticks. CAUTION: 8-token diversity = pseudo-replication risk. FAST-CUT: retire if forward WR<60% by n=20 closes or net-negative at n>=12 distinct tokens. FLEET-CONVEX EXPERIMENT (2026-06-11, AxiS: our entries + the elite payoff curve). Same entries as the parent, payoff reshaped to the decoded elite math (51 pct WR, p90 +107 tails, fast cuts): $25 probes, TP1 +5 sells only 10 pct, TP2 +25 sells 20 pct, 70 pct rides the trail, -15 cut (their median loser), -9 fast bail. PRE-REG: judge vs parent head-to-head on $/tr at n>=25 closes each; convex must beat parent or retire. The smart wallets are the existence proof. [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [DAILY FLOOR $15 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.]

### `young_probe_candidate` — already-disabled
YOUNG-TOKEN probe (#4.1, DORMANT): candidate (premium_tightexit) stack restricted to YOUNG tokens (age < YOUNG_TOKEN_MAX_AGE_H, surfaced past min_age with a liquidity floor). Tests whether the universe-mine age<=2 edge (69-71% WR / +24.9% fwd-peak) survives on REALIZED dip-buy paths. enabled=false + needs YOUNG_TOKEN_PROBE=1 env to run = two deliberate gates. $100 fixed (mults 1.0). [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [THROUGHPUT 2026-06-11: shared young_pond exclusion pool (siblings spread across DISTINCT tokens instead of duplicating - light/candidate closed the same 2 tokens today) + max_concurrent raised. Same entries, more at-bats; $100/day needs shots.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.]

### `young_probe_conviction` — already-disabled
CONVICTION A/B of young_probe_light (2026-06-12 roster decode: ALL 10 profitable wallets size with conviction — dmuX bets 10x the table; our fleet bets flat, every bot). Single variable vs parent: trigger-count sizing (1 + 0.5*(n-1), cap 2x). PRE-REG: judge vs parent $/tr at n>=30 closes each; loser retires. 48h burn-in.

### `young_probe_late` — already-disabled
LATE-YOUNG band probe (6-24h, liq30k+): 358 ev/day, 50% won10, +10.3% med peak — map completeness; weakest band, tightest leash. PRE-REG: judge at n>=30 closes — >=+$1.50/tr at $100 promotes to candidate set; negative retires the band. $60 daily floor; badday-grade rug screens; own exclusion pool. YOUNG CAPTURE BUILD 2026-06-12 (AxiS: the ponds we are proven in offer $500-1300/day at modest capture — the gap is throughput, not edge).

### `young_probe_light` — DISABLED (2026-06-23 cost-trim; config preserved, re-enable to resume research)
YOUNG-TOKEN probe — LIGHT filters (2026-06-02): young (<YOUNG_TOKEN_MAX_AGE_H) tokens with only FRESH-DATA-SAFE filters (btc_overheat/consec_red/huge_wick) + the freshness gate — because the full premium-defender stack keys on h6/h24 windows that DON'T EXIST for <2-6h tokens (false negatives). A/B vs young_probe_candidate (full stack) to isolate whether the filters or the tokens are the blocker. Needs YOUNG_TOKEN_PROBE=1 env. $100 fixed. [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [THROUGHPUT 2026-06-11: shared young_pond exclusion pool (siblings spread across DISTINCT tokens instead of duplicating - light/candidate closed the same 2 tokens today) + max_concurrent raised. Same entries, more at-bats; $100/day needs shots.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.]

### `young_probe_mid` — already-disabled
MID-AGE band probe (2-6h, liq30k+): 198 ev/day, 61% won10, +13.2% med peak. PRE-REG: judge at n>=30 closes — >=+$1.50/tr at $100 promotes to candidate set; negative retires the band. $60 daily floor; badday-grade rug screens; own exclusion pool. YOUNG CAPTURE BUILD 2026-06-12 (AxiS: the ponds we are proven in offer $500-1300/day at modest capture — the gap is throughput, not edge).

### `young_probe_stair` — already-disabled
YOUNG-PROBE CLONE: staircase structure (higher_low_5m>=0.5 AND trend_30m_n_pivot_lows>=3.5) on the young_probe_light template. 2026-06-10 mine of 81 young-probe closes (74% WR base): held-out TEST WR 86% +$4.52/tr n=14; FULL 92% +$5.92/tr n=26 across 7 tokens. THESIS: young-token winners are in confirmed short-term UPTRENDS (staircase pivots + higher lows) — momentum-confirmation, not deep-dip. CAUTION: 7-token diversity = pseudo-replication risk. FAST-CUT: retire if forward WR<60% by n=20 closes or net-negative at n>=12 distinct tokens. [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [THROUGHPUT 2026-06-11: shared young_pond exclusion pool (siblings spread across DISTINCT tokens instead of duplicating - light/candidate closed the same 2 tokens today) + max_concurrent raised. Same entries, more at-bats; $100/day needs shots.] [DAILY FLOOR $60 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.] [06-12: REMOVED from young_pond exclusion pool — ZERO lifetime fires: identical gates to its firing convex twin, but pool siblings (light/candidate/baseflow) always held the scarce young tokens first (stair = last in eval order). Own experiment restored; head-to-head vs stair_convex finally fair. The dedup pool keeps the redundant pair (light/candidate) it was built for.]

### `young_probe_stair_convex` — already-disabled
YOUNG-PROBE CLONE: staircase structure (higher_low_5m>=0.5 AND trend_30m_n_pivot_lows>=3.5) on the young_probe_light template. 2026-06-10 mine of 81 young-probe closes (74% WR base): held-out TEST WR 86% +$4.52/tr n=14; FULL 92% +$5.92/tr n=26 across 7 tokens. THESIS: young-token winners are in confirmed short-term UPTRENDS (staircase pivots + higher lows) — momentum-confirmation, not deep-dip. CAUTION: 7-token diversity = pseudo-replication risk. FAST-CUT: retire if forward WR<60% by n=20 closes or net-negative at n>=12 distinct tokens. FLEET-CONVEX EXPERIMENT (2026-06-11, AxiS: our entries + the elite payoff curve). Same entries as the parent, payoff reshaped to the decoded elite math (51 pct WR, p90 +107 tails, fast cuts): $25 probes, TP1 +5 sells only 10 pct, TP2 +25 sells 20 pct, 70 pct rides the trail, -15 cut (their median loser), -9 fast bail. PRE-REG: judge vs parent head-to-head on $/tr at n>=25 closes each; convex must beat parent or retire. The smart wallets are the existence proof. [STACK-EXEMPT 2026-06-11: the fleet entry stack (age>=24h, mcap>=500k) is the structural opposite of the young pond — it silenced this family from the 06-09 enforcement until now. The young lane + own entry gates are the validated stack here.] [DAILY FLOOR $15 2026-06-11: ~4 stops' worth — cascade-day halt; shadow proved post-halt buys run -EV. Goal candidates must not torch the meter.] [SELF-GATED 06-12: explicit bounds <2h/liq40k+/mcap150k+ = today's exact proven water, so the lane widening (24h/25k/100k for the new band probes) cannot alter this experiment.]

### `young_probe_surgical` — already-disabled
SURGICAL A/B (2026-06-12, the 7Gi3 geometry: median win +154% / median loss -2.2% — kill anything that doesn't work immediately, ride everything that does, uncapped). Parent entries (young_probe_light); geometry: fast-fail -4% any volume, tiny +25% bank (10%) arms a WIDE 8pp trail, no TP cap, -15 gap backstop. PRE-REG: judge vs parent $/tr at n>=30 closes; loser retires. 48h burn-in.

### `young_probe_thinliq` — already-disabled
THIN-LIQ band probe (<2h, liq 25-40k): the ceiling mine's PRIZE — 127 ev/day, 69% won10, +22.6% med peak (recorder, 1.8d). The water the proven lane throws away. PRE-REG: judge at n>=30 closes — >=+$1.50/tr at $100 promotes to candidate set; negative retires the band. $60 daily floor; badday-grade rug screens; own exclusion pool. YOUNG CAPTURE BUILD 2026-06-12 (AxiS: the ponds we are proven in offer $500-1300/day at modest capture — the gap is throughput, not edge).
