"""Dev-wallet RPC cost controls (2026-06-20).

ROOT CAUSE: DevWalletTracker.get_features() fired ~12 SERIAL Solana RPC calls
per new/stale token (8s timeout each) under a 5-min TTL — dev_wallet_rpc=15.8s
PER TOKEN on the main scan. The #1 Railway CPU/egress/memory(OOM) driver.

This module pins the cost-control contract:
  1. Longer env-tunable TTL (DEV_WALLET_BASELINE_TTL_SECS, default 3600).
     A token seen within TTL serves cached with ZERO _rpc_call.
  2. Bounded RPC: per-call timeout env-tunable (DEV_WALLET_RPC_TIMEOUT_S);
     fail-fast on timeout/429 -> {} (fail-open).
  3. Per-cycle refresh cap (DEV_WALLET_MAX_REFRESH_PER_CYCLE, default 15):
     the N+1th cold token in a cycle serves fail-open (NO RPC), picked up
     next cycle after reset_cycle().

filter_dev_dumping preservation: it reads dev_pct_remaining; a cached hit
still populates that key, so the rug filter survives the longer TTL.
"""
import asyncio
import importlib
import time

import feeds.dev_wallet as dw


# ──────────────────────────────────────────────────────────────────────
# (d) env knobs parse with safe defaults
# ──────────────────────────────────────────────────────────────────────
def test_env_knobs_safe_defaults(monkeypatch):
    """No env set -> safe defaults (1h TTL, 3.0s timeout, 15/cycle, 4 conc)."""
    for k in (
        "DEV_WALLET_BASELINE_TTL_SECS",
        "DEV_WALLET_RPC_TIMEOUT_S",
        "DEV_WALLET_MAX_REFRESH_PER_CYCLE",
        "DEV_WALLET_RPC_CONCURRENCY",
    ):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(dw)
    assert dw._baseline_ttl_secs() == 3600.0
    assert dw._rpc_timeout_s() == 3.0
    assert dw._max_refresh_per_cycle() == 15
    assert dw._rpc_concurrency() >= 1


def test_env_knobs_parse(monkeypatch):
    monkeypatch.setenv("DEV_WALLET_BASELINE_TTL_SECS", "7200")
    monkeypatch.setenv("DEV_WALLET_RPC_TIMEOUT_S", "1.5")
    monkeypatch.setenv("DEV_WALLET_MAX_REFRESH_PER_CYCLE", "3")
    monkeypatch.setenv("DEV_WALLET_RPC_CONCURRENCY", "8")
    importlib.reload(dw)
    assert dw._baseline_ttl_secs() == 7200.0
    assert dw._rpc_timeout_s() == 1.5
    assert dw._max_refresh_per_cycle() == 3
    assert dw._rpc_concurrency() == 8


def test_env_knobs_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("DEV_WALLET_BASELINE_TTL_SECS", "not-a-number")
    monkeypatch.setenv("DEV_WALLET_MAX_REFRESH_PER_CYCLE", "")
    importlib.reload(dw)
    assert dw._baseline_ttl_secs() == 3600.0
    assert dw._max_refresh_per_cycle() == 15


# ──────────────────────────────────────────────────────────────────────
# (a) within-TTL token serves cached with ZERO _rpc_call
# ──────────────────────────────────────────────────────────────────────
def test_within_ttl_serves_cached_zero_rpc(monkeypatch):
    """Token seen 40min ago (< 1h TTL) serves cached with ZERO _rpc_call."""
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)

    calls = {"n": 0}

    async def _spy_rpc(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(dw, "_rpc_call", _spy_rpc)

    now = time.time()
    baselines = {
        "MINT_TTL": {
            "dev_wallet_addr": "DevA",
            "baseline_pct_supply": 30.0,
            "baseline_ts": now - 1800,
            "last_seen_ts": now - 40 * 60,   # 40 min ago, well within 1h
            "last_pct_supply": 25.0,
        }
    }
    out = asyncio.run(dw.fetch_dev_features("MINT_TTL", baselines, cache_only=False))
    assert out["dev_features_source"] == "cache"
    assert out["dev_pct_remaining"] == 25.0   # filter_dev_dumping key populated
    assert calls["n"] == 0


def test_old_5min_baseline_now_within_longer_ttl(monkeypatch):
    """A baseline that was STALE under the old 300s TTL is now warm under 1h."""
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)
    calls = {"n": 0}

    async def _spy_rpc(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(dw, "_rpc_call", _spy_rpc)
    now = time.time()
    baselines = {
        "M": {
            "dev_wallet_addr": "D",
            "baseline_pct_supply": 10.0,
            "baseline_ts": now - 4000,
            "last_seen_ts": now - 400,   # 400s: stale under old 300s, warm under 1h
            "last_pct_supply": 9.0,
        }
    }
    out = asyncio.run(dw.fetch_dev_features("M", baselines, cache_only=False))
    assert out["dev_features_source"] == "cache"
    assert calls["n"] == 0


# ──────────────────────────────────────────────────────────────────────
# (b) stale/new token refreshes AND respects the per-cycle cap
# ──────────────────────────────────────────────────────────────────────
def test_stale_token_refreshes_and_per_cycle_cap(monkeypatch):
    """Cold tokens refresh (RPC fires) up to the cap; the N+1th does NOT."""
    monkeypatch.setenv("DEV_WALLET_MAX_REFRESH_PER_CYCLE", "2")
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)

    refreshed = []

    async def _fake_supply(session, mint):
        return 1_000_000.0

    async def _fake_identify(session, mint, supply):
        refreshed.append(mint)
        return ("DevOwner_" + mint, 40.0)

    monkeypatch.setattr(dw, "_get_token_supply", _fake_supply)
    monkeypatch.setattr(dw, "_identify_dev_wallet", _fake_identify)

    t = dw.DevWalletTracker.__new__(dw.DevWalletTracker)
    t._baselines = {}
    t._updates_since_save = 0
    t._save_every = 10_000
    t.reset_cycle()

    # 3 cold tokens, cap=2 -> first 2 refresh, 3rd fails-open this cycle.
    o1 = asyncio.run(t.get_features("COLD1", cache_only=False))
    o2 = asyncio.run(t.get_features("COLD2", cache_only=False))
    o3 = asyncio.run(t.get_features("COLD3", cache_only=False))

    assert o1.get("dev_features_source") == "rpc"
    assert o2.get("dev_features_source") == "rpc"
    assert o3 == {}                       # N+1th capped -> fail-open
    assert refreshed == ["COLD1", "COLD2"]

    # Next cycle: cap resets, COLD3 can refresh.
    t.reset_cycle()
    o3b = asyncio.run(t.get_features("COLD3", cache_only=False))
    assert o3b.get("dev_features_source") == "rpc"
    assert "COLD3" in refreshed


