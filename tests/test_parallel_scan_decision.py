# -*- coding: utf-8 -*-
"""PARALLEL_SCAN_DECISION_MODE (2026-06-17) — read-only prefetch reordering.

Latency lever: the per-token decision stage in `DipScanner._evaluate_pair`
awaits its heavy read-only network fetches (chart_data, recent_trades, the
Jupiter slippage curve) INLINE, one token at a time. PARALLEL_SCAN_DECISION_MODE
warms those fetches concurrently (bounded) into an ADDRESS-keyed cache BEFORE
the serial decision/buy loop, which then reads the cache instead of awaiting.

HARD SAFETY CONTRACT (this is the buy-firing money path):
  (a) flag OFF  -> cache stays empty -> every lookup misses -> inline-await path
      runs -> survivors / decisions / ORDER are byte-identical to serial.
  (b) prefetch is ADDRESS-keyed (lowercased), never symbol-keyed (symbol
      collisions are a known prior money bug).
  (c) a failed / missing prefetch entry degrades GRACEFULLY to the existing
      inline path — no exception escapes, the buy DECISION is unchanged.
  (d) buys fire in the SAME order + count flag-on vs flag-off on a deterministic
      fixture — no double-fire, no extra buys (firing stays SERIAL under the
      shared _buy_fire_lock + per-cycle bought-address guard).

These tests stub the heavy upstream (`_fetch_candidates`,
`_fetch_cycle_sol_features`) and the per-token clients, so we exercise the REAL
dispatcher + prefetch pass + cache-read/fallback contract without driving the
14k-line evaluation body.
"""
import asyncio
import os
from collections import Counter

import pytest

from feeds.dip_scanner import DipScanner, _PREFETCH_MISS


# ── stubs ────────────────────────────────────────────────────────────────

class _StubChartData:
    def __init__(self, tag):
        self.tag = tag
        self.candles_1m = []
        self.candles_5m = []
        self.candles_15m = []


class _StubGTClient:
    def __init__(self):
        self.chart_calls = []
        self.rt_calls = []

    async def fetch_recent_trades(self, pool_address, limit=30):
        self.rt_calls.append(pool_address)
        return [{"kind": "buy", "volume_usd": 1.0}]


class _StubDexsClient:
    def __init__(self, fail=False, empty=False):
        self.fail = fail
        self.empty = empty
        self.rt_calls = []

    async def fetch_recent_trades(self, pool_address, limit=30):
        self.rt_calls.append(pool_address)
        if self.fail:
            raise RuntimeError("dexs boom")
        if self.empty:
            return []
        return [{"kind": "sell", "volume_usd": 2.0}]


def _bare_scanner(pairs, max_concurrent=99, dexs_client=None, gt_client=None):
    """A DipScanner with only the attrs the dispatcher + prefetch touch."""
    s = DipScanner.__new__(DipScanner)
    s.open_positions_ref = {}
    s.max_concurrent = max_concurrent
    s.max_mcap = 100_000_000
    s.min_mcap = 1_000
    s.position_usd = 500.0
    s.min_age_ms = 0
    s._buy_fire_lock = None
    s._cycle_sol_features = {}
    s._cycle_sol_5m = []
    s._h24_history_dirty = False
    s._save_h24_history = lambda: None
    s._jup_slip_cache = {}
    s._jup_slip_ttl = 90.0
    s._scan_prefetch_cache = {}
    s.gt_client = gt_client if gt_client is not None else _StubGTClient()
    s.dexs_client = dexs_client if dexs_client is not None else _StubDexsClient()

    async def _fetch_candidates():
        return list(pairs), Counter()

    async def _fetch_cycle_sol_features():
        return None

    s._fetch_candidates = _fetch_candidates
    s._fetch_cycle_sol_features = _fetch_cycle_sol_features
    return s


def _pair(addr, sym, m5=0.0, h1=0.0, mcap=100_000, created=1, pair_addr=None):
    return {
        "baseToken": {"address": addr, "symbol": sym},
        "priceChange": {"m5": m5, "h1": h1, "h6": 0.0, "h24": 0.0},
        "marketCap": mcap,
        "liquidity": {"usd": 30_000},
        "pairCreatedAt": created,
        "pairAddress": pair_addr if pair_addr is not None else f"pool_{addr}",
    }


