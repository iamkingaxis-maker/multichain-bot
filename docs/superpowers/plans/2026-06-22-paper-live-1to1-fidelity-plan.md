# Paper↔Live 1:1 Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the paper twin (`badday_flush_nf15`) a faithful predictor of the live bot (`badday_flush_nf15_live`) by simulating, in paper, every execution constraint live faces — so paper only counts trades live can actually take, at the prices/costs live actually gets.

**Architecture:** All decision logic stays shared (`_evaluate_pair`). We add ONE new module `core/paper_fidelity.py` of **pure, unit-tested helpers** (entry/exit reprice, realistic slippage+fee, reachability skips, cap simulation, skip-instrumentation), then wire thin calls into the PAPER branch of the buy/sell path in `feeds/dip_scanner.py`. Live paths are untouched (they already reprice + pay real slippage + enforce caps). Every behavior is env-gated and reversible.

**Tech Stack:** Python 3.12, pytest, the existing `core/slippage_model.py`, `OnchainWsFeed`/`_fast_price_for` resolver.

## Global Constraints
- **Live wallet is drained / live paused** — this is paper-only work; no real money moves. Do NOT touch live execution paths (`_execute_bot_buy_live`, `_execute_bot_sell_live`).
- **Capital/wallet-balance parity is OUT OF SCOPE** (AxiS 2026-06-22: "does not matter"). Do not add a paper-respects-live-SOL gate.
- Every new behavior is env-gated `off|on` (or `off|shadow|enforce`), default chosen per task, instantly reversible — pattern of existing gates.
- Pure helpers fail-OPEN (never raise into the trading path); on missing data they return the pre-existing behavior.
- Address-keyed everywhere (symbol collisions cross-poison — the SPCX lesson).
- TDD: failing test first, every task. Commit per task.
- Fresh-price source is the existing resolver `DipScanner._fast_price_for(addr, jupiter_price)` (returns `(price, source)`; on-chain when `ONCHAIN_WS_MODE=on`, else Jupiter) and `_get_current_price_for(token, address=, pair_address=)`.

---

### Task 1: `core/paper_fidelity.py` scaffold + entry-reprice helper

**Files:**
- Create: `core/paper_fidelity.py`
- Test: `tests/test_paper_fidelity.py`

