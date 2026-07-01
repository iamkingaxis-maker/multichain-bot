# OPERATING RUNBOOK — multichain-bot
*Written 2026-07-01 (Fable 5). The system's operating manual: how to read it,
run it, and make decisions without re-deriving three months of lessons.*

---

## 1. THE SCOREBOARD (the only numbers that count)

```
PYTHONPATH=. python scripts/pull_full_trades.py     # once (30-min freshness gate)
PYTHONPATH=. python scripts/honest_book.py           # THE daily number
```
- Quote ONLY the **SCRUB** columns (spike-scrubbed) and **per-token** columns.
- Raw means are contaminated by two dead illusions: stale-snapshot fills and
  latency-spike prints (77 prints = the entire fake "great era" of 06-26..28).
- Nominal n is ~6x inflated by mirror bots buying the same fill — distinct
  tokens is the honest n.
- The dashboard "realized P&L" is a simulated ledger — ignore it for live truth.
  Live truth = on-chain wallet SOL delta + live_swaps.jsonl.

## 2. THE STRATEGY (what this machine is)

A **red-day dip machine**: it buys genuine capitulations (deep dips met by real
buyer demand) and profits from the bounce. Measured regime truth:
- SOL red on the day: +5.60 mean / 44% win. SOL green: −0.83 / 36.5%.
- On green days most "dips" are pump-retraces that keep bleeding (−5.4%, 18.6% win).
- The only green offense cells: mild-green capitulations (pc_h6≤−25: +4.57%/55.6%)
  — fielded as `badday_greencapit_conviction` — and (rip days) oversold_held-only,
  ~breakeven. `GREEN_DAY_MODE` encodes all of this.
- The realizable edge is thin and fat-tailed: median trade is red; profit = cut
  losers fast + let the ~40% winners pay. There is no silver-bullet entry signal
  — 30+ mined axes died; the edge is the ONE thesis, sharpened.

## 3. CONFIG: MATRIX A vs MATRIX B (never confuse them)

- **Matrix A (paper/learning — what runs day-to-day):** SOL gate loose, cohorts
  in shadow, regime gate shadow, wide universe. Purpose: volume + data.
- **Matrix B (live):** `config/LIVE_MATRIX_B.md`. SOL strict + FULL_THESIS +
  OVERSOLD_HELD + regime + green-day all enforce, floors set to REAL capital.
- **Verify with one command** (must print GO before any PAPER_MODE change):
  ```
  railway variables > env.txt
  PYTHONPATH=. python scripts/go_live_preflight.py --env-file env.txt
  ```
- **PAPER_MODE=true is the ONLY barrier** between the enabled fleet and real
  money (the signing key is in the env). Treat any PAPER_MODE change as a
  loaded gun. Never flip under Matrix A. Never flip without the preflight GO
  + the go-live bar (scrubbed cohort ≥ +2pp / ≥300 trades / ≥50 tokens / ≥5 days).

## 4. SIZING & RUIN (why $5)

$50→$10 happened because of 4x oversize ($20 on a $50 bankroll), not the edge.
Ruin math: at $5/position ruin ≈ 0%; at $20 a bad small-n run wipes you.
- `DIP_POSITION_USD=5` until a ≥30-fill slippage probe measures real live cost;
  then $25 (cuts fixed-fee drag 0.7pp → 0.2pp) with a re-run ruin sim.
- `PROBE_AGG_DAILY_KILL_USD=10` (two losers halt the day).
- `WORKING_CAPITAL_FLOOR_USD` must equal ACTUAL starting capital before live
  (profit sweep banks everything above it).
- Expected live economics: **live = scrubbed paper − ~1.0–1.5pp/trade at $5**
  (−~0.5pp at $25). Parity is already real; the job is making scrubbed paper
  worth matching.

## 5. SCENARIO PLAYBOOK

