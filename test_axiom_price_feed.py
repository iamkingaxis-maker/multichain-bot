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
        asyncio.run(feed._handle_user_count_update("TESTADDR", "TEST", 320))
    assert "USER SPIKE" in caplog.text


def test_price_feed_stores_volume_and_liquidity():
    """Price update handler stores volume_usd and liquidity_usd alongside price."""
    import asyncio
    from unittest.mock import MagicMock
    from feeds.axiom_price_feed import AxiomPriceFeed

    auth = MagicMock()
    feed = AxiomPriceFeed(auth_manager=auth)

    price_data = {
        "priceUsd": 0.001,
        "volume": 50000,
        "liquidity": 25000,
        "priceChange": 12.5,
    }
    asyncio.run(feed._handle_price_update("ADDR123", "TEST", price_data))

    assert feed.price_cache["addr123"] == 0.001
    assert feed.volume_cache["addr123"] == 50000
    assert feed.liquidity_cache["addr123"] == 25000
    assert feed.change_cache["addr123"] == 12.5