**Interfaces:**
- Produces: `reprice_entry(decision_mid, fresh_price, max_runup=None) -> tuple[float|None, str]` — returns `(entry_basis, reason)`. If `fresh_price` valid: entry_basis = fresh_price (covers dip AND run-up — paper books the REACHABLE price, no asymmetric abort: paper must mirror what live FILLS, and live fills at fresh on a dip; on a run-up past `max_runup` paper returns `(None, "runup_abort")` to mirror live's enforce-abort so the trade SET matches). If `fresh_price` missing/<=0: `(decision_mid, "stale_fallback")`.
- `paper_fidelity_enabled(flag, default="off")` env reader (mirrors `core/fast_watch.py:rt_mode` style).

- [ ] **Step 1: Write failing tests**
```python
# tests/test_paper_fidelity.py
import pytest
from core.paper_fidelity import reprice_entry

def test_fresh_price_used_as_entry_on_dip():
    # fresh below decision (further dip) -> use fresh
    assert reprice_entry(0.10, 0.09)[0] == 0.09

def test_fresh_price_used_on_subthreshold_runup():
    # fresh slightly above, within max_runup -> use fresh (reachable)
    eb, why = reprice_entry(0.10, 0.104, max_runup=0.05)
    assert eb == 0.104

def test_runup_past_threshold_aborts_to_mirror_live():
    eb, why = reprice_entry(0.10, 0.20, max_runup=0.05)  # +100% runup
    assert eb is None and why == "runup_abort"

def test_missing_fresh_falls_back_to_decision():
    assert reprice_entry(0.10, None)[0] == 0.10
    assert reprice_entry(0.10, 0.0)[0] == 0.10
```

- [ ] **Step 2: Run to verify fail** — `python -m pytest tests/test_paper_fidelity.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**
```python
# core/paper_fidelity.py
"""Pure helpers that make the PAPER twin simulate the LIVE bot's execution
constraints, so paper P&L predicts live. Every helper is pure + fail-open."""
from __future__ import annotations
import os

def paper_fidelity_enabled(flag: str, default: str = "off") -> str:
    try:
        v = os.environ.get(flag, default).strip().lower()
    except Exception:
        return default
    return v if v in ("off", "on", "shadow", "enforce") else default

def reprice_entry(decision_mid, fresh_price, max_runup=None):
    """Entry basis paper should BOOK: the reachable fresh price, mirroring live.
    Returns (entry_basis|None, reason). None => paper skips (mirrors live abort)."""
    try:
        dm = float(decision_mid)
    except (TypeError, ValueError):
        return (None, "bad_mid")
    try:
        fp = float(fresh_price) if fresh_price is not None else 0.0
    except (TypeError, ValueError):
        fp = 0.0
    if fp <= 0:
        return (dm, "stale_fallback")
    if max_runup is not None and dm > 0:
        runup = (fp / dm) - 1.0
        if runup > float(max_runup):
            return (None, "runup_abort")
    return (fp, "fresh")
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_paper_fidelity.py -q` → 4 passed.
- [ ] **Step 5: Commit** — `git add core/paper_fidelity.py tests/test_paper_fidelity.py && git commit -m "feat(paper-fidelity): pure entry-reprice helper + module scaffold"`

---

### Task 2: Realistic slippage + fee helper

**Files:** Modify `core/paper_fidelity.py`; Test `tests/test_paper_fidelity.py`

**Interfaces:**
- Produces: `effective_fill(mid, side, slip_pct, fee_usd, size_usd) -> float` — buy: `mid*(1 + slip_pct/100 + fee_usd/size_usd*100/100)`; sell: `mid*(1 - slip_pct/100 - fee_usd/size_usd)`. `slip_pct` is the MEASURED live slippage for this token class (caller supplies; default from env `PAPER_LIVE_SLIP_PCT`, ~1.5).
- `measured_live_slip_pct() -> float` env reader (default 1.5), `paper_fee_usd() -> float` (default 0.17).

- [ ] **Step 1: failing tests**
```python
from core.paper_fidelity import effective_fill, measured_live_slip_pct, paper_fee_usd
def test_buy_pays_up_slip_and_fee():
    # mid 0.10, 1.5% slip, $0.17 fee on $100 = 0.17% -> ~1.67% pay-up
    f = effective_fill(0.10, "buy", slip_pct=1.5, fee_usd=0.17, size_usd=100)
    assert abs(f - 0.10*(1+0.0167)) < 1e-9
def test_sell_receives_less_slip_and_fee():
    f = effective_fill(0.10, "sell", slip_pct=1.5, fee_usd=0.17, size_usd=100)
    assert abs(f - 0.10*(1-0.0167)) < 1e-9
def test_defaults():
    assert measured_live_slip_pct() == 1.5
    assert paper_fee_usd() == 0.17
```
- [ ] **Step 2: verify fail.**
- [ ] **Step 3: implement** `effective_fill`, `measured_live_slip_pct` (`float(os.environ.get("PAPER_LIVE_SLIP_PCT","1.5"))`, except→1.5), `paper_fee_usd` (`PAPER_FEE_USD_PER_TX` default 0.17). Fail-open: bad mid → return mid unchanged.
- [ ] **Step 4: verify pass.**
- [ ] **Step 5: commit** `feat(paper-fidelity): realistic slippage+fee effective-fill helper`

---

### Task 3: Reachability-skip predicates

**Files:** Modify `core/paper_fidelity.py`; Test `tests/test_paper_fidelity.py`

**Interfaces:**
- Produces: `no_route_skip(fresh_source, mode) -> bool` — True (skip) when `mode in (shadow,enforce)` AND `fresh_source != "onchain"` AND no jupiter price (i.e., source indicates no fresh price). `slippage_cap_skip(modeled_slip_pct, cap_pct=None) -> bool` — True when `modeled_slip_pct >= cap_pct` (default cap from `PROBE_ULTRA_SLIPPAGE_BPS`/100 = 4.0). Both fail-open (False) on missing data.

- [ ] **Step 1: failing tests**
```python
from core.paper_fidelity import no_route_skip, slippage_cap_skip
def test_no_route_skip_when_no_fresh_price():
    assert no_route_skip(fresh_source="none", mode="enforce") is True
    assert no_route_skip(fresh_source="onchain", mode="enforce") is False
    assert no_route_skip(fresh_source="none", mode="off") is False  # gate off
def test_slippage_cap_skip():
    assert slippage_cap_skip(5.0, cap_pct=4.0) is True
    assert slippage_cap_skip(2.0, cap_pct=4.0) is False
    assert slippage_cap_skip(None) is False  # fail-open
```
- [ ] Steps 2-5: verify-fail → implement (read cap default from `PROBE_ULTRA_SLIPPAGE_BPS` env /100, default 4.0) → verify-pass → commit `feat(paper-fidelity): no-route + slippage-cap skip predicates`.

---

### Task 4: Stop gap-through haircut helper

**Files:** Modify `core/paper_fidelity.py`; Test `tests/test_paper_fidelity.py`

**Interfaces:**
- Produces: `gap_through_extra_pct(exit_reason, base_pct=None) -> float` — extra NEGATIVE slippage for gap-prone exits. Returns `GAP_THROUGH_HAIRCUT_PCT` (default 5.0) when `exit_reason` contains hard_stop/fast_bail/giveback/trail-stop; else 0.0. Caller subtracts this from the sell price for those exits only.

- [ ] **Step 1: failing tests**
```python
from core.paper_fidelity import gap_through_extra_pct
def test_hard_stop_gaps():
    assert gap_through_extra_pct("HARD_STOP pnl=-25%") == 5.0
def test_tp_does_not_gap():
    assert gap_through_extra_pct("TP1 pnl=6.0%") == 0.0
def test_none_safe():
    assert gap_through_extra_pct(None) == 0.0
```
- [ ] Steps 2-5: verify-fail → implement (substring match on lowercased reason for `hard_stop`,`stop`,`fast_bail`,`giveback`; env `GAP_THROUGH_HAIRCUT_PCT` default 5.0) → verify-pass → commit `feat(paper-fidelity): stop gap-through haircut helper`.

---

### Task 5: Wire paper BUY — entry reprice + slippage + reachability skips

**Files:** Modify `feeds/dip_scanner.py` (PAPER buy branch ~1745-1790, the `buy_fill_price(decision.entry_price,...)` site identified by the audit). Test: `tests/test_paper_buy_fidelity_wire.py`

**Interfaces:**
- Consumes: `reprice_entry`, `effective_fill`, `no_route_skip`, `slippage_cap_skip`, `measured_live_slip_pct`, `paper_fee_usd`, `paper_fidelity_enabled`; `self._fast_price_for`, `self._get_current_price_for`.
- Env gate: `PAPER_FIDELITY_MODE` (off|shadow|enforce, default **shadow** — log would-change without altering paper fills until validated, then flip enforce).

- [ ] **Step 1:** Write `tests/test_paper_buy_fidelity_wire.py` — construct a `DipScanner` (mirror `tests/test_address_case.py::_make_trader` + a stub pm/config), monkeypatch `_get_current_price_for` to return a fresh price above the stale decision, set `PAPER_FIDELITY_MODE=enforce`, and assert (a) the recorded paper entry == fresh-repriced+slipped price (not the stale `decision.entry_price`), (b) a run-up past `BUY_REPRICE_MAX_RUNUP` makes the paper buy SKIP, (c) with `PAPER_FIDELITY_MODE=off` the entry is unchanged (byte-identical to today).
- [ ] **Step 2:** verify fail.
- [ ] **Step 3:** In the paper buy branch: when `paper_fidelity_enabled("PAPER_FIDELITY_MODE") in (shadow,enforce)`: fetch `fresh = await self._get_current_price_for(token, address=…, pair_address=…)`; `(eb, why) = reprice_entry(decision.entry_price, fresh, max_runup=float(os.environ.get("BUY_REPRICE_MAX_RUNUP","0.05")))`; if `eb is None` (runup_abort) and mode==enforce → skip the paper buy (log `[paper-fidelity] SKIP buy runup`); else set the mid used by `buy_fill_price` to `eb`; apply `effective_fill(eb,"buy",measured_live_slip_pct(),paper_fee_usd(),size)` as the booked entry. Reachability: if `no_route_skip(fresh_source, mode)` or `slippage_cap_skip(modeled_slip)` and mode==enforce → skip. In `shadow` mode: compute + log the deltas, do NOT change the fill. Gate `off` → original path untouched.
- [ ] **Step 4:** verify pass + run `python -m pytest tests/ -q -k "paper_fidelity or paper_buy"`.
- [ ] **Step 5:** commit `feat(paper-fidelity): wire paper buy to fresh-reprice + real slippage + reachability skips (shadow default)`

---

### Task 6: Wire paper SELL — exit reprice + slippage + gap-through

**Files:** Modify `feeds/dip_scanner.py` (PAPER sell branch ~2988, `sell_fill_price(current_price,...)`). Test: `tests/test_paper_sell_fidelity_wire.py`

**Interfaces:** Consumes Task 1-4 helpers + `gap_through_extra_pct`. Same `PAPER_FIDELITY_MODE` gate.

- [ ] **Step 1:** test — monkeypatch fresh price; assert (a) paper exit books `effective_fill(fresh,"sell",slip,fee,size)` minus `gap_through_extra_pct(reason)` on a HARD_STOP, (b) TP1 exit has no gap haircut, (c) mode=off unchanged.
- [ ] **Step 2-4:** verify-fail → implement (fetch fresh for the held token; `eff_exit = effective_fill(fresh_or_current, "sell", slip, fee, size)`; `eff_exit *= (1 - gap_through_extra_pct(exit_decision.reason)/100)`; shadow logs, enforce applies; off untouched) → verify-pass.
- [ ] **Step 5:** commit `feat(paper-fidelity): wire paper sell to fresh-reprice + slippage + stop gap-through`

---

### Task 7: Skip-instrumentation (the 1:1 scoreboard)

**Files:** Create `core/paper_live_reconcile.py` (append-only JSONL logger, mirror `core/live_swap_log.py` fail-open pattern); wire one call at the paper buy decision in `feeds/dip_scanner.py`. Tests: `tests/test_paper_live_reconcile.py`. Expose via a read endpoint in `dashboard/web_dashboard.py` (`/api/paper-live-skips`, mirror `/api/live-swaps` reader).

**Interfaces:**
- Produces: `log_paper_live_decision(token_address, token_symbol, paper_took: bool, live_would_take: bool, skip_reason: str, fresh_source, delta_pct)` → one JSONL line to `DATA_DIR/paper_live_reconcile.jsonl`. `summarize_reconcile(recs) -> dict` (counts paper-only, live-would-skip-reason histogram).

- [ ] **Step 1:** failing tests for `summarize_reconcile` (pure): given records, returns `{paper_only_n, by_skip_reason}`.
- [ ] **Step 2-4:** verify-fail → implement logger (fail-open, `LOG_BASENAME="paper_live_reconcile.jsonl"`, on log_rotator allowlist) + `summarize_reconcile` → verify-pass.
- [ ] **Step 5:** commit `feat(reconcile): paper-vs-live skip instrumentation + /api/paper-live-skips`

---

### Task 8: Simulate live caps in paper (trade-SET parity)

**Files:** Modify `feeds/dip_scanner.py` PAPER buy branch; reuse `core/paper_fidelity.py`. Test: extend `tests/test_paper_buy_fidelity_wire.py`.

**Interfaces:**
- Produces: `caps_would_block(open_n, open_usd, size_usd, max_n, max_usd) -> bool` (pure) — mirrors the LIVE per-token cap arithmetic so paper can flag a buy live's caps would refuse. Wired as a SHADOW flag on paper (logs `[paper-fidelity] caps-would-block`), recorded via Task 7's reconcile logger — does NOT remove paper's own throughput (paper keeps trading for selection data; the flag lets the reconcile scoreboard subtract cap-blocked trades when comparing to live).

- [ ] **Step 1:** failing test for `caps_would_block` (pure arithmetic, matches `LIVE_PER_TOKEN_MAX_*`).
- [ ] **Step 2-4:** verify-fail → implement + wire as shadow flag feeding Task 7's logger → verify-pass.
- [ ] **Step 5:** commit `feat(paper-fidelity): simulate live caps as reconcile flags (trade-set parity)`

---

### Task 9: Arming/eval tick parity audit + fix

**Files:** Investigate `feeds/dip_scanner.py` arming + per-bot eval order; fix only if a real divergence is found. Test as applicable.

- [ ] **Step 1:** Add a diagnostic log/assert: for a given token+tick, record whether paper and live (`badday_flush_nf15` vs `_live`) were both evaluated. Run paper soak, inspect for tokens evaluated for one bot but not the other in the same tick.
- [ ] **Step 2:** If a divergence is found (e.g., arming set or eval order drops one bot), fix so both replica bots evaluate the same armed set each tick. If NO divergence (they share `_evaluate_pair` per-bot in the same pass), document that and close the task.
- [ ] **Step 3:** commit `fix(parity): ensure paper+live replica bots evaluate the same armed set per tick` (or `docs: confirm arming parity, no fix needed`).

---

## Rollout
1. Tasks 1-9 land with `PAPER_FIDELITY_MODE=shadow` (logs deltas, no behavior change) — validate the reconcile scoreboard shows paper and live converging.
2. Flip `PAPER_FIDELITY_MODE=enforce` (paper-only; live wallet still drained) — paper now books reachable prices/costs and skips unreachable trades.
3. Re-run the paper-vs-live comparison: success = paper and live take the same trade SET and record entries within execution-slippage on shared tokens.
4. Only then re-fund live.

## Self-Review notes
- Capital parity intentionally excluded (AxiS). ✓
- Every task is pure-helper-first (testable) + thin wiring, gated `PAPER_FIDELITY_MODE` default shadow. ✓
- No live execution path is modified. ✓
- Skip-instrumentation (Task 7) is the scoreboard that proves 1:1 and explains the unexplained 6.4h silence. ✓
