# SOL Demand-Shape Entry Gate — validation & gate test (2026-07-13)

**Verdict: REAL separator AND a net-positive, OOS-robust tradeable gate — but modest, and
throughput-costly.** The entry-time **acceleration of net flow** (recent-60s demand backed by
sustained 5m flow) separates winners from losers robustly across every OOS slice, and gating on it
flips the badday paper population from **net −$301.72 → +$27.35 (@ $25/pos)**, win-rate **35.2% →
37.9%**, mean return **−1.19% → +0.30%**, with **positive net in all four chronological quarters**.
The earlier ex-top-2 NULL is reconciled two ways (below). The signal is **acceleration**, *not* raw
`net_flow_5m` (which is ~random, AUC 0.522) — and *not* `net_flow_5m_imbalance` (also ~random, AUC
0.507, and regime-unstable). Shipped as paper A/B `config/bots/badday_young_demandshape_ab.json`.

---

## Data & method
- Source: `_trades_cache.json` (39,530 rows). Filtered to `badday_*` family, joined **buy→sell** per
  `(bot_id, token)` FIFO (1,443 pairs), window **entry ≥ 2026-07-03**.
- **SCRUB rule applied** (drop `pnl_pct>0 & hold_secs<10`). Require non-null
  `net_flow_5m_imbalance / net_flow_5m_usd / net_flow_60s_usd`.
- Final set: **n = 1,012** positions. Winners (peak_pnl_pct ≥ 20) = **64**; losers (peak < 6) = **730**;
  middle = 218.
- Features from buy `entry_meta`; outcomes (`peak_pnl_pct`, realized `pnl_pct`, `hold_secs`) from the
  joined sell. **acceleration ≡ net_flow_60s_usd / (net_flow_5m_usd / 5)**.
- **Window reality (honest, up front):** the badday paper bots only have trades on **07-11, 07-12,
  07-13 — ~39 hours across 3 calendar days**. Earlier-July tape is not in the cache for these bots. So
  the requested "chrono × parity four-half" **degenerates**: day-parity collapses to "07-11&07-13 vs
  07-12" (see below). I therefore ran the OOS as **four equal-count chronological quarters** (the best
  available independent split) AND report the parity/chrono halves with that caveat.

---

## Part 1 — Separation: confirmed, but the driver is ACCELERATION, not imbalance

### Reproduction (medians — direction confirms the brief)
| feature | winners (peak≥20) med | losers (peak<6) med | direction |
|---|---|---|---|
| net_flow_5m_imbalance | 0.078 | 0.021 | ✔ higher for winners |
| net_flow_5m_usd | $315.9 | $67.8 | ✔ |
| **acceleration** | **4.88** | **1.38** | ✔ (strongest) |
| net_flow_60s_usd | $250.3 | $70.4 | ✔ |
| n_recurring_buyers_3plus | 3 | 3 | ~flat |

Direction reproduces the confirmed finding. But **medians hide overlap** — the honest separation
metric is AUC (Mann-Whitney, robust to the outliers that made the raw means flip sign).

### Single-feature AUC (winner peak≥20 vs loser peak<6)
| feature | AUC | read |
|---|---|---|
| **acceleration** | **0.642** | **real separator** |
| net_flow_60s_usd | 0.633 | real separator |
| net_flow_5m_imbalance | **0.507** | ~random — the "0.126 vs 0.010" gap is fat-tail-driven, not distributional |
| net_flow_5m_usd | 0.522 | ~random |
| n_recurring_buyers_3plus | 0.519 | weak |
| net_flow_15s_imbalance | 0.384 | **inverse** (winners lower) — excluded |
| large_buyer_volume_pct | 0.442 | inverse — excluded |

**Key correction to the thesis:** imbalance is *not* a strong separator on the full population
(AUC 0.507). Its median gap is driven by the fat tail, not a distributional shift. The tradeable
signal is **acceleration** and **net_flow_60s_usd**. Best composite = **0.6·rank(accel) +
0.4·rank(nf60)**, AUC **0.681** (adding imbalance *lowers* it and destabilizes it — see Q3).

