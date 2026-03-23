# test_axiom_price_feed.py
def test_active_user_spike_logged(caplog):
    """Active user count spike (3x baseline) triggers a WARNING log."""
    import asyncio, logging
    from unittest.mock import MagicMock
    from feeds.axiom_price_feed import AxiomPriceFeed

    auth = MagicMock()
    auth.has_credentials = True
    feed = AxiomPriceFeed(auth_manager=auth)

    # Pre-seed baseline: 3 readings of 100
    feed._user_baseline_window["TESTADDR"] = [100, 100, 100]

    with caplog.at_level(logging.WARNING):
        asyncio.get_event_loop().run_until_complete(
            feed._handle_user_count_update("TESTADDR", "TEST", 320)
        )
    assert "USER SPIKE" in caplog.text
