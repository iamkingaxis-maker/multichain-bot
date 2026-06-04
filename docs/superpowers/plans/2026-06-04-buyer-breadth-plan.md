# Buyer-Breadth Entry Signal (C + A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the buyer-breadth signal as (A) a zero-risk fleet-wide MEASURE-ONLY shadow now, and (C) a ready-to-flip 2nd entry-gate condition on `momentum_grad_probe`, enforced only after the grad-mine n≥100 cross-token test confirms.

**Architecture:** A pure verdict helper (`core/buyer_concentration.py`) is the single source of truth for the "whale-dominated buying" rule. A consumes it as a shadow stamp in `dip_scanner.py` (mirroring `watchlist_bypass_downtrend_shadow`) + a phantom-parity predicate in `live_forward_test.py`. C is a one-line config condition on the probe's `entry_gate`, evaluated by the existing `bot_evaluator` gate loop (which supports only `>=`/`<=`).

**Tech Stack:** Python 3.12, pytest, aiohttp (dashboard untouched), existing fleet config/JSON.

**Spec:** `docs/superpowers/specs/2026-06-04-buyer-breadth-design.md`

---

### Task 1: Pure verdict helper

**Files:**
- Create: `core/buyer_concentration.py`
- Test: `tests/test_buyer_concentration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_buyer_concentration.py
"""Buyer-concentration verdict (core/buyer_concentration). Whale-dominated BUYING
(large_buyer_volume_pct >= thr) is the fresh-token bleed signature (fleet d=-0.80)."""
from core.buyer_concentration import buyer_concentration_verdict


def test_whale_dominated_blocks():
    v, reasons = buyer_concentration_verdict({"large_buyer_volume_pct": 0.81})
    assert v == "BLOCK" and reasons


def test_distributed_passes():
    v, reasons = buyer_concentration_verdict({"large_buyer_volume_pct": 0.10})
    assert v == "PASS" and reasons == []


def test_zero_passes():
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.0})[0] == "PASS"


def test_missing_is_neutral_fail_open():
    assert buyer_concentration_verdict({})[0] == "NEUTRAL"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": None})[0] == "NEUTRAL"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": True})[0] == "NEUTRAL"


def test_threshold_boundary():
    # exactly at threshold = BLOCK (>=); just below = PASS
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.5})[0] == "BLOCK"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.49})[0] == "PASS"


def test_threshold_env_override(monkeypatch):
    monkeypatch.setenv("BUYER_CONC_BLOCK_THR", "0.7")
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.6})[0] == "PASS"
    assert buyer_concentration_verdict({"large_buyer_volume_pct": 0.75})[0] == "BLOCK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_buyer_concentration.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.buyer_concentration'`

- [ ] **Step 3: Write minimal implementation**

```python
# core/buyer_concentration.py
"""Buyer-concentration verdict (2026-06-04).

THE first entry-side signal to clear the fleet's full discipline (held-out-by-token
+ token-clustered null + BH): on FRESH tokens, whale-dominated BUYING bleeds, broad
distributed buying continues. Fleet evidence: large_buyer_volume_pct Cohen d=-0.80
on fresh<24h trades (>=0.5 -> 9% WR vs 0-0.5 -> 78%); grad mine buyer_hhi/n_buyers
survive BH across 50 distinct tokens. Signal WASHES OUT on aged tokens -> fresh-only.

Pure (env read at the edge) so it is unit-testable and shared by the dip_scanner
shadow stamp and documented as the logic behind the momentum_grad_probe gate.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List, Tuple


def block_threshold() -> float:
    try:
        return float(os.environ.get("BUYER_CONC_BLOCK_THR", "0.5"))
    except (TypeError, ValueError):
        return 0.5


def buyer_concentration_verdict(meta: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (verdict, reasons). BLOCK if buying is whale-dominated
    (large_buyer_volume_pct >= threshold); PASS if below; NEUTRAL (fail-open) when
    the feature is absent/non-numeric — a value we cannot read never blocks."""
    thr = block_threshold()
    lbv = meta.get("large_buyer_volume_pct")
    if not isinstance(lbv, (int, float)) or isinstance(lbv, bool):
        return "NEUTRAL", []
    if lbv >= thr:
        return "BLOCK", [f"large_buyer_volume_pct={lbv:.2f}>={thr} (whale-dominated buying)"]
    return "PASS", []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_buyer_concentration.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add core/buyer_concentration.py tests/test_buyer_concentration.py
git commit -m "feat: buyer_concentration verdict helper (whale-dominated buying = fresh-token bleed signal)"
```

