# Market-Wide Flush Cascades — do pond-wide capitulation windows kill our dip buys? (2026-07-06)

**Question:** do flushes cluster across tokens in market-time, and do buys during cluster windows die while isolated flushes bounce? Motivated by the 07-06 morning loss cluster (SEMAN 08:48/09:29, Fro 09:45, Balloon 12:08, SEMAN/Fro ~12:55) vs DONALT winners mid-morning; plus the afternoon ACM/Richard loss clusters (~14:00-17:00Z).

**VERDICT: NO. Cascade-time is NOT a kill signal — every version of the predicate would have LOST money by preferentially killing winners. Do not build a pond-wide hold-fire gate.**

## Data
- Window: 2026-07-05 12:00Z → 2026-07-06 16:00Z (28h).
- Universe: 94 pairs = top-40 by tape activity (from the trough-validation study) ∪ all fleet fresh-window trade pairs ∪ all live-tape pairs alive past 07-06 05:00Z. 93 usable.
- 1m OHLC: cached `bars_full/` (05 12:00→06 11:29) + two incremental io.dexscreener pulls (`cb=1500`, 1 req/pair) extending to 06 16:00Z. Session scratchpad `bars_ext/` + `casc_fetch.py`/`casc_fetch2.py`/`casc2.py`/`casc3.py`.
- Fleet trades: fresh `/api/trades?full=1&limit=900` pull (`fro_tail.json`, covers 07-05 02:23→07-06 16:00, includes ACM/Richard). Entry ts = sell.time − hold_secs; win = pnl_pct>0; deduped to (token, entry-minute) "decisions" so A/B siblings don't multiply-count: **86 decisions, 17 winners**.
- SOL/USD 1m: GeckoTerminal, Raydium SOL-USDC pool, full window.
- Tapes were NOT usable as the primary event source: the recorder has a gap 07-06 04:00→11:00Z (per-session, no 24/7) exactly over the morning loss window. Bars cover it. No flow-fallback pairs were needed — all 93 pairs have bars (flow fallback: not used).

