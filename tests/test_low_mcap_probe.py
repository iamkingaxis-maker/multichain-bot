"""$500k-floor low-mcap probe gating. Default-OFF = zero-op; ON isolates the probe (trades
500k-1M) from production (keeps skipping sub-$1M)."""
from core import low_mcap_probe as lmp


def test_off_is_zero_op():
    assert lmp.keep_below_floor_token(mcap=700_000, liq_usd=99_999, std_min_mcap=1_000_000, probe_on=False) is False
    assert lmp.buy_gate_skip(is_low_mcap_tok=True, is_probe_bot=False, probe_on=False) is False
    assert lmp.buy_gate_skip(is_low_mcap_tok=True, is_probe_bot=True, probe_on=False) is False


def test_is_low_mcap_band():
    assert lmp.is_low_mcap(700_000, std_min_mcap=1_000_000, floor=500_000) is True
    assert lmp.is_low_mcap(400_000, std_min_mcap=1_000_000, floor=500_000) is False   # below floor
    assert lmp.is_low_mcap(1_200_000, std_min_mcap=1_000_000, floor=500_000) is False  # >= fleet floor


def test_discovery_keeps_band_with_liquidity_when_on():
    assert lmp.keep_below_floor_token(700_000, 50_000, 1_000_000, probe_on=True, min_liq=40_000, floor=500_000) is True
    assert lmp.keep_below_floor_token(700_000, 10_000, 1_000_000, probe_on=True, min_liq=40_000, floor=500_000) is False  # low liq
    assert lmp.keep_below_floor_token(400_000, 50_000, 1_000_000, probe_on=True, min_liq=40_000, floor=500_000) is False  # below floor
    assert lmp.keep_below_floor_token(2_000_000, 50_000, 1_000_000, probe_on=True, floor=500_000) is False  # above fleet floor


def test_buy_gate_probe_trades_lowmcap_only():
    assert lmp.buy_gate_skip(is_low_mcap_tok=True, is_probe_bot=True, probe_on=True) is False   # buy low-mcap
    assert lmp.buy_gate_skip(is_low_mcap_tok=False, is_probe_bot=True, probe_on=True) is True    # skip normal


def test_buy_gate_production_skips_lowmcap():
    assert lmp.buy_gate_skip(is_low_mcap_tok=True, is_probe_bot=False, probe_on=True) is True
    assert lmp.buy_gate_skip(is_low_mcap_tok=False, is_probe_bot=False, probe_on=True) is False
