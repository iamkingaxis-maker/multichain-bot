# Trough Anatomy Study — entry delay / stabilization EV curve on OUR pond's flushes
2026-07-05. Data: paper book buys of `badday_flush` + `badday_allday` since 2026-07-01, minute OHLCV from GeckoTerminal (USD, retro via `before_timestamp`).

## TL;DR
**Immediate fire does NOT stand — but neither does "wait 49 minutes."** Our pond's flushes stabilize FAST (confirmed higher-low median 4 min after the low; first +6% bounce median 5 min). The EV curve peaks in the first 1-5 minutes AFTER the local low and decays monotonically; 30-60 min delays are as bad as what we do now. Our actual leak is that we fire MID-flush: median fill is **+14.8% above the eventual flush low**, and in **54% of episodes the low is still ahead of us** (median 1.5 min after our buy). The implementable decision-time trigger — first confirmed higher-low (P-HL) — flips per-episode EV from **-2.51pp to +1.03pp** (crude exit model) and holds direction in both half-splits and in both never-green and other cohorts. **Ship a confirm-window state machine in fast-watch.**

## Episodes
- 404 buys since 07-01 (both bots, `?limit=1000&meta_keys=...`, one pull each), deduped per token per 2h (mirrors + rebuys collapsed) → **172 distinct flush episodes**. NO sampling — full population fetched (172/172 bars pulls OK).
- Scored: **160**. Excluded: 11 time-censored (buy < 2.5h before fetch wall 13:30 UTC — forward window incomplete), 1 pool unknown to GT. By day: 07-01: 20, 07-02: 39, 07-03: 36, 07-04: 47, 07-05: 18.
- 109/160 flagged **never-green** (first joined sell shows peak<=+0.5% and pnl<0), 51 other.
- Bars: GT minute OHLCV, `before_timestamp = buy+4h`, limit=1000 → covers [buy-30m, buy+4h] for every episode. **Note for future studies: the io.dexscreener bars endpoint IGNORES the `to` param at res=1** (returns latest-N only) and quotes in SOL when `q=SOL` — retro minute bars must come from GT (3s pacing + backoff; ~90 min wall for 172 pulls under 429 throttle).

## Definitions
- flush_low / t_low = min low and its minute in [buy-30m, buy+30m].
- Outcome per entry: walk minute bars 60m forward; **TP1-first** = high >= +6% before any low <= -7% (same-bar tie counted as stop — conservative); MAE-30m; close@+60m (**conservative variant**, no peak-picking); crude net pp = TP1-first: 0.75×6 + 0.25×clip(close60, -7, +6); stop-first: -7; neither: clip(close60).
- P-delay(d): entry at close of first bar within 5 min after t_low+d; no bar = tape silent = no fill ("avoid"). P-HL: first higher-low bar after t_low with next-bar confirmation, entry at confirm-bar close. P-VOL: first bar after t_low with vol >= 1.5× flush-bar vol AND close>open.
- **Honesty flag: all P-delay(d) policies anchor on an ORACLE t_low** (you only know the minute was THE low in hindsight). They map the EV curve; they are not directly shippable. P-HL and P-VOL are decision-time implementable.

## Policy scoreboard — ALL scored episodes (n=160)
`medUp60` is the OPTIMISTIC forward-high proxy (known to overstate); `medC60` = close@+60m is the conservative variant. `netEV` = crude pp/episode on fills; `netEV_a0` counts avoids as 0pp.

| policy | n | avoid | TP1-b4-stop % | stop % | medUp60 (proxy) | medMAE30 | medC60 (conserv.) | netEV | netEV_a0 |
|---|---|---|---|---|---|---|---|---|---|
| **P0 (ours)** | 160 | 0 | 36.9 | 60.6 | +9.3 | -9.3 | **-4.5** | **-2.51** | -2.51 |
| D+1m (oracle) | 158 | 2 | 77.8 | 19.0 | +15.9 | -3.0 | +4.1 | +2.66 | +2.63 |
| D+3m | 158 | 2 | 63.9 | 32.3 | +12.6 | -4.2 | +0.7 | +0.84 | +0.83 |
| D+5m | 159 | 1 | 62.9 | 34.0 | +12.5 | -3.6 | +1.5 | +0.74 | +0.74 |
| D+10m | 160 | 0 | 58.8 | 35.6 | +11.8 | -5.4 | -1.0 | +0.21 | +0.21 |
| D+15m | 159 | 1 | 55.3 | 37.1 | +11.5 | -5.1 | -1.9 | -0.16 | -0.16 |
| D+30m | 156 | 4 | 47.4 | 46.2 | +7.0 | -6.5 | -3.5 | -1.08 | -1.05 |
| D+45m | 160 | 0 | 37.5 | 51.9 | +6.6 | -7.4 | -3.7 | -2.11 | -2.11 |
| D+60m | 159 | 1 | 40.3 | 47.8 | +7.2 | -6.7 | -3.0 | -1.63 | -1.62 |
| **P-HL (implementable)** | 160 | 0 | **64.4** | **30.6** | +12.5 | -4.2 | **+0.8** | **+1.03** | +1.03 |
| P-VOL | 90 | 70 | 63.3 | 27.8 | +13.6 | -3.7 | -0.5 | +1.02 | +0.57 |

