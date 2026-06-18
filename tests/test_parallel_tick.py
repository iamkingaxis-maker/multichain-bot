# -*- coding: utf-8 -*-
"""PARALLEL TICK (2026-06-17) — parallelize the per-unique-open-token exit-price
FETCH in DipScanner._tick_all_bots_positions WITHOUT changing exit decisions.

The post-scan position tick fetches an exit price per UNIQUE open token SERIALLY
(deduped by address), then runs pm.tick exit logic. With a big paper fleet that
serial fetch is the ~43s residual of the cycle. The refactor:

  * PARALLEL_TICK_MODE=off (default) -> priced/vols filled lazily in the decision
    loop on first encounter, byte-identical to the old serial behaviour.
  * PARALLEL_TICK_MODE=on -> every UNIQUE token's price+vol is pre-fetched
    concurrently under a bounded semaphore (PARALLEL_TICK_CONCURRENCY, default 12
    / falls back to PARALLEL_SCAN_CONCURRENCY) BEFORE the decision loop; the exit
    DECISIONS (shadows, pm.tick, sells) then run SERIALLY over the gathered
    prices — identical exit logic, just an un-serialized FETCH.

CRITICAL invariants (real money):
  (1) OFF-mode applies the SAME exit decisions per position as before, in order.
  (2) ON-mode runs the FETCHES concurrently (peak parallelism > 1, bounded).
  (3) ON-mode applies the SAME exit decisions per position as OFF-mode.
  (4) Address-keyed: a token's price is never mismatched to another token.
  (5) No double-sell: each position is ticked exactly once and a fired exit
      executes exactly one sell per decision.
"""
import asyncio
import os

import pytest

from feeds.dip_scanner import DipScanner


# ---------------------------------------------------------------------------
# Minimal fakes — enough of the position/PM/scanner surface that the real
# _tick_all_bots_positions + _fetch_tick_price drive without heavy init.
# ---------------------------------------------------------------------------

class _FakePos:
    def __init__(self, address, token, pair_address=None, entry_price=1.0):
        self.address = address
        self.token = token
        self.pair_address = pair_address or address
        self.entry_price = entry_price
        self.tp1_hit = False
        self.peak_pnl_pct = 0.0
        self.entry_time = 0.0
        self.size_usd = 20.0
        self.remaining_fraction = 1.0
        self.state_blob = {}


class _Decision:
    def __init__(self, reason="stop", sell_fraction=1.0):
        self.reason = reason
        self.sell_fraction = sell_fraction


class _FakePM:
    """One bot's positions. tick() returns a fixed decision list per token so we
    can assert exit DECISIONS are identical across modes + fire exactly once."""
    def __init__(self, positions, decisions_by_token=None):
        self._positions = positions
        self._decisions_by_token = decisions_by_token or {}
        self.tick_calls = []           # (token, price) in call order
        self.config = type("C", (), {"live_probe": False})()

    def iter_positions(self):
        return list(self._positions)

    def tick(self, token, current_price, now, vol_m5_usd=None):
        self.tick_calls.append((token, current_price, vol_m5_usd))
        return list(self._decisions_by_token.get(token, []))

    def get_position(self, token):
        for p in self._positions:
            if p.token == token:
                return p
        return None

    def scalein_ready(self, *a, **k):
        return False


def _bare_scanner(pms):
    """A DipScanner with only the attrs the tick loop touches."""
    s = DipScanner.__new__(DipScanner)
    s.bot_position_managers = {f"bot{i}": pm for i, pm in enumerate(pms)}
    s.bot_capitals = {bid: None for bid in s.bot_position_managers}
    s._cycle_sol_features = {}
    s.trader = type("T", (), {"private_key": ""})()

    # Shadows are no-ops for this test (covered elsewhere).
    s._stamp_sol_bail_shadow = lambda *a, **k: None
    s._stamp_liq_drain_shadow = lambda *a, **k: None
    s._sol_flk_1h = lambda: 0

    # Record every sell so we can assert NO double-sell.
    s._sells = []

    async def _exec_sell(bot_id, token, d, price, now):
        s._sells.append((bot_id, token, d.reason, price))

    s._execute_bot_sell = _exec_sell
    return s


