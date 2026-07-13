# Slow-bleeder vs V-bouncer exit detector — OOS validation (2026-07-13)

**Mission:** develop AxiS's in-flight signal — positions whose MAE occurs FAST then
recover do better than positions that keep bleeding — into a decision-time exit rule
detectable ~60–120s into a hold, OOS-validated, shadow-wired for forward grading.

**Data:** `_trades_cache.json`, sell rows, `bot_id` startswith `badday_`, `time >= 2026-07-03`.
Only rows with `mae_at_secs` present. **SCRUB RULE** applied (drop `ret>0 & hold<10s`).
**N = 980** after load+scrub (from 1041 with-MAE rows). Metric = **ex-top-2 token-median**
(per-token median, drop the 2 highest-median tokens, median the rest). Win = `pnl_pct > 0`.

---

## 1. The separation is REAL and strong (confirms the thesis)

Ex-post winrate + median return by `mae_at_secs` (when the position hit its worst point):

| mae_at_secs bucket | n   | winrate | median pnl% |
|--------------------|-----|---------|-------------|
| [0,30)             | 286 | 0.441   | −1.62       |
| **[30,60)**        | 105 | **0.657** | **+6.54** |
| [60,90)            | 124 | 0.347   | −5.74       |
| [90,120)           | 47  | 0.404   | −5.55       |
| [120,300)          | 246 | 0.321   | −6.10       |
| [300,∞)            | 172 | **0.110** | −6.72     |

Collapsed: **MAE ≤ 60s → 49.4% winrate** (V-bouncer, n=395) vs **MAE > 60s → 27.4%**
(slow bleeder, n=585). Monotone. This is a powerful ex-post sorter — the timing of the
trough separates outcomes by ~2x winrate.

---

## 2. Decision-time rule (implementable at ~120s from Position state)

At the decision point T, the ONLY faithful decision-time signals are:
`seconds-since-entry`, current pnl, **peak-so-far** (`p.peak_pnl_pct`), and whether the
position is **currently at a new low** (running trough). The cache has no intrabar path,
so offline I proxy "still making new lows at T" with `mae_at_secs >= T` (global trough not
yet reached ⇒ still descending) and "peak-so-far" via the `tp1_knee_3_secs` first-+3-cross
stamp (a TRUE decision-time value — coverage 324/980 rows).

**Rule tested:** at T (=120s, upper edge of the 60–120 window), **CUT** if
`still_making_new_lows(T)` **AND** `not shown +3 strength by T`. Else HOLD.

### Full-sample result (T=120)
- CUT set: n=390, **winner-kill winrate = 24.6%**, **loser-save = 75.4%**.
- HOLD set winrate 43.9% vs base 36.2%.
- CUT-set ex2 token-median −6.5 vs HOLD −5.5 vs base −6.0.

---

## 3. FOUR-HALF OOS (chrono halves × odd/even) — the rule REPRODUCES

Cut = `still-making-new-lows@120s & not-+3-strong-by-120s`:

| Quarter        | n   | cut | winner-kill wr | **loser-save** | hold wr | base wr | cut ex2 | hold ex2 |
|----------------|-----|-----|----------------|----------------|---------|---------|---------|----------|
| Q1 early-odd   | 245 | 84  | 0.262          | **0.738**      | 0.503   | 0.420   | −6.79   | −5.40    |
| Q2 early-even  | 245 | 81  | 0.235          | **0.765**      | 0.512   | 0.420   | −6.50   | −5.39    |
| Q3 late-odd    | 245 | 108 | 0.250          | **0.750**      | 0.350   | 0.306   | −6.45   | −5.55    |
| Q4 late-even   | 245 | 117 | 0.239          | **0.761**      | 0.359   | 0.302   | −7.08   | −5.55    |