"Avoided dead" barely exists for delay policies (0-4 of 160) — our pond's flush tokens keep printing bars; the avoid channel is not where the edge is. P-VOL's 70 no-fills are the trigger being too strict at bar granularity (many bounces never print a 1.5× flush-volume up-bar), not dead tokens.

## Half-splits (direction must hold — it does)
| policy | H1 netEV (n=80) | H2 netEV (n=80) | H1 medC60 | H2 medC60 |
|---|---|---|---|---|
| P0 | -2.61 | -2.42 | -4.3 | -5.1 |
| D+1m | +2.80 | +2.53 | +3.1 | +5.5 |
| D+5m | +1.35 | +0.13 | +1.5 | +0.6 |
| **P-HL** | **+1.59** | **+0.47** | +0.9 | +0.7 |
| D+30m | -1.60 | -0.54 | -4.3 | -3.3 |
| D+45m | -2.41 | -1.81 | -4.7 | -2.0 |

P-HL is positive in both halves (weaker H2 but same sign); every delay >= 15m is negative or ~0 in both halves. P0 is worst-in-class in both halves.

## Cohort split — never-green is an entry-timing artifact
| policy | never-green netEV (n=109) | other netEV (n=51) |
|---|---|---|
| P0 | **-4.76** (stop rate 78.9%, medMAE30 -12.1) | +2.28 |
| D+1m | +1.99 | +4.12 |
| P-HL | **+0.51** | +2.13 |

The episodes that killed us (never-green, -4.76pp each under this model) become roughly breakeven-to-positive when entry waits for the first confirmed higher-low. This corroborates the never-green decode from the other side: no big buyer met the dump YET when we fired — the HL confirm is the "dump has been met" signal at price granularity.

