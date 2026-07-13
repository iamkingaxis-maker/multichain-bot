# RH Exit-Capture & LP-Pull Rug — mirroring the Solana findings (2026-07-13)

Two questions ported from today's Solana work, answered on the RH paper ledger
(`scratchpad/robinhood_tapes/rh_paper_trades.jsonl`, 457 closed trips, 43 tokens,
07-10..12). Trips reconstructed with the scorecard `load_rh_trips()` join (sells
per `(bot,pool)`, split at `fully==True`, 1970-epoch rows scrubbed). Working tree
only — PAPER, no deploy, no push. Builds on `_rh_winner_decode2_0713.md` (entry
decode) and `_rh_rug_v2_0713.md` (concentration gate).

**HONEST LOW-N up front:** the ledger is still small (most racers n<30 distinct
tokens; greens 10-12). Nothing here is promotable. Everything is ex-top-2
token-median + green-rate, never sums, and OOS-split where the data supports it.

---

## TASK 1 — Capture efficiency & the exit-side leak

### Peak is NOT stamped — so capture is a reconstructed proxy
Sell rows carry `pnl_pct/kind/reason` but **no `peak_pnl_pct`**, and the per-pool
tape files carry trade prints with **no price series**. So realized-vs-peak cannot
be read directly. Instead I reconstruct a **peak proxy** from the exit-leg
structure (faithful, with one stated assumption):
- TP1/TP2 legs: price provably reached the leg's realized `pnl_pct`.
- POST_TP1_TRAIL / MOONBAG_TRAIL legs: a trail fires `trail_gap` (3pp default)
  below the local peak, so `peak ≈ realized + 3`.
- STOP/BAIL legs (position underwater): `peak ≈ realized` (no favorable peak).
Trip peak = max over legs; capture = `realized_ret / peak` for trips with peak>+1%.

### Result: capture ≈ 0.73, the leak is real and shared fleet-wide
`n=245 trips with peak>+1%`: **capture median 0.727** (mean 0.712, p25 0.62,
p75 0.88). OOS-stable: ODD 0.724 / EVEN 0.730. This is LOWER than Solana's 0.82
— RH gives back ~27% of the peak. Crucially the leak is **worse on the runners**:
trips whose peak reached ≥+12% capture only **0.71**, because the ladder banks
0.75 of the position at TP1 (+6) and only 0.25 rides the run.

Per-racer capture is ~flat across the fleet — this is the key answer to
"do the greens win on exit too?":

| bot | nTrip | TP1-reach% | TP2-reach% | capture med |
|-----|------:|-----------:|-----------:|------------:|
| rh_demand_heavy | 50 | 74 | 38 | 0.761 |
| rh_deep_only | 24 | 62 | 33 | 0.803 |
| rh_young_v1 (control) | 50 | 60 | 36 | 0.732 |
| rh_moonbag | 63 | 60 | 29 | 0.725 |
| rh_wide_ladder | 65 | 49 | 20 | 0.828 |
| rh_liq40 | 31 | 45 | 29 | 0.677 |

**The greens win on ENTRY, not exit.** They run the identical exit engine
(decode2 established this); their capture is only marginally above the control,
and what separates them is **TP1/TP2 REACH** — demand_heavy gets 74%/38% of its
entries to TP1/TP2 vs the control's 60%/36%. That is entry SELECTION producing
follow-through, which the shared exit then banks. The exit is not a differentiating
lever, and its ~0.73 capture is a fleet-wide property, not a green-vs-red gap.

### The leak is NOT recoverable by riding more — a faithful counterfactual
Because the price path of a trip is fixed and only the *held fraction* changes
when you move the TP1 sell fraction, reallocating fractions across the KNOWN leg
prices is a **faithful** counterfactual (the trail/TP2/stop still fire at the same
prices). Sweeping the TP1 sell fraction over the 213 two-leg TP1 trips (single-leg
BAIL/STOP trips unchanged):

| TP1 sell frac | ex-top-2 tokmed | trip median | (0.75 = current) |
|--------------:|----------------:|------------:|------------------|
| 0.90 (bank more) | -4.72 | **-0.96** | |
| **0.75 (actual)** | **-4.72** | **-1.21** | ← |
| 0.60 | -4.72 | -1.76 | |
| 0.50 | -4.84 | -1.76 | |
| 0.00 (ride all) | -4.84 | -3.16 | |

