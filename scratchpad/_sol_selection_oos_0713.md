# SOL young-lane entry-selection OOS hunt — 2026-07-13

**Verdict: NOTHING SURVIVES. Entries are irreducibly ~breakeven (deeply negative on current tape); the lever is EXITS/SIZING, not selection.**

No paper A/B bot was written — the task authorizes one only "if a gate survives cleanly." None did.

---

## Method (as mandated)

- **Data:** `_trades_cache.json` (buy+sell feed, fresh to 2026-07-13T02:57 UTC). Buy→sell paired per `(bot_id, token)` chronologically (ps_scan pairing logic), partial sells aggregated into one trip; **trip ret = sell_fraction-weighted `pnl_pct`.**
- **Universe:** `bot_id` startswith `badday_`, buy window `>=2026-07-03`. This fleet's ex-top-2 token-median = **-6.42%**, which is exactly the "-6.4% floor" named in the brief, so it is the correct universe. (Young-lane-only subset = -6.57%, 34 tokens — parallel, not primary.)
- **Metric:** ex-top-2 token-median = per-token median ret → drop the 2 best tokens → median of the rest. Green would require median > 0 AND >=50% of kept tokens green. Baseline is 11% green / -6.42% median — a *deep* hole, not a marginal one.
- **Scrub rule applied:** dropped 33 trips with ret>0 AND hold<10s (863 → 830 trips).
- **OOS = four-half.** Tape is session-based, so the "last 10 days" contains only **3 calendar days with data: 07-11 (430 trips), 07-12 (366), 07-13 (34).** A day-of-month grid is impossible with 3 days, so four-half was realized as **day-parity × chrono-half**: ODD days {07-11,07-13} vs EVEN day {07-12}, each split at its chronological midpoint → 4 disjoint quarters:
  - Q1 odd-early (232 trips), Q2 odd-late (232), Q3 even-early (183), Q4 even-late (183).
  - Per-quarter baseline ex-top-2: **Q1 -5.98, Q2 -6.17, Q3 -6.09, Q4 -6.97**. Stable across folds — the floor is real, not a one-day artifact.
- A gate "survives" only if it lifts ex-top-2 in a **majority (>=3/4)** of quarters.

**Data-power caveat (important):** with 3 days of tape and ~20-30 tokens/quarter (fewer after gating, then minus-2), OOS power is thin. This makes the null result *more* trustworthy (a real edge would have to be large to show through) but means a small in-sample lift can never be promoted to live from this window alone.

---

## (1) Per-quarter OOS table — 81 gates tested

`pos/val` = quarters with positive lift / quarters with enough tokens to score. `thru` = full-sample throughput (% trips kept). Lift = gated ex-top-2 − baseline ex-top-2, in pp, per quarter [Q1 Q2 Q3 Q4].

| gate | pos/val | thru | mean lift | Q1 | Q2 | Q3 | Q4 |
|---|---|---|---|---|---|---|---|
| peak_h24_6h_pct<=100 | 3/4 | 30% | +1.0 | +0.3 | -0.5 | **+3.9** | +0.3 |
| PASS:bs_m5_low | 3/4 | 43% | +0.9 | +0.1 | +0.1 | **+4.1** | -0.8 |
| PASS:dying_volume | 3/4 | 84% | +0.0 | +0.0 | +0.0 | -0.1 | +0.2 |
| n_recurring_buyers_3plus>=1 | 3/4 | 91% | +0.0 | +0.0 | +0.0 | +0.2 | -0.1 |
| PASS:vp_poc | 3/4 | 90% | -0.0 | +0.1 | -0.2 | +0.0 | +0.1 |
| n_recurring_buyers_3plus>=2 | 3/4 | 69% | -0.0 | +0.3 | +0.1 | +0.2 | -0.6 |
| peak_h24_6h_pct<=50 | 3/4 | 26% | -0.1 | +0.0 | -1.2 | +0.5 | +0.3 |
| vol_5m_burst_vs_h1>=1.5 | 3/4 | 25% | -0.2 | -2.0 | +0.1 | +0.9 | +0.3 |
| trade_density_30s_vs_5m>=1.2 | 2/4 | 29% | +1.4 | **+5.9** | -0.3 | -0.5 | +0.6 |
| buy_sell_volume_imbalance>=0.55 | 2/4 | 31% | +1.1 | **+5.6** | +0.1 | -0.5 | -0.7 |
| PASS:extended_chase | 2/4 | 39% | +1.0 | -0.0 | -0.2 | +3.9 | +0.3 |
| BLOCK:blowoff_top | 2/4 | 17% | +0.4 | +6.7 | +0.0 | -1.3 | -3.7 |
| PASS:dev_dumping | 2/4 | 48% | +0.2 | +0.0 | +0.2 | +2.3 | -1.8 |
| net_flow_5m_usd>=0 | 2/4 | 50% | -0.1 | +0.4 | +0.2 | -0.4 | -0.7 |
| net_flow_5m_imbalance>=0.5 | 1/2 | 4% | -0.0 | na | +0.3 | -0.4 | na |
| pc_h24<=0 | 2/4 | 34% | -0.1 | -0.0 | -1.1 | +0.5 | +0.1 |
| **prior: pc_h6<=0 AND buyer>=$34** | **1/4** | 43% | -0.4 | +0.0 | +0.0 | -0.3 | -1.3 |
| prior: pc_h6<=0 | 1/4 | 68% | -0.1 | -0.0 | -0.4 | -0.3 | +0.2 |
| buyer_mean_ge34 | 1/4 | 65% | -0.2 | +0.0 | +0.0 | -0.2 | -0.7 |
| hour_13_22 (UTC prime) | 2/4 | 65% | -0.6 | +0.2 | -0.2 | -2.5 | +0.1 |
| hour_not_03_08 | 0/4 | 96% | -0.1 | +0.0 | +0.0 | -0.3 | +0.0 |
| liquidity_usd>=20000 | 0/4 | 100% | +0.0 | +0.0 | +0.0 | +0.0 | +0.0 |

