"""Legacy PaperSlippageSimulator reconciled onto the fixed-fee model (2026-05-29).

The legacy (trader.py / evm_trader.py) path used to charge base-spread +
0.50% tx_penalty + 1.5-2.5x volatility multiplier + 0.30% min floor, which
over-charged 2-4x and disagreed with the fleet model (core/slippage_model).
After reconciliation both paths use: per_side = market_impact + fixed_fee_pct.
"""
import core.slippage_model as sm
from core.paper_slippage import PaperSlippageSimulator


def _sim():
    return PaperSlippageSimulator("solana")


def test_fee_component_matches_fleet_fixed_fee():
    # The fixed per-tx fee % is the SAME constant the fleet model uses.
    sim = _sim()
    for size in (20.0, 160.0, 650.0):
        est = sim.calculate(size, 50_000, 1.0, "buy")
        assert abs(est.chain_fee_pct - (sm.FEE_USD_PER_TX / size * 100.0)) < 1e-6


def test_no_min_floor_anymore():
    # Old model floored every trade at 0.30%. A big trade in deep liquidity
    # should now cost well under that (fee amortized, impact tiny).
    sim = _sim()
    est = sim.calculate(5000.0, 5_000_000, 1.0, "buy")  # 0.1% of a deep pool
    assert est.total_slippage_pct < 0.30


def test_stop_loss_no_longer_penalized():
    # Volatility multiplier removed — a stop sell costs the same as a normal sell.
    sim = _sim()
    normal = sim.calculate(100.0, 50_000, 1.0, "sell", is_stop_loss=False)
    stop = sim.calculate(100.0, 50_000, 1.0, "sell", is_stop_loss=True)
    assert abs(normal.total_slippage_pct - stop.total_slippage_pct) < 1e-9


def test_no_separate_spread_term():
    sim = _sim()
    est = sim.calculate(100.0, 50_000, 1.0, "buy")
    assert est.base_spread_pct == 0.0


def test_buy_marks_up_sell_marks_down():
    sim = _sim()
    buy = sim.calculate(100.0, 50_000, 1.0, "buy")
    sell = sim.calculate(100.0, 50_000, 1.0, "sell")
    assert buy.adjusted_price > 1.0 > sell.adjusted_price


def test_disabled_flag_zeroes_cost(monkeypatch):
    monkeypatch.setattr(sm, "SLIPPAGE_ENABLED", False)
    sim = _sim()
    est = sim.calculate(20.0, 50_000, 1.0, "buy")
    assert est.total_slippage_pct == 0.0
    assert est.adjusted_price == 1.0