---

### Task 2 (A): Shadow stamp in dip_scanner

**Files:**
- Modify: `feeds/dip_scanner.py` (insert after the `watchlist_bypass_downtrend_shadow` block, ~line 15133, inside the same `entry_meta_dict` shadow-stamping region)

- [ ] **Step 1: Add the shadow stamp (MEASURE-ONLY, fail-open)**

Insert immediately AFTER the `watchlist_bypass_downtrend_shadow` try/except block (the block ending near line 15135), matching that pattern exactly:

```python
            # buyer_concentration — SHADOW 2026-06-04. First entry-side signal to
            # clear the fleet's full discipline (held-out-by-token + token-null + BH):
            # whale-dominated BUYING (large_buyer_volume_pct>=0.5) is the fresh-token
            # bleed signature (fleet d=-0.80; >=0.5 -> 9% WR). MEASURE-ONLY: stamps
            # verdict into entry_meta_dict + counter + log; never appends to
            # _filters_block. Validate forward (live, cross-token, post-crash) before
            # any enforcement spreads beyond momentum_grad_probe. Fail-open.
            try:
                from core.buyer_concentration import buyer_concentration_verdict as _bc_v
                _bc_verdict, _bc_reasons = _bc_v(entry_meta_dict)
                entry_meta_dict["buyer_concentration_shadow"] = _bc_verdict
                entry_meta_dict["buyer_concentration_shadow_reasons"] = _bc_reasons
                if _bc_verdict == "BLOCK":
                    c["buyer_concentration_would_block"] = c.get(
                        "buyer_concentration_would_block", 0) + 1
                    logger.info(
                        f"[DipScanner] buyer_concentration SHADOW would-block: "
                        f"{token_symbol} {';'.join(_bc_reasons)} "
                        f"(age_h={entry_meta_dict.get('lifecycle_age_hours')})")
            except Exception as _e:
                logger.debug(f"[DipScanner] buyer_concentration shadow err: {_e}")
```

- [ ] **Step 2: Verify it compiles**

Run: `python -m py_compile feeds/dip_scanner.py`
Expected: no output (success)

- [ ] **Step 3: Verify the feature is populated (coverage check)**

Run:
```bash
python -c "import json; d=json.load(open('_nf_trades.json')); \
b=[t for t in d if t.get('type')=='buy' and isinstance(t.get('entry_meta'),dict)]; \
n=sum(1 for t in b if isinstance(t['entry_meta'].get('large_buyer_volume_pct'),(int,float))); \
print(f'large_buyer_volume_pct present on {n}/{len(b)} buys')"
```
Expected: present on the large majority of buys (confirms fail-open is rare). Record the rate.

- [ ] **Step 4: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(A): buyer_concentration shadow stamp on fleet entries (measure-only, fail-open)"
```

---

### Task 3 (A): Phantom parity in live_forward_test

**Files:**
- Modify: `scripts/live_forward_test.py` (add a block predicate alongside the other phantom-mirror functions, e.g. after `slip_asym_block`)

- [ ] **Step 1: Add the predicate**

```python
def buyer_concentration_block(c):
    """Phantom mirror for buyer_concentration SHADOW (2026-06-04). Blocks when
    buying is whale-dominated (large_buyer_volume_pct >= BUYER_CONC_BLOCK_THR,
    default 0.5) — the fresh-token bleed signature (fleet d=-0.80). Fails open when
    the candidate snapshot lacks the field."""
    from core.buyer_concentration import buyer_concentration_verdict
    return buyer_concentration_verdict(c)[0] == "BLOCK"
```

- [ ] **Step 2: Register it in the COMBOS dict**

In the `COMBOS = {...}` block, add an entry mirroring the existing `_plus_*` combos:

```python
    'L_B_plus_buyer_conc':   lambda c: not scanner_block_reasons(c) and not turn_block(c) and not buyer_concentration_block(c),
