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

    passed, reason = asyncio.run(
        axiom_enrich_check(auth, "PAIR1", "DEV1")
    )
    assert not passed
    assert "frequent" in reason.lower() or "deployer" in reason.lower()