**"We aren't buying"** (happens after nearly every change — dig, don't guess):
1. Ground truth first: sum `open_position_count` over `/api/bots` (NOT logs);
   per-bot LAST-ENTRY timestamps from the ledger (all bots stopping the same
   second = one cutover event).
2. Trace the funnel stages in logs: `Signal:` → `Position size tier` →
   `hit-rate buy` → position. Where the counts drop is the layer.
3. Known chokepoints, in the order they've actually bitten: regime BUY-GATE
   (`BUY-GATE OFF` lines), fleet-wide env gates that ignore per-bot config
   (`FULL_THESIS_COHORT_MODE` defaults ENFORCE in code!), the buyer-count
   demand floor + rug gate fed by maker data (a fetch failure used to read as
   "0 buyers" and dark the fleet — fixed to fail-open, but check `no real
   buyers` counts), nf15 on thin windows (fixed: needs ≥3 trades), the
   arm→fire handoff, and the hot-path fetch budget.
4. NEVER "fix" it with the stale-snapshot path (`MAIN_SCAN_BUY_MODE=on` is
   banned) and never relax the loser-catcher filters to fake activity.

**Bad day / drawdown:** check the honest book by ENTRY date (a 30-day-old bag
detonating is not strategy failure); check whether the loss is SOL-green
entries (the green-day gate exists for this); do NOT de-size or env-barrage.

**Green/pump day:** standing aside on retraces is correct behavior. The
green-capit bot and (if enforced) GREEN_DAY_MODE handle what's tradeable.

**Enforce decisions (gates/exits):** only on realized trade-joins, spike-
scrubbed, per-token deduped, ≥3 days spread, drop-top-1-token still positive.
The forward-candle shadow scorer OVERSTATES blocked-cohort edge — never use it
to enforce. Check ACCRUED shadows before building anything new (the breakeven
lock was found this way: 119 fires / 22 tokens / +2.46pp saved → enforced;
ng_faststop and sol_bail measured NEGATIVE → never enforce them).

**Costs rising:** egress = consumer behavior (gzip header always, light
endpoints, ≤2 full pulls/day, ≥5-min monitor intervals — the server side is
already engineered). Memory: torch is already gone (numpy encoders, 06-28);
watch RSS with the widened universe; caches are bounded.

## 6. CHANGE DISCIPLINE (the guardrails, paid for in tuition)

1. One env flip at a time, with a written expected effect + revert condition.
2. ≥30 trades or ≥1 full day of logs before concluding anything; a 2-minute
   log snapshot has produced opposite conclusions an hour apart.
3. Every new gate ships off|shadow|enforce, badday_-scoped, fail-open on
   missing data, shadow-first.
4. Fleet-wide cutovers need a volume check the same hour (the arm_only cutover
   silently darked all 9 bots for 26h).
5. Buys/day <30% of the 7-day median = investigate immediately, per-bot
   last-entry timestamps first.
6. Never optimize toward unrealizable prints; the scrub is the law.

## 7. CURRENT STATE (as of 2026-07-01 late UTC)

- Live PAUSED (`PAPER_MODE=true`), hot wallet ~$12 tradeable (+ the OFF-LIMITS
  personal `…Cmoon` token — never touch it).
- Enforced tonight: `BREAKEVEN_LOCK_MODE=enforce` @ `PEAK_MIN=3.0` (measured
  +2.46pp/fire, 22 tokens). Fill-quality: WS feed ON, hot-movers ON, 3s fetch
  budget, fetch window 100.
- Accruing in shadow: GREEN_DAY_MODE, full_thesis, oversold_held, stale_knife,
  dev_not_dumped, wide-exit A/B (`badday_flush_wideexit_ab`), patient-slot A/B,
  rsi A/B, green-capit conviction bot.
- Smart-money index v2 = validated winner wallets (23 green-day + 3 rip-day
  repeatables); `smart_wallet_count_*` in entry_meta is now a real signal — mine it.
- The missing ~2pp to the live bar is a 3-horse race: entry timing (fill-vs-dip
  gap, target median ≤+1.5% / p90 ≤+4%), exit capture (winners realize only 52%
  of peak; breakeven-lock + wide-exit attack it), offense lanes (green-capit +
  the rip-day mine spec).
- 7-day plan: Day 1 volume verdict → Day 2-3 slippage probe (Matrix B lane,
  operator decision) → Day 5 fund-and-scale gate → Days 6-7 scaled live.

*Memory for future Claude sessions lives outside the repo; this file is the
repo-resident truth. Update it when the state changes materially.*