### Four-quarter OOS (equal-count chronological) — AUC MUST hold; it does for accel/nf60
| quarter | span | n | W | L | accel | nf60 | imb | robust score |
|---|---|---|---|---|---|---|---|---|
| Q1 | 07-11 11h→16h | 253 | 25 | 167 | 0.640 | 0.732 | 0.586 | 0.738 |
| Q2 | 07-11 16h→22h | 253 | 17 | 180 | 0.603 | 0.551 | 0.509 | 0.618 |
| Q3 | 07-11 22h→07-12 13h | 253 | 18 | 180 | 0.638 | 0.525 | **0.337 (inverts!)** | 0.611 |
| Q4 | 07-12 13h→07-13 02h | 253 | 4 | 203 | 0.762 | 0.918 | 0.760 | 0.897 |
| **overall** | | 1012 | 64 | 730 | **0.642** | 0.633 | 0.507 | **0.681** |

**Acceleration holds ≥ 0.60 in all four quarters. The robust (accel+nf60) score holds ≥ 0.61 in all
four.** Imbalance **inverts in Q3** (0.337) — this is exactly why the naive imbalance/net_flow gate is
fragile. Degenerate day-parity split (for the record): even-days (07-11&07-13) AUC 0.675 vs odd-day
(07-12) AUC 0.508 — i.e. the composite-with-imbalance is strong on 07-11 and near-random on 07-12;
the accel/nf60 robust score is what survives the split.

---

## Part 2 — GATE TEST (graded on WIN-RATE & NET-$, not ex-top-2)

The gate skips the **loser signature: weak/decaying recent flow**. Two implementations:

### (a) Acceleration reference gate (`accel≥1.5 & nf60≥$20`) — the ideal, not config-expressible
| metric | baseline | gated | Δ |
|---|---|---|---|
| n / throughput | 1012 / 100% | 308 / **30.4%** | −70% positions |
| win-rate | 35.2% | **37.3%** | +2.1pp |
| mean return | −1.19% | **+0.61%** | +1.80pp |
| net @ $25/pos | **−$301.72** | **+$47.23** | **+$348.95** |
| winners kept / skipped | 64 / 0 | 38 / **26** | skips 41% of peak-winners |
| losers avoided | — | **520** | |
| per-quarter Δnet | | **[+44.7, +17.8, +56.4, +230.0]** | **positive in ALL 4** |

### (b) Shipped config gate (`net_flow_60s_usd≥100 & net_flow_5m_usd≥50`) — config-expressible proxy
`entry_gate` supports only `[field, op, constant]` vs `raw_meta` (no field/field ratio), so
acceleration cannot be written directly. Requiring **both** a strong recent-60s floor **and** a
sustained-5m floor is the faithful proxy: it removes both no-demand losers *and* fake-spike-no-base
tokens (which is why `nf60`-alone fails Q2 but adding the `nf5` floor rescues it).
| metric | baseline | gated | Δ |
|---|---|---|---|
| n / throughput | 1012 / 100% | 364 / **36.0%** | −64% positions |
| win-rate | 35.2% | **37.9%** | +2.7pp |
| mean return | −1.19% | **+0.30%** | +1.49pp |
| net @ $25/pos | **−$301.72** | **+$27.35** | **+$329.07** |
| winners kept / skipped | 64 / 0 | 38 / **26** | of 64 |
| losers avoided | — | **477** | of 730 |
| per-quarter Δnet | | **[+58.2, +6.6, +56.4, +207.9]** | **positive in ALL 4** |

**Throughput honesty:** both gates cut positions by ~65%. Net-$ improves *in absolute terms* despite
taking ~1/3 the trades — but for the rate-proving mission this is far fewer fills/day. The gate turns a
small net loser into a small net winner; it does not manufacture a large edge.