## Method notes (two definitions discarded on the way — both matter for future work)
1. **Price-trough (±15-bar local min) events are unusable for bounce outcomes**: the definition guarantees no lower low for 15 forward bars, so "+6% before −7% in 30min" succeeds 94% by construction. Discarded.
2. **Absolute cascade definitions are degenerate**: at −10%/10min drop onsets this pond runs **~94 onsets/hour market-wide (~13 distinct pairs flushing per any 10-min window)**. "≥3 troughs across distinct pairs within 10 minutes" is true for **100% of the 28h window** (also true at ≥4 and ≥5/15min; K/W sensitivity all degenerate). A pond-wide "cascade window" in the absolute sense is the permanent state of this market, not an event.
3. Final framing: **event = drop ONSET** (pair's 10-min close return crosses ≤−10%, real-time computable, no lookahead), 2,619 onsets/93 pairs; **intensity I(t) = distinct pairs with an onset in trailing 10 min** (minute-level: p25=11 med=13 p75=16 p95=21). Cascade-ness = high I.

## 1) Clustering test — flushes do NOT cluster across tokens
- 10-min-bin distinct-pair flush counts: mean 12.8, **Fano factor (var/mean) obs = 1.04** vs circular-shift null (per-pair onset trains shifted, 500 reps) **1.85 ± 0.22 → z = −3.8**: observed cross-pair alignment is *significantly smoother than random*, ≈ Poisson. p95 of bins obs 20 vs null 21.
- Cross-pair inter-onset gap median 60s — because the base rate is huge, not because of bursts.
- Conclusion: no market-time cascade structure beyond the (enormous) base rate. If anything the pond de-synchronizes (steady aggregate flush flow).

## 2) Bounce outcome inside vs outside high-intensity moments — flat
Outcome per onset: trough = min low within 15min of onset; bounce = +6% from trough low within 30min before −7% (2,551 resolved; overall 88.0% bounce / 7.6% fail / 4.4% neither).
| intensity quartile (n other pairs ±10min) | n | bounce% | fail% |
|---|---|---|---|
| Q1 ≤19 | 789 | 88.1 | 7.2 |
| Q2 | 515 | 86.6 | 9.5 |
| Q3 | 675 | 89.2 | 6.8 |
| Q4 >25 | 572 | 87.8 | 7.3 |
z(Q1>Q4) = 0.18 — nothing. Depth-controlled (ret10 −12..−10%) and deep-only (≤−16%) slices: same flatness (deep flushes bounce *slightly better* at Q4, 93.5% vs 92.7% Q1). Isolated flushes do NOT bounce better than cascade flushes.

## 3) Our trades — the relationship is INVERTED
86 decisions (17 W). WR by entry-time intensity I (excluding own pair):
| I at entry | n | WR | avg pnl |
|---|---|---|---|
| 0-9 | 16 | 12% | −2.6 |
| 10-13 | 34 | 15% | −3.9 |
| 14-16 | 19 | 21% | −1.6 |
| 17+ | 17 | **35%** | **+3.9** |
Blackout grid (block if I≥T): every T from 12 to 18 blocks a *higher-WR* cohort than it allows (blocked WR 23-43% vs allowed 12-16%); T=15 kills 10/17 winners and forgoes +78pp net. **Winner-kill fails catastrophically at every threshold** (⭐ winner-kill audit rule: needs ≤5%; actual ≥35%).
- Cited trades: the morning never-greens happened at LOW/median intensity — SEMAN 08:48 I=7 (p1 of window, the *quietest* moment), SEMAN 09:29 I=12, Fro 09:45 I=14 (p51). The 12:5x losses were high-I (19-20) but the two popeyes +35% winners fired at I=19-20 in the same minutes, and Fro 13:43 +9.4 at I=19. ACM (15:01/15:03) and Richard (14:39/15:12) losses: I=10-12 (p10-p27) — low intensity again.
- Loss-streak cross-check (session-discipline study): per-bot 3rd-consecutive-loss moments n=27 — under the absolute cascade definition 100% fall "inside cascade" because 100% of the window is cascade (uninformative); under the intensity framing they span low and high I. Loss streaks are NOT explained by pond-wide capitulation windows — they are more consistent with hour-of-day/tape-quality regime (the 09-13Z dead zone in the market rulebook) than with cross-token flush contagion.

## 4) SOL confounder — closed, negative
corr(minute I, SOL 10-min return) = **+0.04** (n=1,669). SOL 10-min ret median at I≥16: +0.01% vs I≤9: +0.01%. Window SOL was calm (p5 −0.37%, p95 +0.42%). High-flush-intensity moments are NOT SOL dumps on this window (a real SOL crash day remains untested — the existing crash-only SOL gate stays the right owner of that risk).

## 5) Verdict + what to do instead
- **Cascade-time is not a real kill signal.** Flush arrivals across the pond are Poisson-smooth; bounce rates are identical in and out of high-flush moments; and our realized WR was *best* when many tokens were flushing at once (13-16Z prime hours, high churn; I≥17: 6/17 = 35%) and *worst* in quiet minutes (I≤13: 7/50 = 14%).
- Do NOT ship "N tokens printing −X% in 10min → hold fire": at every (X∈{−6,−8,−10}, K∈{3,4}, T∈{12..18}) setting it blocks winners at 2-3x the rate it blocks losers.
- The morning loss cluster the question started from (SEMAN/Fro/Balloon) is better explained by the already-documented **hour-of-day regime** (09-13Z dead zone; market rulebook) than by pond contagion — SEMAN 08:48 fired at the window's intensity minimum, the literal opposite of a cascade.
- If anything is worth a shadow later: the weak POSITIVE read (entries at I≥17 → 35% WR, n=17, hour-of-day-confounded) is just the prime-hours effect wearing a new coat; the rulebook already encodes it. No new lever recommended.

## Caveats
- Single 28h window, one regime; decisions n=86 (17 W) — splits are coarse.
- Intensity uses the 93-pair studied universe as "the pond"; the scanner's live universe differs at the margin.
- Onset detection uses 1m closes (≤60s staleness); entries joined at second precision.
- Trade rows include multi-peel sells collapsed by entry-minute; pnl of first-listed sibling used.
- SOL check covers a calm-SOL window only; says nothing about behavior during a genuine SOL crash.
