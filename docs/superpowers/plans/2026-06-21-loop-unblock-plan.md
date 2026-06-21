# Loop-Unblock Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound the event-loop block caused by the synchronous `feature_compute` span in `_evaluate_pair` to < ~2s, so the ~3s fast-watch tick (and real-time dip detection) is never starved.

**Architecture:** GIL-correct. Process-pool offload was proven infeasible (the span is entangled with `self`/shared state/an await; its pure pieces operate on already-capped inputs — `recent_trades`≤30, candles 100/144/96/48 — too small for IPC to help). So: (1) finer sub-instrumentation to localize the real per-token scaler, (2) cooperative mid-span `await asyncio.sleep(0)` yields at the span's natural seams — this bounds loop-block regardless of which sub-step is slow, (3) targeted reduce-work where the instrumentation points.

**Tech Stack:** Python 3, asyncio, pytest. No new deps. Runtime: Railway.

## Global Constraints

- **Free tools only** — no paid RPC/Jupiter key.
- **No `PAPER_MODE` flip** — this whole build is validated in paper/shadow. Live enforce stays gated on a later explicit AxiS go.
- **Default-on behavior must be safe**: yields are inserted BEFORE the buy-fire decision (same rationale as the existing end-of-span yield), so they cannot split the `_buy_fire_lock` / cause a double-buy. Confirm each yield site precedes buy execution.
- **Validation is runtime, not unit** — these are perf changes to a 19k-line async method with no clean behavioral unit (mirrors the prior Component D). The regression guard is: existing suite green + `python -c "import feeds.dip_scanner"` + a paper soak measuring `[loop-lag]`. State this in each task; do not fabricate a behavioral unit test that asserts nothing.
- **Pure-Python CPU under `to_thread` does NOT free the loop (GIL).** Do not propose `to_thread` for `feature_compute`. Only reduce-work + cooperative yield are valid.
- Scope: `feeds/dip_scanner.py` only (plus an optional tiny test). Do NOT touch `_chameleon_audit.py`.

## Key code anchors (verified; confirm exact lines before editing — the file shifts)
- `feature_compute` span START: `feeds/dip_scanner.py:7196` (`_feat_t0 = time.monotonic()`, comment "the big pure-Python feature + filter-chain region").
- span END / accumulate: `:13517` (`_subop_totals["feature_compute"] += ...`).
- existing end-of-span yield: `:13528-13540`, gated by `EVAL_PAIR_YIELD` (default on) — the pattern to mirror.
- `_SubOp(name)` context manager: accumulates elapsed seconds into the per-call `_subop_totals` dict (e.g. `with _SubOp("btc_klines"):` at `:5461`). The `[phase-timing] subop-breakdown` line prints these.
- Major seams inside the span: tier2 features `:7648-7695`, tier3 features `:7747-7768`, fusion ML score `:7790-7849`, `dev_wallet_rpc` await `:7967`, the ~200-block trigger/filter chain `:~8000-13490`.

---

### Task 1: Finer sub-instrumentation of the feature_compute span

**Goal:** Split the opaque `feature_compute` residual into labeled sub-buckets so the next soak shows WHICH block costs the 3.8–10.6s on high-activity tokens (GIGA/Cambria). Observability only — no behavior change.

**Files:**
- Modify: `feeds/dip_scanner.py` (wrap blocks in `_SubOp(...)` inside the span)
- Test: `tests/test_loop_unblock.py` (create)

**Interfaces:**
- Produces: new `[phase-timing]` subop keys: `feat_tier2`, `feat_tier3`, `feat_fusion`, `feat_triggers_a`, `feat_triggers_b`, `feat_triggers_c`. `feature_compute` residual shrinks to the truly-untracked remainder.

- [ ] **Step 1: Wrap the tier2/tier3/fusion blocks**