*(Full 81-row table produced by `scratchpad/_oos.py`; the rows above cover every gate with pos>=3/4, every task-named feature, the memory priors, and hour-of-day. All omitted rows are <=2/4 with mean lift <=0.)*

### Permutation null — the survivors are noise
A random gate (keep each trip with prob p, no signal) passes the >=3/4 rule with probability:

| throughput p | P(>=3/4 positive lift) |
|---|---|
| 0.3 | 0.094 |
| 0.5 | 0.108 |
| 0.7 | 0.117 |

At ~0.10 per gate × 81 gates tested, the null predicts **~8 false survivors.** We observed **8 gates at 3/4.** The count of "survivors" is exactly what pure chance produces — there is no excess signal. Moreover, the two 3/4 gates with non-trivial mean lift (`peak_h24_6h_pct<=100`, `PASS:bs_m5_low`) get essentially all of it from a **single fold (Q3, even-early = first half of 07-12)**; their other three quarters are ~0. That is the textbook one-fold artifact this exercise exists to reject — the same shape as the net_flow_5m overfit already burned on.

### Magnitude check (in-sample, full recent sample; base -6.42%)
Even ignoring OOS, the "best" gates barely move the floor:

| gate | full ex-top-2 | lift | thru |
|---|---|---|---|
| peak_h24_6h_pct<=100 | -5.98 | +0.45 | 30% |
| trade_density>=1.2 | -6.18 | +0.25 | 29% |
| buy_sell_imb>=0.55 | -6.28 | +0.14 | 31% |
| PASS:bs_m5_low | -6.48 | -0.05 | 43% |
| **prior pc_h6<=0 AND buyer>=$34** | **-6.42** | **+0.00** | 43% |

Reaching green needs **+6.4pp** to touch zero and a green-token flip from 11% → 50%. The best gate delivers **+0.45pp**. Not close, not even in-sample.

## (2) Single best gate that survives
**None.** No gate clears >=3/4 quarters with a magnitude distinguishable from the permutation null. The nominal 3/4 passers are either (a) zero-lift high-throughput gates (do nothing) or (b) single-fold artifacts. The mandated honest conclusion applies: **entries are irreducibly ~breakeven — the lever is elsewhere (exits/sizing/hold), consistent with standing memory.**

## (3) Throughput cost
Not applicable — no gate is recommended. For reference, the only gates with any positive in-sample tilt (`peak_h24_6h_pct<=100`, `trade_density>=1.2`, `buy_sell_imb>=0.55`) keep just **29-31%** of trips, which is already below the >=20 fills/day comfort line (<35% throughput), so even if their +0.2-0.45pp were real it would not be worth the fill starvation.

## (4) Recommendation
1. **Do not ship any entry-selection gate from this window.** Report the null honestly and keep sizing $25 unchanged. This is a *valuable* result: it re-confirms (now on fresh 07-11..13 tape, with a permutation null) that selection is not the reachable lever, which stops the fleet from chasing another net_flow_5m-style overfit.
2. **The historical `coverage_audit` prior (pc_h6<=0 AND buyer>=$34) is dead on current tape** — exactly 0.00pp in-sample lift, 1/4 OOS. Do not re-enable it; update the memory note that it no longer holds.
3. **Redirect effort to exits/sizing** (matches memory: winners' gap is exits/size/churn, MAE keeps worsening past 60s on losers). The realized-loss decomposition lives on the sell side (mae_pct, peak_pnl_pct, giveback/never_runner shadows already in the tape), not the entry side.
4. **If selection is revisited, do it with more days.** 3 calendar days = 4 thin folds. Re-run this exact harness (`scratchpad/_oos.py`) once the tape spans >=6 distinct days with >=5 tokens/quarter after gating; only then can a small lift be trusted enough to promote.

---

### Artifacts
- `scratchpad/_build.py` — buy→sell pairing, scrub, ex-top-2, writes `_trips.pkl`.
- `scratchpad/_oos.py` — four-half (day-parity × chrono-half) gate battery (81 gates).
- `scratchpad/_null.py` — permutation null + in-sample magnitude check.
- No changes to `config/bots/`, no live bots touched, nothing deployed or pushed.