**Loser-save ≥ 0.73 in 4/4 held-out quarters. Winner-kill 0.23–0.26 in 4/4.** Extremely
stable. (T=90 is materially the same: loser-save 0.70–0.77, winner-kill 0.23–0.30.) The
CUT cohort's ex2 token-median is consistently more negative than the HOLD cohort's in every
quarter — the rule concentrates the losers.

**Verdict on the OOS bar:** the rule *saves losers* in a majority (4/4) of held-out quarters
— it passes the "saves losers OR preserves winners in a majority" bar. But it does NOT
preserve winners.

---

## 4. Why it can't be ENFORCED yet — the winner-kill is the deep-V dip edge

The ~25% winner-kill is not noise and does not shrink with a better strength filter. Using
the true decision-time strength proxy, **71 of 115 killed winners (T=90) cross +3 only
AFTER the decision point** — they are genuine deep-V's that bottom late then rip. At 120s a
doomed slow-grind and a late-bottoming deep-V look identical on decision-time signals.

Economic magnitude (T=120), the cut set held-to-close:
- **cut winners forgone upside:** n=96, **median +9.68%, p90 +17.7%, max +24.2%**.
- **cut losers capped:** n=294, median −7.52%, min −27.6%.

The forgone +9.7% median (tail +24%) IS the deep-dip reversal the strategy exists to
capture — the same "V-recovery tail" the code already documents at
`per_bot_position_manager.py:525` (ng_faststop "kills ~40% of never-greens that RECOVER").
Cutting the bleeders necessarily cuts these too. **Not winner-safe → do NOT enforce.**

### The unresolved piece (why forward data is needed)
The cache lacks intrabar **peak-so-far timing** and **pnl-at-T**, so the offline winner-kill
is bounded, not exact. The live signals — true `peak_pnl_pct` at T, true running-trough
state, and **drop velocity** (fast-flush V vs slow-grind death) — may separate the two
populations in a way summary fields cannot. That is precisely what the shadow forward-grades.

---

## 5. Shipped as SHADOW (no behavior change)

**`core/per_bot_position_manager.py`** (after the MAE-tracking block, ~line 449): fires once
at the first tick reaching **120s**, pre-TP1, stamping the TRUE decision-time signals into
`state_blob`:
- `bleed_cut_shadow_would_cut` = `still_low_now AND peak_so_far < +2%`
- `bleed_cut_shadow_{secs,pnl_at_fire,peak_at_fire,mae_at_fire,still_low}`
- `bleed_cut_shadow_drop_vel_pp_s` — pp/s from peak, the fast-V-vs-grind miner

**`feeds/dip_scanner.py`** (sell-record builder, ~line 7052): copies the 8 stamps + a derived
`bleed_cut_shadow_saved_pp` (`pnl_at_fire − final`, only when `would_cut`; >0 = cutting helped)
onto the sell record so remote analysis can see them.

**`scripts/experiment_scorecard.py`**: added `bleed_cut_would_cut` to `SOL_SHADOW` via a new
`sell:<field>` key convention (the loop now cohorts on sell-side flags, not just entry_meta).
Verdict relabelled **SHADOW-EXIT** because the PROMOTE=green semantics are INVERTED for a cut
shadow — a RED would-cut cohort means cutting is *correct*. Scorecard runs clean; currently
NO-DATA (shadow only fires forward). All 13 scorecard tests pass.

**NOT enforced, NOT deployed, NOT pushed.** Working tree only.

---

## Bottom line
The MAE-timing separation is genuine, strong, and OOS-stable (V-bouncer 49% vs slow-bleeder
27% winrate; loser-save ≥73% in 4/4 quarters). But at the 60–120s decision point the doomed
grinders and the late-bottoming deep-V dip-recoverers are **not separable** with the signals
available — winner-kill is a stubborn ~25% and the forgone upside (median +9.7%, tail +24%)
is the exact dip-buy edge. Honest outcome: a **strong ex-post sorter that does not yet make a
winner-safe live cut.** Shadowed with drop-velocity + true peak-so-far so forward tape can
resolve whether real-time microstructure tightens the separation.