```

- [ ] **Step 3: Verify it compiles + imports**

Run: `python -c "import scripts.live_forward_test as m; print('buyer_concentration_block' in dir(m))"`
Expected: `True`

- [ ] **Step 4: Commit**

```bash
git add scripts/live_forward_test.py
git commit -m "feat(A): phantom parity for buyer_concentration shadow in live_forward_test"
```

---

### Task 4 (C): Probe entry-gate condition + test (READY; deploy gated on n≥100)

**Files:**
- Modify: `config/bots/momentum_grad_probe.json` (add the `entry_gate` condition)
- Test: `tests/test_bot_evaluator.py` (add a gate test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_bot_evaluator.py` (use the existing FeatureBundle/BotConfig/BotEvaluator harness in that file; mirror the existing momentum/entry_gate tests):

```python
def test_momentum_gate_rejects_whale_dominated_buying():
    """C: entry_gate large_buyer_volume_pct<=0.5 rejects whale-dominated buying."""
    cfg = _momentum_cfg(entry_gate=[
        ["net_flow_60s_imbalance", ">=", 0.3],
        ["1m_volume_spike", ">=", 0.4],
        ["large_buyer_volume_pct", "<=", 0.5],
    ])
    ev = BotEvaluator(cfg)
    base = {"net_flow_60s_imbalance": 0.5, "1m_volume_spike": 0.6}
    # whale-dominated -> rejected
    b_block = _bundle(raw_meta={**base, "large_buyer_volume_pct": 0.81})
    assert ev._token_regime_passes(b_block) is False
    # distributed -> passes
    b_pass = _bundle(raw_meta={**base, "large_buyer_volume_pct": 0.10})
    assert ev._token_regime_passes(b_pass) is True
    # missing feature -> fail-open (passes)
    b_open = _bundle(raw_meta={**base})
    assert ev._token_regime_passes(b_open) is True
```

NOTE: `_momentum_cfg` and `_bundle` are the file's existing helpers — match their real signatures (read the existing momentum tests in `test_bot_evaluator.py` first and reuse them; do not invent new fixtures). If a helper to set `entry_gate` does not exist, extend the existing config factory with an `entry_gate` kwarg.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bot_evaluator.py -k whale_dominated -q`
Expected: FAIL (config has no buyer-breadth condition yet, so the block case returns True)

- [ ] **Step 3: Add the condition to the probe config**

In `config/bots/momentum_grad_probe.json`, change `entry_gate` from:
```json
    "entry_gate": [
        ["net_flow_60s_imbalance", ">=", 0.3],
        ["1m_volume_spike", ">=", 0.4]
    ],
```
to:
```json
    "entry_gate": [
        ["net_flow_60s_imbalance", ">=", 0.3],
        ["1m_volume_spike", ">=", 0.4],
        ["large_buyer_volume_pct", "<=", 0.5]
    ],
```
(Preserve the file's existing array formatting/indentation.)

- [ ] **Step 4: Run the test + the bot-catalog test to verify pass + config validity**

Run: `python -m pytest tests/test_bot_evaluator.py -k whale_dominated tests/test_bot_catalog.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config/bots/momentum_grad_probe.json tests/test_bot_evaluator.py
git commit -m "feat(C): buyer-breadth 2nd gate condition on momentum_grad_probe (large_buyer_volume_pct<=0.5)"
```

---

### Task 5: Ship A now; stage C on n≥100

- [ ] **Step 1: Full test sweep**

Run: `python -m pytest tests/test_buyer_concentration.py tests/test_bot_evaluator.py tests/test_bot_catalog.py tests/test_egress_throttle.py -q`
Expected: all pass.

- [ ] **Step 2: Push + deploy (A is live shadow; C config rides along but is fresh/probe-scoped)**

```bash
git push origin master
railway up --detach
```
Then VERIFY (mandatory): `railway variables | grep -i PAPER_MODE` shows `true`; tail logs for `buyer_concentration SHADOW would-block` lines appearing on fresh whale-dominated entries.

NOTE on C: shipping the probe config change is low-risk (fresh-only, $2k experimental, fail-open). But per the spec, **judge whether to KEEP it enforced** based on the grad-mine n≥100 incremental result. If n≥100 fails cross-token, revert Task 4's config line (keep A). Decision point — surface the n≥100 verdict to the user before treating C as validated.

- [ ] **Step 3: Forward readout (after ~24-48h)**

- Read `buyer_concentration_shadow` BLOCK-cohort realized P&L vs PASS on live fleet **fresh** entries (the phantom-parity + entry_meta join), confirming the fleet d=-0.80 holds forward post-crash and cross-token.
- Compare `momentum_grad_probe` fills pre/post the C condition (fewer whale-dominated entries; WR/peak-rate change).
