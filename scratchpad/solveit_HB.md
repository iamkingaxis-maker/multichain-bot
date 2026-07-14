# HB — Were the "good" days (06-26/27/28) REAL or a stale-illusion?

## HEADLINE VERDICT
**The good days were REAL. The collapse is a REAL selection shift — NOT "paper stopped lying."**
The fill-honesty flip is a roughly CONSTANT ~3pp/day drag present on EVERY day (incl. the good ones);
it does not step-change at the cutover and cannot explain 06-29/30. The day-over-day collapse lives
on the *fully fill-invariant* snapshot book and in the fill-invariant selection/health metrics.

## Method
- 2,793 sells joined to nearest preceding buy by address (`_full_trades.json`, no RPC).
- Three P&L bases per trade, frac-weighted, clipped to [-100%,+1000%] (feed-glitch outliers like 7977%/30896% removed):
  - **S = exit_mid_price / entry_mid_price** — pure snapshot→snapshot, MAXIMALLY fill-invariant (immune to the fidelity flip entirely).
  - **A = exit_price / entry_mid_price** — stale entry basis, real exit.
  - **B = exit_price / entry_price** — honest fresh-fill basis (= as booked post-cutover).
- Cutover ≈ 2026-06-28T18:13 UTC. Bucketed by ENTRY day.

## DAILY A-vs-B(-vs-S) TABLE (frac-weighted mean %/trade, clipped)
```
day      n  tok    S%     A%     B%  | winB% winS% evgrn% medMAE entryGAP%
06-23  678   50   5.84  15.62  18.16 | 43.5  51.5  61.7   -4.09    3.04
06-24  464   23   2.58   2.27  -2.95 | 22.2  44.0  31.7   -4.75    5.04
06-25  395   19   1.20   0.44  -1.87 | 45.8  52.9  61.3   -3.65    2.12
06-26  429   20   3.08   2.32   0.11 | 51.5  56.6  76.2   -2.68    1.80
06-27  285   15   4.95   4.13   1.59 | 53.3  61.8  78.6   -3.28    1.95
06-28  402   26   9.17  15.66  12.25 | 53.5  68.0  77.9   -3.12    2.02
06-29   62    5   0.26  -0.21  -3.79 | 32.3  67.7  61.3   -4.25    0.93
06-30   78    4  -1.45  -1.87  -3.70 | 28.2  32.1  55.1   -5.27    1.55
```
(06-23 inflated by residual outliers even after clip — ignore; 50 tokens, noisy.)

## FILL-CONTAMINATION IS A CONSTANT, NOT A CUTOVER EVENT
Drag = B(honest) − S(snapshot) per entry-day:
```
06-23 -2.72 | 06-24 -5.52 | 06-25 -3.07 | 06-26 -2.96 | 06-27 -3.36 | 06-28 -4.16 | 06-29 -4.05 | 06-30 -2.25
```
~−3.4pp EVERY day, including the GOOD ones. This is round-trip fill/slippage cost, always present.
It turns a +3.1% snapshot day (06-26) into ~breakeven booked, and a −1.5% snapshot day (06-30) into −3.7% booked.
**Constant offset, not a regression. Paper "stopped lying" by the same ~3pp on good and bad days alike.**

06-28 split at cutover: PRE-cut (n=385) S=9.20 B=5.07 ; POST-cut (n=15) S=8.27 B=2.87 — selection edge intact across the cutover instant itself.

## FILL-INVARIANT METRICS STEP-CHANGED (the real signal)
- **Snapshot P&L (S):** 06-26/27/28 = +3.08 / +4.95 / +9.17% (genuinely positive) → 06-29/30 = +0.26 / −1.45%.
  The "good" was NOT a fake-fill illusion; it survives on the fully fill-invariant book.
- **% ever-green (peak_pnl_pct>0):** 76.2 / 78.6 / 77.9 (26-28) → 61.3 / 55.1 (29/30). Step drop.
- **Snapshot win-rate (winS):** 56.6 / 61.8 / 68.0 → 67.7 (29, but flat P&L) / 32.1 (30).
- **median MAE:** −2.68 / −3.28 / −3.12 → −4.25 / −5.27. Worse drawdowns.

## WHAT ACTUALLY REGRESSED = SELECTION DRIFTED INTO DEEP FALLING KNIVES
Pure entry-time inputs (fill-independent), entry-day medians:
```
day    pc_h6  rsi15  mtf  dev%  medBuy$  %mtf<=-2  %pc_h6<=0
06-26  +15.2  46.1  -2.0  10.5   15.4     52.0      29.4
06-27  -25.3  38.6  -3.0  11.1   21.4     65.6      65.6
06-28  -27.2  37.4  -3.0  14.1   22.8     78.4      74.9
06-29  -45.3  34.4  -4.0  10.3    7.4     82.3      82.3
06-30  -46.3  19.1  -4.0  13.4   40.7    100.0     100.0
```
Monotonic march into deeper/oversold/strong-bear tokens. By 06-30, 100% of buys are pc_h6<=0 AND
chart_mtf<=-2 (strong multi-tf downtrend), rsi median 19 (deep knife). The bot is buying nothing but
the deepest falling knives in a green market — classic dip-strategy-in-green-regime failure, plausibly
amplified by the RT_DIP/arm_only fresh-reeval stack admitting only the deepest movers.

## CAVEATS
- **Thin n:** 06-29 = 5 distinct tokens (n=62 fills), 06-30 = 4 tokens (n=78). The P&L "collapse"
  rests on 4-5 tokens and is fat-tail-fragile. 06-29 S is actually ~flat (+0.26, winS 67.7%); only
  06-30 is bad on every basis.
- Cannot fully separate confound (b) new fire-stack selection vs (c) green-regime here (both push the
  same direction: only deep knives qualify as "dips"). The selection-feature drift is real and monotonic.

## RECOMMENDATION
- Stop attributing the loss to the fidelity flip — it's a constant ~3pp round-trip cost on all days,
  not the cause of the 29/30 drop. The fix is NOT to revert fidelity.
- The real lever is SELECTION drift into deep strong-bear knives (pc_h6→−46, rsi→19, mtf 100% strong-bear).
  Gate/de-weight the deepest-knife cohort (e.g. block pc_h6<=−40 AND mtf<=−2 in green regime) OR pause
  dip-buying when the only available "dips" are strong-bear knives. Confirm on more tokens before enforce (thin n).
