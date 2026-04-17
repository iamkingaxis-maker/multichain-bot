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
    cfg.scalp_min_mcap = 200_000
    cfg.scalp_min_age_days = 1.0
    cfg.scalp_min_volume_h24 = 75_000
    cfg.scalp_max_watch_candidates = 40
    cfg.scalp_watch_expiry_minutes = 30.0
    cfg.scalp_stop_cooldown_minutes = 30.0
    # Dip-buy gate
    cfg.scalp_min_m5_change_pct = -6.0
    cfg.scalp_max_m5_change_pct = -1.0
    cfg.scalp_min_volume_h1_usd = 30_000
    cfg.scalp_min_m5_buy_ratio = 0.55
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_pair(mcap=2_000_000, age_ms=None, vol_h24=500_000, change_h24=5.0, change_h6=3.0, addr="ADDR1", symbol="TEST", price="0.001", chain_id="solana"):
    if age_ms is None:
        age_ms = time.time() * 1000 - 10 * 86_400 * 1000  # 10 days ago
    return {
        "chainId": chain_id,
        "baseToken": {"address": addr, "symbol": symbol},
        "marketCap": mcap,
        "pairCreatedAt": age_ms,
        "volume": {"h24": vol_h24},
        "priceChange": {"h24": change_h24, "h6": change_h6},
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
    pair = make_pair(mcap=100_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_young_pair():
    q, _, _ = make_queue()
    pair = make_pair(age_ms=time.time() * 1000 - 0.5 * 86_400 * 1000)  # 12h
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_low_volume():
    q, _, _ = make_queue()
    pair = make_pair(vol_h24=50_000)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_h24_downtrend():
    # Tokens in a 24h downtrend bleed against short-term m5 pumps — skip them.
    q, _, _ = make_queue()
    pair = make_pair(change_h24=-2.0)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_h6_downtrend():
    # Even if 24h is positive, a 6h downtrend means current trend is bearish.
    q, _, _ = make_queue()
    pair = make_pair(change_h24=5.0, change_h6=-3.0)
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_non_solana_chain():
    q, _, _ = make_queue()
    pair = make_pair(chain_id="ethereum")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_eth_style_0x_address():
    q, _, _ = make_queue()
    pair = make_pair(addr="0x311935abcdef")
    assert q._passes_quality_gates(pair, "0x311935abcdef") is False


def test_gate_rejects_already_in_open_positions():
    q, _, _ = make_queue()
    q.open_positions_ref["ADDR1"] = object()
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_stop_cooldown():
    q, _, _ = make_queue()
    q._stop_cooldowns["addr1"] = time.monotonic() + 1000
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_rejects_stop_cooldown_case_mismatch():
    # Regression: PM stored cooldown as lowercase (via trader) but scan passes
    # mixed-case DexScreener addresses. Gate must normalize before lookup.
    q, _, _ = make_queue()
    q._stop_cooldowns["zjgjgr9fabc"] = time.monotonic() + 1000
    pair = make_pair(addr="zjGjGR9FABC")
    assert q._passes_quality_gates(pair, "zjGjGR9FABC") is False


def test_gate_rejects_scanner_global_cooldown():
    # Universal cross-strategy cooldown: when scanner._sl_cooldown blocks a token
    # (from any dip_buy/scalp/scanner close), the scalp gate must respect it too.
    q, _, _ = make_queue()
    q.scanner = MagicMock()
    q.scanner._sl_cooldown = {"addr1": time.monotonic() + 1000}
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is False


def test_gate_passes_when_scanner_cooldown_expired():
    # Expired scanner cooldown must not block — entry eligibility resumes cleanly.
    q, _, _ = make_queue()
    q.scanner = MagicMock()
    q.scanner._sl_cooldown = {"addr1": time.monotonic() - 1}
    pair = make_pair(addr="ADDR1")
    assert q._passes_quality_gates(pair, "ADDR1") is True


def test_gate_passes_after_cooldown_expires():
    q, _, _ = make_queue()
    q._stop_cooldowns["addr1"] = time.monotonic() - 1  # expired
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
    assert "addr1" in q._stop_cooldowns  # stored lowercase
    assert q._stop_cooldowns["addr1"] > time.monotonic()


def test_on_scalp_close_tp_no_cooldown():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.on_scalp_close("ADDR1", "scalp_tp2", pnl_usd=7.0)
    assert "addr1" not in q._stop_cooldowns
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


# ── Momentum gate ────────────────────────────────────────────────

def _seed_watch_and_momentum(q, addr="ADDR1", m5=-3.0, h1_vol=50_000, buy_ratio=0.70, txns=20):
    q._watch[addr] = {"symbol": "TEST", "entry_price": 0.001, "entry_ts": time.monotonic()}
    q._pair_momentum[addr.lower()] = {
        "m5_change": m5,
        "h1_vol": h1_vol,
        "m5_buy_ratio": buy_ratio,
        "m5_txns": txns,
        "ts": time.time(),
    }


@pytest.mark.asyncio
async def test_momentum_gate_fires_buy_when_all_conditions_met():
    q, trader, capital = make_queue()

    async def fake_buy(**kwargs):
        q.open_positions_ref[kwargs["token_address"].lower()] = object()
    trader.buy.side_effect = fake_buy

    _seed_watch_and_momentum(q)

    await q._check_momentum_gate("ADDR1")

    assert "ADDR1" not in q._watch
    trader.buy.assert_awaited_once()
    buy_kwargs = trader.buy.call_args.kwargs
    assert buy_kwargs["strategy"] == "scalp"
    assert buy_kwargs["override_usd"] == 200.0
    assert capital.deployed_usd() == 200.0


@pytest.mark.asyncio
async def test_momentum_gate_rejects_not_red_enough():
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q, m5=0.5)  # above -1.0 ceiling — not dipping
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_m5_high == 1
    # Stays on watch — dip may deepen next poll
    assert "ADDR1" in q._watch


@pytest.mark.asyncio
async def test_momentum_gate_drops_on_capitulation():
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q, m5=-12.0)  # below -6.0 floor — falling knife
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_m5_low == 1
    # Capitulating — evicted from watch, don't catch a falling knife
    assert "ADDR1" not in q._watch


