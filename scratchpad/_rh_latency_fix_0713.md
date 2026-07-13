# RH quote-leg latency: attribution + fixes (2026-07-13)

GOAL: cut RH detect→fill latency under Solana parity (median lat_total_s ≤ 1.71s
AND p90 ≤ 2.0s). Paper lane only; live-execution triple-gate untouched; nothing
deployed. All behavior changes env-gated, default = current behavior.

## 1. Attribution (measured, not assumed)

Re-ran the ledger `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` on the 462
`ev:buy` rows that carry all three latency stamps (internally consistent:
`lat_total_s == lat_trigger_lag_s + lat_quote_s` exactly, max residual 0.005s):

| leg              | med   | mean  | p90   | p95   | p99   | max    |
|------------------|-------|-------|-------|-------|-------|--------|
| lat_trigger_lag  | 0.640 | 0.939 | 1.170 | 1.320 | 17.95 | 18.35  |
| **lat_quote**    | **1.062** | 1.060 | 2.114 | 2.172 | 3.195 | 3.195 |
| lat_total        | 1.785 | —     | 3.040 | 3.530 | —     | 18.510 |

- **51% of fills (236/462) exceed the 1.71s parity budget.** In those over-budget
  rows the QUOTE leg is the culprit 91% of the time (median quote 1.872s vs
  median trigger 0.77s). The brief's numbers reproduce exactly.
- The **quote leg is the median problem AND the p90/p95 problem** (its own p90 =
  2.11s).
