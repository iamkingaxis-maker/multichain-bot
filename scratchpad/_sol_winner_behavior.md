# SOL Winner-Behavior Decode — the exit-discipline gap (2026-07-12)

**Question (AxiS): "we aren't winning — decode winners to see what we're missing."**
BEHAVIORAL patterns to build as strategy, NOT identity copying. Union-counted on
distinct tokens; ex-top-2 token-median is the honest metric. Priors respected:
identity copy is dead, copy-cohort was refuted today, winners are harvesters.

## TL;DR — the ONE thing winners do that we don't: they HOLD; we PANIC-CUT.
Winners and our bots have the **same win rate** and **similar size/breadth**. The
only large behavioral gap is **hold time**: winners give a position **minutes**;
we cut ~half of ours in **under 2 minutes**, at a loss, on shallow noise dips —
before the absorption/mean-reversion thesis we bought can play out. Same-token
union evidence: on tokens where one bot panic-cut and another held ≥120s, holding
beat cutting on **72%** of tokens and flipped the median from **-7.9% → +0.4%
(+10pp)**. This is a pure exit-discipline change, no new selection edge required.

---

## 1. Winners vs us — where the gap ISN'T (kills the usual suspects)

Winner traders = 60 top pnl-7d wallets (`scratchpad/_toptrader_wallets.json`);
winner round-trips = `follow_exits.jsonl` (484 realized, 16 wallets / 133 tokens);
us = `scratchpad/sol_selection/_trips.json` (955 realized trips / 151 tokens,
post-scrub, 07-02..07-12).

| behavior            | WINNERS                     | US                          | gap |
|---------------------|-----------------------------|-----------------------------|-----|
| **win rate**        | 46.2% med (p25 41 / p75 55) | **45.9%**                   | **~0 — NONE** |
| **avg cost / size** | $168 med avg-cost           | $100 med amount_usd         | we're smaller, not the lever |
| **breadth/velocity**| 704 tx/wk (~100/day)        | ~95 trips/day, 151 tokens   | comparable — NOT the lever |
| **hold time**       | **~535 min avg** (p25 198m) | **134 s median (2.2 min)**  | **HUGE — the gap** |

Winner WR ≈ our WR **confirms the harvester prior**: winners are not better
selectors. Their edge is not a genius entry filter and not raw breadth (we already
spray 151 tokens). It is that they **let positions live**. Their capital sits in a
position ~9 hours on average; ours flips in ~2 minutes.

(Caveat on the winner round-trip tape: `follow_exits.jsonl` returns are pegged at
+40 — it is a simulated +40% TP / ~5-min-box roster shadow, so its 69.6% WR and
+40 median are exit-policy artifacts, not raw winner P&L. The **trader-level
stats** (`_toptrader_wallets.json`: 46% WR, 535-min hold) are the un-simulated
ground truth and are what the headline rests on.)

---

## 2. Our own tape proves the hold gap is the leak (not selection, not exit-on-winners)

### Exit-on-winners is already GOOD — not our problem
- Capture ratio (ret / peak) = **0.83–0.92** across every hold bucket. We bank
  most of what a position offers.
- Only **6.9%** of trips that reached +10% round-tripped back to red.
- Median peak-giveback = 2.6pp. **We are not holding winners too long or cutting
  them too early.** The winner exit-discipline gap the task hypothesized (sell
  strength / trail) is not where our money leaks.

### The leak is the FIRST 2 MINUTES — RET/WR by hold bucket (all 955 trips)
| hold        |  n  | WR  | med ret | mean ret | med peak |
|-------------|----:|----:|--------:|---------:|---------:|
| **0–60 s**  | 220 | 25% | **-6.4**|   -0.5   |   +0.0   |
| **60–120 s**| 236 | 47% | **-4.0**|   +3.1   |   +0.0   |
| 120–300 s   | 297 | 56% | **+4.5**|   +2.7   |   +4.0   |  ← sweet spot
| 300–600 s   | 124 | 54% |  +2.3   |   +2.0   |   +4.2   |
| 600 s+      |  77 | 51% |  +0.0   |   -3.4   |   +7.2   |  ← tail losers

**48% of all trips exit under 2 minutes and they are red** (25% / 47% WR). Our own
best bucket is **120–300 s (56% WR, +4.5 median)** — the exact window we cut short.

### Fast cuts do NOT avoid rugs (kills the "cutting saves us" defense)
Catastrophic losses (ret ≤ -25%) by bucket: 0–60s **0.5%**, 60–120s 3.8%,
120–300s 2.0%, **600s+ 11.7%**. The rug tail lives in the LONG holds, not the
short ones. And of our sub-120s red cuts, **49% are cut at a shallow -8%-or-better
loss** (med MAE -5.0) — panic on noise, not rug-avoidance.

---

