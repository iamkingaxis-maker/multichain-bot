# BLACKOUT Live-Buy Incident — Root-Cause Analysis
**Incident:** 2026-07-05 18:54:09–12 UTC — `badday_young_absorb_live` live-bought $25 of BLACKOUT (sig `3dofK…y8bCG`, −0.3101 SOL) seconds after a `STRUCTURE-EDGE BLOCK` (ENFORCE) line for the same token, followed by a `LIVE buy aborted pre-spend; capital refunded` line.
**Status of code:** read-only investigation; no production files edited. All fixes below are proposals.

---

## TL;DR

* **Bug A (the gate bypass): `structure_edge` is STICKY-ROLLED-BACK.** `/data/gate_rollback.json` (Railway volume) carries `structure_edge.rolled_back=true` — the auto-rollback watcher (core/gate_rollback.py) permanently demoted the gate to shadow at some earlier date. The gate code **still prints the word "BLOCK"** in enforce mode but **skips the `return`** when rolled back (feeds/dip_scanner.py:2067-2078). Nothing about the fast path, `tier=alpha_trigger`, or pc_h6 data divergence bypassed the gate — the gate has not been enforcing for **any** buy. Corroboration: **1,074 of 1,589** recent buys (Jul 1–5) have `entry_meta` `pc_h6<0 AND liq<$48k` — including 12+ today after the go-live deploy — while `STRUCTURE_EDGE_MODE=enforce` is set in the Railway env (verified via `railway variables`). The BLACKOUT live buy's own entry_meta reads `pc_h6=-54.02, liquidity_usd=25348.37` — exactly the blocking inputs from the quoted BLOCK line — and it bought anyway, as did its two paper twins in the same second.
* **Bug B (the "refund after swap"): the refund did NOT undo the executed swap.** `_execute_bot_buy_live` has **no** code path that returns `None` after a successful swap — every post-success failure returns `{'spent': True, …}` or the full result dict (feeds/dip_scanner.py:3866-3939). The `aborted pre-spend` line at the tail of the sequence came from a **second, later `_execute_bot_buy` invocation** for the same bot (double-eval race; fast-tick drain vs arm-instant-fire) that reserved fresh capital and then hit the `pm.get_position(token) is not None` pre-check (dip_scanner.py:3597-3599 — "already open") → returned `None` **before spending anything** → correctly refunded **its own** reservation. The ledger for the real buy is intact (reservation held in `in_flight`, position persisted, survived restart). The log line is misleading because line 3087 does not name the token or the abort reason. A **real** latent hole in the same method exists on the *failure* branch (see B-3) but did not fire here.

---

## Bug A — full mechanism and evidence

### The code path
All entry gates for every bot live inside **one choke point**: `_execute_bot_buy` (feeds/dip_scanner.py:1545-3475). It is called from exactly one place — `_fast_route_decisions` (dip_scanner.py:6613) under `_buy_fire_lock` — which is used by *both* the main-scan fan-out (dip_scanner.py:23388, only when `MAIN_SCAN_BUY_MODE=on`) and the fast-watch path (`_fast_eval_one` → `_evaluate_pair` → fan-out). Railway env: `MAIN_SCAN_BUY_MODE=arm_only`, `FAST_WATCH_MODE=enforce` → **every** buy today came through the fast path, and **every** buy runs the identical gate sequence. There is **no path-dependent gate skip** (hypothesis (a) refuted as the primary cause).

The structure-edge gate (dip_scanner.py:2017-2078):

```python
if _se_block:
    logger.info("[DipScanner] bot=%s STRUCTURE-EDGE %s: %s %s", bot_id,
                "BLOCK" if _se_mode == "enforce" else "SHADOW-would-block", ...)  # 2068-2070
    _se_rb = _irb2("structure_edge")     # core.gate_rollback.is_rolled_back  # 2071-2076
    if _se_mode == "enforce" and not _se_rb:
        return                                                                # 2077-2078
```

