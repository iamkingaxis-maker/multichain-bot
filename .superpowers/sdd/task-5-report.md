# Task 5 Report: Wire RT_DIP_MODE into fast-watch reprice site

## Status: DONE
Commit: `0a0c253` (branch `feat/realtime-dip-rebuild`)

## What was done (TDD)
1. Wrote `tests/test_rt_dip_mode.py` (4 tests targeting `_apply_rt_dip` directly).
2. Ran -> FAILED with `AttributeError: 'DipScanner' object has no attribute '_apply_rt_dip'`.
3. Implemented:
   - `self._rt_dip_windows = {}` added in `DipScanner.__init__` (right after `_rt_dip_bar_cache`, ~line 394).
   - `_apply_rt_dip(self, pair, snap_price, fresh_price, mode, *, bars, now)` method on `DipScanner`, placed just before `_get_rt_dip_bars` (~line 21729). Exact code from brief.
   - Wiring block at the reprice site inside `_eval_one_survivor`, immediately after the `RT_TRIGGER` enforce + shadow-stats block (after ~line 6558).
4. Ran -> 4 passed.
5. Ran full suite + pre-live invariants -> 32 passed.
6. Committed with the exact brief message.

## Test outputs
```
$ python -m pytest tests/test_rt_dip_mode.py -q
....                                                                     [100%]
4 passed in 0.53s

$ python -m pytest tests/test_realtime_dip.py tests/test_rolling_high_from_bars.py tests/test_rt_dip_bar_cache.py tests/test_rt_dip_mode.py tests/test_pre_live_invariants.py -q
................................                                         [100%]
32 passed in 0.90s

$ python -c "import feeds.dip_scanner"
IMPORT_OK
```

## Final wiring block inserted (at the reprice site, after the RT_TRIGGER block)
```python
                # RT_DIP (2026-06-29): real-time dip reference off io.dexscreener
                # bars + the in-memory rolling buffer, superseding the stale-anchor
                # reprice_all above when usable. RT_DIP_MODE off=byte-identical;
                # enforce overwrites priceChange only when coverage != NONE (else
                # falls back to the reprice result — never fail-open into a buy).
                from core.fast_watch import rt_mode as _rt_mode
                _rt_dip = _rt_mode("RT_DIP_MODE")
                if _rt_dip != "off" and _snap_price and _fresh_price and _fresh_price > 0:
                    if addr not in self._rt_dip_windows:
                        from core.realtime_dip import RollingPriceWindow as _RPW
                        self._rt_dip_windows[addr] = _RPW()
                    _rt_dex_id = (pair.get("dexId") or "").lower()
                    _rt_slug = {"pumpswap": "pumpfundex", "pumpfun": "pumpfundex",
                                "raydium": "solamm", "meteora": "meteora"}.get(
                                    _rt_dex_id, _rt_dex_id or "pumpfundex")
                    _rt_bars = []
                    try:
                        _rt_bars = await self._get_rt_dip_bars(
                            addr, _rt_slug, pair_addr, res="1m")
                    except Exception:
                        _rt_bars = []
                    self._apply_rt_dip(_pair, _snap_price, _fresh_price, _rt_dip,
                                       bars=_rt_bars, now=now)
```

## Deviations / notes
- Followed the CRITICAL SCOPE CORRECTION: did NOT use `_1s_slug_primary` / `_1s_pair` (out of scope at the reprice site). Derived `_rt_slug` from `pair.get("dexId")` via the verbatim mapping and used `pair_addr` (computed at line 6474) for the bar fetch.
- Confirmed all referenced locals in scope at the site: `addr`, `pair`, `_pair`, `pair_addr`, `_snap_price`, `_fresh_price`, `now` (`now` already used at line 6455).
- `rt_mode` is already imported at line 6529 in the same block; re-imported as `_rt_mode` per the brief snippet (harmless, explicit).
- Block placed at the same indentation level as the RT_TRIGGER `if` (not nested under it), so RT_DIP runs independently of RT_TRIGGER_MODE and supersedes the stale-anchor reprice when usable.
- `off` default = byte-identical (no-op, no priceChange touch). `enforce` overwrites only when coverage != NONE; NONE falls back to the reprice result. `shadow` logs, no mutation. Bar fetches go through the off-loop `_get_rt_dip_bars` (guarded by try/except -> []).

## Task 5 — RT_DIP review fixes (window-key consistency + dup import + window eviction)

### Fix 1 — CRITICAL: window-key mismatch
`_apply_rt_dip` resolved its window key from `pair["address"]`, which does not exist in this
scanner (token addr lives at `pair["baseToken"]["address"]`, pair addr at `pair["pairAddress"]`).
In production the helper computed `addr=""`, got `win=None`, and never appended fresh prices —
the in-memory rolling buffer was dead (only io.dx bars contributed). The 4 prior tests passed
only because they injected a synthetic top-level `"address":"AAA"`.

Fix: added an `addr` keyword param so the wiring and helper share the SAME in-scope `addr`
from `_eval_one_survivor`. Explicit param wins; falls back to
`pair["address"] or pair["baseToken"]["address"] or ""`. Window lookup uses that addr.
New test `test_enforce_uses_explicit_addr_key_no_toplevel_address` uses a baseToken-shaped
pair with NO top-level "address" and proves enforce overwrites priceChange via the addr key.

### Fix 2 — MINOR: duplicate import
Removed the redundant `from core.fast_watch import rt_mode as _rt_mode` (~line 6565); the wiring
now reuses `rt_mode` already imported ~line 6530 in the same block. Call changed to
`rt_mode("RT_DIP_MODE")`.

### Fix 3 — MINOR: unbounded _rt_dip_windows growth
Added module-level `_RT_DIP_WINDOWS_MAX = 3000`. After creating a new window in the wiring, if
the dict exceeds the cap, evict entries whose addr is NOT in `self._rt_dip_bar_cache` (stale);
if still over, drop oldest-inserted entries until under the cap. Cheap, never raises.
New test `test_rt_dip_windows_eviction_bounds_dict` proves the dict stays bounded.

### Final helper signature
    def _apply_rt_dip(self, pair, snap_price, fresh_price, mode, *, bars, now, addr=None):
        ...
        if addr is None:
            addr = (pair.get("address") or (pair.get("baseToken") or {}).get("address") or "") if isinstance(pair, dict) else ""
        win = self._rt_dip_windows.get(addr)

### Wiring call
    self._apply_rt_dip(_pair, _snap_price, _fresh_price, _rt_dip,
                       bars=_rt_bars, now=now, addr=addr)

### Test commands + outputs
1) python -m pytest tests/test_rt_dip_mode.py -q
   -> 6 passed in 1.95s
2) python -m pytest tests/test_realtime_dip.py tests/test_rolling_high_from_bars.py tests/test_rt_dip_bar_cache.py tests/test_rt_dip_mode.py tests/test_pre_live_invariants.py -q
   -> 34 passed in 0.92s
3) python -c "import feeds.dip_scanner"  -> import clean

Semantics unchanged: off=byte-identical, enforce overwrites only when coverage!=NONE,
shadow no-mutation, helper never raises, io.dx fetch stays off-loop.