**ex-top-2 tokmed is FLAT (-4.72) across every fraction**, and trip-median gets
BETTER as you bank MORE. Riding more of the position only helps the top-2 fat-tail
tokens that ex-top-2 discards. So on the robust metric there is **no exit tweak
that lifts ex-top-2** — the leak's value is entirely in the fat tail.

### Why fast harvest is correct: the abandoned tail dies
Post-exit +6h price checks (`rh_postexit.jsonl`, n=270) settle the "did we exit too
early?" question decisively:

| exit kind | n | median +6h move | % ran up |
|-----------|--:|----------------:|---------:|
| PRE_STOP_BAIL | 93 | -27.3% | 35 |
| POST_TP1_TRAIL | 68 | **-59.3%** | 28 |
| HARD_STOP | 54 | -72.5% | 7 |
| TP2 | 50 | -48.8% | 30 |
| MOONBAG_TRAIL | 2 | +690.7% | 50 |
| **ALL** | 270 | **-56.0%** | **27** |

The median token is **down -56% six hours after we sell** (only 27% run up); even
POST_TP1_TRAIL exits are followed by a -59% median further fall. **We are not
exiting early — the token dies.** The mean is +58% purely from the rare fat tail
(MOONBAG_TRAIL +690%). Fast harvest is the correct default; the only exit-side
money left is capturing that rare tail with a runner that cannot give back.

### Deliverable: `rh_bankfast` racer (the bank-heavy end of the harvest axis)
Added to the ROSTER (now 29 racers). A **verbatim rh_deep_only entry clone**
(deep -25, scalp age, default $50 demand, all shared guards — so it is graded
AGAINST deep_only), differing ONLY in the exit: **bank 0.90 at TP1 +6** (the
robust-median-best fraction) + a small **0.10 breakeven-FLOORED moonbag on a 12pp
trail** (captures the +690% tail with ~0 giveback after TP1). It completes the
harvest-aggressiveness axis the fleet already spans:
`rh_strength_trail` (ride all) → `rh_deep_barbell` (0.30 runner) → **`rh_bankfast`
(0.10 runner)** — the bank-heavy end the abandoned-tail finding favors.

**Honestly framed in the racer's pre-registration:** the reallocation shows
fraction does NOT lift ex-top-2 (flat), so `rh_bankfast` is a DIRECTIONAL
fat-tail-vs-robust-median test, not a proven lift. Confirm bar: n≥30 closes vs
rh_deep_only, tokmed ex-top-2 ≥ deep_only AND green-rate ≥ deep_only AND the fat
tail (mean/p90) recovered AND cat≤1/20; else retire to kills, no re-tune.

*(Not shippable as an enforced change: nothing here beats the current ladder on
the robust median. A tighter trail on the 0.25 remainder — bank +4 not +3 — could
help but is UNTESTABLE from this data (no price path to price the whipsaw cost);
flagged, not built.)*

---

## TASK 2 — The LP-pull rug class (Halp)

### What Halp actually was (confirmed from the ledger)
Halp: bought at **liq $17,187, dip -19.89, pool age 0.12h (7 min)**, then
**HARD_STOP at -90.04% just 10 seconds later** (single pre-fleet config, session-1,
07-10). This is a **single-block TOTAL LP pull**: the entire reserve was removed in
one block, so the very next sell quote already read ~-84%. Holder concentration
was a winner shape (rug_v2: top1 1.6 / top10 12.1) — **structurally invisible to
any pre-buy distribution gate**, exactly as rug_v2 concluded.

### Two honest truths about the single-block class
1. **No pre-buy holder signal exists for it.** rug_v2's sweep already showed every
   predicate that caught Halp (nhold<250, fat shoulder, float≥60, pool<25) also
   killed 2-20 of 22 winners. The mechanism-aligned pre-buy defense is **LP
   CUSTODY** (`lp_any_eoa_owner`, rug_v2 shadow stamp) — which fires 0 on today's
   launchpad-custodied hood.fun pools and awaits a non-hood.fun EOA-LP pool to
   grade. That gap is unchanged by this work.
2. **No EXIT can save the single-block case.** By the time reserves are gone the
   sell quote is already ~-84%; the price stop (which fired at -90) IS the fastest
   possible detector and can only realize the collapse. Incidentally, the config
   that bought Halp no longer would: the current **MIN_POOL_AGE_H=1.0** blocks a
   7-min pool and **MIN_LIQ 30k** blocks a $17k pool — the fresh-launch pull shape
   is already fenced (coincidentally, not as a general LP-pull defense).