**Winners-skipped vs losers-avoided (why win-rate AND net both improve despite skipping 41% of
peak-winners):** separation is measured on `peak_pnl_pct` (did the token *run*), but the gate is graded
on *realized* `pnl_pct`. Many of the 26 skipped "peak-winners" were sold at a loss anyway (peaked then
gave back) — skipping those *helps* realized net. Losers avoided (477) vastly outnumber winners
skipped (26), and their avoided losses exceed the forgone gains → net rises.

---

## Part 3 — Ex-top-2 reconciliation (the crux)

**Earlier this session a naive `net_flow_5m` threshold gate FAILED OOS on ex-top-2 median. That NULL is
reconciled — and it had TWO independent causes, both real:**

**Cause 1 — wrong FEATURE.** The earlier gate thresholded raw `net_flow_5m_usd`, whose AUC is **0.522
(≈ random)**. A gate on a non-separating feature genuinely doesn't help — on *any* metric. It would
have failed even on win-rate/net. The tradeable signal is **acceleration** (nf60/(nf5/5), AUC 0.642),
a *different* feature the naive gate never used. So part of the null was simply gating on noise.

**Cause 2 — wrong METRIC (this is the structural blind spot).** Even the *good* acceleration gate leaves
ex-top-2 median **flat**:
| | mean | net @ $25 | ex-top-2 median |
|---|---|---|---|
| baseline | −1.19% | −$301.72 | **−5.725%** |
| config gate | +0.30% | +$27.35 | **−5.397%** |

Ex-top-2 median barely moves because:
1. It **drops the 2 biggest winners** — the exact fat tail the gate exists to keep. The gate *keeps* both
   top-2 winners, so removing them erases the headline signal.
2. The **median of a loss-dominated survivor pool is still a loss** (>60% of kept positions are red), so
   the median is pinned near −5% regardless of how many losers you avoid or how the win-rate/mean move.

**The decisive test — does the benefit survive removing the fat tail?** Yes.
Net **excluding the top-2 winners**: baseline −$338.77 → config gate **−$9.70** = **+$329.08
improvement even with both fat-tail winners deleted.** The acceleration ref gate: identical +$348.95
ex-fat-tail. **So the gate's entire net benefit is loser-avoidance, not fat-tail luck** — yet ex-top-2
median cannot see it. Ex-top-2 median was structurally the wrong grader for this gate; win-rate and net
are the honest metrics, and both improve, robustly, in every quarter.

---

## Honest caveats / do-not-oversell
1. **Only ~39 hours / 3 calendar days** of badday paper tape in-window. The four "quarters" are
   chronological slices within 3 days, not independent regimes. Robustness holds across them, but this is
   a **short window** — treat the paper A/B forward run (n≥30, ≥5 days) as the real test.
2. **Throughput −65%.** Real cost for a rate mission; net-$ up but on ~1/3 the fills.
3. **Marginal magnitude.** Baseline is only −$0.30/pos; the gate makes it ~+$0.075/pos. This avoids the
   worst entries; it is not a large standalone edge.
4. **Q4 concentration.** Δnet is largest in Q4 (worst regime) — the gate is *defensive* (helps most when
   the tape is bad), which is desirable, but a chunk of the total sits there. Q1–Q3 are all still positive.
5. **`peak` ≠ realized.** Separation graded on peak, tradeability on realized — reported both; they agree
   in direction here.
6. `net_flow_15s_imbalance` and `large_buyer_volume_pct` are **inversely** related to winning — do NOT
   add them to a demand gate (a prior instinct to stack all "demand" fields would backfire).

---

## Shipped artifact
`config/bots/badday_young_demandshape_ab.json` — clone of `badday_young_rt_paper` entry, **+2 gate
predicates** (`net_flow_60s_usd≥100`, `net_flow_5m_usd≥50`), `base_position_usd:25.0`,
`live_probe:false`, own `exclusion_pool:"badday_young_demandshape_ab"`, `entry_gate_require_data:true`
(unknown demand = skip). Loads clean via `BotRegistry.from_directory` (161 bots); `test_bot_catalog.py`
31/31 pass. **Not deployed, not pushed** — working tree only. Grade forward on win-rate + net-$ vs the
`badday_young_rt_paper` twin, **never** on ex-top-2 median.
