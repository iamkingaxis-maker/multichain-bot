# RH demand-shape entry gate — reconstruction + regime test (2026-07-13)

**Challenge (AxiS):** "you can always tell them apart. figure it out" — find the
entry-time signal that separates RH tokens that RUN (PONS, +40%) from ones that
DIE (HOODBIRD, volume collapses → cut at −6%). Thesis: the tell is the on-chain
trade-flow SHAPE at entry (heavy+accelerating sells = distribution top; persistent
net-dollar inflow = real retrace), computed by `core/retrace_microstructure.py`.

**Bottom line up front:**
- The four confirmed-on-Solana windowed features — `sell_rate_60`, `sell_traj`,
  `cum_nf_60`, `pos_subwins` — **do NOT separate runners from diers on the RH lane
  in a regime-robust way.** Every one flips sign or goes flat across the three
  regime days. The pooled `cum_nf_60` AUC of 0.68 is a **magnitude confound**
  (Simpson's), not a real tell. **This is a null result for the sell-distribution
  thesis on this lane.**
- The RH lane fires into **demand VACUUMS, not distribution tops**: median
  `sell_rate_60` at entry is **0.00** on the busy days. Step-B has no substrate here.
- The **only** entry-time separator that pointed the SAME direction on all three
  days is cruder: **whether there is live trade flow at all in the 60s pre-entry
  window** (`n_trades_60`). Dip into a LIVE tape → higher run-rate (+17 / +32 /
  +46 pp across the 3 days); dip into a DEAD tape (HOODBIRD: attention/liquidity
  already gone) → tends to die. It is a **~15–45 pp odds-shift (a size/downsize
  lever), NOT a deterministic filter**, and on **net-$ it holds 2 of 3 days** (it
  degrades on 07-10, whose exits were bug-bleeding — an exit problem, not entry).
- Shipped **SHADOW-only** (`RH_DEADTAPE_SHADOW`, default off): stamps
  `deadtape_would_skip` on entries, never blocks/resizes, forward-grades on the
  exact stamped `n_trades_60`.

---

## 1. Reconstruction method

The tape has no per-line timestamps, but the periodic
`[fh] N.Nmin: … taped=K …` lines are anchors: at elapsed time `N*60 s`, exactly
`K` fh-tape trades have been emitted.

1. **Anchor validation.** Cumulative fh-tape line count exactly equals `taped=K`
   at every anchor for sessions 1 and 7 (diff = 0 throughout). Session 10 drifts
   by −119 / 7173 (1.7%) in the final minutes (truncated capture). Handled by
   interpolating on my **own** cumulative fh-tape count vs `(cum_at_anchor,
   elapsed_sec)` — self-consistent regardless of the `taped` counter drift.
2. **Per-line ts** = piecewise-linear interpolation of a trade's cumulative index
   against the anchor points (uniform arrival assumed within each ~60 s segment).
3. **ref_ts** for each `[rh-paper] BUY/SELL` = ts of the immediately preceding
   fh-tape line ("now", since the lane fires on the dip).
4. **Per-token trade streams** ({kind, volume_usd, ts}) fed to the **exact** pure
   functions `sell_distribution_flag` / `net_flow_persistence` at each BUY's
   ref_ts — same 60s window, 30s sub-trajectory, 3×20s sub-windows the live lane uses.
5. **Two log formats** handled: 07-10 sessions have no bot-id column
   (`BUY  SYM  $25`), 07-11/12 do (`BUY  rh_bot  SYM  $25`). Symbols may contain
   spaces ("VIBE CAT"); volumes may have commas.
6. **Outcome label.** An *entry event* = a batch of BUYs at one tick (one
   `_paper_buy` call). `best_pnl` = max exit pnl among that token's positions
   before the next same-token event. **RAN = best_pnl ≥ +6%** (a racer got the
   chance to capture ≥6%); DIED otherwise. `net-$/position = $25 × Σ(frac·pnl%)`.

**Scale:** 228 entry events, 208 with a realized outcome. Regime coverage is
balanced: 07-10 = 70, 07-11 = 65, 07-12 = 73 labeled events.

**Honest limitation:** intra-minute uniform interpolation flattens bursts, so the
sub-window features (`sell_traj`, `pos_subwins`) are the least trustworthy; the
60s-aggregate features (`sell_rate_60`, `cum_nf_60`) are the most interpolation-
robust — and those still fail the regime test, which strengthens the null.
Also the firehose only tapes **watched** tokens and samples, so `n_trades_60`
under-counts absolute activity; the relative (live vs dead) signal survives, but
the raw threshold is a tape-density threshold, not a true-market count.

---

## 2. Separation results — the four thesis features are NOT regime-robust

**AUC(RAN) per regime** (label=1 is RAN; robust ⇒ same side of 0.50 on all 3 days):

| feature | 07-10 (bad) | 07-11 (bad) | 07-12 (good) | POOLED | verdict |
|---|---|---|---|---|---|
| `sell_rate_60` | 0.64 | 0.70 | 0.44 | 0.55 | flips |
| `sell_traj` | 0.60 | 0.67 | 0.45 | 0.55 | flips |
| `cum_nf_60` | 0.45 | 0.77 | 0.43 | 0.68 | works 1/3 only |
| `pos_subwins` | 0.47 | 0.58 | 0.43 | 0.57 | noise |
| `buy_rate_60` | 0.50 | 0.74 | 0.81 | 0.72 | flat on worst day |
| `vol_60` | 0.50 | 0.73 | 0.81 | 0.72 | flat on worst day |
| **`n_trades_60`** | **0.55** | **0.71** | **0.78** | **0.71** | **ROBUST (same side 3/3)** |

Readings:
- **`sell_rate_60` / `sell_traj` contradict the thesis on the bad days.** The
  thesis says heavy/accelerating sells ⇒ DIED (RAN should be *lower*, AUC < 0.5).
  But on 07-10 and 07-11 RAN events have *heavier/accelerating* sells (AUC 0.60–0.70),
  and only 07-12 is weakly thesis-consistent (0.44–0.45). Sign is unstable.
- **`cum_nf_60` "works" only on 07-11** (0.77) and is inverted on 07-10 (0.45) and
  07-12 (0.43). The pooled 0.68 is a **magnitude confound**:

  | regime | median `cum_nf_60` RAN | DIED |
  |---|---|---|
  | 07-11 | 476.7 | 260.8 |
  | 07-12 | 2802.6 | 3286.5 |

  07-12's net-flow is ~10× higher than 07-11's *regardless of outcome*, so pooling
  RAN (weighted toward the high-net-flow good day) vs DIED manufactures a spurious
  gap. Within each day it's flat/inverted. This is exactly the good-day-mirage trap.
- The null is **robust to the outcome cutoff**: re-labeling RAN at ≥0/3/6/10%
  leaves `cum_nf_60` at 07-10 ≈ 0.38–0.48, 07-11 ≈ 0.71–0.77, 07-12 ≈ 0.42–0.43.

---

## 3. The one robust separator — dead vs live tape (`n_trades_60`)

"Is there any trade flow in the 60s before entry?" separates monotonically, same
direction, on **all three** regime days:

| regime | LIVE tape (≥3 trades) run% | DEAD tape (<3) run% | gap |
|---|---|---|---|
| 07-10 (bad) | 51.9% (n=27) | 34.9% (n=43) | **+17 pp** |
| 07-11 (bad) | 65.0% (n=20) | 33.3% (n=45) | **+32 pp** |
| 07-12 (good) | 88.1% (n=42) | 41.9% (n=31) | **+46 pp** |
| ALL | 71.9% (n=89) | 36.1% (n=119) | **+36 pp** |

This is not the sell-distribution *shape*; it is an **attention/liquidity-presence**
signal. A dip into an active tape resumes more often; a dip into a dead tape is the
HOODBIRD "volume collapses" signature. It is a **~15–45 pp odds-shift**, not a tell.

---

## 4. Gate design — dead-tape DOWNSIZE (honest winner-kill / loser-avoid)

Gate = skip/downsize entries with `n_trades_60 < K` (dead tape). Winner-kill = RAN
wrongly skipped; loser-avoid = DIED correctly skipped; net-$ is per realized position.

**K = 3** (the retrace fail-open cutoff; coded default):

| regime | KEEP run% / net$ | SKIP run% / net$ | winner-kill | loser-avoid |
|---|---|---|---|---|
| 07-10 | 52% / **−$1.29** | 35% / −$0.41 | 15/29 (52%) | 28/41 (68%) |
| 07-11 | 65% / −$0.18 | 33% / −$1.17 | 15/28 (54%) | 30/37 (81%) |
| 07-12 | 88% / +$2.24 | 42% / −$0.02 | 13/50 (26%) | 18/23 (78%) |
| ALL | 72% / +$0.89 | 36% / −$0.72 | 43/107 (40%) | 76/101 (75%) |

**K = 2** (more balanced skip point):

| regime | winner-kill | loser-avoid | KEEP net$ | SKIP net$ |
|---|---|---|---|---|
| 07-10 | 10/29 (34%) | 18/41 (44%) | −$0.83 | −$0.63 |
| 07-11 | 6/28 (21%) | 21/37 (57%) | −$0.39 | −$1.63 |
| 07-12 | 8/50 (16%) | 16/23 (70%) | +$1.91 | +$0.03 |
| ALL | 24/107 (22%) | 55/101 (54%) | +$0.46 | −$0.87 |

**Expectancy lift** from skipping the dead-tape bucket entirely (K=3), mean
net-$/position over all positions:

| regime | full book | live-tape only | lift |
|---|---|---|---|
| 07-10 | −$0.75 | −$1.29 | **−$0.54** ✗ |
| 07-11 | −$0.84 | −$0.18 | **+$0.65** ✓ |
| 07-12 | +$1.30 | +$2.24 | **+$0.94** ✓ |
| ALL | −$0.01 | +$0.89 | **+$0.90** ✓ |

**Honest verdict on the gate:**
- **Run-rate: robust 3/3.** Live tape lifts run-rate on every regime day.
- **Net-$: 2/3.** It lifts net-$ on 07-11 and 07-12 but **degrades 07-10** (−$0.54/pos).
  Cause: 07-10's exits were bleeding (the "slice-cost bug 2026-07-10" ledger
  corrections) — even the higher-run-rate live-tape entries lost on exits. That is
  an **exit/size** leak, orthogonal to entry selection, and consistent with the
  standing finding that exits are the lever.
