# test_axiom_smart_wallet_tracker.py
def test_position_close_detected(caplog):
    """When tracked wallet's open position disappears, logs a close alert."""
    import asyncio, logging
    from unittest.mock import MagicMock, AsyncMock
    from feeds.axiom_smart_wallet_tracker import AxiomSmartWalletTracker

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    # Second call returns empty — position closed
    client.get_meme_open_positions.side_effect = [
        [{"tokenAddress": "TOKEN1", "tokenTicker": "TST"}],
        [],
    ]

    tracker_obj = AxiomSmartWalletTracker(
        auth_manager=auth, trader=MagicMock(),
        signal_evaluator=None, security_checker=None,
        telegram=AsyncMock(), tracker=MagicMock(),
    )
    # Baseline: wallet has TOKEN1 open
    tracker_obj._wallet_positions["WALLET1"] = {"TOKEN1"}

    # First call: TOKEN1 still open
    asyncio.run(tracker_obj._check_wallet_positions("WALLET1"))

    with caplog.at_level(logging.INFO):
        # Second call: TOKEN1 gone
        asyncio.run(tracker_obj._check_wallet_positions("WALLET1"))
    assert "closed position" in caplog.text.lower()
