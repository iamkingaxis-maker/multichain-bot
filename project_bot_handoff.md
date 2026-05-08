# Multichain Bot — Session Handoff (2026-05-08)

## 2026-05-08 mid-day — overnight deep-dive + 2 critical fixes

**Latest commits** (deployed): `d970124` filter_vp_poc, `d958416` price-spike confirmation.

### Overnight 2026-05-08 02:00→14:00 UTC summary

- **67 closed dip_buy trades, 23W/44L (34% WR), NET -$142.49.** clean_break dominant bleeder (-$77/36 trades, 28% WR despite Gates A-D + carve-out). high_regime improved (48% WR with hard gates).
- **TP system mixed**: ladder partials (TP1 → TP2) net +$6.53 over 21 cleanest cases. slow_bleed fired 5× capping losses below stop. smart_bearflip fired 0× overnight. Counterfactual: TP1=100% would have been -$34.84 vs ladder -$49.25 (-$14 worse for one night, but phantom-validated lifetime lift retained).
- Token rebuy clusters: KIDO 7×, AMERICA 8×, conviction 4×, ROFL 4×, COMPUTA 6×.
- **Manual sell**: HANTA-Kun closed for +$15.46 at 14:31 (user-initiated, before fixes deployed) — visible to user via dashboard but not to bot's internal price feed (see commit `d958416`).

### Methodology this session

- Pulled `/api/trades`, paired buys to sells per (address, pair_address, time-window).
- Cohen's d feature scan: `chart_vp_poc_distance_pct` was the most discriminating chart-derived feature (winner median -6.0% vs loser median +3.2%).
- Held-out validation: ALL gate proposals tested against lifetime cohort (n=946) BEFORE accepting.
- Visual chart sweep: opened dexscreener for every unique winner and loser (19 loser tokens, 17 winner tokens) across 5m/15m/1h. Confirmed visual taxonomy: losers = post-pump distribution; winners = fresh breakouts or V-recoveries.

### What shipped this session

1. **`d970124`** — `filter_vp_poc_above` ENFORCED in dip_scanner.py.
   - Block entries when `chart_vp_poc_distance_pct > 0` (entry above volume POC).
   - Held-out lifetime: blocks 254/946 trades, swings **+$112.34**, **ZERO** big winners (>$10) killed, kept cohort 57.2% WR (vs 54% baseline).
   - Overnight in-sample: blocks 30/63, cohort -$33.79 → +$10.65, 58% WR.
   - Mechanism: when entry price is above the volume profile POC, the heaviest volume traded BELOW current price → bot is chasing, not dipping. Real dips have entry below the volume center.
   - Fail-open when feature missing (51% lifetime / 97% overnight coverage).
   - Validation script: `scripts/test_filter_vp_poc.py`.