def test_cap_does_not_count_warm_cache_hits(monkeypatch):
    """Warm cache hits do NOT consume the per-cycle refresh budget."""
    monkeypatch.setenv("DEV_WALLET_MAX_REFRESH_PER_CYCLE", "1")
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)

    refreshed = []

    async def _fake_supply(session, mint):
        return 1_000_000.0

    async def _fake_identify(session, mint, supply):
        refreshed.append(mint)
        return ("Dev_" + mint, 40.0)

    monkeypatch.setattr(dw, "_get_token_supply", _fake_supply)
    monkeypatch.setattr(dw, "_identify_dev_wallet", _fake_identify)

    now = time.time()
    t = dw.DevWalletTracker.__new__(dw.DevWalletTracker)
    t._baselines = {
        "WARM": {
            "dev_wallet_addr": "D",
            "baseline_pct_supply": 50.0,
            "baseline_ts": now,
            "last_seen_ts": now,
            "last_pct_supply": 49.0,
        }
    }
    t._updates_since_save = 0
    t._save_every = 10_000
    t.reset_cycle()

    # Warm hit shouldn't burn budget; then one cold token still gets its refresh.
    asyncio.run(t.get_features("WARM", cache_only=False))
    cold = asyncio.run(t.get_features("COLD", cache_only=False))
    assert cold.get("dev_features_source") == "rpc"
    assert refreshed == ["COLD"]


# ──────────────────────────────────────────────────────────────────────
# (c) timeout / 429 path returns {} fail-open without raising
# ──────────────────────────────────────────────────────────────────────
def test_timeout_returns_empty_fail_open(monkeypatch):
    """A timeout inside the RPC chain -> {} (fail-open), never raises."""
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)

    async def _timeout_supply(session, mint):
        raise asyncio.TimeoutError("simulated RPC timeout")

    monkeypatch.setattr(dw, "_get_token_supply", _timeout_supply)
    out = asyncio.run(dw.fetch_dev_features("MINT_T", {}, cache_only=False))
    assert out == {}


def test_429_returns_none_fail_open(monkeypatch):
    """getTokenLargestAccounts 429 -> _identify returns None -> {} fail-open."""
    monkeypatch.delenv("DEV_WALLET_BASELINE_TTL_SECS", raising=False)
    importlib.reload(dw)

    async def _fake_supply(session, mint):
        return 1_000_000.0

    async def _none_identify(session, mint, supply):
        return None   # mirrors a 429/empty getTokenLargestAccounts

    monkeypatch.setattr(dw, "_get_token_supply", _fake_supply)
    monkeypatch.setattr(dw, "_identify_dev_wallet", _none_identify)
    out = asyncio.run(dw.fetch_dev_features("MINT_429", {}, cache_only=False))
    assert out == {}


def test_rpc_call_uses_env_timeout(monkeypatch):
    """_rpc_call honors DEV_WALLET_RPC_TIMEOUT_S for its ClientTimeout."""
    monkeypatch.setenv("DEV_WALLET_RPC_TIMEOUT_S", "2.5")
    importlib.reload(dw)
    captured = {}

    class _FakeResp:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self): return {"result": "ok"}

    class _FakeSession:
        def post(self, url, json=None, timeout=None):
            captured["timeout"] = timeout
            return _FakeResp()

    res = asyncio.run(dw._rpc_call(_FakeSession(), "getX", []))
    assert res == "ok"
    assert captured["timeout"].total == 2.5
