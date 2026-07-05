"""Same-symbol ≠ same-token in the exit-pricing loop (2026-06-12 SPCX incident).

Six different mints all ticker'd "SPCX" traded on 2026-06-12. The management
loop's per-cycle price map and exit-price-guard state were keyed by SYMBOL, so
one token's real price (~0.0034, E6ifp2…) poisoned guard["SPCX"].last_good for
a different token (real ~8.5e-5, BhTPX…). The poisoned bot's first tick saw its
own real price as a "-97% suspect drop", the guard deferred and returned the
OTHER token's price, and TP1+TP2 booked +3,913% (+$7.8k phantom).

Fix: price map + guard are keyed by ADDRESS via DipScanner._price_key.
"""
import asyncio
import types

from feeds.dip_scanner import DipScanner


def test_price_key_prefers_address_then_pair_then_symbol():
    assert DipScanner._price_key("MintA", "PairA", "SPCX") == "minta"
    assert DipScanner._price_key(None, "PairA", "SPCX") == "paira"
    assert DipScanner._price_key(None, None, "SPCX") == "spcx"
    assert DipScanner._price_key("", "", "SPCX") == "spcx"


class _Pos:
    def __init__(self, token, address, pair_address, entry_price):
        self.token = token
        self.address = address
        self.pair_address = pair_address
        self.entry_price = entry_price
        self.state_blob = {}


class _PM:
    """Minimal per-bot PM: one position; records the price tick() received."""
    def __init__(self, pos):
        self._pos = pos
        self.ticked_prices = []
        self.config = types.SimpleNamespace(scalein_enabled=False)

    def iter_positions(self):
        return [self._pos]

    def tick(self, token, current_price, now, vol_m5_usd=None):
        self.ticked_prices.append(current_price)
        return []   # no exit decisions — we only assert the price routing

    def scalein_ready(self, *a):
        return False


def _mk_scanner(prices_by_address):
    ds = DipScanner.__new__(DipScanner)
    ds._exit_price_guard = {}
    ds._exit_price_guard_ts = {}  # production inits this in __init__ (dip_scanner L613)
    ds.trader = types.SimpleNamespace(private_key="")
    ds._stamp_sol_bail_shadow = lambda *a, **k: None

    async def _price_for(token, address="", pair_address=""):
        return prices_by_address[address]

    async def _vol_for(token):
        return None

    ds._get_current_price_for = _price_for
    ds._get_vol_m5_for = _vol_for
    return ds


def test_same_symbol_positions_get_their_own_prices():
    # The incident shape: two bots hold DIFFERENT tokens that share the symbol
    # "SPCX". Token A really trades at 0.0034; token B really trades at 9.16e-5.
    # Entries near the live price so the glitch guard's (by-design) one-cycle
    # deferral of big entry-vs-first-tick gaps doesn't blur the assertion.
    pos_a = _Pos("SPCX", "E6ifp2mint", "pairA", entry_price=0.00340)
    pos_b = _Pos("SPCX", "BhTPXmint", "pairB", entry_price=8.5e-5)
    pm_a, pm_b = _PM(pos_a), _PM(pos_b)

    ds = _mk_scanner({"E6ifp2mint": 0.003417, "BhTPXmint": 9.163e-5})
    ds.bot_position_managers = {"pool_a_broad_control": pm_a,
                                "badday_flush_conviction": pm_b}

    # Several cycles — pre-fix, cycle 1 wrote guard["SPCX"].last_good=0.003417
    # and cycle 2+ returned that POISON as bot B's actionable price (+3913% TP).
    for _ in range(3):
        asyncio.run(ds._tick_all_bots_positions())

    assert pm_a.ticked_prices, "token A was never ticked"
    assert pm_b.ticked_prices, "token B was never ticked"
    # Each position must be ticked with ITS OWN token's price, every cycle.
    assert all(abs(p - 0.003417) < 1e-12 for p in pm_a.ticked_prices)
    assert all(abs(p - 9.163e-5) < 1e-12 for p in pm_b.ticked_prices), (
        f"cross-symbol price contamination: token B ticked at {pm_b.ticked_prices}"
    )
    # And the guard state must hold one slot per ADDRESS, not a shared symbol slot.
    assert "e6ifp2mint" in ds._exit_price_guard
    assert "bhtpxmint" in ds._exit_price_guard
    assert "SPCX" not in ds._exit_price_guard
