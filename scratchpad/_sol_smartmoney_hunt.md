# Smart-Money / Wallet-Follow Green-Cohort Hunt — 2026-07-13

**Verdict: NO green copyable smart-follow cohort. Stop re-mining this lever until per-token
REALIZED copy outcomes are logged.** Honest negative below.

## What was asked
Find a FILTERED smart-follow cohort (e.g. dial-good AND K>=4 AND flush-gated) whose
EX-TOP-2 token-median (drop the 2 best tokens) is > 0 with >=50% tokens green, n>=15 distinct
tokens, OOS-stable. Lifetime SUM banned as a verdict.

## Data pulled (Railway gracious-inspiration, 2026-07-13)
- `/api/follow-logs?limit=2000`: 1635 fires (07-09 12:55 -> 07-13 01:05 UTC) with full stamps
  (tier, copy_dial, flush_gate, dist_guard, thin_book, conviction_mult, fq_mean, n, state.price),
  + 180 fire_unconverted + 185 elite_exit + 2000 elite exit round-trips.
- `/api/follow-capital`: pool realized + copy_dial.
- `/api/trades?full=1&all=1`: 10921 trades, 05-12 -> 07-13.
- Local `follow_signals.jsonl` was STALE (42 fires 06-08, pre-stamps) — unusable.

## The blocking data gap (why this can't be measured on realized, per-token)
Recent smart_follow copies are **NOT** retrievably recorded per token:
- Trades store: `smart_follow` tag only on **baseline_v1**, and only **06-08 -> 06-17**
  (325 buys; the 10 "sells" are all `cancelled on restart`, pnl 0). baseline_v1 itself ends 07-05.
- Recent window (07-09+) trades are ALL fleet bots (`badday_*`, strategy None) — not copies.
- All 185 recent `elite_exit` records show `position_closed=False` → copies aren't closing/booking.
- The only realized signal is **follow_capital aggregate** + its last-40 `recent_closes`
  (no per-token history). So an ex-top-2 token-median on REALIZED copy P&L is not computable.

Consequence: the only per-token, filter-conditionable signal is the ROSTER's OWN round-trip
return (`follow_exits.jsonl` wallet_return_pct) — an **UPPER BOUND** on any copy (we enter later/
higher than the elite and pay exit slippage; copy return <= elite own return).

## REALIZED ground truth (the verdict)
- **follow pool realized_since_epoch (06-11) = -$122.03**. swept $6.39. hot equity $871.58.
- **copy_dial = "bad" (exp -$1.66/close, n=20)** right now — AND stamped **"bad" on 100% of the
  1635 recent fires**. The bad-regime detector has been pegged the entire window.

## Elite-OWN-return UPPER BOUND, ex-top-2 token-median (before copy-tax; survivorship-biased)
Join rate: 980/1635 fires have a joinable elite exit (only ~60% — completed round-trips only;
the missing 40% skew to bag-held losers / dead tokens = optimistic bias).

| cohort | n_tok | ex-top-2 | green% | note |
|---|---|---|---|---|
| ALL fires | 353 | -0.1 | 50% | |
| Gate-passed (flush+dist+liq>=20k) | 159 | **+2.8** | 58% | best broad subset |
| tier=convex ($25 tail-hunters) | 126 | **+3.5** | 62% | fat-tail pod |
| flush=pass (real flush) | 124 | +1.6 | 56% | |
| tier=k3 (K>=3 consensus) | 56 | +0.6 | 54% | |
| tier=solo | 49 | +0.0 | 51% | |
| fq_mean>0 | 120 | +1.4 | 56% | |
| **n>=4 consensus** | 15 | **-4.2** | 33% | hypothesized "high-K" leg — WORST |
| **n>=5 consensus** | 4 | -9.9 | 25% | underpowered, negative |
| **conviction>=2x** | 4 | -11.0 | 50% | underpowered, negative |
| k3 + flush + n>=4 | 15 | -4.2 | 33% | |

OOS 4-half (gate-passed): Q1..Q4 ex-top-2 = +3.8 / +1.6 / +2.8 / +1.1 (mildly + but shrinking).
OOS 4-half (convex): +0.4 / +4.0 / +5.1 / **-0.5** (Q4 negative — not stable).
Roster's own round-trips overall (n=2000): median +1.5%, mean +4.8% (fat-tailed), 53% green,
median hold **38 min** (fast scalpers).

## Why the upper bound that looks green is NOT a green copyable cohort
1. **copy_dial "good" filter = n=0.** The dial never left "bad" in the mineable window; the
   specific "dial-good AND K>=4 AND flush" filter the task hypothesized is EMPTY.
2. **Copy-tax eats it.** Elite own edge is ~+1.5% median on 38-min scalps. Code-measured copy
   chase = **+1.56% mean** (enter after the elite), plus $50 exit slippage in thin books. A +2.8%
   elite-own ex-top-2 minus ~1.5-2% chase minus exit slippage lands at breakeven-to-negative —
   which is exactly what the REALIZED pool shows (-$122). Textbook "quality != copyable."
3. **More consensus INVERTS.** n>=4/n>=5/conviction>=2x are the MOST negative cohorts, not the
   best. Confirms the standing prior: roster wallets are harvesters (~51-53% WR), not detectors.
4. **Survivorship bias.** Only 60% of fires have a joinable elite exit; the missing 40% (elites
   bag-holding / tokens dead) are disproportionately losers → +2.8% is optimistic.
5. **convex is a fat-tail pod** — its thesis IS the tail that ex-top-2 explicitly strips; its own
   follow_quality scores are red (-0.11, -0.10).

## Conclusion
No filtered smart-follow cohort clears the honest bar on realized outcomes. The realized pool is
red and the dial is pegged "bad." The only marginally-green signal (elite-OWN upper bound,
gate-passed +2.8% / convex +3.5%) is fully consumed by documented copy-tax, survivorship-biased,
and inverts under the very "high-K / high-conviction" filters that were hypothesized to help.
This matches the hard priors (identity dead; harvesters not detectors; copy-tax un-copyable).

**No shadow-stamp** — nothing green to shadow. The ONE precondition that would make re-mining
worthwhile: land **per-token REALIZED copy-outcome logging** (currently missing — that gap is the
only reason a proxy was needed). Until copies book realized per-token P&L, any green claim here is
a proxy / fat-tail artifact — the exact failure reverted earlier today.
