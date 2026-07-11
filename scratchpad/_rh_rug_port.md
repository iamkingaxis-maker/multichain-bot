# RH-Chain Rug-Defense Port — Retro Cases, Signal Choices, Costs (2026-07-11)

SHADOW-STAMP ONLY port of the Solana rug defenses (hidden-supply gate, LP custody,
labeled cohort) to the RH EVM lane. Nothing blocks; stamps accrue in the paper ledger
and the labeled outcomes grade them. Working-tree only, no commits, no live changes.

## What shipped (working tree)
- `core/rh_rug_signals.py` — signal computations: pure replay/aggregation helpers +
  `compute_entry_stamp()` (paced, budgeted, tiered, FAIL-OPEN — never raises).
- `scripts/rh_paper_lane.py` — shadow stamper wired in: after EVERY booked paper fill,
  a daemon thread appends an `{"ev":"rug_signals"}` row to `rh_paper_trades.jsonl`
  (single-flight lock, 10-min per-pool cache, `RH_RUG_STAMP=0` kill switch; entry path
  never waits). Applies at next lane restart — the running process was not touched.
- `tests/test_rh_rug_signals.py` (16) + `TestRugSignalStamp` in
  `tests/test_rh_paper_lane.py` (4). Full RH suite: 130 passed.
- `scratchpad/rh_rug_port/` — retro harness (`retro.py`), per-case artifacts
  (`retro_*.json`), grader (`grade_stamps.py`), `PROGRESS.md`.

## Research answers

### RQ1 — holder structure on RH: HOW and at what cost
No archive state on the public RPC (historical `eth_call` fails) and no usable keyless
explorer API (`explorer.mainnet.chain.robinhood.com` is a Next.js frontend returning
HTML on every `/api/v2/...` path probed). The working method is **ERC20 Transfer-log
replay** (`eth_getLogs` is full-history, no Solana 1000-sig cap): replay from token
genesis -> any block = exact holder map at that block. Two shortcuts make it cheap:
- `pool_pct_of_supply` (the HOODLANA shape) = `balanceOf(pool)/totalSupply()` at
  latest = **2 eth_calls, no logs at all** (tier A of the stamp).
- Genesis reached is verifiable: replayed supply must match `totalSupply()`
  (`replay_supply_match` stamped; mismatch flags the holder numbers as partial).

Measured cost (live public RPC, paced 0.25s/call):
| token class | transfer logs | RPC calls | wall time |
|---|---|---|---|
| fresh (<24h, e.g. CASHCATGAME/Halp/KUNA) | 1-9k | ~10-20 | 5-30s |
| aged mid (Ape ~5k xfers, measured end-to-end) | 4.9k | **11** | **9.1s** |
| aged hot (MONSIEUR/RANGER, 30k+ xfers) | 32-36k | ~40-60 | 60-90s |
Budgets cap the worst case: 60k logs / 90s / halve-on-timeout chunking; blown budget
still returns tier A + LP custody with `truncated: true`.

### RQ2 — LP custody on RH
All 9 pools examined (5 rugs + 4 survivors, all hood.fun-graduated V3 1% pools) show
the SAME custody: liquidity minted through the canonical NonfungiblePositionManager
(`0x73991a25c818bf1f1128deaab1492d45638de0d3` — WETH9()/factory() match the chain
canon) with a near-identical protocol-constant liquidity (~3.6819e22), and every
position NFT — rug and survivor alike — is owned by ONE launchpad custodian:
`0x7f03effbd7ceb22a3f80dd468f67ef27826acd85` (contract, 4.8kB code).
**The creator cannot directly pull liquidity on this launchpad**, and in the labeled
rugs the LP was in fact NEVER pulled (net liquidity unchanged through death).
=> RH rugs observed so far are **DUMP-class** (insiders sell supply into the pool),
not Solana-style LP-pulls. LP-custody signals are stamped anyway
(`lp_any_eoa_owner` etc.) because non-hood.fun pools (Noxa direct V3, Robinfun V2)
can carry EOA-held/pullable liquidity — that class just hasn't been traded yet.

### RQ3 — the retrospective test (at-entry reconstruction, event-log replay)
At OUR ledger entry timestamps (binary-searched to blocks; survivors use a synthetic
common timestamp 07-10T21:33Z — they were not all actual entries):

| case | label | pool% | top1 | top10 | shoulder | sh/top10 | float% | holders | absorb Δpp (head-entry) |
|---|---|---|---|---|---|---|---|---|---|
| CASHCATGAME | RUG -97.7% | 15.8 | **11.9** | 22.7 | 6.9 | 0.30 | 61.5 | 718 | **+71.2** |
| MONSIEUR | RUG -94% post-exit | 8.8 | 2.0 | 16.4 | 10.3 | **0.63** | 74.7 | 835 | +14.1 (ongoing) |
| Halp | RUG -90% | 24.5 | 1.6 | 12.1 | 8.5 | **0.71** | 63.4 | 177 | **+63.5** |
| TREAT | RUG -65% pop | 17.5 | 2.0 | 15.9 | 12.0 | **0.76** | 66.6 | 449 | **+39.5** |
| KUNA | RUG -69% pop | 18.8 | 2.0 | 17.1 | 10.9 | **0.64** | 64.1 | 452 | **+21.3** |
| Ape | SURVIVOR | 14.9 | 4.4 | 21.9 | 13.2 | 0.60 | 63.2 | 276 | +1.1 |
| RANGER | SURVIVOR | 6.8 | 5.0 | 18.3 | 9.3 | 0.51 | 74.9 | 965 | -0.0 |
| hehe | SURVIVOR | 66.6 | 1.9 | 12.9 | 6.8 | 0.53 | 20.5 | 147 | -24.1 |
| BILLY | SURVIVOR | 53.2 | 5.5 | 21.3 | 11.3 | 0.53 | 25.5 | 86 | -12.2 |