Wrap each existing block in a `_SubOp`, mirroring the existing `with _SubOp("btc_klines"):` style. Around the tier2 builders (`:7648-7695`):
```python
                with _SubOp("feat_tier2"):
                    # ... existing tier2 compute_* calls unchanged ...
```
Same for tier3 (`:7747-7768`) → `_SubOp("feat_tier3")`, and the fusion score (`:7790-7849`) → `_SubOp("feat_fusion")`. **Do not change the logic inside — only wrap.** Preserve any `await` already inside a block (the `_SubOp` accumulates wall-time, which is acceptable for localization).

- [ ] **Step 2: Bracket the trigger chain into thirds**

The trigger chain (`:~8000-13490`) has no single function to wrap. Add three lightweight monotonic checkpoints that accumulate into `_subop_totals` directly (not `_SubOp`, to avoid wrapping 5000 lines). At ~1/3 and ~2/3 boundaries (pick clean seams between trigger blocks — confirm by reading), capture `time.monotonic()` deltas:
```python
        _trg_t0 = time.monotonic()   # just after fusion block (~:7850)
        # ... first third of trigger chain ...
        _subop_totals["feat_triggers_a"] = _subop_totals.get("feat_triggers_a", 0.0) + (time.monotonic() - _trg_t0)
        _trg_t1 = time.monotonic()
        # ... second third ...
        _subop_totals["feat_triggers_b"] = _subop_totals.get("feat_triggers_b", 0.0) + (time.monotonic() - _trg_t1)
        _trg_t2 = time.monotonic()
        # ... final third ...
        _subop_totals["feat_triggers_c"] = _subop_totals.get("feat_triggers_c", 0.0) + (time.monotonic() - _trg_t2)
```
Place the boundary lines at real seams between trigger `try/except` blocks so no block is split. Document in your report the exact line of each boundary.

- [ ] **Step 3: Regression guard test**

In `tests/test_loop_unblock.py`:
```python
def test_dip_scanner_imports():
    import feeds.dip_scanner  # noqa: F401

def test_subop_keys_documented():
    # Guard: the new instrumentation keys are referenced in the module source
    # (localization buckets for the loop-unblock soak). Source-text check —
    # there is no behavioral unit for inline instrumentation (runtime-validated).
    import inspect, feeds.dip_scanner as ds
    src = inspect.getsource(ds)
    for k in ("feat_tier2", "feat_tier3", "feat_fusion",
              "feat_triggers_a", "feat_triggers_b", "feat_triggers_c"):
        assert k in src, k
```

- [ ] **Step 4: Run + import-smoke**

Run: `pytest tests/test_loop_unblock.py -v` → PASS. Then `python -c "import feeds.dip_scanner"` → no error. Then full suite sanity: `pytest tests/test_realtime_dip_detection.py -q` → still green.

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py tests/test_loop_unblock.py
git commit -m "perf(loop): finer feature_compute sub-instrumentation (tier2/tier3/fusion/trigger-thirds)"
```

---

### Task 2: Cooperative mid-span yields

**Goal:** Insert `await asyncio.sleep(0)` at the span's natural seams so the loop breathes mid-compute — bounding max loop-block to the work between yields, regardless of which sub-step is slow. This is the primary fix.

**Files:**
- Modify: `feeds/dip_scanner.py`
- Test: `tests/test_loop_unblock.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: env flag `EVAL_PAIR_MID_YIELD` (default `"on"`; `"off"` disables the new mid-span yields for A/B without code change).

- [ ] **Step 1: Add a gated yield helper call at the seams**

Mirror the existing end-of-span yield (`:13528-13540`). Add `await asyncio.sleep(0)` at these seams, each guarded by the flag, placed AFTER the labeled block and BEFORE any buy-fire decision:
- after `feat_tier2` (~:7695)
- after `feat_fusion` (~:7849)
- after `feat_triggers_a` boundary
- after `feat_triggers_b` boundary

Pattern (read the existing `EVAL_PAIR_YIELD` block and match its env-read style):
```python
                if os.environ.get("EVAL_PAIR_MID_YIELD", "on").strip().lower() not in ("off", "0", "false"):
                    await asyncio.sleep(0)
```
(DRY: if the existing end-of-span yield already reads an env flag via a cached helper, reuse that helper with the new flag name instead of re-reading os.environ each time.)

- [ ] **Step 2: Confirm yield sites precede buy execution**

