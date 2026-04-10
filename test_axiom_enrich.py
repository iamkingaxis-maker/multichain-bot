# test_axiom_enrich.py
def test_dev_scoring_high_frequency_deployer_blocked():
    """Dev who launched 10+ tokens in 30 days is blocked."""
    import asyncio, time
    from unittest.mock import MagicMock
    from feeds.axiom_scanner import axiom_enrich_check

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    client.get_holder_data.return_value = []

    now_ms = int(time.time() * 1000)
    day_ms = 86400 * 1000
    # 10 tokens with non-zero liquidity (so dead_count=0 — isolates frequency check)
    client.get_dev_tokens.return_value = [
        {"liquidity": 1000.0, "volume24h": 500.0, "createdAt": now_ms - (i * day_ms * 2)}
        for i in range(10)
    ]

    passed, reason, _ = asyncio.run(
        axiom_enrich_check(auth, "PAIR1", "DEV1")
    )
    assert not passed
    assert "frequent" in reason.lower() or "deployer" in reason.lower()

def test_tracked_wallet_holders_checked():
    """get_holder_data is called twice — once for all holders, once for tracked only."""
    import asyncio
    from unittest.mock import MagicMock, call
    from feeds.axiom_scanner import axiom_enrich_check

    auth = MagicMock()
    client = MagicMock()
    auth.get_client.return_value = client
    client.get_holder_data.side_effect = [
        [{"percentage": 5.0}],   # all holders — concentration OK
        [{"percentage": 2.0}, {"percentage": 1.5}, {"percentage": 1.0}],  # 3 tracked
    ]
    client.get_dev_tokens.return_value = []

    passed, reason, _ = asyncio.run(
        axiom_enrich_check(auth, "PAIR1", "DEV1")
    )
    assert passed
    calls = client.get_holder_data.call_args_list
    assert any(
        c == call("PAIR1", True) or c == call("PAIR1", only_tracked_wallets=True)
        for c in calls
    )