@pytest.mark.asyncio
async def test_momentum_gate_rejects_low_h1_volume():
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q, h1_vol=5_000)  # below 30k min
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_vol_h1 == 1


@pytest.mark.asyncio
async def test_momentum_gate_rejects_low_buy_ratio():
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q, buy_ratio=0.40)  # below 0.60 min
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_buy_ratio == 1


@pytest.mark.asyncio
async def test_momentum_gate_no_data_rejects():
    q, trader, _ = make_queue()
    q._watch["ADDR1"] = {"symbol": "TEST", "entry_price": 0.001, "entry_ts": time.monotonic()}
    # No _pair_momentum entry — poll hasn't populated yet
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_no_data == 1


@pytest.mark.asyncio
async def test_momentum_gate_rejects_stale_data():
    # Regression: without a TTL, the same momentum snapshot fired multiple
    # entries on the same addr after a prior stop. Reject data older than 90s.
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q)
    q._pair_momentum["addr1"]["ts"] = time.time() - 300  # 5 min old
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_not_called()
    assert q._mg_no_data == 1


@pytest.mark.asyncio
async def test_momentum_gate_rejects_cooldown_evicts_watch():
    # Regression: scan only checks cooldown when ADDING to watch; the momentum
    # gate must also enforce it so a post-stop re-entry is blocked even if the
    # addr was already in _watch.
    q, trader, _ = make_queue()
    _seed_watch_and_momentum(q, addr="zjGjGR9FABC")
    # PM stored cooldown lowercase (via trader) while scan uses mixed case.
    q._stop_cooldowns["zjgjgr9fabc"] = time.monotonic() + 1000
    await q._check_momentum_gate("zjGjGR9FABC")
    trader.buy.assert_not_called()
    assert "zjGjGR9FABC" not in q._watch


@pytest.mark.asyncio
async def test_momentum_gate_skips_record_open_when_buy_silently_fails():
    """trader.buy() silently returns on kill switch, LP unlock, etc. — don't leak slot."""
    q, trader, capital = make_queue()
    _seed_watch_and_momentum(q)
    # trader.buy returns without populating open_positions_ref (silent failure)
    await q._check_momentum_gate("ADDR1")
    trader.buy.assert_awaited_once()
    assert capital.deployed_usd() == 0.0
    assert "ADDR1" not in capital._open


def test_reconcile_drops_phantom_slots():
    q, _, capital = make_queue()
    capital.record_open("PHANTOM1", 200.0)
    capital.record_open("PHANTOM2", 200.0)
    capital.record_open("REAL1", 200.0)
    q.open_positions_ref["real1"] = object()  # lowercase — matches trader convention

    q._reconcile_open_slots()

    assert "PHANTOM1" not in capital._open
    assert "PHANTOM2" not in capital._open
    assert "REAL1" in capital._open
    assert capital.deployed_usd() == 200.0


def test_reconcile_noop_when_no_phantoms():
    q, _, capital = make_queue()
    capital.record_open("ADDR1", 200.0)
    q.open_positions_ref["addr1"] = object()
    q._reconcile_open_slots()
    assert capital.deployed_usd() == 200.0
