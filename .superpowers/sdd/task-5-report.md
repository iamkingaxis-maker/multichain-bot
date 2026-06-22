# Task 5 Report — Wire paper BUY fidelity

## STATUS: COMPLETE

## What was done

### 1. Pure composition helper (`core/paper_fidelity.py`)
Added `paper_entry_decision(decision_mid, fresh_price, fresh_source, modeled_slip_pct, mode, size_usd, slip_pct=None, fee_usd=None, max_runup=0.05) -> (entry_basis|None, reason)`.

Composition order (when `mode != "off"`):
1. `mode == "off"` → `(decision_mid, "off")` unchanged
2. `no_route_skip(fresh_source, mode)` → `(None, "no_route")`
3. `reprice_entry(...)` → if `None` → `(None, "runup_abort")`
4. `slippage_cap_skip(modeled_slip_pct)` → `(None, "slippage_cap")`
5. `effective_fill(repriced, "buy", slip_pct or measured_live_slip_pct(), fee_usd or paper_fee_usd(), size_usd)` → `(eff, "fresh")`

FAIL-OPEN: any exception → `(decision_mid, "error_fallback")`.

### 2. Unit tests (`tests/test_paper_fidelity.py`)
Added 7 tests for the new helper: off-unchanged, fresh-used-with-slip-and-fee,
runup→skip, no-route→skip, slippage-cap→skip, defaults-when-None, fail-open.
Full module: **28 passed**.

### 3. Wired into paper buy branch (`feeds/dip_scanner.py`)
Inserted directly before the existing `buy_fill_price(decision.entry_price, ...)`
call in the PAPER (`_live is False`) branch (was ~line 1755). The wire:
- Reads `PAPER_FIDELITY_MODE` (default **shadow**) via `paper_fidelity_enabled`.
- When `shadow`/`enforce`: fetches fresh price via
  `await self._get_current_price_for(token, address=…, pair_address=…)`,
  source via `self._fast_price_for(addr, fresh)[1]` (fallback `"jupiter"`),
  reads `BUY_REPRICE_MAX_RUNUP` (default 0.05), and calls `paper_entry_decision`.
- **enforce**: `entry_basis is None` → log `[paper-fidelity] SKIP <reason>`,
  refund capital reservation, `return` (skip the paper buy); else the repriced
  basis becomes the mid passed to `buy_fill_price`.
- **shadow**: log the would-book delta / would-skip, do NOT change the fill.
- **off**: original path byte-identical — `_pf_entry_mid` stays `decision.entry_price`.
- FAIL-OPEN: any exception in the block logs and falls back to `decision.entry_price`.

## Constraints honored
- FAIL-OPEN throughout (helper + wire); never raises into the buy path.
- Default mode **shadow** → no behavior change on deploy.
- Live path (`_execute_bot_buy_live`) untouched.
- Address-keyed (`decision.address` / `_addr_by_token` fallback).
- TDD: pure-helper test written + run before wiring.

## Verification
- `python -m pytest tests/test_paper_fidelity.py -q` → **28 passed**
- `python -m pytest tests/ -q -k "paper_fidelity or paper_buy"` → **28 passed, 1262 deselected**
- `python -c "import ast; ast.parse(...dip_scanner.py...)"` → AST OK
- `python -c "import feeds.dip_scanner"` → IMPORT OK

## Concerns / deviations from brief
- The brief's Step-1 full-DipScanner integration test
  (`tests/test_paper_buy_fidelity_wire.py`) was REPLACED per the controller
  resolution with a pure-helper unit test of `paper_entry_decision`. The
  dip_scanner wire is a thin shadow/enforce/off gate verified by AST + import
  only (no behavioral integration test).
- In `enforce`, the SKIP refunds the just-reserved capital
  (`capital.balance_usd += _used_size; capital.in_flight_usd -= _used_size`)
  before returning, mirroring the other paper rejection paths in this function.

---

## Review-fix pass (2026-06-22) — 3 findings resolved

**FINDING 1 (double-slippage in enforce) — FIXED.** `paper_entry_decision` returns
`_eb` already = `effective_fill(mid, "buy", slip, fee)`. The dip_scanner PAPER wire
fed it back into `buy_fill_price`, applying impact+fee a 2nd time. Fix: added
`_pf_owns_slippage` flag (set True only in enforce when a usable entry returns). At
the booking site, when the flag is set we book `_pf_entry_mid` DIRECTLY as `eff_entry`
and discard `buy_fill_price`'s price — but STILL call `buy_fill_price(decision.entry_price, ...)`
to derive `slip_pct` (the per-side impact estimate the sell leg reuses). shadow/off
keep the original `buy_fill_price(_pf_entry_mid, ...)` path → byte-identical.
Verified: enforce entry = mid × (1 + slip/100 + fee_frac) ONCE (0.10 → 0.10167).

**FINDING 2 (no_route_skip treats "jupiter" as no-route) — FIXED.** `no_route_skip`
now skips only when gated AND source NOT in ("onchain","jupiter"). "onchain"→False,
"jupiter"→False, "none"/""/None/unknown→True (when gated), mode off/on→False (fail-open
on error). Default config (ONCHAIN_WS_MODE=off → "jupiter") no longer skips every buy.
Tests updated/added: jupiter-is-route (both modes), None→no-route-when-gated,
unknown/empty→no-route, paper_entry jupiter does-not-skip, no_route case switched to "none".

**FINDING 3 (dead import) — FIXED.** Removed unused `effective_fill as _eff_fill`
from the dip_scanner wire import block.

Constraints honored: FAIL-OPEN preserved (all helpers + wire wrapped, error→original
fill); shadow/off byte-identical; no LIVE path touched; address-keyed unchanged.

### Test command + result
```
python -m pytest tests/test_paper_fidelity.py -q
31 passed in 0.33s
```
```
python -c "import ast; ast.parse(open('feeds/dip_scanner.py',encoding='utf-8').read()); print('AST OK')"
AST OK
```
