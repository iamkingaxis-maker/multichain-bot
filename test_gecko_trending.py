from feeds.gecko_ohlcv import GeckoTerminalClient


def test_parse_trending_maps_fields():
    sample = {
        "data": [
            {
                "id": "solana_POOL1",
                "attributes": {
                    "name": "FOO / SOL",
                    "address": "POOL1",
                    "base_token_price_usd": "0.00123",
                    "reserve_in_usd": "42000.50",
                    "fdv_usd": "1200000",
                    "volume_usd": {"m5": 5000, "h1": 100_000, "h6": 400_000, "h24": 900_000},
                    "price_change_percentage": {"m5": -1.5, "h1": 3.0, "h6": 10.0, "h24": 25.0},
                    "pool_created_at": "2026-04-19T10:00:00Z",
                },
                "relationships": {
                    "base_token": {"data": {"id": "solana_FOO"}},
                },
            }
        ],
        "included": [
            {
                "id": "solana_FOO",
                "attributes": {"address": "FOO", "symbol": "FOO"},
            }
        ],
    }
    out = GeckoTerminalClient._parse_trending(sample)
    assert len(out) == 1
    p = out[0]
    assert p["chainId"] == "solana"
    assert p["baseToken"]["address"] == "FOO"
    assert p["baseToken"]["symbol"] == "FOO"
    assert p["pairAddress"] == "POOL1"
    assert p["liquidity"]["usd"] == 42000.50
    assert p["volume"]["m5"] == 5000
    assert p["priceChange"]["h24"] == 25.0
    assert p["pairCreatedAt"] > 0
    assert p["_source"] == "geckoterminal"


def test_parse_trending_falls_back_to_id_for_address():
    # No included block — address must come from base_token_id after underscore
    sample = {
        "data": [{
            "id": "solana_X",
            "attributes": {
                "address": "POOL",
                "base_token_price_usd": "1",
                "reserve_in_usd": "1000",
                "volume_usd": {},
                "price_change_percentage": {},
            },
            "relationships": {"base_token": {"data": {"id": "solana_ABCDEF"}}},
        }]
    }
    out = GeckoTerminalClient._parse_trending(sample)
    assert out and out[0]["baseToken"]["address"] == "ABCDEF"


def test_parse_trending_drops_evm_addresses():
    sample = {
        "data": [{
            "id": "solana_X",
            "attributes": {"address": "P", "volume_usd": {}, "price_change_percentage": {}},
            "relationships": {"base_token": {"data": {"id": "solana_0xdeadbeef"}}},
        }]
    }
    assert GeckoTerminalClient._parse_trending(sample) == []
