"""Unit tests for OnchainWsFeed (Task B3).

PURE logic/parse tests with a FAKE websocket -- NO real network.

The async socket I/O is NOT tested here (that's runtime B4). We test the SYNC
decode/plan/handle logic directly:
  - _plan_connections(mints)  -> chunking <=90 subs/conn
  - _handle_account_data(mint, b64) -> cache write / migrated skip / exception-safe
  - run() is a true no-op when ONCHAIN_WS_MODE is off (default).
"""

import base64
import math
import struct

import pytest

from core.onchain_ws_feed import OnchainWsFeed


def _live_curve_bytes(vtok, vsol, complete=False):
    """Build a pump.fun bonding-curve account: 8B disc + 5 u64 LE + bool."""
    disc = b"\x00" * 8
    body = struct.pack(
        "<QQQQQ",
        vtok,            # virtual_token_reserves
        vsol,            # virtual_sol_reserves
        800_000_000_000,  # real_token_reserves
        5_000_000_000,    # real_sol_reserves
        1_000_000_000_000,  # token_total_supply
    )
    flag = b"\x01" if complete else b"\x00"
    return disc + body + flag


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _feed(sol_usd=150.0):
    return OnchainWsFeed(get_sol_usd=lambda: sol_usd)


# --- _plan_connections chunking ---------------------------------------------

def test_plan_connections_chunks_at_90():
    feed = _feed()
    mints = [f"m{i}" for i in range(200)]
    chunks = feed._plan_connections(mints)
    # ceil(200/90) = 3 connections
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [90, 90, 20]
    # every chunk <= 90 subs
    assert all(len(c) <= 90 for c in chunks)
    # no mint lost
    assert sum(len(c) for c in chunks) == 200


def test_plan_connections_empty():
    feed = _feed()
    assert feed._plan_connections([]) == []


# --- notification handling: cache write -------------------------------------

def test_handle_account_data_writes_usd_cache():
    sol_usd = 150.0
    feed = _feed(sol_usd=sol_usd)
    vtok = 1_000_000_000_000_000
    vsol = 30_000_000_000
    raw = _live_curve_bytes(vtok, vsol, complete=False)
    mint = "9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdHPUMP"  # mixed case

    feed._handle_account_data(mint, _b64(raw))

    price_sol = (vsol / 1e9) / (vtok / 1e6)
    expected_usd = price_sol * sol_usd

    key = mint.lower()
    assert key in feed.price_cache
    assert math.isclose(feed.price_cache[key], expected_usd, rel_tol=1e-12)
    assert key in feed.ts
    assert feed.ts[key] > 0
    # address-keyed, lowercased -- original case not present
    assert mint not in feed.price_cache or mint.islower()


def test_get_price_returns_usd_and_ts():
    feed = _feed(sol_usd=100.0)
    raw = _live_curve_bytes(1_000_000_000_000_000, 30_000_000_000)
    mint = "AbCdEf"
    feed._handle_account_data(mint, _b64(raw))
    got = feed.get_price(mint)
    assert got is not None
    usd, ts = got
    assert usd > 0 and ts > 0
    # unknown mint -> None
    assert feed.get_price("nope") is None


# --- migrated / None: no write, counter increments --------------------------

def test_handle_account_data_migrated_skipped():
    feed = _feed()
    # complete=True AND vtok=0 -> migrated
    raw = _live_curve_bytes(0, 0, complete=True)
    mint = "MiGrAtEd"
    before = feed.migrated_skips
    feed._handle_account_data(mint, _b64(raw))
    assert mint.lower() not in feed.price_cache
    assert feed.migrated_skips == before + 1


def test_handle_account_data_zero_usd_not_written():
    # sol_usd 0 -> usd 0 -> not written (usd>0 guard)
    feed = _feed(sol_usd=0.0)
    raw = _live_curve_bytes(1_000_000_000_000_000, 30_000_000_000)
    mint = "ZeRo"
    feed._handle_account_data(mint, _b64(raw))
    assert mint.lower() not in feed.price_cache


# --- exception safety -------------------------------------------------------

def test_handle_account_data_malformed_does_not_crash():
    feed = _feed()
    # not valid base64
    feed._handle_account_data("x", "!!!not-base64!!!")
    # valid base64 but too short to decode
    feed._handle_account_data("y", base64.b64encode(b"short").decode("ascii"))
    # None data
    feed._handle_account_data("z", None)
    # nothing written, no exception
    assert feed.price_cache == {}


def test_handle_account_data_bad_sol_usd_callable_safe():
    def boom():
        raise RuntimeError("sol price source down")

    feed = OnchainWsFeed(get_sol_usd=boom)
    raw = _live_curve_bytes(1_000_000_000_000_000, 30_000_000_000)
    feed._handle_account_data("m", _b64(raw))
    assert feed.price_cache == {}  # caught, no crash


# --- mode off = true no-op (no sockets) -------------------------------------

def test_run_noop_when_mode_off(monkeypatch):
    monkeypatch.delenv("ONCHAIN_WS_MODE", raising=False)
    feed = _feed()

    # If run tries to open a socket the test would need network; instead it must
    # return immediately. We assert via a flag set by run() before any I/O.
    import asyncio
    asyncio.run(feed.run(["m1", "m2"]))
    assert feed.last_run_was_noop is True


def test_run_noop_when_mode_explicit_off(monkeypatch):
    monkeypatch.setenv("ONCHAIN_WS_MODE", "off")
    feed = _feed()
    import asyncio
    asyncio.run(feed.run([f"m{i}" for i in range(120)]))
    assert feed.last_run_was_noop is True
