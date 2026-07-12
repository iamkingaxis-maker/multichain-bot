# SOL young-lane SELECTION mine (2026-07-12)

AxiS: "repeat fills aren't as much of an issue as us not being able to identify a bad
entry from a good one; the entry amount is redundant." SELECTION is the only lever. Goal:
the entry-time feature(s) that SEPARATE green from red young-lane realized trips — or the
honest proof of what still doesn't and what data would decide it.

## Data
- Fresh /api/trades?full=1 (5000 cap, 07-08..12) + stale _full_trades.json (07-02..09),
  deduped. Young-lane = all `badday_young_*` bots (paper + `_live`).
- Joined sells -> prior buys for entry_meta. SCRUB RULE applied (dropped ret>0 & hold<10s).
- **955 realized trips, 151 distinct tokens, 438 green / 517 red.** All carry entry_meta.
- Token-median (per-token realized median) is the metric; ex-top-2 (Hoppy 44 legs, mogdog
  42) reported. Baseline **all-trips ex-top2 token-median = -5.8%** (winrate 45.9%) — the
  fat-tail signature: median trip loses ~6% while the book lives in the right tail.
- Build/analysis code: scratchpad/sol_selection/{build_dataset.py, analyze.py, oos.py,
  combo.py, neigh.py, _trips.json}.

## Rigor
Out-of-sample = a separator must keep a POSITIVE pass-minus-fail token-median gap in ALL
FOUR halves: chrono1 / chrono2 AND odd-day / even-day. Reported ex-top2 token-median per
half, distinct-token n per side, and p90 return (winner-kill / fat-tail preservation).

---

## 1. The NEW RH axes (arc position + proven volume + moderate dip) — DO NOT PORT

The RH candidate factory's separator (winners buy MODERATE pullbacks ~-8.6% EARLY in the
arc on PROVEN-volume pools; losers buy DEEP flushes LATE) **inverts on Solana young lane.**

| RH signature ported | pass ex2 tokmed | fail ex2 | gap (all 4 halves) |
|---|---|---|---|
| moderate dip (-20..-40) + early arc (peak_h24<750) + proven vol (>=800k) | **-6.7** | -5.6 | NEGATIVE every half |
| proven-volume isolated: vol<400k / 400k-1M / 1-2M / >2M | -6.3 / -6.3 / -6.1 / -4.9 | — | flat-red, no separation |

Arc-position proxies (lifecycle_peak_h24_pct, pc_h24) and pre-entry proven-volume
(entry_vol_h24, rt_buys_usd) do **not** separate green from red here. If anything the
highest-volume band is marginally *less* red (-4.9) — the opposite of "thin = loser." The
RH method was real on that chain; the signature does not transfer.

## 2. What DOES separate (single-axis quintile token-medians, ex-top2)

Every axis keeps a RED token-median in every bucket (the hard prior holds — no axis flips
green). But a consistent DIRECTION emerges: the lane is **least-red buying capitulation /
downtrend, worst chasing strength.**

| axis | least-red side | most-red side | direction |
|---|---|---|---|
| pc_h1 (1h change) | <=-48: **-3.5** | >-22: -6.5 | deeper 1h dip = less red |
| pct_off_peak | deepest (<=-950): **+1.5** | shallow (>-181): -7.0 | deeper off-peak = less red |
| h24_ratio_to_peak | <=0.31 (far below peak): **-4.0** | >0.65 (near peak): -7.6 | further below peak = less red |
| chart_mtf_score | <=-1 (downtrend): **-4.3** | >1 (uptrend): -8.2 (wr 23%) | downtrend = less red |
| chart_score | <=46.5 (low "quality"): **-4.0** | >52 (high): -7.0 | high chart-quality = a TRAP |

All five are the SAME underlying signal: buy the flush, not the breakout.

## 3. Out-of-sample threshold test (positive gap in all 4 halves?)