# ── (a) flag OFF => identical survivors / decisions / order ───────────────

@pytest.mark.asyncio
async def test_flag_off_identical_order_and_decisions(monkeypatch):
    """With the flag OFF the prefetch cache stays empty and _evaluate_pair is
    invoked in the exact same pairs order with the exact same telemetry as a
    serial reference run."""
    pairs = [_pair(f"addr{i}", f"T{i}") for i in range(12)]

    def make_eval(order_sink, cache_state_sink):
        async def fake_eval(self, pair, ctx):
            sym = (pair.get("baseToken") or {}).get("symbol")
            order_sink.append(sym)
            # Record whether the cache was empty at decision time.
            cache_state_sink.append(len(self._scan_prefetch_cache))
            await asyncio.sleep(0)
            return Counter({"fetched": 1, "decided": 1}), 1, False
        return fake_eval

    # Serial reference (flag explicitly off).
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_MODE", "off")
    monkeypatch.setenv("PARALLEL_SCAN_MODE", "off")
    ref_order, ref_cache = [], []
    monkeypatch.setattr(DipScanner, "_evaluate_pair", make_eval(ref_order, ref_cache), raising=True)
    s1 = _bare_scanner(pairs)
    await s1._scan_cycle()

    # Default (unset) must also be off => same order.
    monkeypatch.delenv("PARALLEL_SCAN_DECISION_MODE", raising=False)
    def_order, def_cache = [], []
    monkeypatch.setattr(DipScanner, "_evaluate_pair", make_eval(def_order, def_cache), raising=True)
    s2 = _bare_scanner(pairs)
    await s2._scan_cycle()

    expected = [f"T{i}" for i in range(12)]
    assert ref_order == expected, "flag-off order diverged from pairs order"
    assert def_order == expected, "default (unset) order diverged from pairs order"
    # Cache empty for every decision in off-mode (byte-identical fall-through).
    assert all(n == 0 for n in ref_cache), "off-mode populated the prefetch cache"
    assert all(n == 0 for n in def_cache), "default-mode populated the prefetch cache"


# ── (b) prefetch is address-keyed ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_prefetch_is_address_keyed(monkeypatch):
    """The warm pass keys the cache by LOWERCASED address — never by symbol.
    Two DIFFERENT tokens that share a symbol must produce two distinct entries."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)

    # Same SYMBOL "DUP", different addresses (incl. a mixed-case one).
    pairs = [
        _pair("AaaAddrOne", "DUP"),
        _pair("BbbAddrTwo", "DUP"),
    ]
    s = _bare_scanner(pairs)
    await s._prefetch_scan_reads(pairs, now_ms=10_000)

    # Two distinct entries, keyed by lowercased address (NOT collapsed by symbol).
    assert set(s._scan_prefetch_cache.keys()) == {"aaaaddrone", "bbbaddrtwo"}, (
        f"prefetch not address-keyed: {list(s._scan_prefetch_cache.keys())}"
    )
    # Each cached chart_data is the one fetched for its own pool address.
    assert s._scan_prefetch_cache["aaaaddrone"]["chart_data"].tag == "pool_AaaAddrOne"
    assert s._scan_prefetch_cache["bbbaddrtwo"]["chart_data"].tag == "pool_BbbAddrTwo"


@pytest.mark.asyncio
async def test_prefetch_reused_by_evaluate_pair_address_keyed(monkeypatch):
    """A warmed address-keyed entry is the one _evaluate_pair's lookup reads."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)
    pairs = [_pair("MixedCaseAddr", "X")]
    s = _bare_scanner(pairs)
    await s._prefetch_scan_reads(pairs, now_ms=10_000)

    # The dispatcher reads via `_addr_lower` — lowercased lookup must hit.
    entry = s._scan_prefetch_cache.get("mixedcaseaddr")
    assert entry is not None and entry["chart_data"].tag == "pool_MixedCaseAddr"
    # And recent_trades was warmed (DexScreener primary) and address-keyed.
    assert entry["recent_trades"] == [{"kind": "sell", "volume_usd": 2.0}]


# ── (c) failed / missing prefetch degrades without raising ────────────────