def _wire_price_fetch(s, prices_by_addr, fetch_log=None, fetch_delay=0.0,
                      conc_state=None):
    """Stub the NETWORK-bound fetch (_get_current_price_for) + vol. Routes the
    guarded-price call through a fake shared_client so the real guard isn't
    exercised here (guard correctness is tested in its own suite). Address-keyed
    return proves no cross-token mismatch."""
    async def _get_price(token, address=None, pair_address=None):
        key = (address or pair_address or token or "").lower()
        if fetch_log is not None:
            fetch_log.append(key)
        if conc_state is not None:
            conc_state["running"] += 1
            conc_state["peak"] = max(conc_state["peak"], conc_state["running"])
        if fetch_delay:
            await asyncio.sleep(fetch_delay)
        if conc_state is not None:
            conc_state["running"] -= 1
        return prices_by_addr.get(key)

    async def _get_vol(token):
        return 1234.0

    s._get_current_price_for = _get_price
    s._get_vol_m5_for = _get_vol
    s._get_liq_for = None  # LIQ_DRAIN_MODE forced off in tests

    # Fake shared_client()._run_fetch -> guard returns the raw price unchanged
    # (identity), so priced[pkey] == the address-keyed raw price. This isolates
    # the FETCH-parallelism contract from the guard's internal logic.
    import feeds.dexscreener_client as dsc

    class _FakeClient:
        async def _run_fetch(self, fn, *args, **kwargs):
            # args = (guard, pkey, raw); return raw (identity guard).
            return args[2]

    s._exit_price_guard = object()
    return dsc, _FakeClient


# ---------------------------------------------------------------------------
# (1) OFF-mode: serial, fetch-once-per-unique-token, decisions in order.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_off_mode_fetches_once_per_unique_token(monkeypatch):
    monkeypatch.delenv("PARALLEL_TICK_MODE", raising=False)
    monkeypatch.setenv("LIQ_DRAIN_MODE", "off")

    # bot0 + bot1 BOTH hold token A (same address) -> A must be fetched ONCE.
    pA1 = _FakePos("AAA", "A")
    pB = _FakePos("BBB", "B")
    pA2 = _FakePos("AAA", "A")
    pm0 = _FakePM([pA1, pB])
    pm1 = _FakePM([pA2])
    s = _bare_scanner([pm0, pm1])

    fetch_log = []
    dsc, FakeClient = _wire_price_fetch(
        s, {"aaa": 1.0, "bbb": 2.0}, fetch_log=fetch_log)
    monkeypatch.setattr(dsc, "shared_client", lambda: FakeClient())

    await s._tick_all_bots_positions()

    # Deduped by address: A fetched once, B fetched once.
    assert sorted(fetch_log) == ["aaa", "bbb"], fetch_log
    # Every position ticked with its OWN token's price (no cross-mismatch).
    assert pm0.tick_calls == [("A", 1.0, 1234.0), ("B", 2.0, 1234.0)]
    assert pm1.tick_calls == [("A", 1.0, 1234.0)]


# ---------------------------------------------------------------------------
# (2) ON-mode: fetches run CONCURRENTLY (peak > 1) and are bounded.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_mode_fetches_concurrently_and_bounded(monkeypatch):
    monkeypatch.setenv("PARALLEL_TICK_MODE", "on")
    monkeypatch.setenv("PARALLEL_TICK_CONCURRENCY", "4")
    monkeypatch.setenv("LIQ_DRAIN_MODE", "off")

    positions = [_FakePos(f"T{i:02d}", f"S{i}") for i in range(20)]
    pm = _FakePM(positions)
    s = _bare_scanner([pm])

    conc = {"running": 0, "peak": 0}
    prices = {f"t{i:02d}": float(i + 1) for i in range(20)}
    dsc, FakeClient = _wire_price_fetch(
        s, prices, fetch_delay=0.01, conc_state=conc)
    monkeypatch.setattr(dsc, "shared_client", lambda: FakeClient())

    await s._tick_all_bots_positions()

    # Concurrency actually happened...
    assert conc["peak"] > 1, "fetches did not run concurrently in on-mode"
    # ...but stayed within the bound.
    assert conc["peak"] <= 4, f"concurrency exceeded bound (peak={conc['peak']})"


