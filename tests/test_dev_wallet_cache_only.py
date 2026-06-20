"""FAST-WATCH cache-only dev-wallet path (2026-06-20).

The dev-wallet feature fetch on a cache MISS fires a serial chain of up to ~12
Solana RPC calls (8s timeout each) under a global Semaphore(1) — the measured
16-35s fast-watch survivor stall. cache_only=True must NEVER hit the RPC chain:
warm baseline -> cached features; cache miss -> {} (fail-open). The main scan
(cache_only=False) keeps the baseline warm.
"""
import asyncio
import time

import feeds.dev_wallet as dw


def test_cache_only_miss_makes_no_rpc_call(monkeypatch):
    """Cache MISS + cache_only=True -> return {} and NEVER call the RPC layer."""
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("RPC must NOT be called in cache_only fast path")

    # Any RPC entrypoint touched would raise; assert none are.
    monkeypatch.setattr(dw, "_get_token_supply", _boom)
    monkeypatch.setattr(dw, "_identify_dev_wallet", _boom)

    baselines = {}   # empty -> guaranteed cache miss
    out = asyncio.run(dw.fetch_dev_features("MINTXYZ", baselines, cache_only=True))
    assert out == {}           # fail-open, no features
    assert calls["n"] == 0     # zero RPC calls
    assert baselines == {}     # nothing written


def test_cache_only_warm_baseline_returns_cached_features(monkeypatch):
    """Warm baseline (<5min) + cache_only=True -> cached features, still no RPC."""
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("RPC must NOT be called on a warm cache hit")

    monkeypatch.setattr(dw, "_get_token_supply", _boom)
    now = time.time()
    baselines = {
        "MINTXYZ": {
            "dev_wallet_addr": "DevAddr111",
            "baseline_pct_supply": 20.0,
            "baseline_ts": now,
            "last_seen_ts": now,           # fresh -> warm
            "last_pct_supply": 18.0,
        }
    }
    out = asyncio.run(dw.fetch_dev_features("MINTXYZ", baselines, cache_only=True))
    assert out["dev_features_source"] == "cache"
    assert out["dev_wallet_addr"] == "DevAddr111"
    assert out["dev_pct_remaining"] == 18.0
    assert calls["n"] == 0


def test_tracker_get_features_passes_cache_only(monkeypatch):
    """DevWalletTracker.get_features forwards cache_only to fetch_dev_features."""
    seen = {}

    async def _fake_fetch(mint, baselines, cache_only=False, **kwargs):
        seen["cache_only"] = cache_only
        return {}

    monkeypatch.setattr(dw, "fetch_dev_features", _fake_fetch)
    t = dw.DevWalletTracker.__new__(dw.DevWalletTracker)
    t._baselines = {}
    t._updates_since_save = 0
    t._save_every = 20
    asyncio.run(t.get_features("MINTXYZ", cache_only=True))
    assert seen["cache_only"] is True