| gate (pass = less-red side) | gap>0 all 4 halves | n>=20/side/half | pass ex2 (ALL) |
|---|---|---|---|
| **pc_h1 <= -45 (DEEP capitulation)** | **YES** | no (thin halves 15/10) | **-3.0** (wr 51, p90 +28) |
| pct_off_peak <= -800 | YES | no | -0.8 |
| chart_score <= 47 | YES | no | -4.5 |
| h24_ratio_to_peak <= 0.35 | no (ODD -0.4) | no | -5.0 |
| chart_mtf_score <= -1 (down-MTF) | no (ODD -0.4) | yes | -4.3 |
| **BASELINE pc_h6>=0 OR liq>=48k** | **no (gap~0)** | no | -6.1 (fail -6.0) |
| **BASELINE mean_buy >= $34** | **no (INVERTS)** | yes | -6.3 (fail -6.2) |
| **BASELINE mtf < 0** | no (ODD fails) | yes | -4.3 |

**All three prior baselines FAIL the 4-half test on the young lane.** The pc_h6/liq
structure edge does not separate at all here (gap ~0); the buyer>=$34 gate INVERTS
(bigger buyers = marginally more red); down-MTF is directional but breaks in the odd half.

## 4. Best separator: DEEP capitulation (pc_h1 <= -45)

- **Separation:** ex2 token-median **-3.0** (deep) vs **-6.3** (rest); winrate 51 vs 44.
- **Out-of-sample:** positive gap in ALL FOUR halves (+1.9 / +6.2 / +1.8 / +5.4).
- **Neighborhood (overfit check):** every threshold -35, -40, -45, -50, -55, -60 shows a
  positive gap (+1.3 .. +3.3). Not a lone spike — the whole deep band separates.
- **Selection not exit artifact:** deep bucket med_peak +1.2 (vs 0.0), med_mae -2.5 (vs
  -2.8), shorter hold 83s (vs 163s) — the entries genuinely reach more upside and draw
  down less. It's the buy, not the sell.
- **Winner-kill:** p90 return in the deep bucket = +28 (ALL), +38 (chrono2/even) — the
  fat-tail winners live INSIDE the deep-dip cohort. Favoring deep dips does NOT clip the
  right tail; it concentrates it.
- **The honest ceiling:** it is LESS-RED, not GREEN. Pass side is still -3.0 token-median.
  And it is UNDERPOWERED — the two thin halves (chrono2, even) have only 10-15 distinct
  deep-dip tokens/side, below the n>=20 bar. So: a real directional gate, not yet a
  green-maker, not yet certifiable.

## Verdict

- **No axis flips the young-lane token-median green** — the fat-tail prior stands; winners
  and losers still overlap on every single feature. The book is carried by the right tail.
- **The one robust, out-of-sample, winner-preserving separator is DEEP 1h capitulation
  (pc_h1 <= ~-45).** It cuts the median loss roughly in half (-6.3 -> -3.0), beats all
  three prior baselines (which themselves fail OOS here), and keeps the fat tail. Direction
  is the OPPOSITE of the RH signature and of the "buyer-size / structure-edge / high-chart-
  quality" instincts — this lane is a knife-catcher's lane.
- **The specific data gap that would decide it:** ~2x more deep-dip trips per half. Today
  the deep-dip cohort is 52 tokens overall but only 10-15/side in the thin halves. Grading
  the shadow stamp forward (below) to n>=20 deep-dip tokens/side/half — and checking whether
  the pass side crosses from -3.0 into green on fresh tape — is the single measurement that
  turns this from "directionally real" into "enforce or kill."

## Wired (SHADOW ONLY — no enforce, no sizing, no commit)

feeds/dip_scanner.py, alongside the existing shadow stamps: `deep_capitulation_shadow` =
"DEEP" when entry pc_h1 <= -45 else "SHALLOW" (+ raw `deep_capitulation_pc_h1`), fail-open,
would-favor counter logged. Measure-only: it stamps every young entry for the realized
join so the separator grades forward on fresh tape. Enforce/de-size decision waits on
green pass side + n>=20 deep-dip tokens/side/half. py_compile OK.