## 3. The decisive test — same-token union count (distinct tokens, apples-to-apples)

61 distinct tokens had BOTH a fast panic-cut (<120 s, red) by one bot AND a ≥120 s
hold by another bot on the same token, same window:

- median fast-cut ret **-7.9%**  →  median held ret **+0.4%**
- holding beat cutting on **44/61 tokens = 72%**
- the held trip closed **green on 51%** of these tokens (money the cut left behind)
- median improvement from holding = **+10.0 pp**

This is union-counted on anonymous distinct tokens (no identity, no single-address
trust) and it isolates hold-time by holding the token fixed. It is the strongest
evidence in the set: our panic-cuts are premature on ~3 of 4 tokens.

The panic-cut cohort = **292 trips, 31% of all volume, med -8.1%, 99 distinct
tokens.** It is the single biggest drag on our ex-top-2 token-median.

---

## 4. THE #1 MISSING LEVER — a min-hold "no-panic" floor  ⭐ SHADOW HYPOTHESIS

**Winners hold through the opening chop; we don't. Build that as a rule.**

### Hypothesis (testable, paper/shadow — do NOT enforce without AxiS + forward)
Suppress every **soft pre-TP1 cutter** for the first **~120 s** after entry, and
let the absorption/mean-reversion thesis reach the 120–300 s sweet spot. Keep only
a **hard-rug tripwire** live during the floor.

Concretely, in `core/per_bot_position_manager.py` the sub-120s cutters are:
- **velocity-bail** `peak<2 AND pnl<=-4 AND drop_vel>=0.012` (never-green fast
  collapse) — fires in the first seconds on exactly the shallow dips that recover.
- **in-flight -7% MAE floor** (`IN_FLIGHT_FLOOR_PCT`, already has a `shadow` mode).
- **-9 fast-dump bail** / **pre-stop bail** (pre-TP1).

Shadow spec: add `MIN_HOLD_FLOOR_SECS` (default 120, env-tunable). While
`now - entry_time < MIN_HOLD_FLOOR_SECS` and `not tp1_hit`, gate off velocity-bail
/ in-flight-floor / fast-dump / pre-stop, EXCEPT a hard-rug tripwire that still
fires: **liquidity pull (liq drop > ~40%), top-1 holder dump, or price ≤ -25%.**
Stamp the counterfactual (would-have-cut vs held-to-floor) via the existing
`filter_shadow_recorder` / `state_blob` pattern; fail-open on missing fields.
Grade forward on REALIZED trips: ex-top-2 token-median of the floored cohort vs
the panic-cut cohort, n≥15 distinct tokens, green in ≥3/4 OOS halves.

### Why this is the highest-value lever
- It attacks the **biggest loss cohort** (31% of volume, med -8.1%) directly.
- It requires **no new selection edge** — it monetizes entries we already take.
- It is **behaviorally what winners do** (46% WR + long holds = harvesters who let
  positions work), and it composes with the aged-pond absorb thesis (buyers need
  2–5 min to eat the dip; we currently cut before they finish).
- Prior corroboration: memory already flags "velocity-bail inverted → wickride_ab"
  — this generalizes that single finding into a time-based floor over ALL soft
  cutters.

### Upper-bound impact (HEAVILY haircut — a direction, not a projection)
If the 292 panic-cuts instead realized the 120–300 s bucket median (+4.5%), our
**ex-top-2 token-median moves -5.8 → +4.5**. This is a survivorship-biased UPPER
BOUND (assumes cut tokens follow the surviving-hold distribution). The **robust,
non-counterfactual number is the same-token union: +10 pp on 72% of tokens.**
Even a fraction of that flips the median green.

### Guardrails (do not over-hold)
- 600 s+ is our WORST bucket (11.7% catastrophic, -3.4 mean). This is a **FLOOR,
  not a longer target** — keep the existing upper time-box and hard stop past the
  floor. The rule is "don't panic in the first 2 min," not "hold for hours."
- The hard-rug tripwire MUST stay live during the floor so it never rides a real
  liquidity pull down.

---

## Verdict
The gap is **not** selection (WR ties winners), **not** size, **not** breadth,
**not** exit-on-winners (capture 0.83). It is that **winners give positions time
and we panic-cut ~48% of trips inside 2 minutes** at a 25–47% win rate, on shallow
noise, without avoiding a single meaningful rug. The one lever most likely to move
our ex-top-2 median toward green is a **min-hold "no-panic" floor (~120 s) that
gates soft cutters while keeping a hard-rug tripwire** — validated forward as a
shadow before any live enforce.

_SHADOW-stamped as hypothesis `min_hold_no_panic_floor` — spec above; measure-only;
no code committed, no live change. Data: `_trips.json` (955), `follow_exits.jsonl`
(484), `_toptrader_wallets.json` (60). All writes utf-8._
