"""Units for the P7 regime dial (2026-06-10): signal math, asymmetric
enforcement, exemptions."""
import os
import sys
import pathlib
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.regime_dial import RegimeDial, _dial_mode


def _mk_trades(yesterday_wins, yesterday_losses, today=None):
    """Synthesize store sells: yesterday (CT) with given win/loss counts."""
    now = datetime.now(timezone.utc)
    y = now - timedelta(days=1)
    out = []
    for i in range(yesterday_wins):
        out.append({"type": "sell", "time": y.isoformat(), "pnl": 2.0, "reason": "tp"})
    for i in range(yesterday_losses):
        out.append({"type": "sell", "time": y.isoformat(), "pnl": -2.0, "reason": "stop"})
    for t in (today or []):
        out.append(t)
    return out


def test_bad_yesterday_halves_defense():
    d = RegimeDial()
    # yesterday WR 40% (<55%) -> m1=0.5 -> defense 0.5
    d.set_trades_provider(lambda: _mk_trades(8, 12))
    cur = d._compute()
    assert cur["signals"]["m_yesterday"] == 0.5
    assert cur["mult_defense"] == 0.5


def test_consensus_upside_is_shadow_only():
    d = RegimeDial()
    # ALL THREE signals good (consensus): yesterday WR 80%, today first-quarter
    # 100% wins, rolling-20 ev +2 -> mult_full=1.5 BUT defense stays capped 1.0
    now = datetime.now(timezone.utc)
    today = [{"type": "sell", "time": now.isoformat(), "pnl": 2.0, "reason": "tp"}
             for _ in range(20)]
    d.set_trades_provider(lambda: _mk_trades(16, 4, today=today))
    cur = d._compute()
    assert cur["mult_full"] == 1.5
    assert cur["mult_defense"] == 1.0


def test_single_good_signal_is_not_consensus():
    d = RegimeDial()
    # only yesterday is good -> full stays 1.0 (upsize needs agreement)
    d.set_trades_provider(lambda: _mk_trades(16, 4))
    cur = d._compute()
    assert cur["signals"]["m_yesterday"] == 1.5
    assert cur["mult_full"] == 1.0


def test_rolling_expectancy_catches_loss_size_days():
    d = RegimeDial()
    now = datetime.now(timezone.utc)
    # today: 20 closes, WR 50% but avg -$2.5 (loss-size day) -> m3=0.5
    today = []
    for i in range(10):
        today.append({"type": "sell", "time": now.isoformat(), "pnl": 1.0, "reason": "tp"})
        today.append({"type": "sell", "time": now.isoformat(), "pnl": -6.0, "reason": "stop"})
    d.set_trades_provider(lambda: _mk_trades(12, 8, today=today))
    cur = d._compute()
    assert cur["signals"]["m_rolling"] == 0.5
    assert cur["mult_defense"] == 0.5


def test_no_provider_is_neutral():
    d = RegimeDial()
    cur = d._compute()
    assert cur["mult_defense"] == 1.0


def test_mode_off_neutralizes_enforcement():
    os.environ["REGIME_DIAL_MODE"] = "shadow"
    try:
        d = RegimeDial()
        d.set_trades_provider(lambda: _mk_trades(8, 12))
        assert d.defense_multiplier() == 1.0   # shadow: compute but don't act
    finally:
        os.environ.pop("REGIME_DIAL_MODE", None)
    assert _dial_mode() == "enforce"
