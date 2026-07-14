# RH Rug Gate — ARM-TIME PREWARM + ENFORCE (2026-07-13 / 2026-07-14 UTC)

AxiS mandate: *"a dead token dropping 50% vs a winner should be obvious enough to
enforce immediately."* The obvious detector for the concentrated-DUMP rug class
(CASHCATWIF −100%, CASHCATGAME −98%) already existed and graded clean
(0/22 winner-kill), but was SHADOW because reading holder concentration at entry
cost up to 90s (eth_getLogs replay) — unusable inside the RH ~1s detect→fill
budget. This ships the **arm-time Blockscout PREWARM** so the read is free, then
**ENFORCES** the gate. Paper (RH lane) only — no deploy/push (AxiS deploys).

Working tree: `core/rh_rug_signals.py`, `scripts/rh_paper_lane.py`,
`tests/test_rh_paper_lane.py`, `tests/test_rh_paper_fleet.py`. Verify helper:
`scratchpad/_rh_rug_enforce_verify.py`.

---

## 1. The gate (unchanged math, now enforced)

`core.rh_rug_signals.rug_gate_verdict(stamp)` — PURE. Blocks when
**`top1_pct >= 9` OR `top10_pct >= 30`**. Prefers Blockscout `bs_top1_pct/
bs_top10_pct` (the prewarm source), falls back to the eth_getLogs recon
`top1/top10`. FAIL-OPEN: neither present → `block=False, source="none"`.

Thresholds env-tunable (`RH_RUG_GATE_TOP1`=9, `RH_RUG_GATE_TOP10`=30).

---

## 2. ARM-TIME PREWARM design (off the hot path, 0 added latency)

**Where it arms:** `PaperLane._quote_hot()` — the loop that quotes the live
watch candidates (pools recently traded, in `feed.watch`, not held). This is the
real "arm" point: a pool being quoted for entry. On the entry-candidate branch
(after `_token_for`, before the buy quote) the lane calls `_prewarm_rug(pool,
token, now)`. Held pools are skipped (already bought). It only touches the small
hot-candidate set — **never the 46k discovered pools**.

**What it does:** spawns a daemon thread that calls
`core.rh_blockscout.blockscout_stamp(token, pool_addr=pool)` (2 HTTP calls,
~1-6s cold, **0 on cache hit**, 10-min internal TTL) and stores the result in
`self._bs_prewarm[token] = (fetched_ts, bs_stamp)`. This also warms
blockscout's own `_CACHE`, so the post-fill SHADOW stamper (`compute_entry_stamp`
→ `_blockscout_merge`) reuses it for free and its `rug_signals` rows finally
carry `bs_*` fields.

**Discipline (respects the RH quote + Blockscout budgets):**
- Deduped by a per-token in-flight set + a fresh-cache check (`_prewarm_fresh`)
  → one fetch per token per TTL, never a duplicate thread.
- Concurrency-bounded: `MAX_BS_PREWARM_INFLIGHT=4`.
- Two TTLs: good stamp 600s (= Blockscout cache); empty/failed 45s so a
  transient blip re-arms instead of failing-open for the full 10 min.
- Kill switch `RH_RUG_PREWARM=0`; no-op when `RH_RUG_GATE=off`.
- Fail-open, never raises: `blockscout_stamp` returns a null stamp on any error;
  a spawn failure discards the in-flight marker and prints.

**The entry-decision read is a pure dict lookup.** `_consider_entries` calls
`_rug_gate_lookup(token)` → `rug_gate_verdict(self._bs_prewarm_read(token))`.
`_bs_prewarm_read` is a lock-guarded `dict.get` + TTL check — **no network on
the detect→fill path, ever.**

---

## 3. ENFORCE wiring

In `_consider_entries`, after the per-config gates build the `entering` list and
before the honeypot network call:

```
rug_v = self._rug_gate_lookup(token)                 # 0-latency dict read
if rug_v and rug_v["rug_gate_block"] and rug_gate_enforcing():
    <record "rug_gate" in each entering config's block_hist>
    <emit one {"ev":"rug_gate_block"} ledger row per pool>
    continue                                         # skip the WHOLE pool
```

Concentration is a per-TOKEN property, so a block skips every entering config for
that pool (mirrors the honeypot whole-pool skip) and saves the honeypot call too.