### Where an exit bail CAN help: the STAGED / partial-drain class
The class an exit bail defends is **staged** pulls — liquidity bled over
seconds-to-minutes, where reserves fall meaningfully BEFORE the price path fully
collapses, leaving a book to sell into. The lane's existing `LP_DRAIN` exit is the
right idea but too slow for this: it uses a **900s rolling window that needs ≥2
in-window samples** and is fed at **maintenance cadence** (≥60s liq refresh) — it
did not (could not) fire on Halp, and would miss a fast staged pull.

### Deliverable: fast per-tick LP-pull bail (SHADOW, env-gated)
`core/rh_rug_signals.fast_liq_bail_verdict(entry_liq, cur_liq)` — **PURE**:
compares current reserves to the **fixed AT-ENTRY baseline** (no window, no
2-sample requirement) and fires on the FIRST tick a `≥35%` collapse is seen.
FAIL-OPEN on missing/invalid/zero liq (a total pull reads cur=0 → fail-open; the
price stop owns that case). Thresholds env-tunable (`RH_FAST_LIQ_BAIL_PCT=-35`).

Wired into `rh_paper_lane._manage_exits` right after the existing LP_DRAIN check:
- `meta["entry_liq"]` now stamps the entry baseline.
- Each held position, each tick: read `feed.watch[pool]["liq"]` (already in memory
  — **zero added latency, well inside the ≤2s budget**), compute the verdict.
- **Mode `shadow` (default):** append ONE `{"ev":"fast_liq_bail"}` would-fire row
  per position and change NOTHING about trading. **`block`:** immediate full
  FAST_LIQ_BAIL exit. **`off`:** no keys. `RH_FAST_LIQ_BAIL` env.

**UNVALIDATED — flagged:** the ledger has **0 staged pulls** (Halp was
single-block; `feed.watch` liq wouldn't have refreshed in its 10s life anyway), so
this cannot be graded yet. It runs in shadow to ACCRUE would-fire + outcome data —
winner-kill in particular must be measured before promotion (a large v3 swap can
transiently move concentrated reserves >35%, which would false-fire). Promotion
bar mirrors rug_v2: catch the staged class, winner-kill ≤5%, then AxiS approval.

---

## Files touched (working tree, PAPER)
- `core/rh_rug_signals.py` — `fast_liq_bail_verdict` (pure) + `_fast_liq_bail_mode`
  + `FAST_LIQ_BAIL_PCT`. Fail-open, shadow default.
- `scripts/rh_paper_lane.py` — import; `entry_liq` on position meta; fast-liq-bail
  shadow stamp / optional enforce in `_manage_exits`; **`rh_bankfast`** racer in
  ROSTER (29 racers).
- `tests/test_rh_rug_signals.py` — `TestFastLiqBailVerdict` (+8 tests: collapse,
  stable, liq-up, boundary, fail-open ×3, custom thr).
- **Suites:** `test_rh_rug_signals.py` + `test_rh_paper_lane.py` +
  `test_rh_factory_racers.py` = **136 passed.** ROSTER smoke: rh_bankfast builds
  (tp1 0.90/tp2 0.0/moon 0.10@12pp); fast-liq-bail default mode = shadow.

## Bottom line
- **Task 1:** RH capture ≈0.73 (leak real, OOS-stable), but it is a FLEET-WIDE
  exit property — the greens win on ENTRY (higher TP1/TP2 reach), not on a better
  exit. The leak is NOT recoverable on the robust median (ex-top-2 is flat to the
  TP1 fraction; the token dies -56% median post-exit, so fast harvest is correct).
  The only exit-side money is the rare fat tail, best captured by a bank-heavy
  floored runner — shipped as `rh_bankfast` (directional test, not a proven lift).
- **Task 2:** The single-block LP-pull (Halp) has NO pre-buy holder signal and NO
  possible exit save — its defense is LP-CUSTODY (rug_v2, awaiting an EOA-LP pool).
  For the STAGED-pull class a fast per-tick liq-collapse bail is genuinely useful
  and was shipped SHADOW (zero latency), but is UNVALIDATED (0 staged pulls
  observed) and must accrue data before any enforce.
