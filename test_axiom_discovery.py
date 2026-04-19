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


@pytest.mark.asyncio
async def test_worker_proxy_fallback_parses_response(monkeypatch):
    """When direct calls all fail, fetch should hit the Worker /rest-proxy
    and normalize its JSON body into DexScreener pair dicts."""
    from feeds import axiom_discovery as mod

    monkeypatch.setenv("AXIOM_REFRESH_RELAY_URL", "https://w.example.dev/refresh")
    monkeypatch.setenv("AXIOM_REFRESH_RELAY_SECRET", "s3cret")

    direct_calls = {"count": 0}
    worker_calls = {"count": 0, "url": None, "payload": None}

    class _FakeResp:
        def __init__(self, status, data=None, is_json=True):
            self.status = status
            self._data = data
            self._is_json = is_json

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self, content_type=None):
            return self._data

    class _FakeSession:
        def __init__(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def get(self, url, headers=None, timeout=None):
            direct_calls["count"] += 1
            return _FakeResp(526)  # Cloudflare rejection — all servers 5xx

        def post(self, url, json=None, timeout=None):
            worker_calls["count"] += 1
            worker_calls["url"] = url
            worker_calls["payload"] = json
            # Worker returns an array of Axiom records directly
            return _FakeResp(200, [
                {"mint": "TOK1", "symbol": "ONE", "priceUsd": "1"},
                {"mint": "TOK2", "symbol": "TWO", "priceUsd": "2"},
            ])

    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda *a, **kw: _FakeSession())

    auth = MagicMock()
    auth.auth_token = "JWT"
    out = await fetch_axiom_trending_pairs(auth)

    assert direct_calls["count"] == 3  # tried all 3 direct servers
    assert worker_calls["count"] == 1
    assert worker_calls["url"] == "https://w.example.dev/rest-proxy"
    assert worker_calls["payload"]["secret"] == "s3cret"
    assert worker_calls["payload"]["cookie"] == "auth-access-token=JWT"
    assert worker_calls["payload"]["path"].startswith("/users-trending-v2")
    assert [p["baseToken"]["address"] for p in out] == ["TOK1", "TOK2"]


@pytest.mark.asyncio
async def test_worker_proxy_skipped_when_env_not_set(monkeypatch):
    from feeds import axiom_discovery as mod

    monkeypatch.delenv("AXIOM_REFRESH_RELAY_URL", raising=False)
    monkeypatch.delenv("AXIOM_REFRESH_RELAY_SECRET", raising=False)

    class _FakeResp:
        status = 526
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def json(self, content_type=None): return {}

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, *a, **kw): return _FakeResp()
        def post(self, *a, **kw):
            raise AssertionError("worker should not be called without env vars")

    monkeypatch.setattr(mod.aiohttp, "ClientSession", lambda *a, **kw: _FakeSession())

    auth = MagicMock()
    auth.auth_token = "JWT"
    out = await fetch_axiom_trending_pairs(auth)
    assert out == []