Yes — there WAS a pre-death on-chain tell, and it mirrors the Solana finding: the
dump class enters **low-pool-share / low-visible-concentration** (supply hidden in
many sub-top-10 wallets — fat shoulder) or with a **single whale overhang**
(CASHCATGAME's 25%->11.9% wallet; its top1 was 25.4% one hour earlier). Creator
(first-mint recipient) = 0% at entry in ALL cases — launchpad conduit; dead signal
as measured. Candidate-predicate hit/miss on this set:

| predicate (flag if) | catch (rugs n=5) | winner-kill (surv n=4) |
|---|---|---|
| pool_pct < 25 | 5/5 | 2/4 |
| top1 >= 10 | 1/5 | 0/4 |
| shoulder/top10 >= 0.6 (fat shoulder) | 4/5 | 1/4 |
| visible_float >= 60 | 5/5 | 2/4 |
| thin_base < 200 holders | 1/5 | 2/4 (anti-signal) |
| joint_dump_shape: pool<25 AND (top1>=10 OR fat shoulder) | **5/5** | **1/4** |

`joint_dump_shape` catches everything but still kills Ape (25% >> the 5% bar) — at
n=5/4 NOTHING is promotable, which is exactly why this ships as shadow stamps.
**The at-head delta is the money finding for LABELING**: rugs absorb +14..+71pp of
supply INTO the pool (insider exit), survivors drift <= +1pp. `Δpool_pct >= +15pp`
is the RH dump-class labeler (union it with post-exit-died; MONSIEUR sits at +14.1
and still bleeding but is caught by the -94% post-exit label).

### RQ4 — what is stamped per entry (and cost budget)
One `{"ev":"rug_signals"}` row per booked entry (per-pool computation cached 10 min;
re-entries write a `cached: true` row so the outcome join stays per-entry):
- **Tier A (2-3 eth_calls, always)**: `pool_pct_of_supply`, `dead_pct`, `total_supply`
  — the HOODLANA hidden-supply shape for ~1s of RPC.
- **Tier C (1-2 getLogs + <=4 getCode)**: `lp_n_owners`, `lp_top_owner(+share,
  is_contract)`, `lp_any_eoa_owner`, `lp_owners[:4]` — the loaded-gun custody read.
- **Tier B (budgeted Transfer replay)**: `n_holders`, `top1_pct(+addr)`, `top10_pct`,
  `shoulder_11_20_pct`, `token_contract_pct`, `creator(+pct)`, `replay_supply_match`,
  plus derived `visible_float_pct`, `whale_overhang_pct`, `shoulder_to_top10_ratio`.
- Bookkeeping: `truncated`, `err`, `cost{rpc_calls,logs,secs}`, `v`, `bot_ids`,
  `entry_ts`, `cached`.
Typical cost ~10-20 paced RPC calls / 5-30s per NEW pool, hard-capped 90s/60k logs,
computed strictly AFTER the fill on a daemon thread behind a single-flight lock —
zero latency added to entries, no RPC burst contention with the strategy loop.

## Grading plan (once labels accrue)
1. Stamps accrue automatically from the next lane session (default ON; `RH_RUG_STAMP=0`
   to disable). The aged-pool racers' longer holds make their entries the ones that
   most need this coverage — they are stamped identically.
2. Labels per stamped pool-entry, all already-automatic or one command:
   worst realized sell pnl <= -60 (ledger) OR post-exit +6h died/<=-80
   (`rh_postexit.jsonl`, existing machinery) OR absorption `Δpool_pct >= +15pp`
   (re-read = 2 eth_calls; `python scratchpad/rh_rug_port/grade_stamps.py --absorb`).
3. `grade_stamps.py` prints the catch/winner-kill table over all accrued stamps for
   the predicate set above (stamps carry raw features, so NEW predicates grade
   retroactively over all history).
4. Promotion bar (unchanged from the Solana resume gate): n>=30 rugged stamped
   entries, catch the cap-hitting class, winner-kill <= 5%, then AxiS approval before
   any BLOCK/de-size ships. At the decode's 8% rug rate over ~10-30 distinct
   tokens/session, expect n>=30 in roughly 2-4 weeks of normal sessions.

## Honest caveats
- n=5 rugs / n=4 survivors; survivor "entry" timestamps are synthetic (not all were
  actual lane entries). No threshold here is promotable — accrual decides.
- All retro cases are hood.fun V3 graduations; V2/Noxa pools may rug differently
  (EOA LP custody) — `lp_any_eoa_owner` is stamped for exactly that class.
- Creator tracking via first-mint recipient is launchpad-nulled (0% everywhere);
  a better creator proxy (graduation-tx sender / bonding-curve buyer) is a follow-up.
- MONSIEUR-class slow bleeds straddle the +15pp absorption line — label by UNION.