- The **18.5s extreme max is `trigger_lag` (firehose)**, NOT quotes — quote's own
  max is 3.2s. The firehose stall is a rare (p99≈18s) sequencer-feed event, a
  separate problem from the quote path this task targets. Flagged, not fixed here
  (it's in `rh_firehose_feed.py`, not the quote path).

### Where the 1.062s quote median comes from — exactly

In `scripts/rh_paper_lane.py::_paper_buy`, `lat_quote_s = t_fill - t_decide`
spans **TWO sequential batched QuoterV2 POSTs**:

1. `quote_buy(token, eth_in)` → `_best_quote` → `_quote_all_tiers_batched` — one
   JSON-RPC batch POST (4 fee tiers in one HTTP round trip).
2. `quote_sell(token, q.amount_out)` — the RT-cost friction gate; a SECOND
   batch POST, quoting the sell of the buy's **exact** output.

Each batched POST measures **~0.5s server-side on the public RPC**
`https://rpc.mainnet.chain.robinhood.com` (raw TCP RTT is ~55ms; the ~0.5s is
QuoterV2 `eth_call` evaluation + queueing on the shared public node). 2 × ~0.5s ≈
the observed 1.06s median. A timing sim at 0.5s/POST reproduces it: split path
1.010s.

What is **already optimal** (verified, not re-touched):
- **Connection reuse** — `RhExecutor._batch_session` is a persistent
  `requests.Session` and `self.ex` is a process singleton (`_executor()`), so
  there is NO per-quote TLS/TCP handshake. The 4-tier sweep is already batched
  into ONE POST (the 2026-07-11 fix). `token_decimals` is memoized.
- So the ~0.5s/POST is **RPC server latency**, not client overhead.

Root causes, ranked:
1. **Two sequential, dependent round trips** (buy → RT-cost sell). The sell input
   is the buy's output, so they can't be naively parallelized. This doubles the
   floor. ← addressable from code.
2. **Public-RPC per-call latency (~0.5s/POST).** ← the median FLOOR; needs infra.
3. **No fast-fail on the tail:** the batch POST timeout was hardcoded `10s`, and
   on a batch miss `_best_quote` falls back to a **sequential 4-`eth_call` sweep**
   (each at the provider's 15s timeout). A slow buy+sell can stack toward the
   p90/p95 (2.1s) and the quote max (3.2s). ← addressable from code.

## 2. Fixes implemented (all env-gated, default = current, fail-safe)

### A. Per-leg latency attribution (always on, zero behavior change)
Split the single `lat_quote_s` stamp into `lat_quote_buy_s` + `lat_quote_rt_s`
(+ `quote_mode`) on every buy ledger row. The buy-vs-RT split was previously
unmeasured; now every future fill attributes the two POSTs so the A/B below is
readable straight from the ledger.

### B. `RH_QUOTE_TIMEOUT_S` (default 10.0) — cut the tail
Batched quote POST timeout is now configurable (`core/rh_execution.py::
_quote_timeout_s`, used by `_quote_all_tiers_batched` and the new roundtrip
method). Set to e.g. `2.5` to fast-fail an RPC-latency spike. FAIL-SAFE: a
timeout returns `None` → no quote → no trade (never a late/bad fill).

### C. `RH_QUOTE_FALLBACK` (default `seq`) — cut the sequential-sweep tail
`RH_QUOTE_FALLBACK=none` makes `_best_quote` skip the slow per-tier sequential
sweep on a batch miss and fast-fail instead. FAIL-SAFE: a missed quote is a
missed fill, never a bad one. Default `seq` = prior behavior.

### D. `RH_RT_COMBINED` (default OFF) — the median lever from code
Folds the buy quote + RT-cost sell quote into **ONE** batched POST
(`build_roundtrip_quote_batch` / `parse_roundtrip_quote_batch` /
`RhExecutor.quote_roundtrip_batched`). The sell leg quotes an **estimated** token
amount (`_est_token_out`, from the pool's last quote px) — used ONLY for the
rt-cost friction gate; **the booked fill price always comes from the EXACT buy
quote in the same response.** The only approximation is a small error in the
rt-cost gate INPUT, bounded by the gate's multi-pp threshold (`max_rt_cost_pct`
= 6%). FAIL-SAFE: no px basis, bad estimate, or any batch problem → falls back to
the exact two-POST path. Default OFF ⇒ working-tree behavior is byte-identical.

## 3. Expected / simulated improvement

Timing sim at the measured 0.5s/POST (fake session, real code paths):

```
split (2 POST) = 1.010s   combined (1 POST) = 0.508s   saved ≈ 0.50s
```

Projected medians with `RH_RT_COMBINED=1` (holding trigger_lag at its 0.64s
median, which this task does not touch):

| metric      | now    | projected | target  |
|-------------|--------|-----------|---------|
| lat_quote   | 1.062s | ~0.53s    | —       |
| lat_total   | 1.785s | **~1.17s**| ≤ 1.71 ✅|
| p90 total   | 3.040s | ~1.7–1.8s | ≤ 2.0  (borderline; B+C help) |

So the combined round-trip alone brings the **median under parity**. The p90 also
falls (fewer double-POST slow draws); B (`RH_QUOTE_TIMEOUT_S≈2.5`) + C
(`RH_QUOTE_FALLBACK=none`) further clip the quote p90/p95/max tail. These are
projections from a unit-timed sim + the exact 2-POST decomposition, NOT a live
A/B — validate by running the lane a session with the flags set and reading the
new `lat_quote_buy_s` / `lat_quote_rt_s` / `quote_mode` stamps.

Recommended A/B to hand AxiS (paper, no live):
```
RH_RT_COMBINED=1  RH_QUOTE_TIMEOUT_S=2.5  RH_QUOTE_FALLBACK=none
```
vs a control session with the defaults. Grade on median + p90 lat_total_s and
confirm rt-cost-gate selection is materially unchanged (fill count / rt_cost
block rate) before trusting the estimate.

## 4. The biggest lever needs infra (for AxiS)

The ~0.5s/POST is the **public RH RPC's server-side `eth_call` latency** — the
hard floor under everything above. Even a perfect single POST is bounded by it.

- **A closer / paid / dedicated RH-chain RPC** would cut BOTH the median and the
  tail proportionally. If a private endpoint served QuoterV2 `eth_call` in ~150ms
  (typical for a dedicated node), the single-POST quote leg drops to ~0.15s and
  lat_total median → ~0.8s — deep under parity with headroom for the trigger tail.
- No code change needed to try one: `RhExecutor` already honors `RH_RPC_URL`.
  Point it at a faster endpoint and re-verify chain_id 4663 (the executor's
  `connect()` fails closed on mismatch). NOTE: this env also feeds the
  live-execution path — for a quotes-only A/B without touching the verified live
  RPC, a follow-up could add an `RH_QUOTE_RPC_URL` override scoped to the paper
  quote executor (not built here; flagged).
- Expected gain from a ~150ms-RTT node: quote median 1.06s → ~0.15–0.30s; the
  single biggest cut available, larger than any code lever.

## 5. Files changed + env flags

Changed (working tree only, no deploy):
- `core/rh_execution.py` — `_quote_timeout_s()`, `build_roundtrip_quote_batch()`,
  `parse_roundtrip_quote_batch()`, `RhExecutor.quote_roundtrip_batched()`;
  `_quote_all_tiers_batched` honors the timeout env; `_best_quote` honors
  `RH_QUOTE_FALLBACK`.
- `scripts/rh_paper_lane.py` — `_rt_combined()`, `_est_token_out()`; `_paper_buy`
  refactored to the two-leg-timed / opt-in-combined quote path; new ledger stamps
  `lat_quote_buy_s`, `lat_quote_rt_s`, `quote_mode`.
- `tests/test_rh_quote_latency.py` — new; 16 tests (timeout env, fallback env,
  roundtrip batch build/parse/executor, lane flag). All pass.

Env flags (all default = current behavior):
- `RH_QUOTE_TIMEOUT_S` (default `10.0`) — batched quote POST timeout, secs.
- `RH_QUOTE_FALLBACK` (default `seq`; `none` = fast-fail, skip sequential sweep).
- `RH_RT_COMBINED` (default `0`/off; `1` = single-POST round trip).

Tests: `tests/test_rh_quote_latency.py` 16/16 pass. Full RH suite green EXCEPT
`test_rh_paper_fleet.py::TestRoster::test_roster_racers_unique_ids` — a
**pre-existing** stale assertion (`len(ROSTER)==25` vs the current 29 racers;
fails identically on the clean tree, verified via `git stash`). NOT introduced by
this change (no racers were touched).

## 6. Constraints honored
- Live-execution triple gate (`RH_LIVE_CONFIRMED` / `RH_PAPER_MODE` /
  `RH_PRIVATE_KEY`) untouched; no live enabled; nothing signed.
- Every quote failure returns `None` → no fill (fail-safe; a bad/late quote never
  fires a trade). The combined path never books the estimated sell as the fill.
- No deploy / push. Working tree only.
