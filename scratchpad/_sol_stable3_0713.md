# SOL Young-Lane STABILITY mine — dispersion-minimization (2026-07-13)

**Brief (AxiS):** "both sides show extreme volatility with profit and loss from each individual
bot; we need stability." **Stability = kill the per-bot P&L swings.** Entry SELECTION is proven
dead (81-gate OOS mine, memory `reachability_mission`), so the only levers are on the EXIT/variance
side. **Objective: find the exit ladder that MINIMIZES per-trip return STD while keeping mean ≥ 0,
consistent across 4 OOS halves — NOT the highest mean (that's the fat-tail lottery we're killing).**

**Data.** `scratchpad/sol_selection/_trips.json` — 955 realized young-lane trips (07-02..07-12),
scrub already applied (verified 0 `ret>0 & hold<10`). Each trip carries `ret / peak / mae / hold`
→ full exit re-simulation incl. MAE-gated stops and min-hold reprice. **Entry population = the
least-bad entries: rt+absorb families** (`young_rt`, `young_rt_paper`, `young_absorb`,
`young_absorb_live`) = **377 trips / 89 distinct tokens**. rt-only (110) and vsnap (78) reported as
robustness. Sim model = the blessed `cap_oos_replay.py` ladder, extended with variable
TP1/TP2/stop + BE-lock + min-hold. Scripts: `scratchpad/stable_mine.py`, `stable_final.py`.

**Mandates honored:** ex-top-2 token-median (group by token, median per token, drop 2 highest-count,
median the rest); SCRUB; 4-half OOS (W1≤07-06/W2 × odd/even day-of-month), per-half reported;
catastrophic = `ret < -20` (bar ≤5%); fat-tail promotion trap (median/dispersion must hold, not the
sum); NO entry-gate mining (proven dead).

---

## 1. The baseline swing (why the fleet feels violent)

The current `young_rt` **exit ladder** (6/.75, 12/.25, 2pp trail, −12 stop) applied to the
rt+absorb entries, WITHOUT the min-hold floor:

| metric | value |
|---|---|
| per-trip **STD** | **11.53**  ← the "swing" (matches the task's stated `sd~11`) |
| mean | +0.03 |
| ex-top-2 token-median | **−0.94** |
| tokens green | 51% |
| 4-half OOS green | **1/4** |
| catastrophic (<−20) | 2.7% |

Mean ≈ 0 with STD 11.5 and only 1/4 halves green = exactly AxiS's "extreme volatility, P&L from
each side." **The dispersion, not the entry, is the felt problem.**

---

## 2. Dispersion frontier (RT+ABSORB, n=377, 4-half OOS)

Each row is a pure EXIT change on the SAME entries. `STDcut` is vs the sd~11 control baseline.

| config | mean | med | **STD** | STDcut | ex2 | WR | cat% | tok-grn | 4-half |
|---|---|---|---|---|---|---|---|---|---|
| CONTROL (young_rt exit, no min-hold) | +0.03 | +4.66 | **11.53** | — | −0.94 | 54 | 2.7 | 51% | 1/4 |
| DEPLOYED young_rt (min-hold only) | +2.37 | +5.45 | 10.10 | −12.4% | +2.90 | 71 | 1.1 | 73% | 4/4 |
| **stable1** bank .85@+6, .15@+12, stop −12 | +2.23 | +5.45 | **10.02** | **−13.1%** | +2.90 | 71 | 1.1 | 73% | 4/4 |
| **stable2** bank .90@+5, .10@+12, stop −10 | +1.87 | +4.92 | **9.77** | **−15.3%** | +2.90 | 71 | 1.1 | 73% | 4/4 |
| **stable3** full-scalp 1.0@+4, stop −10 | +1.39 | +4.00 | **9.54** | **−17.3%** | +2.84 | 73 | 1.1 | 73% | 4/4 |
| — full-scalp 1.0@+3 (over-tight ref) | +1.02 | +3.00 | 9.40 | −18.5% | +3.00 | 74 | 1.1 | — | 4/4 |
| *(contrast)* capture_ab .6@+6, mb.3@3pp | +3.67 | +5.42 | 11.64 | **−0.9%** | +3.36 | 70 | 1.1 | 73% | 4/4 |

**Readouts:**

1. **The frontier is monotone: tighter/earlier banking → lower STD → lower mean.** Consistent
   banking truncates the RIGHT tail (winners book a tight, repeatable +4..+6 instead of a
   lottery ride to +50 or a round-trip to −13). That is the exact swing AxiS wants gone. Every
   step down the ladder trades ~0.4pp of mean for ~1.5% of STD.

2. **min-hold does the heavy lifting; tight-banking is the net-new add.** min-hold alone
   (already on live `young_rt`) cuts STD −12.4% AND flips ex2 −0.94→+2.90 and 4-half 1/4→4/4 —
   it reprices panic-cuts (the whipsaw losers) to the held-median. On top of that, tight-banking
   adds a further **−3 to −6%** STD (10.10→9.54). So vs the sd~11 baseline the stack is −13 to
   −17%; vs the already-deployed min-hold young_rt it is −1 to −6%.

3. **`capture_ab` is the OPPOSITE bet and proves the trade-off.** It has the highest mean (+3.67)
   and highest ex2 (+3.36) but the WORST STD of the group (11.64, ~0% cut) — its 0.30 house-money
   moonbag catches the upside tail and thereby RE-INTRODUCES the dispersion. **A fat moonbag is
   anti-stability** (memory's `moonbag_ab` cautionary tale). The stable configs deliberately carry
   NO fat bag: bank the whole position by +12, or full-out at +4.

4. **The hard downside cap (−12 vs −10) is a near-zero lever here** (STD moves ~0.02–0.03pp; cat
   stays 1.1%). Winners rarely touch the stop and the real losers are slow-bleeds/rugs a price stop
   can't cheaply catch. The downside is floored by min-hold, not by the hard stop. −10 is adopted
   in stable2/3 as a marginal, safe tightening (min-hold protects the first 120s from whipsaw).

5. **Downside deviation is pinned at ~7.0 across every variant** — total-STD reduction here is
   almost entirely **right-tail (upside) truncation via consistent banking**, with the left tail
   already floored by min-hold. This is the healthiest kind of variance cut: it removes the
   feast-or-famine upside swing, not the catastrophe protection.

---

## 3. Per-quarter stability (RT+ABSORB, 4 OOS halves)

The winner must hold across all four halves, not on the pooled sum (fat-tail trap).

| config | W1 (n93) | W2 (n284) | odd (n260) | even (n117) |
|---|---|---|---|---|
| | mean / std / ex2 | mean / std / ex2 | mean / std / ex2 | mean / std / ex2 |
| CONTROL | +0.37 / 8.70 / −3.47 | −0.08 / 12.32 / −3.04 | +0.28 / 12.42 / +0.55 | −0.52 / 9.25 / −3.47 |
| **stable1** | +2.85 / 6.87 / +3.36 | +2.02 / 10.85 / +2.24 | +2.26 / 11.07 / +3.42 | +2.16 / 7.18 / +2.84 |
| **stable2** | +2.37 / 6.63 / +3.36 | +1.71 / 10.59 / +2.24 | +1.91 / 10.82 / +3.03 | +1.79 / 6.86 / +2.84 |
| **stable3** | +1.83 / 6.35 / +3.49 | +1.25 / 10.36 / +2.22 | +1.44 / 10.62 / +2.86 | +1.28 / 6.51 / +2.84 |

**Every stable candidate is mean-positive, ex2-positive, and lower-STD than control in all 4
halves.** The min-hold+bank stack is not a single-window artifact.

---

## 4. Robustness across entry populations

| population | n | best-stable STDcut | ex2 (stable3) | 4-half | note |
|---|---|---|---|---|---|
| **RT+ABSORB** (primary) | 377 | −13 to −17% | **+2.84** | 4/4 | clears the full stability bar |
| ALL-least-bad (+vsnap) | 455 | −11 to −16% | +1.84 | 4/4 | holds; pattern identical |
| **RT-ONLY** (the cloned entry) | 110 | −3 to −8% | **−6.14** | **0/4** | ⚠ see §5 |
| VSNAP alone | 78 | −1 to +2% | −5.33 | 0/4 | noisy (small n), not the target |

---

## 5. Honest reality — what stability CAN and CANNOT buy on this tape

**SOL is hard, and I will not pretend otherwise.**

- **On the pooled rt+absorb population the stability bar is fully cleared** (ex2 +2.84..+2.90 ≥ 0,
  73% tokens green ≥ 50%, cat 1.1% ≤ 5%, 4/4 halves green, STD −13..−17%). The three candidates
  are genuinely lower-variance AND non-negative-mean AND consistent.

- **But on the PURE `young_rt` entry that these three configs clone (n=110), the token-median stays
  RED (ex2 −6.14, 0/4 green).** The positive ex2 in §2 is carried by the ABSORB entries, which
  reach more winners. Exits cut the swings and lift WR (54→56-59) and STD (−3 to −8%) even on
  rt-only — but **they cannot manufacture an edge the entry does not have.** This is the standing
  truth (memory `winner_selection`, `entry_opportunity_mine`): exits are a VARIANCE lever, not a
  turn-red-green lever.

- **The mean is modest (+1.4 to +2.2 per-trip on the pooled tape).** This is **"stable, low-variance,
  mildly-positive,"** not "clearly profitable." Per the brief's honest fallback: **a stable bot that
  stops bleeding and kills the swings IS progress toward the stability goal, and forward tape may
  lift the mean.** I am delivering the reachable target, labelled as such — not a phantom green.

- **Volume is 100% retained** (no entry dropped, no sizing change) — matches AxiS's "keep high
  trade volume." The only mechanism is reshaping the exit.

**Not built into the 3 (flagged):** a 10-min hold-time box (`variance_reduction` Lever 2) cuts a
further ~5% STD at ~8% volume cost; and widening the shadow per-token daily cap is the heavyweight
DAY-variance lever (−42% at K=5). Both are additive next steps if forward grading confirms the
banking stack — kept out here to preserve volume and isolate the banking lever.

---

## 6. The 3 shipped candidates (paper, working tree only)

All three: **entry byte-identical to `badday_young_rt_paper`** (verified 0 entry-field diffs — same
gate, filters, mcap/vol/liq, adaptive-size, retrace-avoid, LP-rug insurance, all soft downside
cutters). Only the EXIT differs. `live_probe=false`, `base_position_usd=25.0`, own `exclusion_pool`,
`min_hold_floor_secs=120 / rug −25` added (the proven ex2/STD mover; rt_paper lacks it).
`config/bots/badday_young_stableN_ab.json`. Fleet loads 157→160 clean via `BotRegistry.from_directory`.

| bot | TP1 | TP2 | stop | STD (cut) | mean | ex2 | WR | role |
|---|---|---|---|---|---|---|---|---|
| **stable1_ab** | +6 / 0.85 | +12 / 0.15 | −12 | 10.02 (−13.1%) | +2.23 | +2.90 | 71 | gentlest — most mean kept |
| **stable2_ab** | +5 / 0.90 | +12 / 0.10 | −10 | 9.77 (−15.3%) | +1.87 | +2.90 | 71 | early-bank + tight cap |
| **stable3_ab** | +4 / 1.00 (full-out) | — | −10 | **9.54 (−17.3%)** | +1.39 | +2.84 | **73** | max consistency — lowest swing |

They span the dispersion frontier: **stable1** keeps the most mean, **stable3** is the tightest
swing-killer (whole position exits at +4 → no remainder can round-trip; highest WR, lowest STD).
The min-hold floor lets TP1/TP2 fire while suppressing panic-cuts in the first 120s (position
manager line 674: "TP1/TP2 gains still fire (winner-safe)").

### Pre-registered forward grade
Grade at **n≥30 closes** each, vs `badday_young_rt_paper` / `badday_young_exit_control`, on realized
closes only (shadow scorer overstates). **WIN = materially LOWER per-trip STD than the control with
mean NOT worse, holding across ≥3/4 OOS halves, cat ≤1/20.** Dispersion is the metric, mean is the
constraint — do NOT promote on a fat-tail sum. Promote to live only on forward-green + explicit AxiS
go. The moonbag-free banking is a full model (no MFE-truncation), so the replay STD is a faithful
estimate, not a lower bound.