When `is_rolled_back("structure_edge")` is True, the log **says "BLOCK"** (mode is enforce) but the `return` is skipped and execution falls through to every downstream gate — which is precisely the observed log shape: the BLOCK line immediately followed by *liq-exit-floor / not-dipping / full-thesis / dev-not-dumped / oversold-held* SHADOW lines **for the same bot+token**. All five of those gates sit *after* structure-edge's `return` (liq-exit-floor starts at 2089, not_dipping at 2317, full-thesis at 2412 …). If the enforce `return` had executed, none of those lines could have printed for that invocation. The sequence itself is a fingerprint of the rollback latch.

### The rollback latch (core/gate_rollback.py)
* `structure_edge` is in `MONITORED_GATES` (gate_rollback.py:34-36).
* `run_gate_rollback_check` (112-131) is invoked after every forward-candle scoring pass (core/shadow_pnl_scorer.py:218-222). Rollback is **STICKY** by design (13-16): once written to `{DATA_DIR}/gate_rollback.json` it stays until a human deletes it. It alarms **once** (first WARN at 129); afterwards it only returns "already rolled back (sticky)" into a list nobody reads.
* `is_rolled_back` fails **False** on IO error (84-90) — so a latched True cannot be a glitch; the file genuinely contains the flag.

### Why the latch is likely spurious — the wr-mismatch bug
`evaluate_gate_rollback` (gate_rollback.py:43-72) treats `stats["wr"]` as *"the blocked cohort's forward win-rate — for a pure-BLOCK gate wr==block WR"*. But `compute_filter_pnl` (scripts/audit_filter_shadow_log.py:239) computes `wr = 100*wins/n` over **all scored records — PASS and BLOCK mixed**. `structure_edge` records **every PASS** (`STRUCTURE_EDGE_PASS_SAMPLE=1`, set in the Railway env; dip_scanner.py:2025-2064), so its `wr` is dominated by the PASS cohort. Trigger condition `wr>=50 AND block_avg>0 AND block_n>=20` can therefore latch a **permanent** rollback off healthy PASS winners plus a marginally-positive blocked sample. (Current snapshot: n=20, wr=45, block_n=7, block_avg=−3.48 — would not trip today; the latch predates this window. The file's `ts`/`stats` fields will date the actual trip.)

### Evidence chain (all verified this session)
1. Railway env: `STRUCTURE_EDGE_MODE=enforce` (via `railway variables --kv`). The quoted log word "BLOCK" independently proves mode==enforce at 18:54.
2. The live BLACKOUT buy's `entry_meta` (from `/api/trades?full=1`): `pc_h6=-54.024799`, `liquidity_usd=25348.37` — bit-identical to the BLOCK line's `pc_h6=-54%<0 AND liq=$25348<$48000`. The buyer's own decision inputs were blocking inputs. (`pc_h1==pc_h6` exactly — the RT_DIP enforce overwrite computing both horizons off the same window high; peak was 51 min ago, inside both windows. Data was *fine*.)
3. **1,074 / 1,589** buys since Jul 1 violate `pc_h6<0 AND liq<48k`, continuously through today (14:11, 16:29, 16:57, 17:33, 17:38, 18:19, 18:32, 18:54, 18:56, 19:02 UTC…) — systemic non-enforcement, not a one-token race.
4. Three bots (`badday_adolescent_absorb` 18:54:10.65, `badday_young_absorb` 18:54:11.02, `badday_young_absorb_live` 18:54:12.94) bought BLACKOUT in the same fan-out — all "passed" the same rolled-back gate.
5. `structure_edge_blocks` itself is pure and correct (core/bot_evaluator.py:380-406); with these inputs it returns True. The only code that can swallow a True verdict in enforce mode is the `not _se_rb` clause.

### Hypothesis dispositions
* (a) different-pass data divergence: **refuted** as the cause (buyer's own meta carried blocking values). Note the real divergence hazards found while checking — see "Adjacent latent defects".
* (b) `tier=alpha_trigger`: **confirmed sizing-only** (dip_scanner.py:22961-22981; `deep_1h_dip` ⇒ 1.5x tier label). Not a route. Actual route: fast-watch → `_fast_route_decisions` → `_execute_bot_buy` → `_execute_bot_buy_live`.
* (c) per-bot scoping / young-lane exemption: structure-edge is **fleet-global** (no bot_id scoping at 2017-2078, unlike liq-exit-floor's `startswith("badday_")` at 2090). The BLOCK line's `bot=badday_young_absorb_live` attribution is genuine — the same bot logged BLOCK and then bought, in one invocation.

### Which enforce-capable gates can be bypassed this way
All seven `MONITORED_GATES` share the identical `…and not _rb: return` pattern with the identical "log-says-BLOCK-while-not-blocking" flaw:
`falling_day_flush`, `solpump_neg_gate`, `structure_edge` (2071-2078), `liquidity_exit_floor`, `consec_red_knife`, `not_dipping` (2366-2373), `pump_retrace_gate` (2000-2007). Any of them may also be latched in `/data/gate_rollback.json` — **read the whole file**, not just the structure_edge entry. Gates *without* the rollback guard (anti-rug floor, regime buy-gate, young-holder-guard, chameleon standby, exclusion pool, risk floors, live per-token caps) cannot be bypassed by this mechanism. Both buy paths (main-scan fire and fast-watch fire) run the same `_execute_bot_buy` body, so the table is path-invariant.

### Fixes (precise)
1. **Truthful logging** — feeds/dip_scanner.py:2068-2070 (and the six sibling gates): resolve `_se_rb` *before* the log line and print a third label, e.g.
   `"BLOCK" if (_se_mode=="enforce" and not _se_rb) else ("ROLLED-BACK-would-block" if _se_mode=="enforce" else "SHADOW-would-block")`.
   An enforce gate must never print "BLOCK" for a buy it lets through.
2. **Fix the trip metric** — scripts/audit_filter_shadow_log.py:~234-243: emit `block_wr = 100*block_wins/len(block_rs)` alongside `block_avg`; core/gate_rollback.py:61-67: consume `stats.get("block_wr")` (fall back to `wr` only for gates with `pass_n==0`). The docstring's "pure-BLOCK gate" assumption died when PASS recording was added to structure_edge/not_dipping/pump_retrace.
3. **Loud sticky-state** — core/gate_rollback.py:120-122: WARN (not just list-append) on every check while a gate stays rolled back, and surface `read_rollback_state()` in the `/api/filter-shadow` payload (dashboard/web_dashboard.py:read_filter_shadow_payload, ~1829-1872) so it is visible without ssh.
4. **Ops (AxiS action)**: `railway ssh -- cat /data/gate_rollback.json` → record `ts`/`reason`/`stats` for the structure_edge entry (dates the exposure window); delete the entry **only after** fixes 1–2 are deployed, else the wr bug can immediately re-latch it.

---

## Bug B — the "abort-after-spend" that wasn't

### What the code allows
`_execute_bot_buy_live` (dip_scanner.py:3566-3939) return paths:
* `None` — **only before** any spend: already-open (3597-3599), max_concurrent (3600-3603), bad mid (3604-3606), BUY-REPRICE abort (3641-3647), pre-swap read errors (3676-3701), bad lamports (3707-3714), SOL reserve (3716-3718), and swap-reported-FAIL with no adoption (3861-3864).
* After `res.success` is true, **every** path returns a dict: decimals failure → `{'spent': True}` (3866-3873), zero out-tokens → `{'spent': True}` (3874-3878), `open_position` ValueError → `{'spent': True}` (3890-3898), success → full dict (3938-3939). The telemetry emitter never raises (3781-3842, blanket except). **`None`-after-success is unreachable.** An uncaught exception after the swap would propagate (no refund log would print) and be caught at `_fast_eval_one`:7911 as "eval failed".

### What actually happened
The incident's own ordering proves two invocations: the refund log (3087) is followed by `return` (3088), so it can never print *after* the `[DipScanner] BUY` completion line (3466-3469) **within one invocation**. Sequence:
1. Invocation 1 (the buyer, 18:54:~11-12.9): gates "passed" (rollback), reserved $25 (3068), swap ok, `[Probe] LIVE BUY` (3927), position opened + persisted, `[DipScanner] BUY … tier=alpha_trigger` (3466). Its $25 stays in `in_flight_usd` and settles at close (5580) — **never refunded**.
2. Invocation 2 (seconds later; the double-eval race the code itself documents as "double-buy guard #2/#3", dip_scanner.py:7967-7969): same bot+token re-decided, reserved a **new** $25, entered `_execute_bot_buy_live`, hit the already-open pre-check (3597) → `None` → caller refunded **that** $25 (3083-3088). Net ledger effect: zero. Prediction for verification: the full log contains `[Probe] live buy skipped: BLACKOUT already open` immediately before the `aborted pre-spend` line.

So the wallet-truth −0.3101 SOL is fully accounted: one swap, one tracked position, one held reservation. No mis-ledger occurred.

### The real latent hole (fix it anyway)
dip_scanner.py:3844-3864: when Ultra **reports failure** but the tx actually landed (timed-out execute), the M7 adoption re-check rescues it **only if** the pre-swap atomic balance read succeeded (`_pre_bal >= 0`, 3848). If that read failed (`_pre_bal == -1`, 3685/3704-3705) — or the 2s `PROBE_ADOPT_WAIT_S` window is too short, or the adoption re-read itself errors (3854-3855) — a **landed** swap returns `None` at 3864 → the caller refunds → *money spent + capital credited back + no position*. That is the genuine "swap-may-have-landed ⇒ never-refund" violation.

**Fix (precise):** at dip_scanner.py:3861-3864, before returning `None`, distinguish *confirmed-not-landed* from *unknown*:
```python
else:
    _maybe_landed = bool(res.get("signature")) and (_pre_bal < 0)
    if _maybe_landed:
        logger.error("[Probe] live BUY UNCONFIRMED (sig=%s, adoption blind) — treating as SPENT, manual reconcile", res.get("signature"))
        _emit_buy_telemetry(False, None, None, None, "unconfirmed_execute_adoption_blind")
        return {"spent": True, "signature": res.get("signature"), "reason": "unconfirmed_execute_adoption_blind"}
    ... existing FAIL path ...
```
(Stronger variant: when a signature exists, poll `getSignatureStatuses` once before deciding; only a *confirmed-failed/expired* tx may return `None`.) Invariant restated: **the refund branch (3083-3088) must be reachable only when it is provable no transaction was broadcast or the broadcast tx is confirmed dead.**

**Log-clarity fix:** dip_scanner.py:3087 — include the token and abort reason: have every pre-spend `return None` instead return `{"aborted": "<reason>"}` (or log `decision.token` + a reason threaded via an out-param) so the line reads `LIVE buy aborted pre-spend (already_open) token=BLACKOUT`. This single change would have prevented the incident misdiagnosis.

### Blast radius (June live era)
Checked all 280 `/api/live-swaps` records: 125 buys, **4 failed buys — all `tx_signature=null`, error "Insufficient funds"** (never signed/broadcast → refunds correct), **0 adopted orphans**, no success-record lacking a matching position/trade. No local log capture contains any `aborted pre-spend` adjacent to a swap-ok. **No historical mis-ledgered buy found.** The 3861-3864 hole has never fired; it remains a live-money time bomb for the timeout+blind-adoption coincidence.

---

## Adjacent latent defects found during the trace (not causal here)
1. **Zero-coercion of missing h6** — dip_scanner.py:8363 `pc_h6 = (pair.get("priceChange") or {}).get("h6", 0) or 0`: a priceChange-less injected pair (the documented fast-watch case, see the 23182-23194 fix comment) makes the round-2 fallback chain deliver `bundle.pc_h6 = 0.0` — which **passes** structure_edge/pump_retrace/terminal_collapse as if it were real data, defeating their None-fail-open semantics. Fix: make the iteration-top local `None`-preserving (`.get("h6")` without the `, 0) or 0`) or have the bundle fallback treat `0.0`-from-default as None. (Same trap class as the "windowed metrics are $0 under token age" rule.)
2. **`sol_spent` telemetry always 0.0** — the A1 cached `sol_before` (3760-3766) equals the post-swap forced `sol_after` in every recent record (`sol_before==sol_after`, `sol_spent=0.0` incl. BLACKOUT) — the cached pre-balance is being read *after* the reserve check already refreshed `_sol_balance` post-swap, or the cache is stale-then-equal. Pure telemetry, but it blinds cost-reconciliation (wallet-truth had to catch this incident instead).

---

## The phantom BLACKOUT position — neutralization (recommendation only)

**Where it lives:** `/data/bot_state/badday_young_absorb_live.json` (Railway volume) → `"open_positions"` list, one entry: `token="BLACKOUT"`, `address="A8wXmAgVy3peeHtifXNvYUUirGdNu4ezJoDno9trpump"`, `entry_price≈0.0001019087`, `size_usd=25.0`, `state_blob.live_signature="3dofK…"` (the definitive marker), plus `entry_liquidity_usd=25348.37`. The paired BUY row also exists in the trades ledger (buys book no P&L — harmless to leave).

**Why it will book phantom P&L:** disabling the bot (`enabled=false`, commit 7ace606) only stops **new entries** (`bot_manager.evaluate_all` skips disabled at bot_manager.py:40). The scanner wiring (dip_scanner.py:665-744) builds a capital+PM for **every** registry config and restores positions; the tick loop iterates **all** PMs (5368). With `PAPER_MODE=true` the exit routes paper → a paper close (velocity-bail/floor, likely within minutes of any price print) books fictional P&L into the bot's capital/ledger. As of this writing only the buy exists — the exit may fire at any tick. A redeploy with the bot config *removed* (not just disabled) would stop the ticking (no evaluator → no PM) but leaves the record dormant on the volume to resurface on any re-add — **not** a clean fix.

**Cleanest neutralization (do BEFORE any re-enable of the probe):** via `railway ssh` (or a one-off ops script), atomically edit `/data/bot_state/badday_young_absorb_live.json`:
1. remove the BLACKOUT entry from `open_positions`;
2. `in_flight_usd -= 25.0` (→ 0.0) and `balance_usd += 25.0` (the paper ledger refund — the real $25 loss is wallet-truth's to report, not the paper book's);
3. leave `realized_pnl_total_usd`/trades untouched.
Then restart (or accept the small race that the in-memory PM still holds the position until restart — the file edit must be followed by a restart to take effect, since the scanner will re-persist its in-memory book on the next book change). If the phantom paper SELL has already booked by the time this is done: additionally subtract that sell's `pnl` from `realized_pnl_total_usd`/`daily_pnl_usd` in the same file and annotate the trades row out of analyses (its reason string will be an ordinary exit reason — grep the sell by token+bot_id+date).

---

## Tests to add (sketches — not created)

```python
# tests/test_live_buy_never_refund_after_spend.py (sketch)

async def test_swap_ok_implies_never_none(monkeypatch, scanner, decision, pm):
    """INVARIANT: once _execute_swap_ultra returns success=True,
    _execute_bot_buy_live must NEVER return None (the caller's refund branch
    must be unreachable), no matter what post-fill instrumentation does."""
    scanner.trader._execute_swap_ultra = AsyncMock(return_value={
        "success": True, "out_amount": 10**9, "signature": "SIG"})
    failures = [
        ("decimals", lambda: monkeypatch.setattr(scanner.trader, "_get_token_decimals", AsyncMock(side_effect=RuntimeError))),
        ("zero_out", lambda: scanner.trader._execute_swap_ultra.configure(return_value={"success": True, "out_amount": 0, "signature": "SIG"})),
        ("open_pos", lambda: monkeypatch.setattr(pm, "open_position", Mock(side_effect=ValueError("max_concurrent")))),
        ("sol_after", lambda: monkeypatch.setattr(scanner.trader, "_get_sol_balance", AsyncMock(side_effect=RuntimeError))),
        ("telemetry", lambda: monkeypatch.setattr("core.live_swap_log.log_live_swap", Mock(side_effect=RuntimeError))),
    ]
    for name, arm in failures:
        arm()
        r = await scanner._execute_bot_buy_live(decision, pm, 25.0)
        assert r is not None, f"{name}: None after successful swap = refund of spent money"
        assert r.get("spent") or r.get("pos"), name

async def test_reported_fail_with_signature_and_blind_adoption_is_spent(scanner, decision, pm):
    """Timed-out execute + failed pre-balance read (_pre_bal=-1): must return
    {'spent': True, reason='unconfirmed...'} — NOT None (the tx may have landed)."""
    scanner.trader._get_token_balance_atomic = AsyncMock(side_effect=RuntimeError)  # -> _pre_bal = -1
    scanner.trader._execute_swap_ultra = AsyncMock(return_value={
        "success": False, "reason": "execute timeout", "signature": "SIG"})
    r = await scanner._execute_bot_buy_live(decision, pm, 25.0)
    assert r is not None and r.get("spent")

async def test_confirmed_no_broadcast_fail_refunds(scanner, decision, pm):
    """No signature at all (order build failed) -> None IS correct (true pre-spend)."""
    scanner.trader._execute_swap_ultra = AsyncMock(return_value={
        "success": False, "reason": "Insufficient funds", "signature": None})
    assert await scanner._execute_bot_buy_live(decision, pm, 25.0) is None

# tests/test_gate_rollback_truthful.py (sketch)

async def test_rolled_back_enforce_gate_never_logs_BLOCK(scanner, caplog, tmp_rollback):
    """With STRUCTURE_EDGE_MODE=enforce and gate_rollback latched, the buy proceeds
    AND the log must NOT contain 'STRUCTURE-EDGE BLOCK' (must say ROLLED-BACK)."""
    set_rollback("structure_edge", True, "test")
    await run_buy_with(pc_h6=-54.0, liq=25_348)
    assert "STRUCTURE-EDGE BLOCK" not in caplog.text
    assert "ROLLED-BACK" in caplog.text
    assert buy_executed()

async def test_enforce_gate_blocks_reach_no_capital(scanner, capital):
    """enforce + NOT rolled back + blocking inputs: reserve_for_buy never called."""
    await run_buy_with(pc_h6=-54.0, liq=25_348)
    assert capital.reserve_calls == 0 and not buy_executed()

def test_rollback_uses_blocked_cohort_wr():
    """A gate whose PASS cohort wins 90% but blocked cohort loses must NOT roll back."""
    stats = {"block_n": 25, "wr": 72.0, "block_wr": 30.0, "block_avg": +0.4}
    should, _ = evaluate_gate_rollback(stats)
    assert not should
```

---

## Confidence
* **Bug A = sticky gate-rollback latch:** ~95%. Env mode, the buyer's own blocking entry_meta, 1,074 systemic violations, and the log-shape (post-gate shadow lines in the same invocation) admit no other in-code mechanism; the only unverified link is the file's literal content (ssh denied this session — one `cat /data/gate_rollback.json` closes it).
* **Bug A contributing cause (wr-mismatch tripped the latch):** ~70% — the defect is real and verified in code; whether it produced *this* latch is dated by the file's `stats` snapshot.
* **Bug B = benign second-invocation refund, no ledger corruption:** ~90% (code proof that None-after-success is unreachable + return-before-BUY-line ordering + intact ledger; the "already open" log line is the remaining confirmable prediction).
* **Bug B latent hole (3861-3864 blind-adoption refund):** 100% that the path exists; it has never fired (0 occurrences in 280 live-swap records).