@pytest.mark.asyncio
async def test_prefetch_chart_failure_stores_sentinel_no_raise(monkeypatch):
    """assemble_chart_data raising during the warm pass must NOT propagate; the
    entry stores the _PREFETCH_MISS sentinel so _evaluate_pair falls back."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")

    async def _boom(gt_client, pair_addr, dexs_client=None):
        raise RuntimeError("chart boom")

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _boom, raising=True)
    pairs = [_pair("FailAddr", "F")]
    s = _bare_scanner(pairs, dexs_client=_StubDexsClient(fail=True))

    # Must not raise.
    await s._prefetch_scan_reads(pairs, now_ms=10_000)
    entry = s._scan_prefetch_cache["failaddr"]
    assert entry["chart_data"] is _PREFETCH_MISS, "failed chart prefetch did not store the MISS sentinel"
    # dexs failed AND gt fallback returned a value -> recent_trades is the GT list,
    # NOT a miss (mirrors _evaluate_pair's DexScreener-fail -> GT-fallback path).
    assert entry["recent_trades"] == [{"kind": "buy", "volume_usd": 1.0}]


@pytest.mark.asyncio
async def test_recent_trades_total_failure_stores_sentinel(monkeypatch):
    """If BOTH dexs and gt recent-trades fetches fail, the entry is the MISS
    sentinel (so _evaluate_pair degrades to its own inline await) — no raise."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)

    class _GTFail:
        async def fetch_recent_trades(self, pool_address, limit=30):
            raise RuntimeError("gt boom")

    pairs = [_pair("BothFail", "B")]
    s = _bare_scanner(pairs, dexs_client=_StubDexsClient(fail=True), gt_client=_GTFail())
    await s._prefetch_scan_reads(pairs, now_ms=10_000)
    entry = s._scan_prefetch_cache["bothfail"]
    assert entry["recent_trades"] is _PREFETCH_MISS


@pytest.mark.asyncio
async def test_missing_prefetch_entry_means_inline_fallback(monkeypatch):
    """A token with NO prefetch entry (e.g. it was culled from the survivor set,
    or the whole warm pass failed) must be evaluated normally — the dispatcher
    leaves the cache empty for it and _evaluate_pair's `.get()` returns a miss."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_MODE", "on")
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")
    monkeypatch.setenv("PARALLEL_SCAN_MODE", "off")

    # Force the entire prefetch pass to blow up — dispatcher must fail-open.
    async def _boom_prefetch(self, pairs, now_ms):
        raise RuntimeError("prefetch pass exploded")

    monkeypatch.setattr(DipScanner, "_prefetch_scan_reads", _boom_prefetch, raising=True)

    seen = []

    async def fake_eval(self, pair, ctx):
        # The cache must be a usable dict even though the warm pass raised.
        assert isinstance(self._scan_prefetch_cache, dict)
        seen.append((pair.get("baseToken") or {}).get("symbol"))
        return Counter({"fetched": 1}), 0, False

    monkeypatch.setattr(DipScanner, "_evaluate_pair", fake_eval, raising=True)
    pairs = [_pair(f"m{i}", f"T{i}") for i in range(5)]
    s = _bare_scanner(pairs)
    # Must NOT raise despite the prefetch pass exploding.
    await s._scan_cycle()
    assert seen == [f"T{i}" for i in range(5)], "fail-open did not evaluate every pair"


# ── (d) buys fire same order + count flag-on vs flag-off ──────────────────

def _make_buy_recording_eval():
    """Returns a fake _evaluate_pair that emulates the real serial buy-fire
    region: acquire the shared lock, dedup by address, record the fire."""
    async def fake_eval(self, pair, ctx):
        addr_lower = (pair.get("baseToken") or {}).get("address", "").lower()
        async with self._buy_fire_lock:
            if addr_lower in self._cycle_bought_addrs:
                return Counter({"double_buy_guard": 1}), 0, False
            self._cycle_bought_addrs.add(addr_lower)
            # Yield inside the lock to surface any interleave.
            await asyncio.sleep(0)
            self._fire_log.append(addr_lower)
        return Counter({"buy": 1}), 1, False
    return fake_eval


@pytest.mark.asyncio
async def test_buys_same_order_and_count_on_vs_off(monkeypatch):
    """Deterministic fixture: the set/order/count of fired buys is identical
    flag-on vs flag-off, with no double-fire and no extra buys."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")
    monkeypatch.setenv("PARALLEL_SCAN_MODE", "off")  # _evaluate_pair stays serial

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)
    monkeypatch.setattr(DipScanner, "_evaluate_pair", _make_buy_recording_eval(), raising=True)

    pairs = [_pair(f"buy{i}", f"T{i}") for i in range(15)]

    async def run(mode):
        monkeypatch.setenv("PARALLEL_SCAN_DECISION_MODE", mode)
        s = _bare_scanner(pairs)
        s._fire_log = []
        await s._scan_cycle()
        return s._fire_log

    off_fires = await run("off")
    on_fires = await run("on")

    expected = [f"buy{i}" for i in range(15)]
    assert off_fires == expected, f"flag-off fire order wrong: {off_fires}"
    assert on_fires == expected, f"flag-on fire order wrong: {on_fires}"
    assert off_fires == on_fires, "buy order/count diverged on vs off"
    # No double-fire, no extra buys.
    assert len(on_fires) == len(set(on_fires)) == 15