## Anatomy histograms (n=160)
Minutes from t_low to first CONFIRMED higher-low (median 4.0, max 28 — 100% inside 30 min):
```
[ 2, 5): 110  ############ (69%)
[ 5,10):  35  #### (22%)
[10,20):  13  # (8%)
[20,30):   2
```
Minutes from t_low to first +6% touch above the low-bar close (median 5.0; 74% inside 20 min; 94% found within 4h):
```
[ 0, 2): 21   [ 2, 5): 48   [ 5,10): 28   [10,20): 24
[20,30):  7   [30,60): 13   [60,120): 4   >=120: 5   never: 10
```
Our fill vs the flush low: median +14.8% above (IQR +9.1 to +25.9). t_low relative to our buy: median +1.5 min (54% of lows happen AFTER we've already bought).

**The 49-minute elite-dipper delay does NOT transfer to our pond: scored on our own flushes it is -1.6 to -2.1pp/episode — as bad as immediate fire.** Elite wallets fish a different (older, larger) pond; ours bounces or dies inside ~10 minutes.

## Honesty notes
- medUp60 is the optimistic forward-candle proxy (memory rule: scorer overstates); the conservative close@+60m column and the TP1-before-stop ordering walk are the load-bearing numbers. Both agree on ranking.
- The netEV exit model (+6 TP1 on 75%, -7 floor, clip elsewhere) is crude and identical across policies — only relative comparisons are meaningful. It ignores our real trail/stall exits and fees (~equal per policy; fees hit high-churn P0 hardest, so this UNDERSTATES the gap).
- Entries for D/HL/VOL are at bar closes (1-min granularity); live fills via fast-watch (2-3s sampling) would be finer-grained; slippage applies to all policies alike.
- t_low for D-policies is an oracle (stated above); P-HL and P-VOL are fully decision-time.
- 11 episodes censored for recency; 1 no GT pool; otherwise full-population coverage (no sampling).
- Never-green flag uses first joined sell per episode; mirror-bot rebuys collapsed, flag OR-ed across collapsed buys.

## Verdict + implementation
**Best implementable policy: P-HL (first confirmed higher-low after the running low).** Per-episode swing vs P0: **~+3.5pp on the crude model (-2.51 → +1.03), TP1-before-stop rate 36.9% → 64.4%, stop rate 60.6% → 30.6%, median MAE-30m -9.3% → -4.2%**, holds in both half-splits and in both cohorts. The oracle D+1m ceiling (+2.66) shows most of the theoretical edge is captured by HL's +1.03; a print-level confirm in fast-watch may recover more of the gap than 1-min bars can.

Buildable with EXISTING machinery (confirm-window state machine):
1. Dip signal fires → **ARM, don't buy**. Fast-watch already samples fresh prices every 2-3s; track `running_low` from those samples.
2. Fire the buy when price makes a **confirmed higher low**: no new `running_low` for >= 120-180s AND last price >= running_low × 1.01 (two consecutive 60-90s windows with rising lows = the bar-level pattern scored here).
3. **Max arm window 30 min** (100% of confirmed HLs arrived inside 28 min; median 4). Arm expiry with no confirm = no trade (in this dataset that path is rare and was the death path).
4. Invalidation: tape silent > 5 min or price < arm-price × 0.5 → disarm (rug/death).
5. Median wait from our current signal to HL fill is ~7 min; in ~46% of episodes the low precedes our signal so the confirm can fire almost immediately — the state machine must check the pattern against the pre-arm price buffer (warm tape cache) at arm time, not start blind.
6. Ship as SHADOW stamps first (arm_ts, running_low, confirm_ts, confirm_px vs actual fill) on the badday bots, then A/B one mirror with enforcement — 74% of episodes the HL fill was also CHEAPER than our actual fill (median -3.8%), so this is not paying up for safety.

Do-not-do: 30-60 min stabilization delays (negative here), volume-bar confirmation as a hard AND (44% no-fill), or reverting any of this on GT/DS bar-availability grounds (bars only, GT retro — DS `to` is dead).

Intermediates: session temp scratchpad (`_ta_episodes_all.json`, `_ta_episodes_keyed.json`, `_ta_rows.json`, `_ta_anat.json`, `_ta_scoreboard.txt`, `ta_gt/` bar files, `build_episodes.py`, `fetch_gt.py`, `ta_analyze.py`, `ta_sweep.py`, `_ta_sweep_out.txt`).

## Sensitivity sweep (2026-07-05 follow-up: is P-HL robust or knife-edge?)
Same 160 cached episodes, no refetch. Grid over the HL trigger: confirm bars c in {1,2,3} (HL bar + 0/1/2 subsequent bars holding above the prior low) x bounce requirement f in {1.000, 1.005, 1.01, 1.02} (entry close >= flush_low x f). Entry at close of the last pattern bar; `netEV` = crude exit model on fills; `evC60` = conservative close-only EV (mean clip(close@60m, -7, +6)) — no peak-picking, no TP1 credit.

| cell | n | avoid | TP1-b4-stop % | stop % | medUp60 (proxy) | medC60 | netEV | evC60 |
|---|---|---|---|---|---|---|---|---|
| HL c=1 f=1.000 | 159 | 1 | 65.4 | 30.2 | +14.0 | +1.4 | +1.06 | +0.52 |
| **HL c=1 f=1.005** | 158 | 2 | 66.5 | 29.1 | +14.2 | +1.6 | **+1.18** | **+0.56** |
| HL c=1 f=1.010 | 158 | 2 | 65.8 | 29.7 | +14.0 | +1.3 | +1.10 | +0.50 |
| HL c=1 f=1.020 | 157 | 3 | 65.6 | 30.6 | +14.0 | +1.8 | +1.03 | +0.48 |
| HL c=2 f=1.000 | 159 | 1 | 62.9 | 32.7 | +12.5 | +0.7 | +0.83 | +0.14 |
| HL c=2 f=1.005 | 158 | 2 | 63.9 | 31.6 | +12.5 | +0.7 | +0.94 | +0.17 |
| **HL c=2 f=1.010 (shipped analog)** | 158 | 2 | 63.9 | 31.6 | +12.5 | +0.7 | **+0.94** | +0.17 |
| HL c=2 f=1.020 | 157 | 3 | 63.7 | 33.1 | +12.4 | +0.6 | +0.82 | +0.05 |
| HL c=3 f=1.000 | 159 | 1 | 64.2 | 32.7 | +11.8 | +0.9 | +0.82 | +0.04 |
| HL c=3 f=1.005 | 157 | 3 | 65.6 | 31.2 | +11.9 | +0.9 | +0.98 | +0.12 |
| HL c=3 f=1.010 | 157 | 3 | 65.6 | 31.2 | +11.9 | +0.9 | +0.97 | +0.06 |
| HL c=3 f=1.020 | 157 | 3 | 65.6 | 31.8 | +11.6 | +0.9 | +0.94 | +0.04 |
| D+3m (oracle ref) | 158 | 2 | 63.9 | 32.3 | +12.6 | +0.7 | +0.84 | +0.24 |
| D+5m (oracle ref) | 159 | 1 | 62.9 | 34.0 | +12.5 | +1.5 | +0.74 | +0.29 |

**12/12 grid cells positive on crude netEV AND 12/12 positive on conservative evC60** (P0 baseline: netEV -2.51, evC60 well negative). Parameter surface is FLAT: bounce factor is a don't-care (spread <= 0.15pp within any c), confirm depth c=1 slightly beats c=2/c=3 (+1.1 vs +0.9 — earlier fill, less bounce forfeited), extra confirmation bars (c=3) add nothing.

**Leave-one-day-out (headline cell c=2 f=1.01):** netEV positive in ALL 5 splits (+0.65, +0.80, +0.91, +1.03, +1.27); P0 is -2.17 to -2.77 in every split, so the RELATIVE improvement (~+3pp/episode) survives every day-drop. Soft spot to disclose: the conservative close-only evC60 dips slightly negative on 2 of 5 splits (drop-07-02: -0.24, drop-07-04: -0.08) — i.e., in pure close@60m terms the absolute edge is ~zero without the TP1 leg; the case for HL is "stop bleeding -2.5/episode and capture TP1s", not "closes are higher an hour later".

**Per-bot cohort (headline cell):** badday_flush n=17 netEV +0.26 (evC60 +1.48), badday_allday n=141 netEV +1.02 (evC60 +0.01) — both positive; flush n is small, treat its point estimate loosely.

**Winner-cost / fat-tail check (headline cell):** only 2/160 episodes get NO fill (SHROOM 07-02, Martolexx 07-03) and both were P0 stop-outs (-7) — the misses were wins. Worst degradations: 5 episodes where P0 hit TP1 but the later/higher HL entry stopped out — HOPE 07-01, HOPE 07-02 (P0 up60 +58%), ice 07-05, BILLBOARD 07-02, #fairs 07-01 — a -13pp swing each, ~3% of episodes. No large winner is missed entirely; the trigger's cost is occasional worse entry on instant V-bounces, already inside the aggregate numbers.

**Low-before vs low-after signal (headline cell):** low-BEFORE our signal (n=74): HL +1.60 vs P0 +0.55; low-AFTER (n=84): HL +0.36 vs P0 -5.15. Both halves positive, so the value is NOT carried solely by the warm-cache-seeded cohort — the dominant win is refusing to buy the falling knife (low-after swing +5.5pp/episode). Warm-cache seeding at arm time is still wanted (the low-before cohort fills near-instantly and carries the highest absolute EV) but is priority 2 behind the arm-and-wait machinery itself. Conservative caveat: low-AFTER evC60 is -0.68 — that cohort's edge is TP1-shaped, not close-shaped.

**Sweep verdict: ROBUST.** Positive in 12/12 grid cells (crude AND conservative), all 5 leave-one-day-out splits, both bots, both low-timing cohorts; only soft spot is the close-only variant hovering around 0 on two day-drops. Best cell: c=1 f=1.005 (+1.18 / +0.56). Config recommendation for the shipped state machine: **keep bounce ~1.005-1.01 (immaterial within 1.000-1.02); shorten the no-new-low hold from ~150s toward ~90-120s (c=1/c=2 territory, worth ~+0.2pp and an earlier fill); do NOT add a deeper confirmation window (c=3 buys nothing).** Adjacent cells differ by <0.3pp — do not over-tune; every cell in the grid beats P0 by ~3-3.5pp/episode.