2. **`d958416`** — Confirmation-based price-spike guard in position_manager.py.
   - **Bug**: anti-corruption guards rejected ANY single-tick move >20% as "corrupted feed". HANTA-Kun 2026-05-08 legitimate +106% pump triggered this — every WS tick at the new price kept getting rejected against the stale pre-pump ref_price. Internal state stuck at $0.000128 while market traded at $0.000264. Bot's `/api/positions` showed `+$1.02 / +10%` while dashboard showed `+$14.05 / +128%`. TPs/stops never fired.
   - **Fix**: replaced bare reject with `_spike_should_accept()` helper. After 3 same-price ticks (within 5%, <60s window) OR 30s sustained rejection → accept. Real fast pumps confirm in <1s; Goblin -94% one-off glitches don't accumulate.
   - Applied to both `_apply_price_update` (REST/poll path) and `check_stop_loss_realtime` (WS path). TP path self-corrects via shared `_last_realtime_price`.
   - Validation script: `scripts/test_spike_confirmation.py` — 6 scenarios pass (HANTA-Kun fix, Goblin protection, normal data, different prices don't accumulate, sustain timeout, ±5% drift tolerance).

### Visual chart taxonomy (from full sweep)

**Losing pattern** (15 of 19 loser tokens):
- Massive prior pump (peak_h24 typically 200-8500%)
- Volume profile POC sits at the top of the pump
- Bot bought during sustained downtrend / distribution
- Multiple rebuys clustered within 1-2 hours

**Winning pattern**:
- Fresh breakout from flat consolidation OR sustained uptrend
- Modest prior pump (peak<200%) or very recent breakout
- Entry at or below volume cluster
- Quick TP exits work; later ones get caught by slow_bleed

**Edge cases**:
- CHONKERS V-recovery: violent collapse + bottom consolidation, gate keeps wins (vp_poc deeply negative)
- KIDO: dead-cat bounces during bleed, gate keeps wins (vp_poc=-22, -49)
- AMERICA slow_bleed loss: range pullback in uptrend, saved by exit not catchable at entry

### Same-symbol bug

Two tokens both named "HANTA":
- Hantavirus ($7.4M mature) — 3 losses overnight
- Hanta-Kun ($263k → $264k pre-bonding) — 2 wins overnight + manual sell at +$15.46

Bot distinguishes correctly by address. Symbol-level analysis would conflate them; aggregate by address.

### Forward-watch notes

**filter_vp_poc forward-track caveat (2026-05-08 14:40 UTC):** the HANTA-Kun
buy at 14:20:48 — which became the +$15.46 manual-sell winner — had
`chart_vp_poc_distance_pct = +3.555` in its entry_meta. Under the new
filter, that buy would have been BLOCKED. Held-out lifetime validation
said zero big winners (>$10) killed; this represents the first such
case. Lifetime swing remains +$112 net positive, so the filter is
still net-positive on validated historical data — but log every
filter_vp_poc_block from forward data and reconcile against the
lifetime expectation. If forward shows >2 big-winner kills per week,
re-evaluate the threshold (consider `> 5%` instead of `> 0%`).

### Pending issues / future work

1. **Anti-rebuy** — overnight cohort showed strong rebuy-after-loss pattern (KIDO 4×, conviction 4×, etc.). But lifetime test showed anti-rebuy KILLS 147 big winners ($+5406+) including BULL/MAGA. Need surgical version: e.g. "block 3rd+ same-token buy in 4h IF prior 2/3 lost AND peak_h24 > 500%". Validate before shipping.
2. **POCKET-style residual losses** — compound triggers firing in distribution with vp_poc<0 (passes new gate). Need compound-trigger-specific gate.
3. **Phantom-validated filters** P_slip_vel and C_var_b not yet promoted from SHADOW to ENFORCED. Phantom showed +$0.65/trade and +$0.58/trade lift over current production stack.
4. **TP1 ladder** — actual overnight underperformed counterfactual TP1=100% by -$14. Phantom predicted +$0.74/trade lift lifetime. One night noisy; track 24-48h before deciding.
5. **Same-symbol detection** — bot already handles via address, but UI/symbol-level analysis can confuse manual review.

### Methodology takeaways (reinforced)

- Visual chart hypothesis often overfits — `pc_h1_lb<=-10` would have killed CHONKERS V-recovery winners. Always validate against held-out lifetime.
- Counterintuitive findings often have edge — `chart_vp_poc_distance_pct > 0` (price ABOVE volume center) catches losers; the gate predicate is "entry too high", not "entry too low".
- Anti-corruption guards need confirmation logic, not just hard thresholds. Real moves accumulate; glitches are one-off.

---

## 2026-05-08 early AM — late session continuation (data-driven gates + exit refactor)

**Pre-mid-day commit**: `236222d` (superseded by mid-day work above)

Continued work from the 2026-05-07 PM session into early 2026-05-08.
Drove from "shipping based on intuition" to "shipping only what
held-out validation supports." Today's full closed-trade tally went
to -$42.76 (54% WR, 30W/26L) before all the gates landed; with all
of tonight's work retroactively applied, today's data would have
been roughly +$60-80.

### What shipped this session (in order)

1. **`03a9f5a`** — `_get_token_price` pair-pinning (entry-price path).
   Companion to `7799f12` price-feed fix from earlier session.
   PENGUIN/USDHC bug: bot's entry_price was being set from a non-pinned
   DexScreener token endpoint that picks highest-liq pair — for
   PENGUIN that was raydium @ $0.16 instead of pumpswap @ $0.004
   (41× off). Now `_get_token_price(addr, pair_address=...)` queries
   the specific pair endpoint first. Both buy() and sell() pass
   pair_address. Closed PENGUIN (-$0.22) and USDHC (-$18.81 manual
   sell with corrected price) before deploy.

2. **`867b988`** — `trigger_high_regime` HARD gates (vs>=0.5 AND dev>=2.0).
   Replaced the conditional suppression gates from earlier with two
   absolute requirements. Validation on today's 27 high_regime fires
   (`scripts/analyze_high_regime_today.py`):
     - LOOSE (current): 41% WR, -$44.77 net
     - vs>=0.5 alone: 64% WR, +$8.08
     - dev>=2 alone: 46% WR, -$17.58 (kicks GMAR x6 with dev=0.5%)
     - **vs>=0.5 AND dev>=2: 70% WR, +$15.74 (10 fires of 27)**
   Net swing on today's data: -$44.77 → +$15.74 (+$60).

3. **`dbc45c0`** — clean_break gates A + B (dev<1 + post-pump-dead-vol).
   Validation on today's 26 clean_break fires
   (`scripts/analyze_clean_break_today.py`): both are STRICT improvements
   (zero winners blocked). Catches GMAR x3 (-$19.74) and mask -$4.73.
   Today's swing: clean_break -$2.48 → +$21.99.

4. **`ad25828`** — `SLOW_BLEED` exit at 60min/-5%.
   `dip_buy` had no max-hold protection (scalp has 45min, breakout 4hr).
   Hold-time analysis (`scripts/analyze_hold_time_today.py`) showed:
     - 0-30min: 12 trades, 67% WR, +$3.91
     - 1-2hr: 11 trades, 45% WR, -$4.47
     - **2-4hr: 9 trades, 44% WR, -$18.28**
     - **4-9hr: 11 trades, 36% WR, -$38.85** (the killer bucket)
   New rule: `if hold>=3600s AND pnl<=-5% AND pnl>-12%: close`. Catches
   GMAR-style slow-bleeders at -5% instead of waiting for -12% stop.

5. **`ae57ac3`** — TP1=100% revert to 50% ladder + smart_bearflip ENFORCED.
   Two coupled changes from phantom forward-test analysis on 1507
   held-out candidates (`scripts/analyze_phantom_exits.py`):
     - Phantom showed TP1=100% (the 2026-05-07 in-sample-tuned change)
       was -6.08%/trade vs ladder on changed exits (16 better / 33
       worse, total -297.7% across 49 changes). Held-out wins over
       in-sample.
     - smart_bearflip was permanently dead under TP1=100% mode (its
       gate `state.tp1_hit and not state.tp2_hit` was unreachable).
       With ladder restored, it can fire on the post-TP1 50% remainder.
       Phantom delta: +0.74%/trade lift, 29 better / 18 worse / 185
       ties out of 232 evaluated.
   Coexists with the 3.5% trail (safety net for cases where bear-flip
   pattern doesn't trigger).

6. **`5ea8140`** — clean_break gates C + D (chart_conf>=80, regime<10).
   Deeper feature mining via `scripts/dig_clean_break_v2.py`. Cohen's-d
   scan over 284 numeric entry_meta features surfaced two non-obvious
   discriminators on the residual losses (post-existing-gates):
     - Gate C: `chart_pattern_5m_conf >= 80` — counterintuitively,
       "textbook" 5m bullish patterns are bull-traps in distribution
       contexts. AMERICA had conf=100, oGNOME conf=99.9 — both lost.
       +$5.57 swing.
     - Gate D: `regime_dip_breadth_pct < 10` — isolated bounces with
       no market coordination. Catches Hantavax -$1.46.
   Combined clean_break swing today: -$2.03 → +$29.47.
   Validation rejected an earlier hypothesis (1h_change<=-10 AND
   peak>=200 — visually suggested by AMERICA/SELLOR charts) — would
   have killed HANTAGUY/soothsayer V-recovery winners. Held-out
   validation prevented the overfit ship.

7. **`236222d`** — clean_break compound-trigger carve-out.
   Found gate-interaction bug after shipping `5ea8140`: gates C/D were
   killing 2 of 3 compound-trigger winners (HANTAGUY +$1.46, soothsayer
   +$1.63 both had conf=100 but matched high_regime base conditions).
   Carve-out: when both clean_break PASS AND `regime>=11 AND cum3>=0`,
   skip the soft gates C/D. Hard gates A/B remain unconditional.
   Net swing improves +$7.03 → +$10.12.

### Cumulative impact on today's trade volume

Per-trigger projection if all of tonight's gates were live this morning:

| Trigger | Actual today | Projected with gates |
|---|---|---|
| high_regime | -$44.77 (41% WR) | +$15.74 (70% WR) |
| clean_break | -$2.48 (62% WR) | +$29.47 |
| clean_break_high_regime | +$4.50 (100% WR) | +$4.50 (preserved by carve-out) |
| **Total dip_buy** | **-$42.76** | **~+$50-65** |

Plus the slow-bleed exit and pair-pinning fixes would have prevented
~$30 of additional one-off losses (PENGUIN/USDHC pair bug -$19, GMAR
slow-bleed -$11+ savings vs -12% stops).

### Validation tooling created this session

- `scripts/analyze_high_regime_today.py` — gate validation on 27 fires
- `scripts/analyze_clean_break_today.py` — gate validation on 26 fires
- `scripts/analyze_hold_time_today.py` — hold-time bucket analysis
- `scripts/analyze_phantom_verdicts.py` — phantom filter combo P&L
- `scripts/analyze_phantom_exits.py` — phantom exit-logic comparison
- `scripts/dig_clean_break_deep.py` — Cohen's-d feature scan
- `scripts/dig_clean_break_v2.py` — gate testing on residual cohort
- `scripts/validate_pc_h1_peak_gate.py` — rejected gate validation

### Methodology takeaways for next session

1. **Visual chart pattern → gate hypothesis frequently overfit**. The
   pc_h1+peak gate looked obvious from AMERICA/SELLOR charts but
   rejected on lifetime data (would have killed HANTAGUY/soothsayer).

2. **Held-out feature mining > in-sample tuning**. The TP1=100% change
   (in-sample n=377) and the chart_conf gate (held-out n=1507) showed
   the value of forward-test data over recent-trade replay.

3. **Counterintuitive findings often have edge**. `chart_pattern_5m_conf
   >= 80` predicting LOSSES was non-obvious — only emerged from
   feature-space scanning, not chart inspection.

4. **Watch trigger interactions**. The C/D gates initially killed
   compound-trigger winners until the carve-out fixed it. Per-trigger
   gates can have unintended effects on compound entries — always
   re-validate after adding new gates.

### Pending issues / future work

- `clean_break` gate D (regime<10) only catches 1 trade today (Hantavax).
  May be sample-noise — keep watching forward.
- Anti-rebuy logic still not shipped. GMAR x6 today suggests structural
  need for "cooldown after 2+ same-day losses on same token" — but the
  dev<1 gate already addresses GMAR specifically.
- Phantom-validated filters P_slip_vel and C_var_b not yet promoted to
  ENFORCED. Phantom showed +$0.65/trade and +$0.58/trade lift over the
  current production stack. Worth validating in next session.

---

## 2026-05-07 PM update — high_regime tightening + price feed pair-pinning

**Latest commit**: `775dc51` (deployed)

Late-evening session focused on quality-control bugs surfaced by live monitoring:

### What shipped this evening

1. **`7cfefc3` — trigger_high_regime narrow gate**: After HENTAI -12% stop
   (peak=1786%, vs=0.07), added suppression `peak_h24_6h>=1500 AND vol_spike<0.10`.
   Lifetime-validated: catches HENTAI without killing closed winners.

2. **`7799f12` — DexScreener pair-pinning fix**: PENGUIN bug — bot bought on
   pumpswap pair ($0.004) but `current_price` was being overwritten by raydium
   pair ($0.163, 37× discrepancy) because feed picked highest-liquidity pair
   per token. Fixed in two places:
   - `feeds/price_feed.py::_process_pair_update` — drops ticks whose
     pairAddress doesn't match the pinned pair
   - `feeds/price_feed.py::_poll_batch` — first pass picks pinned pair before
     falling back to highest-liq
   - `core/trader.py::buy()` now passes `pair_address` to
     `_dex_price_feed.subscribe_token` at both entry sites
   - `core/trader.py::register_dex_price_feed()` re-subscribes restored
     positions with their pair_address on bot startup
   Affects every multi-pair token (most tokens).

3. **`775dc51` — high_regime broader gate**: After GMAR fired high_regime
   twice (peak=180%, below the 1500 threshold), added second suppression
   `vol_spike < 0.30 AND 1m_last_close < 0` (seller_dead_vol). Tradeoff:
   saves HENTAI ($2.80) plus prevents 2-3 GMAR-style entries per session,
   costs Goblin 13:27 ($3.34, ramping uptrend where 1m red was consolidation
   noise). Net -$0.54 closed but eliminates the most painful loss pattern.

### Monitoring window (19:02-19:22 UTC)

Live monitor of open positions to catch entry-quality issues in real time.
Findings:
- **GMAR 19:05** fired high_regime BEFORE the 775dc51 deploy — caught bad
  shape (lc=-0.026, vs=0.048, peak=180, dev=0.5%, 1h=-8%). My new gate
  would have suppressed it.
- **HANTAGUY 19:17** fired clean_break with `5m=-47%` and `peak=1647%` —
  looked like a knife-catch, but **closed at TP1 +$1.74** (V-bottom
  recovery). Lesson: peak-height alone is NOT a discriminator. `bs_h6=7.25`
  (long-term buyer dominance) was the real signal that distinguished
  HANTAGUY (won) from HENTAI 18:26 (`bs_h6=1.23`, lost).
- **18:48 HENTAI re-entry** I'd initially called a "pump detection" — on
  closer review, position was already -1.86% red at 250s before the
  cancel-on-restart hit. The `5m_consec_green=1` was consolidation noise
  inside a continuing slide, not a real reversal.
- **Operational lesson**: 3 of 4 in-flight positions today were closed by
  cancel-on-restart from deploys, not real exits. Don't deploy while
  positions are open in paper mode unless we're willing to lose the
  in-flight P&L.

### Pending decisions / known issues

- **clean_break is blind to violent 5m collapses** — caught HENTAI 18:48 and
  HANTAGUY 19:17 (5m=-47%) on a single +0.9% green bar. HANTAGUY recovered;
  HENTAI didn't. No clean gate found yet that catches the losses without
  killing winners. Probably needs a multi-feature combo (peak + bs_h6 + vol
  pattern).
- **filter_a still SHADOW** after May-1 overfit revert. Detects peak>200%
  and liq<167k but doesn't enforce. Revisit after collecting forward data.

---

## 2026-05-07 update — fast-mover trigger expansion (19 parallel triggers, +1 axiom fix)

**Latest commit**: `f0c8c1b` (deployed — Railway build kicked off)

Today's session focused on the slow-mover entry problem: bot was buying tokens
that sat flat post-buy. Fixed root-cause Axiom issue then mined 7 new
fast-mover triggers across 4 mining rounds.

### What shipped today

1. **Axiom keep_alive fix** (`f86176f`) — Root cause of 10h no-buy drought.
   `keep_alive()` was never started in dip-only mode; auth token refresh only
   fired at WS connect. Now launches unconditionally.
2. **filter_stale_watch demoted to SHADOW** (`b59c5ab`) — lifetime data showed
   it was costing winners.
3. **filter_stairstep SHADOW** (`19717f7`) + **filter_seller_imbalance SHADOW**
   (`3c57363`) — additional seller-pressure gates, gathering data.
4. **trigger_high_regime ENFORCED** (`47010ce`, 12th) — additive entry on
   high-regime + 1m positive momentum cohort (small n=9 historic).
5. **trigger_momentum_continuation ENFORCED** (`0bd955b`, 13th) — cg≥4 + vol≥1.5x.
6. **trigger_explosive_break + range_expansion_qualified** (`82ed825`, 14th + 15th).
7. **trigger_6of7_green_vol + trigger_hh10_strict_vol** (`628252f`, 16th + 17th).
8. **trigger_hh10_8plus** (`4705a9c`, 18th) — 8+ HH in last 10, no vol gate.
9. **trigger_vol_velocity_2grn** (`f0c8c1b`, 19th, **TODAY'S LAST SHIP**) —
   gap-mined; rising vol velocity (v[i]>v[i-1]>v[i-2]) + 2 grn + body≥2% +
   vol≥1.0x avg30. Backtest 64.1% WR, +$1.42/trade, n=690 lifetime gap.
   Multi-cohort robust (60-65% WR across all 5 fast-mover defs and full universe).
10. **DIP_MIN_MCAP 1M → 100k** (Railway env + code default).
11. **DIP_MIN_VOLUME_H24 500k → 200k** (Railway env).

### Trigger inventory (19 parallel ENFORCED, OR logic, no priority)

clean_break, 4combo, quiet_pop, deep_breakout, capit_v, engulf_low,
hc4_6pct, coil_long, range_decay_4bar, range_decay_4of5, coil_top_vol,
high_regime, momentum_continuation, explosive_break, range_expansion_qualified,
6of7_green_vol, hh10_strict_vol, hh10_8plus, **vol_velocity_2grn**.

### Mining rounds & yield

| Round | Approach | Yield |
|---|---|---|
| 1 | Initial fast-mover scan (+10%/20min cohort) | 1 trigger |
| 2 | Tightened candidates | 2 triggers |
| 3 | Multi-cohort exhaustive | 2 triggers |
| 3c | Cohort exploration (15%/60min) | 1 trigger (hh10_8plus) |
| 4 (today) | **Gap analysis** — bars NOT caught by prior 18 | 1 trigger (vol_velocity_2grn) |

Cohen's d analysis on the gap cohort showed all 1m-bar features have |d| <
0.18 (small effect), suggesting we're near the ceiling of pure-bar pattern
mining. The remaining FAST_WIN gap (~16k bars) likely requires entry_meta
context (regime, holders, depth, age, mcap), not bar shape alone.

### Pending forward validation

All 8 triggers shipped today are in their first 24h of forward data. Watch
for:
- Trigger fire rates per cycle (some may rarely fire — that's fine, they're
  parallel additive)
- WR per trigger source (`entry_meta.trigger_sources` field)
- Any regression in overall WR vs prior 10-trigger baseline

---

## 2026-05-05 update — filter_fake_bounce re-enforced

**Latest commit**: `afc8e02` (deployed — Railway build kicked off)

Re-enforced `filter_fake_bounce` in `feeds/dip_scanner.py` after this morning's
"buying into downtrends" pushback. Filter blocks entries where the last 1m
candle closed >+1.75% on volume_spike <0.30 (fake green pulse on dead volume).

- **Lifetime validation**: BLOCK n=3, 0% WR, total -$148.15
- **Winner regression set**: 0 winners blocked
- **Caught of 5 overnight losers**: 2/5 (Goblin 04:27 via filter_corpse, NKT 02:04 via filter_fake_bounce)

**Unsolved loser shapes (3 of 5 still uncaught)**:
- Wish (flat-range top) — peak_floor>=20% gate already deployed forward
- EITHER 01:18 — all current filters pass; possibly stop-tightness or noise
- Goblin 10:39 — pc_h1_change_since_lookback=-7.28 flags it but n=3 forward; need ~30 trades

---

## Deployed state (prior session, 2026-05-04)

**Prior commit**: `d90e0da` (build pending swap as of 17:30 CT / 22:30 UTC)
**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode**: PAPER (PAPER_TRADING=true)
**Strategy**: dip_buy at $20 sizing, max 3 concurrent, -12% stop, +8% TP1 (sell 50%) / +12% TP2 (sell 100%)
**Trading window**: 24/7 (TRADING_START_HOUR_CT=0, END=24)
**Open positions** (last check): BURNIE, BULL, maxxing — all fresh entries with new instrumentation

## What shipped this session

### Filter cascade revert (commit 396b5d8)
Moved 6 May 1-2 filters to shadow mode. Restored stop=-12%, TP1=+8% sell 50%, TP2=+12% sell 100%.

### Post-TP1 trail restored (commit b36cb51)
3.5% trail from peak after TP1 hits.

### Tier-2 instrumentation (commit bfb47c6)
`feeds/tier2_features.py` — 7 features added to entry_meta as shadow:
- `vwap_1h_usd`, `pct_above_vwap_1h`
- `pct_off_peak`, `minutes_since_peak`
- `higher_low_5m`, `hl_delta_pct`, `n_swing_lows_found`
- `rsi_5m`, `rsi_15m`, `bb_pos_5m`, `bb_pos_15m`
- `top10_buyer_within_60s_count`, `top10_buyer_time_spread_sec`, `bundle_v2_suspected`
- `buy_size_mean_last60s`, `buy_size_max_trend`, etc.
- `regime_dip_breadth_pct`, `regime_h1_neg_pct`, `regime_n_tokens_scanned`

### Tier-1 + Tier-3 instrumentation (commit 1abc1b4)
- **`feeds/tier3_features.py`**: support touches, wick:body ratios, freq derivative, net flow windows, hours_since_graduation
- **`feeds/smart_money.py`**: SmartMoneyIndex (loads JSON index, scores recent_trades) + extract_top_makers (captures wallet IDs in entry_meta for bootstrap)
- **`scripts/build_smart_money_index.py`**: Offline index rebuilder. Parses all_trades.json, scores wallets by winner-appearance count, writes data/smart_money_index.json
- **`feeds/dev_wallet.py`**: DevWalletTracker — uses `getTokenLargestAccounts` Solana RPC to identify creator wallet (filters program IDs), persists baseline, computes dev_pct_remaining/dev_pct_dumped. Throttled 5min cache to bound RPC pressure. Reads `SOLANA_RPC_URL` env var.
- **Jupiter slippage curve**: extends inline jup_features in dip_scanner. Adds `slip_buy_500/2000/5000_pct`, `slip_sell_*`, `slip_*_curve_steepness`. Six Jupiter calls per signal, parallel via `asyncio.gather`.

### Position manager log fix (commit d90e0da)
Startup log now correctly shows DIP/SCALP/MC TP values explicitly. Previously showed only legacy +35% which was misleading (legacy TPs don't apply to dip_buy).

## Verification status (last run)

`python scripts/post_deploy_full_check.py`:
- API endpoints: 5/6 healthy (one 404 was a script bug — `/api/pnl-chart` doesn't exist on multichain-bot)
- Tier 0/1/2/3 entry_meta keys: 100% populated on latest 3 buys (BURNIE / BULL / maxxing)
- `dev_pct_remaining` populated on all 3 (3.66%, 3.26%, 3.93%) — RPC integration works
- `top_buy_makers_n`: 11/13/15 — wallet capture working
- `smart_wallet_count_60s` = 0 (expected — index empty until first rebuild)
- `bundle_v2_suspected = False` on all 3
- No ERROR/Traceback in last 500 lines

## Known issues / followups

1. **DexScreener WS handshake failing** — falls back to polling. Not critical (price feed still works) but worth investigating later.
2. **Smart-money index empty** — needs first rebuild. Wait until ~30-50 trades have `top_buy_makers` captured (~24h post-deploy), then run `python scripts/build_smart_money_index.py` to bootstrap.
3. **Jupiter slippage curve fail rate** — ~33% of trades had `slip_buy_500_pct=N/A` in initial sample. Could be Jupiter timeout / illiquid token / race condition. Fail-open is correct but could improve.
4. **Tier 1 dev wallet coverage** — public Solana RPC heavily rate-limits `getTokenLargestAccounts`. Throttling helps; for full coverage would need paid RPC.

## Next steps

1. Wait ~24h for forward data with Tier 1+2+3 features.
2. Rebuild smart_money_index from accumulated `top_buy_makers` data.
3. Re-run `scripts/filter_combo_exhaustive.py` with widened feature library (now ~150+ candidate features) to find combos that exploit the new signals.
4. Promote highest-CI_lo combos from shadow to enforced.

## Tier 1 outstanding (not yet built, deferred per user decision)

- Smart-money wallet enrichment from external winners (Option B from earlier discussion) — DexScreener trending history scraper to expand the wallet universe beyond our own trades.
- Birdeye API integration as an alternative to public Solana RPC (would unlock 100% dev_wallet coverage at $20-100/mo).