@pytest.mark.asyncio
async def test_no_double_fire_same_address_with_prefetch_on(monkeypatch):
    """Belt-and-suspenders: two pairs sharing an address fire ONE buy even with
    the prefetch warm pass active (the per-cycle bought-address guard holds)."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_MODE", "on")
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")
    monkeypatch.setenv("PARALLEL_SCAN_MODE", "off")

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)
    monkeypatch.setattr(DipScanner, "_evaluate_pair", _make_buy_recording_eval(), raising=True)

    pairs = [_pair("DUPE", "A"), _pair("DUPE", "A")]
    s = _bare_scanner(pairs)
    s._fire_log = []
    await s._scan_cycle()
    assert s._fire_log == ["dupe"], f"double-fire under prefetch: {s._fire_log}"


# ── cheap survivor filter is side-effect-free + a superset ────────────────

@pytest.mark.asyncio
async def test_cheap_survivor_excludes_only_unbypassable_rejects():
    """The first-pass survivor gate excludes ONLY tokens that _evaluate_pair
    would unconditionally skip (no addr, stablecoin, already-open, mcap>max,
    bad age, no pool) — never a token that could reach the fetch stage."""
    s = _bare_scanner([])
    s.open_positions_ref = {"openaddr": object()}

    keep = _pair("goodaddr", "GOOD", mcap=100_000, created=5)
    assert s._cheap_scan_survivor(keep, now_ms=10_000) is True

    assert s._cheap_scan_survivor(_pair("", "NOADDR"), 10_000) is False
    assert s._cheap_scan_survivor(_pair("a", "USDC"), 10_000) is False  # stablecoin symbol
    assert s._cheap_scan_survivor(_pair("openaddr", "OPEN"), 10_000) is False
    assert s._cheap_scan_survivor(_pair("big", "BIG", mcap=10**12), 10_000) is False
    assert s._cheap_scan_survivor(_pair("noage", "NA", created=0), 10_000) is False
    assert s._cheap_scan_survivor(_pair("nopool", "NP", pair_addr=""), 10_000) is False

    # Side-effect-free: no state mutated by the survivor check.
    before = dict(s.__dict__.get("_scan_prefetch_cache", {}))
    s._cheap_scan_survivor(keep, 10_000)
    assert dict(s._scan_prefetch_cache) == before


@pytest.mark.asyncio
async def test_concurrency_clamped_to_16(monkeypatch):
    """PARALLEL_SCAN_DECISION_CONCURRENCY is clamped to <=16 (rate-limit safety):
    even with a huge requested value, the warm pass never runs more than 16
    fetches at once."""
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_CONCURRENCY", "1000")
    monkeypatch.setenv("PARALLEL_SCAN_DECISION_SLIP_WARM", "off")

    state = {"running": 0, "peak": 0}

    async def _assemble(gt_client, pair_addr, dexs_client=None):
        state["running"] += 1
        state["peak"] = max(state["peak"], state["running"])
        await asyncio.sleep(0.002)
        state["running"] -= 1
        return _StubChartData(pair_addr)

    monkeypatch.setattr("feeds.chart_data.assemble_chart_data", _assemble, raising=True)

    pairs = [_pair(f"c{i}", f"T{i}") for i in range(60)]
    s = _bare_scanner(pairs)
    await s._prefetch_scan_reads(pairs, now_ms=10_000)
    assert state["peak"] <= 16, f"concurrency exceeded the 16 clamp (peak={state['peak']})"
