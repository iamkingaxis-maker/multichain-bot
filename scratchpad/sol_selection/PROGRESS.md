# SOL young-lane SELECTION mine — PROGRESS

## Done
- Pulled fresh /api/trades?full=1 (5000 cap, 07-08..12) + /api/live-swaps. Combined with stale _full_trades.json (07-02..09).
- Built joined young-lane trip dataset: scratchpad/sol_selection/_trips.json
  - 955 trips post-scrub (dropped ret>0 & hold<10s), 151 distinct tokens, 438 green / 517 red.
  - ALL have entry_meta. Axes extracted: pct_off_peak, entry_vol_h24, liq, arc proxies (pc_h1/h6/h24,
    lifecycle_peak_h24_pct, h24_ratio_to_peak, minutes_since_peak), demand (net_flow_15s/60s/5m,
    unique_buyers_n, buy_sell_imbal, buys_per_min, rt_*), buy-size (mean/median/p90), rug/supply
    (hidden_supply_pct, rugcheck_score, top10/top1 holder, lp_locked), baseline (pc_h6, liq, mtf, chart).

## DONE — findings
- Deliverable written: scratchpad/_sol_selection_mine.md (full axis tables + verdict).
- RH signature (moderate dip + early arc + proven vol) DOES NOT PORT — inverts. Proven-vol
  flat-red across all bands.
- Every single axis keeps a RED token-median (fat-tail prior holds). Direction: lane is
  least-red buying capitulation/downtrend, worst chasing strength/high-chart-quality.
- BEST SEPARATOR = DEEP 1h capitulation pc_h1<=-45: ex2 tokmed -3.0 vs -6.3 rest;
  positive gap in ALL 4 halves; neighborhood -35..-60 all positive; SELECTION not exit
  (med_peak +1.2 vs 0.0); winner-preserving (p90 +28). BUT least-red not green, and
  underpowered (10-15 tok/side in thin halves < 20 bar).
- All 3 prior baselines FAIL the 4-half test here: pc_h6>=0|liq>=48k (gap~0),
  mean_buy>=$34 (inverts), mtf<0 (fails odd half).
- WIRED shadow-only: deep_capitulation_shadow in feeds/dip_scanner.py (no enforce, no
  sizing, no commit). py_compile OK.

## Next (main session / forward)
- Grade deep_capitulation_shadow forward to n>=20 deep-dip tokens/side/half; check if pass
  side crosses -3.0 -> green on fresh tape. Then enforce-or-kill.