- **Winner-kill is high** (22–54%): this is why it is a **DOWNSIZE lever, not a hard
  skip.** As a size dial (e.g. dead tape → 0.4×), it de-risks the 64–86% dier-heavy
  dead-tape bucket while keeping optionality on the 15–42% of runners it contains.
- Threshold is env-tunable (`RH_DEADTAPE_MIN_TRADES`, default 3); K=2 trades fewer
  killed runners for fewer avoided diers. Forward shadow data picks the operating point.

**Gate on the thesis features (`cum_nf_60`) is a value-destroyer** and was rejected:
at every threshold winner-kill ≈ loser-avoid, and on 07-12 it kills 3–11 runners to
avoid a single dier.

---

## 5. Metrics summary (mandatory)

| regime | events | win-rate (run%) | positions | mean net-$/pos | median net-$/pos |
|---|---|---|---|---|---|
| 07-10 (bad) | 70 | 41.4% | 70 | −$0.75 | −$0.06 |
| 07-11 (bad) | 65 | 43.1% | 214 | −$0.84 | −$1.09 |
| 07-12 (good) | 73 | 68.5% | 175 | +$1.30 | +$1.69 |
| ALL | 208 | 51.4% | 459 | −$0.01 | +$0.80 |

---

## 6. What shipped (SHADOW-only, never blocks)

