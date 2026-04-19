import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone
from core.position_manager import PositionState


def test_position_state_has_scalp_meta_field():
    s = PositionState(
        token_address="A", token_symbol="T", chain_id="solana",
        entry_price=1.0, entry_volume_usd=0.0,
        position_size_usd=200.0, original_size_usd=200.0,
        entry_time=datetime.now(timezone.utc),
        strategy="scalp",
        current_price=1.0, peak_price=1.0,
    )
    assert s.scalp_meta is None
    s.scalp_meta = {"sweep_low": 0.95, "stop_price": 0.94, "tp1_price": 1.10,
                    "entry_close_time": 1700000000}
    assert s.scalp_meta["sweep_low"] == 0.95
