# Price-Trough Timing Validation — 2026-07-06

**Purpose:** re-test the 07-06 behavior-decode timing finding (buys 0-60s after a trough → 57% winner share) with REAL PRICE troughs instead of the flow-drawdown proxy. Analysis only; no bot changes.

**Fresh window:** 2026-07-05T12:00Z → 2026-07-06T11:29Z (same as decode).
**Scripts / data (session scratchpad):** `step1_select.py`, `step2b_bars_full.py`, `step3_analyze.py`, bars in `bars_full/`, results in `step3_results.json` under `C:\Users\jcole\AppData\Local\Temp\claude\C--Users-jcole-multichain-bot\ecbaef77-2f98-4dc5-9231-4bd9a529e92c\scratchpad\`.

## Data & coverage
- Top 40 pairs by fresh-window trade count (range 6,911 → 1,368 trades). 72 pairs dropped below the cap (largest dropped: HATER, 1,318 trades) — full list in `top40.json`.
- **1m OHLC:** io.dexscreener bars endpoint (curl_cffi chrome, `res=1&cb=1500`, all 40 pairs pumpswap→`pumpfundex` slug) caps at ~999 bars (~16h); the older half was filled from **GeckoTerminal minute OHLC** (`currency=token` = SOL-denominated, `before_timestamp` pagination). Seam verified: worst adjacent-bar jump at the DS/GT seam 0.0-0.1% → scales match, merged series clean. Requests paced 2.3-3s, single process, zero 429 failures.
- **Bars obtained: 40/40 attempted. Analyzable: 39/40** — Bullmerica250 skipped (bars end 10:44Z, >30min short of window end). "Partial-looking" pairs (Silas, TAC, WIFBULL, ANSEMJAK...) are young pools born mid-window; bars cover their full traded life, so they are IN.
- Tape dedupe key `(ts, maker, kind, usd)` (tapes carry no tx sig).

## Trough definition (documented change from spec)
- **Spec starting thresholds (≥3% prior decline / ≥2% bounce) were degenerate:** 73-87 troughs/pair (~3.4/hour) — on these tokens a 3% wick is noise; every buy would sit "near a trough".
- **Final candidate definition (DIP tier):** local min of the 1m low series (min over ±15 bars, first-of-tie), prior decline ≥10% from max high of the preceding 60 min, bounce ≥5% within the following 15 min, troughs <600s apart deduped keep-lower. → median 23 candidates/pair.
- **FLUSH tier (primary, decode-comparable):** per pair keep the top-K deepest candidates, K = that pair's flow-trough count under the decode definition (D=max($400, 1.5% gross), rebound 0.35D; median 5/pair). Matches the decode's trough density without using flow locations.
- **Trough timestamp:** trough bar identified by price; second-level ts refined to the intra-minute cum-netflow minimum inside that bar's 60s (falls back to bar+30s). A pure-price variant (bar+30s, no flow input) is reported as robustness.

## Outcomes (same scrub as decode)
Union-counted wallet-pair episode delta over the fresh window; round-trip filter (≥1 buy, ≥1 sell, sells ≥50% of buys); scrub delta>0 & hold<10s. 10,301 episodes, base episode WR 58.1% (decode: 56.1% on 89 pairs — consistent; this is the top-40 liquid subset).

## Result — winner share of buys by timing vs PRICE trough

**TIER FLUSH (primary; 4,784 joined buys):**

| bucket | nW | nL | cnt-WR | USD-wtd WR | wallets | pairs |
|---|---|---|---|---|---|---|
| pre-low −600..0s | 1327 | 860 | 60.7% | 60.0% | 973 | 37 |
| **0..30s** | **381** | **177** | **68.3%** | **62.7%** | 358 | 32 |
| 30..60s | 97 | 50 | 66.0% | 49.9% | 123 | 26 |
| 60..120s | 130 | 97 | 57.3% | 39.3% | 163 | 29 |
| 120..300s | 315 | 223 | 58.6% | 59.1% | 331 | 33 |
| 300..600s | 682 | 443 | 60.6% | 52.1% | 640 | 37 |

**TIER DIP (sensitivity; 13,937 joined buys):** same shape — 0..30s 64.7% (n=1,429), 30..60s 59.1%, pre-low 56.1%, 60..120s 53.4% (worst), 120..300s 57.4%, 300..600s 55.9%.

- Two-proportion z (0-30s vs pre-low): FLUSH z≈3.3, DIP z≈5.9 — not noise.
- Per-pair: 0-60s WR > 60-300s WR in 15/23 (FLUSH) and 23/35 (DIP) pairs with ≥5 joined buys each — directional, same strength as the flow decode's 26/45.
- **Robustness A (pure price ts, no flow refinement):** 0..30s 65.6% vs pre-low 61.5%, 120-300s 58.1% — edge attenuates (±30s timing smear from bar midpoint) but survives.
- **Robustness B (episode-level, first buy):** 0..30s **77.6%** (n=250 eps) vs pre-low 65.7%, 300-600s 56.4% — the sharpest view yet.
- New nuance vs flow decode: the edge is concentrated in **0-30s**, not 0-60s; 60-120s is the worst bucket (57.3% count, 39.3% USD-wtd). And unlike the flow decode (USD-wtd ≤50% everywhere), on price troughs the 0-30s bucket leads USD-weighted too (62.7%) — big money buying the first 30s after a real price low won on this window.

## Flow-trough vs price-trough agreement
n=200 flow troughs (decode def) matched to nearest price-trough candidate: **median |dt| = 318s; within 60s only 38%; within 300s 48%; within 600s 62%.**
→ The flow proxy locates the *flush event* but NOT the second-level bottom: typical error ~5min. The timing finding replicated anyway because both definitions mark the same flush at coarse scale — but second-granularity claims from flow troughs alone should be discounted.

## Bonus — HL-confirm bucket size at price troughs
- **60s-bucketed HL confirm** (bar low > prev bar low AND close ≥ trough_low·1.005, known at bar close): fired at 196/196 FLUSH troughs (832/832 DIP), **median +90s after the trough; lands in 60-300s zone 99-100% of the time, 0-60s 0%** — it structurally misses the winning pocket.
- **30s-bucketed variant is NOT exactly computable** from available data (min bar res is 1m; tapes carry no price). Bound: its earliest possible fire is ~30-60s post-trough, i.e. it can reach the 30-60s bucket (66.0% FLUSH) which the 60s version never does. **Directionally 30s wins;** a live shadow (which has tick prices) is the right instrument to settle it.

## Verdicts
1. **(a) 0-60s post-low edge on price troughs: CONFIRMED — and sharpened to 0-30s.** 68.3%/64.7% vs base and all other buckets; survives pure-price timestamps and per-pair checks; episode-level first-buy 0-30s = 77.6%. The flow decode underestimated the pocket (57%) because its trough clock is ~5min noisy.
2. **(b) 30s vs 60s HL bucket: 30s wins directionally.** The 60s confirm lands in the 60-300s zone essentially always (median +90s) and never inside 0-60s. Ship the 30s-bucket variant to shadow (already the decode's suggestion) — this validation raises its priority.
3. **(c) Trust level for flow-proxy troughs: MODERATE for event detection, LOW for sub-minute timing.** Use flow troughs for cohort/behavior questions (they found the right answer here); do not use them to calibrate confirm-window seconds — fetch price bars for that.

## Caveats
- 1m bar resolution → trough ts uncertainty up to ~60s even after refinement; the pure-price robustness bounds the effect.
- Winner = same-window USD delta; moonbag/partial-exit bias (biases against peelers) identical to the decode, so bucket comparisons are internally consistent.
- Top-40-by-activity subset (39 pairs) vs the decode's 89; base WR differs accordingly (58.1% vs 56.1%).
- Single ~23.5h window; same-regime replication, not out-of-sample across days.