- **`core/retrace_microstructure.py`** — `sell_distribution_flag` now stamps
  `n_trades_60` in the fail-open branch (was dropped), so the dead-tape case (0/1/2
  trades) is captured, not lost. Pure; existing tests unaffected (they assert only
  `["block"]`).
- **`scripts/rh_paper_lane.py`** — `RH_DEADTAPE_SHADOW` (default off) +
  `RH_DEADTAPE_MIN_TRADES` (default 3). In `_paper_buy`, once per entry event,
  computes `deadtape_would_skip = n_trades_60 < K`, prints `[rh-shadow] deadtape
  would_skip …`, and stamps `deadtape_would_skip` / `deadtape_min_trades` into the
  entry `micro` block. **Never blocks or resizes.** Paper rows are byte-identical
  when the flag is off. Forward-grades on the exact stamped `n_trades_60`.
- **Tests:** 85 passed (`test_retrace_microstructure`, `test_rh_paper_lane`,
  `test_rh_paper_fleet`). Not enforced, not deployed, not pushed.

**Reproduce:** `scratchpad/_rh_recon.py` (reconstruction+outcomes),
`_rh_recon2.py` (extra features), `_rh_analyze.py` / `_rh_analyze2.py` (tables);
inputs `scratchpad/robinhood_tapes/paper_lane_session{1..10}.log`.

---

## 7. Answer to the challenge

You can **not** always tell them apart from the trade-flow *shape* on the RH lane:
the confirmed Solana sell-distribution / net-flow-persistence signals are
regime-inconsistent here and their pooled edge is a magnitude confound. The lane
fires into demand vacuums, so the distribution-top substrate isn't even present.
The one thing that carries a **regime-robust directional** signal is far cruder —
**whether the tape is alive at entry at all** — and it is a modest odds-shift
(size lever), confirmed on run-rate 3/3 but on net-$ only 2/3 (07-10's exit bleed
breaks it). Best treated as a **downsize dial**, now forward-grading in shadow. The
sell-distribution *tell* itself is, on this lane, **noise** — which closes that
question.