**Ledger stamping (for grading):**
- Filled entries: `_paper_buy` stamps `rec["rug_gate"] = {block, reason, source,
  top1, top10, mode}` on the buy row (present whenever the gate isn't `off`).
  In enforce mode a booked fill always carries `block=False` (a blocking verdict
  never reaches `_paper_buy`); in shadow it may carry `block=True` — the
  would-block the grader scores.
- Blocked entries: one `{"ev":"rug_gate_block", pool, token, sym, bot_ids,
  rug_gate_*}` row per pool (deduped via `_rug_blocked_pools`, since the pool
  re-arms every ~2s).

**Env flag (reversible):** `RH_RUG_GATE = enforce | shadow | off`, **default
`enforce`** (AxiS ship decision). `block` accepted as a legacy alias for
`enforce`. `shadow` = stamp only, never skip. `off` = no `rug_gate_*` keys,
buy rows byte-identical to pre-change, and the prewarm is a no-op. Same vocab as
the Solana `RUG_GATE_MODE` gate in `core/bot_evaluator.py`.

---

## 4. VERIFICATION — 0 winner-kill (bar to ship)

`scratchpad/_rh_rug_enforce_verify.py` runs the **shipped** `rug_gate_verdict`
(not a re-implemented predicate) over the combined labeled at-entry set (ledger
stamps + retro reconstructions, `_rh_rug_port.md` RQ3) and the accrued ledger.

```
=== SHIPPED rug_gate_verdict on combined labeled set (3 RUG / 23 WIN / 4 LOSS) ===
  CATCH (rugs blocked):       2/3   ['CASHCATWIF', 'CASHCATGAME']
  WINNER-KILL (wins blocked): 0/23  []
  LOSS-HIT (losses blocked):  0/4   []
  PONS (top1 2.49 / top10 19.67) -> PASS  (correct)
  winner-kill rate = 0.0%  (bar: <= 5%)

=== accrued ledger stamps (20 distinct tokens) forward-grade ===
  flagged: 1/20  [CASHCATWIF  concentration:top1_10.61>=9.0,top10_45.88>=30.0]
```

- **Catch-rate: 2/3** catastrophic dump-class rugs (CASHCATWIF, CASHCATGAME).
- **Winner-kill: 0/23 = 0.0%** — clears the ≤5% bar. **PONS passes** (2.49/19.67,
  as mandated). No winner exceeds top1 7.77 (BROKEBEAR) or top10 23; the rugs sit
  at 10.6/11.9 — real margin.
- **Loss-hit: 0/4.**
- Live forward-grade on the 20 accrued stamps: **1 flagged (CASHCATWIF), 0
  winners.**

→ 0-winner-kill bar met. Ship enforce.

---

## 5. Latency proof

`RH_RUG_GATE=enforce`, `blockscout_stamp` monkeypatched to raise if the hot path
ever calls it:

```
ENTRY-DECISION read: block=True source=bs
  200000 lookups in 595.6ms -> 2.978 us/lookup (pure dict, NO network)
  fail-open (no warm data): block=False source=none
  -> NO AssertionError raised => hot path never called blockscout_stamp

PREWARM arm() returned in 0.258ms (does NOT wait on the 1.5s fetch)
  background worker completed the fetch off-path; warmed verdict readable
```

- Entry decision: **~3 µs/read**, pure dict, provably no network (the hot path
  never touched `blockscout_stamp`).
- Prewarm `arm()` returns in **0.26 ms** while a simulated 1.5s Blockscout fetch
  runs on the daemon thread — the detect→fill stamp is **not** inflated.

---

## 6. HONEST SCOPE

Concentration catches the **concentrated-DUMP class only** (2/3 rugs). It does
NOT catch the **single-block LP-pull class**: Halp (−90%) reads top1 1.6 /
top10 12.1 at entry — a winner shape, indistinguishable on holders. Every
predicate that caught Halp (nhold<250, fat shoulder, float≥60, pool<25) killed
2-20 of 22 winners, so Halp is deliberately left to the fast_liq_bail / LP-custody
path (`lp_any_eoa_owner`), which is out of scope here. Halp is also already
fenced by MIN_LIQ 30k + MIN_POOL_AGE 1h (it was $17k / 7 min). QUANT (−31%) has
no captured at-entry features.

**Low-n caveat:** n=3 labeled rugs (1 ledger-stamped + 2 retro). The grade is
clean and the mechanism is sound (a whale positioned to dump an oversized stake),
but this is paper and the gate is fully reversible; forward accrual (now with
`bs_*` populated by the prewarm) continues to grade it at n≥30.

---

## 7. Tests (all green)

- `tests/test_rh_rug_signals.py` — unchanged gate math still passes; mode default
  now enforce.
- `tests/test_rh_paper_lane.py` `TestRugGatePrewarmEnforce` (+11): prewarm
  populates cache off-hot-path, dedup by in-flight + cache, no-op when gate off,
  enforce blocks + one deduped ledger row, shadow does not enforce, fail-open on
  absent/stale data, winner (PONS) passes, gate-off returns None, mode default =
  enforce + `block` alias.
- `tests/test_rh_paper_fleet.py` (+2): end-to-end `_consider_entries` — enforce
  blocks the whole pool (no entries, no quote spent, `rug_gate` in block_hist,
  one `rug_gate_block` row); shadow allows entry with the verdict stamped.
- **Full suite: 2995 passed, 2 skipped** (incl. `test_rh_rug_signals`, fleet,
  `test_pre_live_invariants`, `test_rh_pre_live_invariants`).

## 8. Exact env flags

| flag | default | effect |
|---|---|---|
| `RH_RUG_GATE` | `enforce` | `enforce` blocks entries · `shadow` stamps only · `off` disables + byte-identical rows |
| `RH_RUG_PREWARM` | `1` | `0` disables the arm-time Blockscout prewarm |
| `RH_RUG_GATE_TOP1` | `9` | top1 concentration threshold (%) |
| `RH_RUG_GATE_TOP10` | `30` | top10 concentration threshold (%) |

Reverse with **`RH_RUG_GATE=shadow`** (no deploy needed once the env is set).
