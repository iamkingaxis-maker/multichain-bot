"""BUILD A (2026-06-17) — slug/quote identity cache persistence across restarts.

These mappings (pool->dex-slug, pool->quote-mint) are stable IDENTITY mappings,
not prices. In-memory only, they were wiped on every restart, forcing a cold
cycle to re-resolve ~190 tokens via the UNTHROTTLED public meta call
(api.dexscreener.com/.../pairs/solana/{pair}). Persisting them to DATA_DIR makes
a restart reuse prior resolutions -> 0 meta calls for already-cached pools.

Guard: behind SLUG_CACHE_PERSIST (default ON). When OFF, no file is written and
behaviour is byte-identical to the prior in-memory-only client.
"""
import asyncio
import importlib
import json
import os


def _fresh(tmp_path, persist="on"):
    os.environ["DATA_DIR"] = str(tmp_path)
    os.environ["SLUG_CACHE_PERSIST"] = persist
    import feeds.dexscreener_client as m
    importlib.reload(m)
    return m


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload


class _Sess:
    """Dummy curl_cffi session — `sess.get` is accessed (as the callable handed
    to _run_fetch) before the (mocked) fetch runs, so it must exist."""
    def get(self, *a, **k):  # pragma: no cover - never actually invoked
        raise AssertionError("real network get must not be called in tests")


def _meta_payload(slug_dex_id="raydium", quote="So11111111111111111111111111111111111111112"):
    return {"pairs": [{"dexId": slug_dex_id, "quoteToken": {"address": quote}}]}


def test_save_load_roundtrip(tmp_path):
    m = _fresh(tmp_path)
    c = m.DexScreenerClient()
    c._slug_cache["POOL_A"] = "solamm"
    c._quote_cache["POOL_A"] = "So11111111111111111111111111111111111111112"
    c._persist_dirty = True
    c._save_slug_cache(force=True)

    path = os.path.join(str(tmp_path), "dexs_slug_quote_cache.json")
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    assert data["slug"]["POOL_A"] == "solamm"
    assert data["quote"]["POOL_A"] == "So11111111111111111111111111111111111111112"

    # New client (simulated restart) reuses the persisted mappings.
    c2 = m.DexScreenerClient()
    assert c2._slug_cache.get("POOL_A") == "solamm"
    assert c2._quote_cache.get("POOL_A") == "So11111111111111111111111111111111111111112"


def test_cold_start_makes_zero_meta_calls_for_cached_pools(tmp_path):
    m = _fresh(tmp_path)

    # Warm a client and resolve one pool via a mocked public meta call.
    warm = m.DexScreenerClient()
    calls = {"n": 0}

    async def fake_run_fetch(fn, *args, **kwargs):
        calls["n"] += 1
        return _Resp(200, _meta_payload())

    warm._run_fetch = fake_run_fetch  # type: ignore[assignment]
    warm._ensure_session = lambda: _Sess()  # avoid curl_cffi

    slug, quote = asyncio.run(warm._resolve_pool_meta("POOL_A"))
    assert slug == "solamm" and quote
    assert calls["n"] == 1  # one cold resolution
    warm._save_slug_cache(force=True)

    # Restart: a brand-new client loads the saved cache; resolving the SAME
    # pool must make ZERO meta calls.
    cold = m.DexScreenerClient()
    cold_calls = {"n": 0}

    async def cold_run_fetch(fn, *args, **kwargs):
        cold_calls["n"] += 1
        return _Resp(200, _meta_payload())

    cold._run_fetch = cold_run_fetch  # type: ignore[assignment]
    cold._ensure_session = lambda: _Sess()

    slug2, quote2 = asyncio.run(cold._resolve_pool_meta("POOL_A"))
    assert slug2 == "solamm" and quote2
    assert cold_calls["n"] == 0  # served entirely from the persisted cache


def test_persist_disabled_writes_no_file(tmp_path):
    m = _fresh(tmp_path, persist="off")
    c = m.DexScreenerClient()
    assert c._persist_enabled is False
    c._slug_cache["POOL_A"] = "solamm"
    c._quote_cache["POOL_A"] = "So11111111111111111111111111111111111111112"
    c._persist_dirty = True
    c._save_slug_cache(force=True)
    path = os.path.join(str(tmp_path), "dexs_slug_quote_cache.json")
    assert not os.path.exists(path)


def test_corrupt_file_falls_back_to_cold(tmp_path):
    path = os.path.join(str(tmp_path), "dexs_slug_quote_cache.json")
    with open(path, "w") as f:
        f.write("{ not valid json")
    m = _fresh(tmp_path)
    c = m.DexScreenerClient()  # must not raise
    assert c._slug_cache == {}
    assert c._quote_cache == {}


def test_save_throttled_until_interval(tmp_path):
    m = _fresh(tmp_path)
    c = m.DexScreenerClient()
    path = os.path.join(str(tmp_path), "dexs_slug_quote_cache.json")

    c._slug_cache["P"] = "solamm"
    c._quote_cache["P"] = "Q"
    c._persist_dirty = True
    c._save_slug_cache(force=True)
    assert os.path.exists(path)

    # A second change without force, inside the throttle window, must NOT rewrite.
    os.remove(path)
    c._slug_cache["P2"] = "meteora"
    c._persist_dirty = True
    c._save_slug_cache(force=False)
    assert not os.path.exists(path)


def test_clean_save_is_noop(tmp_path):
    m = _fresh(tmp_path)
    c = m.DexScreenerClient()
    path = os.path.join(str(tmp_path), "dexs_slug_quote_cache.json")
    # nothing dirty -> no write
    c._save_slug_cache(force=True)
    assert not os.path.exists(path)


def teardown_module(module):
    # Restore import-time module env to defaults so other tests reloading the
    # client don't inherit our temp DATA_DIR / flag.
    os.environ.pop("DATA_DIR", None)
    os.environ.pop("SLUG_CACHE_PERSIST", None)
    import feeds.dexscreener_client as m
    importlib.reload(m)
