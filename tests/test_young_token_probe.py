"""Young-token probe gating (#4.1). Default-OFF must be a zero-op; ON isolates
young-only probe bots from production (which keeps skipping young tokens)."""
from core import young_token_probe as ytp


def test_off_is_zero_op_discovery_and_buy():
    # probe OFF -> sub-min-age tokens are never kept, and the buy gate never skips
    assert ytp.keep_subminage_token(liq_usd=999_999, probe_on=False) is False
    assert ytp.buy_gate_skip(is_young_tok=True, is_probe_bot=False, probe_on=False) is False
    assert ytp.buy_gate_skip(is_young_tok=True, is_probe_bot=True, probe_on=False) is False


def test_discovery_keeps_young_only_with_liquidity_when_on():
    assert ytp.keep_subminage_token(liq_usd=50_000, probe_on=True, min_liq=40_000) is True
    assert ytp.keep_subminage_token(liq_usd=10_000, probe_on=True, min_liq=40_000) is False  # below floor
    assert ytp.keep_subminage_token(liq_usd=None, probe_on=True, min_liq=40_000) is False


def test_is_young():
    assert ytp.is_young(1.5, max_h=2.0) is True
    assert ytp.is_young(3.0, max_h=2.0) is False
    assert ytp.is_young(None, max_h=2.0) is False


def test_buy_gate_probe_bot_trades_young_only():
    # probe bot: buy young (no skip), skip old
    assert ytp.buy_gate_skip(is_young_tok=True, is_probe_bot=True, probe_on=True) is False
    assert ytp.buy_gate_skip(is_young_tok=False, is_probe_bot=True, probe_on=True) is True


def test_buy_gate_production_bot_skips_young():
    # production bot: skip young (no accidental rug buys), buy old as normal
    assert ytp.buy_gate_skip(is_young_tok=True, is_probe_bot=False, probe_on=True) is True
    assert ytp.buy_gate_skip(is_young_tok=False, is_probe_bot=False, probe_on=True) is False


# ── wiring: _execute_bot_buy honors the young gate (probe ON) ──
import asyncio
import types
from feeds.dip_scanner import DipScanner


class _SpyCap:
    def __init__(self):
        self.reserved = []
        self.daily_pnl_usd = 0.0
    def reserve_for_buy(self, u):
        self.reserved.append(u)
        raise ValueError("stop-after-gate")     # halt cleanly once we pass the gate
    def daily_loss_breached(self, *a):
        return False


def _mk(probe_bot, age):
    ds = DipScanner.__new__(DipScanner)
    pm = types.SimpleNamespace(
        config=types.SimpleNamespace(reentry_cooldown_secs=None, young_token_probe=probe_bot,
                                     daily_loss_limit_usd=None, max_token_buys_per_day=None,
                                     live_probe=False, pool_sizing_derates_enabled=False),
        token_buys_today=lambda *a: 0)
    ds.bot_position_managers = {"b": pm}
    cap = _SpyCap(); ds.bot_capitals = {"b": cap}
    ds.trade_store = None
    ds.trader = types.SimpleNamespace(private_key="")
    ds._token_registry = None
    ds._addr_by_token = {}
    dec = types.SimpleNamespace(bot_id="b", token="T", address="addr", pair_address="pair",
                                entry_price=1.0, size_usd=20.0, size_tier="base", triggers_fired=[])
    bundle = types.SimpleNamespace(raw_meta={"age_hours": age})
    return ds, cap, dec, bundle


def test_wiring_production_bot_skips_young_when_probe_on(monkeypatch):
    monkeypatch.setenv("YOUNG_TOKEN_PROBE", "1")
    ds, cap, dec, bundle = _mk(probe_bot=False, age=1.0)   # young
    asyncio.run(ds._execute_bot_buy(dec, bundle))
    assert cap.reserved == []                              # skipped before reserving


def test_wiring_probe_bot_buys_young_when_probe_on(monkeypatch):
    monkeypatch.setenv("YOUNG_TOKEN_PROBE", "1")
    ds, cap, dec, bundle = _mk(probe_bot=True, age=1.0)    # young
    asyncio.run(ds._execute_bot_buy(dec, bundle))
    assert cap.reserved == [20.0]                          # proceeded past the gate


def test_wiring_probe_bot_skips_old_when_probe_on(monkeypatch):
    monkeypatch.setenv("YOUNG_TOKEN_PROBE", "1")
    ds, cap, dec, bundle = _mk(probe_bot=True, age=100.0)  # old -> probe trades young-only
    asyncio.run(ds._execute_bot_buy(dec, bundle))
    assert cap.reserved == []


def test_wiring_off_is_noop_production_buys_normally(monkeypatch):
    monkeypatch.delenv("YOUNG_TOKEN_PROBE", raising=False)  # probe OFF (default)
    ds, cap, dec, bundle = _mk(probe_bot=False, age=1.0)    # young, but gate is off
    asyncio.run(ds._execute_bot_buy(dec, bundle))
    assert cap.reserved == [20.0]                           # no young gating when off
