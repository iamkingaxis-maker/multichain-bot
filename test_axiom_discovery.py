import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from feeds.axiom_discovery import fetch_axiom_trending_pairs, _normalize


def test_normalize_maps_fields_to_dexscreener_shape():
    raw = [{
        "mint": "TOK1",
        "symbol": "TEST",
        "priceUsd": "0.5",
        "liquidityUsd": 40_000,
        "marketCap": 200_000,
        "priceChange24h": 12.5,
        "priceChange6h": 4.0,
        "priceChange1h": 1.2,
        "priceChange5m": -0.3,
        "volumeH1": 150_000,
        "volumeM5": 9_000,
        "buysM5": 10,
        "sellsM5": 4,
        "pairCreatedAt": 1700000000000,
        "pairAddress": "POOL1",
    }]
    out = _normalize(raw)
    assert len(out) == 1
    p = out[0]
    assert p["chainId"] == "solana"
    assert p["baseToken"]["address"] == "TOK1"
    assert p["baseToken"]["symbol"] == "TEST"
    assert p["pairAddress"] == "POOL1"
    assert p["liquidity"]["usd"] == 40_000
    assert p["volume"]["m5"] == 9_000
    assert p["priceChange"]["h24"] == 12.5
    assert p["pairCreatedAt"] == 1700000000000
    assert p["_source"] == "axiom"


def test_normalize_drops_evm_and_missing_address():
    raw = [
        {"mint": "0xabc", "priceUsd": 1.0},   # EVM
        {"symbol": "NOADDR", "priceUsd": 1.0},  # no address
        {"mint": "OK", "priceUsd": 1.0},       # good
    ]
    out = _normalize(raw)
    assert [p["baseToken"]["address"] for p in out] == ["OK"]


@pytest.mark.asyncio
async def test_fetch_returns_empty_without_token():
    auth = MagicMock()
    auth.auth_token = ""
    out = await fetch_axiom_trending_pairs(auth)
    assert out == []


@pytest.mark.asyncio
async def test_fetch_returns_empty_when_auth_manager_none():
    out = await fetch_axiom_trending_pairs(None)
    assert out == []
