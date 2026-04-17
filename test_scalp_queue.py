# test_scalp_queue.py
import pytest
import pytest_asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from core.scalp_capital import ScalpCapitalManager
from feeds.scalp_queue import ScalpQueue


def make_config(**overrides):
    cfg = MagicMock()
    cfg.scalp_position_usd = 200.0
    cfg.scalp_min_mcap = 1_000_000
    cfg.scalp_min_age_days = 7.0
    cfg.scalp_min_volume_h24 = 200_000
    cfg.scalp_max_watch_candidates = 25
    cfg.scalp_watch_expiry_minutes = 30.0
    cfg.scalp_max_entry_move_pct = 3.0
    cfg.scalp_tick_ratio_min = 0.65
    cfg.scalp_tick_consecutive_min = 3
    cfg.scalp_stop_cooldown_minutes = 30.0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_pair(mcap=2_000_000, age_ms=None, vol_h24=500_000, change_h24=5.0, addr="ADDR1", symbol="TEST", price="0.001"):
    if age_ms is None:
        age_ms = time.time() * 1000 - 10 * 86_400 * 1000  # 10 days ago
    return {
        "baseToken": {"address": addr, "symbol": symbol},
        "marketCap": mcap,
        "pairCreatedAt": age_ms,
        "volume": {"h24": vol_h24},
        "priceChange": {"h24": change_h24},
        "priceUsd": price,
    }


def make_queue(**cfg_overrides):
    trader = MagicMock()
    trader.buy = AsyncMock()
    capital = ScalpCapitalManager()
    cfg = make_config(**cfg_overrides)
    q = ScalpQueue(
        trader=trader,
        axiom_price_feed=None,
        open_positions_ref={},
        scalp_capital=capital,
        config=cfg,
    )
    return q, trader, capital


# ── Quality gate tests ──────────────────────────────────────────

def test_gate_passes_good_pair():
    q, _, _ = make_queue()
    pair = make_pair()
    assert q._passes_quality_gates(pair, "ADDR1") is True


def test_gate_rejects_low_mcap():
    q, _, _ = make_queue()
    pair = make_pair(mcap=500_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_young_pair():
    q, _, _ = make_queue()
    pair = make_pair(age_ms=time.time() * 1000 - 3 * 86_400 * 1000)  # 3 days
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_low_volume():
    q, _, _ = make_queue()
    pair = make_pair(vol_h24=100_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_downtrend():
    q, _, _ = make_queue()
    pair = make_pair(change_h24=-2.0)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_already_in_open_positions():
    q, _, _ = make_queue()
    q.open_positions_ref["ADDR1"] = object()
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_stop_cooldown():
    q, _, _ = make_queue()
    q._stop_cooldowns["ADDR1"] = time.monotonic() + 1000
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_passes_after_cooldown_expires():
    q, _, _ = make_queue()
    q._stop_cooldowns["ADDR1"] = time.monotonic() - 1  # expired
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is True


def test_gate_rejects_when_watch_full():
    q, _, _ = make_queue(scalp_max_watch_candidates=2)
    q._watch["X"] = {}
    q._watch["Y"] = {}
    pair = make_pair(addr="ADDR3")
    assert q._passes_quality_gates(pair, "ADDR3") is False


# ── on_scalp_close tests ────────────────────────────────────────

def test_on_scalp_close_stop_sets_cooldown():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.on_scalp_close("ADDR1", "stop_loss", pnl_usd=-8.0)
    assert "ADDR1" in q._stop_cooldowns
    assert q._stop_cooldowns["ADDR1"] > time.monotonic()


def test_on_scalp_close_tp_no_cooldown():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.on_scalp_close("ADDR1", "scalp_tp2", pnl_usd=7.0)
    assert "ADDR1" not in q._stop_cooldowns


def test_on_scalp_close_updates_capital():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    assert capital.deployed_usd() == 200.0
    q.on_scalp_close("ADDR1", "scalp_tp2", pnl_usd=7.0)
    assert capital.deployed_usd() == 0.0


# ── Watch set pruning tests ─────────────────────────────────────

def test_prune_removes_expired_watches():
    q, _, _ = make_queue(scalp_watch_expiry_minutes=0.001)  # ~0.06s
    q._watch["OLD"] = {"symbol": "OLD", "entry_price": 0.001, "entry_ts": time.monotonic() - 10}
    q._prune_watch_set()
    assert "OLD" not in q._watch


def test_prune_keeps_fresh_watches():
    q, _, _ = make_queue()
    q._watch["NEW"] = {"symbol": "NEW", "entry_price": 0.001, "entry_ts": time.monotonic()}
    q._prune_watch_set()
    assert "NEW" in q._watch


# ── Buy/sell ratio calculation ──────────────────────────────────

def test_buy_sell_ratio_all_buys():
    q, _, _ = make_queue()
    now = time.time()
    apf = MagicMock()
    apf._tick_buffers = {
        "ADDR1": [(now - 1, 0.001), (now - 2, 0.002), (now - 3, 0.003)]
    }
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == 1.0


def test_buy_sell_ratio_mixed():
    q, _, _ = make_queue()
    now = time.time()
    apf = MagicMock()
    # 3 buys (positive price change), 1 sell (negative)
    apf._tick_buffers = {
        "ADDR1": [(now - 1, 0.001), (now - 2, 0.002), (now - 3, 0.003), (now - 4, -0.001)]
    }
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == pytest.approx(0.75)


def test_buy_sell_ratio_empty_buffer():
    q, _, _ = make_queue()
    apf = MagicMock()
    apf._tick_buffers = {}
    ratio = q._get_buy_sell_ratio(apf, "ADDR1", 30)
    assert ratio == 0.0


# ── Tick gate happy-path ────────────────────────────────────────

@pytest.mark.asyncio
async def test_tick_gate_fires_buy_when_all_conditions_met():
    q, trader, capital = make_queue()
    now_wall = time.time()

    # Set up a watched token
    q._watch["ADDR1"] = {"symbol": "TEST", "entry_price": 0.001, "entry_ts": time.monotonic()}

    # Set up axiom price feed mock with all gates passing
    apf = MagicMock()
    apf.price_cache = {"ADDR1": 0.001005}  # 0.5% move — under 3% gate
    apf.get_tick_count = MagicMock(return_value=4)   # > 3 consecutive
    apf.get_tick_trend = MagicMock(return_value=0.01) # positive trend
    # 3 buy ticks (positive), 1 sell — ratio = 0.75 > 0.65
    apf._tick_buffers = {
        "ADDR1": [(now_wall - 1, 0.001), (now_wall - 2, 0.002), (now_wall - 3, 0.003), (now_wall - 4, -0.001)]
    }
    q.axiom_price_feed = apf

    await q._check_tick_gate("ADDR1")

    # Token removed from watch, buy called, capital recorded
    assert "ADDR1" not in q._watch
    trader.buy.assert_awaited_once()
    buy_kwargs = trader.buy.call_args.kwargs
    assert buy_kwargs["strategy"] == "scalp"
    assert buy_kwargs["override_usd"] == 200.0
    assert capital.deployed_usd() == 200.0