Read the code between the last new yield and the buy-fire (`_buy_fire_lock` acquisition / `_execute_bot_buy*`). Confirm every new yield is BEFORE the buy decision for this pair — so a yield cannot interleave inside a buy. Document the buy-fire line and that all yields precede it in your report. If any seam is AFTER a buy-fire, do NOT place a yield there.

- [ ] **Step 3: Regression guard**

Add to `tests/test_loop_unblock.py`:
```python
def test_mid_yield_flag_referenced():
    import inspect, feeds.dip_scanner as ds
    assert "EVAL_PAIR_MID_YIELD" in inspect.getsource(ds)
```
Run: `pytest tests/test_loop_unblock.py -v` → PASS. `python -c "import feeds.dip_scanner"` → OK. `pytest tests/test_realtime_dip_detection.py -q` → green.

- [ ] **Step 4: Commit**

```bash
git add feeds/dip_scanner.py tests/test_loop_unblock.py
git commit -m "perf(loop): cooperative mid-span yields in feature_compute (EVAL_PAIR_MID_YIELD, default on)"
```

---

### Task 3: Deploy, soak, and targeted reduce-work (gated, data-driven)

**Goal:** Validate the loop-block dropped < ~2s; if a specific sub-bucket still dominates, apply targeted reduce-work. NO blind reduce-work — driven by Task 1's instrumentation.

**Files:** `feeds/dip_scanner.py` (only if the data points to a specific fix).

- [ ] **Step 1: Full suite + import green**

`pytest tests/test_loop_unblock.py tests/test_realtime_dip_detection.py -q` → all pass. `python -c "import feeds.dip_scanner"` → OK.

- [ ] **Step 2: Deploy (paper) and soak**

This is operational (controller-run, with the existing shadow soak already live): merge to master + `railway up` (PAPER_MODE stays true; RT_*_MODE stays shadow). Then read `railway logs | grep -E "loop-lag|phase-timing"`:
- Confirm `[loop-lag]` max drops to < ~2s (was ~10–16s).
- Read the new `[phase-timing]` buckets (`feat_tier2/tier3/fusion/triggers_a/b/c`) on high-activity tokens (GIGA/Cambria/ANSEM) to see which dominates.

- [ ] **Step 3: Targeted reduce-work IF still > ~2s**

Based on Step 2's breakdown only:
- If a `feat_triggers_*` third dominates → add another mid-span yield inside that third, OR (the agent's top suspect) suppress/sample the per-match `logger.info` reason-string building on tokens matching many triggers.
- If `feat_tier2` dominates → trim the largest candle limit feeding RSI/VWAP (5m 144→~60; safe only if downstream windows fit — `compute_rsi_bb` needs ≥14, anchored VWAP uses the 1h window) behind a flag.
- If `feat_fusion` dominates → cache/skip the fusion score in the fast path.
Each reduce-work change: TDD where a pure unit exists; otherwise import-smoke + re-soak. Commit per change with a `perf(loop):` message.

- [ ] **Step 4: Re-soak to confirm < ~2s, then report**

Report the before/after loop-lag and the bucket breakdown. Once loop-block < ~2s sustained, the Component D prerequisite is met → the real-time detection (already shipped) can proceed to its gated capped-live A/B (separate, AxiS-gated).

---

## Self-Review

**Coverage:** instrument (Task 1) → yield (Task 2, primary) → validate + targeted reduce-work (Task 3). Matches the confirmed approach (process-pool dropped). ✓
**Constraints:** GIL-correct (no to_thread for CPU); yields precede buy-fire (no double-buy); paper-only, no PAPER_MODE flip; free tools. ✓
**Honesty:** TDD is weak for inline perf changes — the plan says so and uses source-text guards + runtime soak as validation (same as the prior Component D), rather than pretending a behavioral unit exists. The real validation is the `[loop-lag]` soak in Task 3.
**Type consistency:** subop keys (`feat_tier2/tier3/fusion/triggers_a/b/c`) and the flag `EVAL_PAIR_MID_YIELD` are used consistently across tasks.