# ---------------------------------------------------------------------------
# (3) ON-mode applies the SAME exit decisions per position as OFF-mode,
#     address-keyed prices, and NO double-sell.
# ---------------------------------------------------------------------------

def _build(prices):
    # Two bots; bot0 holds A,B,C; bot1 holds B (shared) + D. C fires a STOP sell.
    pos = {
        "A": _FakePos("AAA", "A"),
        "B": _FakePos("BBB", "B"),
        "C": _FakePos("CCC", "C"),
        "Bb": _FakePos("BBB", "B"),
        "D": _FakePos("DDD", "D"),
    }
    pm0 = _FakePM([pos["A"], pos["B"], pos["C"]],
                  decisions_by_token={"C": [_Decision("stop")]})
    pm1 = _FakePM([pos["Bb"], pos["D"]],
                  decisions_by_token={"D": [_Decision("tp1", 0.75)]})
    return pm0, pm1


async def _run_mode(monkeypatch, mode):
    monkeypatch.setenv("PARALLEL_TICK_MODE", mode)
    monkeypatch.setenv("LIQ_DRAIN_MODE", "off")
    prices = {"aaa": 1.5, "bbb": 2.5, "ccc": 0.3, "ddd": 4.0}
    pm0, pm1 = _build(prices)
    s = _bare_scanner([pm0, pm1])
    dsc, FakeClient = _wire_price_fetch(s, prices)
    monkeypatch.setattr(dsc, "shared_client", lambda: FakeClient())
    await s._tick_all_bots_positions()
    return s, pm0, pm1


@pytest.mark.asyncio
async def test_on_and_off_apply_identical_decisions(monkeypatch):
    s_off, off0, off1 = await _run_mode(monkeypatch, "off")
    s_on, on0, on1 = await _run_mode(monkeypatch, "on")

    # (3a) Per-position tick calls (token -> price) identical across modes.
    assert on0.tick_calls == off0.tick_calls
    assert on1.tick_calls == off1.tick_calls

    # (4) Address-keyed: each token ticked with ITS OWN price (no mismatch).
    expected0 = [("A", 1.5, 1234.0), ("B", 2.5, 1234.0), ("C", 0.3, 1234.0)]
    expected1 = [("B", 2.5, 1234.0), ("D", 4.0, 1234.0)]
    assert off0.tick_calls == expected0
    assert off1.tick_calls == expected1

    # (5) No double-sell: exactly the fired decisions execute, once each, with
    # the correct address-keyed price — identical set across modes.
    assert sorted(s_off._sells) == sorted(s_on._sells)
    assert sorted(s_off._sells) == [
        ("bot0", "C", "stop", 0.3),
        ("bot1", "D", "tp1", 4.0),
    ]


# ---------------------------------------------------------------------------
# (6) ON-mode with a None price (feed zero/glitch) -> that position is SKIPPED,
#     no sell, exactly as off-mode. (continue on price is None.)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_none_price_skips_position_both_modes(monkeypatch):
    async def run(mode):
        monkeypatch.setenv("PARALLEL_TICK_MODE", mode)
        monkeypatch.setenv("LIQ_DRAIN_MODE", "off")
        prices = {"aaa": None, "bbb": 2.0}  # A feed returns nothing.
        pm = _FakePM([_FakePos("AAA", "A"), _FakePos("BBB", "B")],
                     decisions_by_token={"A": [_Decision("stop")],
                                         "B": [_Decision("stop")]})
        s = _bare_scanner([pm])
        dsc, FakeClient = _wire_price_fetch(s, prices)
        monkeypatch.setattr(dsc, "shared_client", lambda: FakeClient())
        await s._tick_all_bots_positions()
        return s, pm

    s_off, pm_off = await run("off")
    s_on, pm_on = await run("on")

    # A is skipped (price None) in BOTH modes -> only B ticks, only B sells.
    assert pm_off.tick_calls == [("B", 2.0, 1234.0)]
    assert pm_on.tick_calls == [("B", 2.0, 1234.0)]
    assert s_off._sells == [("bot0", "B", "stop", 2.0)]
    assert s_on._sells == [("bot0", "B", "stop", 2.0)]
