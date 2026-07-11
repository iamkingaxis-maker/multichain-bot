"""Unit tests for scripts/rh_chain_feed.py pure decode helpers (task #494).

NO network: swap-log -> tape-row decoding, buy/sell classification for both
token0/token1 orderings (V3 signed deltas + V2 in/out amounts), factory
creation-event parsing, slot0 -> ETH/USD math, symbol decoding, dedupe keys,
percentile helper, and the exact rip_tape output schema.
"""
import json

import pytest

from scripts.rh_chain_feed import (
    TOPIC_PAIR_CREATED,
    TOPIC_POOL_CREATED,
    WETH,
    classify_v2_swap,
    classify_v3_swap,
    decode_symbol,
    dedupe_key,
    iso_utc,
    parse_pair_created,
    parse_pool_created,
    pctl,
    sqrtprice_to_eth_usd,
    tape_row,
)

TOKEN = "0x1111111111111111111111111111111111111111"
POOL = "0x2222222222222222222222222222222222222222"
MAKER = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _u256(n: int) -> str:
    return format(n, "064x")


def _i256(n: int) -> str:
    return format(n & (2 ** 256 - 1), "064x")


def _pad_topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:].lower()


# ── V3 Swap classification (signed pool deltas) ──────────────────────────────
class TestClassifyV3:
    def _data(self, amount0: int, amount1: int) -> str:
        # (amount0, amount1, sqrtPriceX96, liquidity, tick)
        return "0x" + _i256(amount0) + _i256(amount1) + _u256(1 << 96) + \
            _u256(10 ** 18) + _i256(-100)

    def test_buy_weth_is_token0(self):
        # pool receives 0.5 WETH (amount0 > 0), sends tokens (amount1 < 0)
        d = self._data(5 * 10 ** 17, -(10 ** 21))
        assert classify_v3_swap(d, weth_is_token0=True) == ("buy", 5 * 10 ** 17)

    def test_sell_weth_is_token0(self):
        d = self._data(-(3 * 10 ** 17), 10 ** 21)
        assert classify_v3_swap(d, weth_is_token0=True) == ("sell", 3 * 10 ** 17)

    def test_buy_weth_is_token1(self):
        # pool receives WETH on the token1 side
        d = self._data(-(10 ** 21), 7 * 10 ** 16)
        assert classify_v3_swap(d, weth_is_token0=False) == ("buy", 7 * 10 ** 16)

    def test_sell_weth_is_token1(self):
        d = self._data(10 ** 21, -(2 * 10 ** 16))
        assert classify_v3_swap(d, weth_is_token0=False) == ("sell", 2 * 10 ** 16)

    def test_zero_weth_delta_is_none(self):
        d = self._data(0, 10 ** 21)
        assert classify_v3_swap(d, weth_is_token0=True) is None

    def test_orientation_flips_kind(self):
        # the SAME log must classify oppositely under the opposite ordering
        d = self._data(10 ** 18, -(10 ** 21))
        assert classify_v3_swap(d, True)[0] == "buy"
        assert classify_v3_swap(d, False)[0] == "sell"


# ── V2 Swap classification (unsigned in/out amounts) ─────────────────────────
class TestClassifyV2:
    def _data(self, a0i: int, a1i: int, a0o: int, a1o: int) -> str:
        return "0x" + _u256(a0i) + _u256(a1i) + _u256(a0o) + _u256(a1o)

    def test_buy_weth_is_token0(self):
        d = self._data(10 ** 18, 0, 0, 5 * 10 ** 20)   # WETH in, token out
        assert classify_v2_swap(d, weth_is_token0=True) == ("buy", 10 ** 18)

    def test_sell_weth_is_token0(self):
        d = self._data(0, 5 * 10 ** 20, 9 * 10 ** 17, 0)  # token in, WETH out
        assert classify_v2_swap(d, weth_is_token0=True) == ("sell", 9 * 10 ** 17)

    def test_buy_weth_is_token1(self):
        d = self._data(0, 4 * 10 ** 17, 10 ** 20, 0)
        assert classify_v2_swap(d, weth_is_token0=False) == ("buy", 4 * 10 ** 17)

    def test_sell_weth_is_token1(self):
        d = self._data(10 ** 20, 0, 0, 6 * 10 ** 17)
        assert classify_v2_swap(d, weth_is_token0=False) == ("sell", 6 * 10 ** 17)

    def test_net_weth_used_when_both_sides_present(self):
        # weird router: 1.0 WETH in AND 0.4 WETH out -> net 0.6 buy
        d = self._data(10 ** 18, 0, 4 * 10 ** 17, 5 * 10 ** 20)
        assert classify_v2_swap(d, weth_is_token0=True) == ("buy", 6 * 10 ** 17)

    def test_zero_net_is_none(self):
        d = self._data(10 ** 18, 0, 10 ** 18, 0)
        assert classify_v2_swap(d, weth_is_token0=True) is None


# ── factory creation events ──────────────────────────────────────────────────
class TestCreationParsing:
    def test_pool_created_v3(self):
        # real shape from the live chain: data = [tickSpacing, pool]
        log = {
            "topics": [TOPIC_POOL_CREATED, _pad_topic(WETH), _pad_topic(TOKEN),
                       "0x" + _u256(10000)],
            "data": "0x" + _u256(200) + "0" * 24 + POOL[2:],
            "blockNumber": hex(5_900_000),
        }
        info = parse_pool_created(log)
        assert info == {"pool": POOL, "token0": WETH, "token1": TOKEN,
                        "fee": 10000, "dex": "v3", "block": 5_900_000}

    def test_pair_created_v2(self):
        # data = [pair, allPairsLength]
        log = {
            "topics": [TOPIC_PAIR_CREATED, _pad_topic(TOKEN), _pad_topic(WETH)],
            "data": "0x" + "0" * 24 + POOL[2:] + _u256(52),
            "blockNumber": hex(5_910_123),
        }
        info = parse_pair_created(log)
        assert info == {"pool": POOL, "token0": TOKEN, "token1": WETH,
                        "fee": None, "dex": "v2", "block": 5_910_123}


# ── ETH/USD from slot0 ───────────────────────────────────────────────────────
class TestEthUsd:
    def test_live_verified_value_weth_token0(self):
        # sqrtPriceX96 read live from the WETH/USDG pool on 2026-07-10 gave
        # $1785.80 (USDG = 6 decimals, WETH = token0)
        p = sqrtprice_to_eth_usd(3348079389020515470009793, True, 6)
        assert p == pytest.approx(1785.80, abs=0.5)

    def test_inverse_orientation(self):
        # stable as token0: price(t1/t0) must invert to the same ETH/USD
        # raw price for 1785.8 USD/ETH with stable(6) token0, WETH token1:
        # raw = weth_atomic/usd_atomic = (1/1785.8) * 10^(18-6)
        raw = (1 / 1785.8) * 10 ** 12
        sqrtp = int((raw ** 0.5) * 2 ** 96)
        p = sqrtprice_to_eth_usd(sqrtp, False, 6)
        assert p == pytest.approx(1785.8, rel=1e-4)


# ── tape row schema (exact Solana rip_tape + lag_secs) ───────────────────────
class TestTapeRow:
    def test_schema_and_math(self):
        row = tape_row(kind="buy", weth_wei=5 * 10 ** 17, eth_price_usd=1785.80,
                       block_ts=1783672089, maker=MAKER, pool=POOL,
                       sym="MEME", seen_ts=1783672090.73)
        assert list(row.keys()) == ["kind", "volume_usd", "ts", "maker",
                                    "pair", "sym", "lag_secs"]
        assert row["kind"] == "buy"
        assert row["volume_usd"] == pytest.approx(892.90, abs=0.01)
        assert row["ts"] == "2026-07-10T08:28:09+00:00"
        assert row["maker"] == MAKER
        assert row["pair"] == POOL
        assert row["sym"] == "MEME"
        assert row["lag_secs"] == pytest.approx(1.73)
        assert "tx" not in row  # per spec: no tx field on disk
        json.dumps(row)  # jsonl-serializable

    def test_iso_utc_format_matches_gt_recorder(self):
        assert iso_utc(0) == "1970-01-01T00:00:00+00:00"


# ── symbol decode ────────────────────────────────────────────────────────────
class TestDecodeSymbol:
    def test_abi_string(self):
        s = "WETH".encode().hex()
        h = "0x" + _u256(32) + _u256(4) + s + "0" * (64 - len(s))
        assert decode_symbol(h) == "WETH"

    def test_bytes32_style(self):
        h = "0x" + "USDG".encode().hex() + "0" * 56
        assert decode_symbol(h) == "USDG"

    def test_garbage_falls_back(self):
        assert decode_symbol("0x") == "?"
        assert decode_symbol("zz-not-hex") == "?"
        assert decode_symbol("0x" + _u256(32) + _u256(10 ** 30)) == "?"


# ── dedupe + percentile ──────────────────────────────────────────────────────
class TestUtils:
    def test_dedupe_key_tx_plus_logindex(self):
        a = dedupe_key({"transactionHash": "0xAB", "logIndex": "0x2"})
        b = dedupe_key({"transactionHash": "0xab", "logIndex": "0x2"})
        c = dedupe_key({"transactionHash": "0xab", "logIndex": "0x3"})
        assert a == b == ("0xab", 2)
        assert a != c

    def test_dedupe_key_int_logindex(self):
        assert dedupe_key({"transactionHash": "0xff", "logIndex": 7}) == ("0xff", 7)

    def test_pctl(self):
        assert pctl([], 0.5) == 0.0
        assert pctl([1.0], 0.95) == 1.0
        vals = sorted([0.5, 1.0, 1.5, 2.0, 9.9])
        assert pctl(vals, 0.5) == 1.5
        assert pctl(vals, 0.95) == 9.9


# ── AGED MODE (phase 2, 2026-07-11): liq-ranked candidate handling ───────────
import os as _os  # noqa: E402

import scripts.rh_chain_feed as mod  # noqa: E402
from scripts.rh_chain_feed import Feed, rank_candidates, rank_watch_keep  # noqa: E402


class TestRankCandidates:
    """Pure aged-mode candidate ordering: promotable knowns -> young
    (newest-first, legacy behavior) -> aged unknowns -> failed aged."""

    def test_tier_ordering(self):
        items = [
            ("failed_aged", 30.0, 100.0),      # checked, below floor
            ("young_new", 1.0, None),
            ("aged_unknown_older", 50.0, None),
            ("promotable_small", 30.0, 6000.0),
            ("young_old", 2.0, None),
            ("aged_unknown", 40.0, None),
            ("promotable_big", 48.0, 90000.0),
        ]
        assert rank_candidates(items, min_liq=5000.0, young_age_h=24.0) == [
            "promotable_big", "promotable_small",     # liq desc
            "young_new", "young_old",                 # newest first (legacy)
            "aged_unknown", "aged_unknown_older",     # audition queue
            "failed_aged",                            # checked & failed: last
        ]

    def test_boundaries(self):
        # liq exactly at the floor = promotable; age exactly at the boundary
        # = aged; unknown age = treated young (fail-open, rare)
        out = rank_candidates([("at_floor", 30.0, 5000.0),
                               ("at_age_boundary", 24.0, None),
                               ("no_age", None, None)],
                              min_liq=5000.0, young_age_h=24.0)
        assert out == ["at_floor", "no_age", "at_age_boundary"]

    def test_young_below_floor_stays_young_not_failed(self):
        # a checked young pool below the floor keeps its newest-first slot
        # (young pools grow liq later — today's feed rechecks them)
        out = rank_candidates([("young_checked_low", 2.0, 100.0),
                               ("aged_checked_low", 30.0, 100.0)],
                              min_liq=5000.0, young_age_h=24.0)
        assert out == ["young_checked_low", "aged_checked_low"]


class TestRankWatchKeep:
    def test_none_quota_is_legacy_top_liq(self):
        items = [("a", 10.0, False), ("b", 30.0, True), ("c", 20.0, False)]
        assert rank_watch_keep(items, 2, aged_max=None) == {"b", "c"}

    def test_aged_quota_protects_young(self):
        # 3 aged pools all out-liq the young ones, but only 2 aged slots
        items = [("a1", 100.0, True), ("a2", 90.0, True), ("a3", 80.0, True),
                 ("y1", 10.0, False), ("y2", 9.0, False), ("y3", 8.0, False)]
        assert rank_watch_keep(items, 4, aged_max=2) == {"a1", "a2",
                                                         "y1", "y2"}

    def test_unused_young_slots_backfilled_by_aged(self):
        items = [("a1", 100.0, True), ("a2", 90.0, True), ("a3", 80.0, True),
                 ("y1", 10.0, False)]
        assert rank_watch_keep(items, 4, aged_max=2) == {"a1", "a2",
                                                         "y1", "a3"}

    def test_unused_aged_slots_go_to_young(self):
        items = [("a1", 100.0, True),
                 ("y1", 10.0, False), ("y2", 9.0, False), ("y3", 8.0, False)]
        assert rank_watch_keep(items, 4, aged_max=2) == {"a1", "y1",
                                                         "y2", "y3"}


class TestAgedModeDefaultsInert:
    def test_defaults_identical_when_env_unset(self):
        for var in ("RH_FEED_MAX_AGE_H", "RH_FEED_CAND_MAX",
                    "RH_FEED_LIQ_PER_CYCLE", "RH_FEED_WATCH_AGED_MAX"):
            if _os.environ.get(var):
                pytest.skip(f"{var} set in env")
        assert mod.AGED_MODE is False        # every aged branch inert
        assert mod.MAX_AGE_H == 24.0
        assert mod.CAND_MAX == 5000          # env-ified, defaults unchanged
        assert mod.LIQ_PER_CYCLE == 25
        assert mod.WATCH_AGED_MAX == mod.WATCH_MAX // 2


def _feed(monkeypatch):
    f = Feed("http://127.0.0.1:1")           # Rpc constructed, never called
    f.latest_block = 1_000_000
    f.latest_ts = 2_000_000
    f.spb = 1.0                               # 1 block/s -> 3600 blocks/hour
    f.eth_price = 2000.0
    return f


def _blk(age_h, latest=1_000_000):
    return int(latest - age_h * 3600)


def _cand(age_h, liq=None):
    c = {"dex": "v3", "weth0": True, "token": "0xt",
         "created_block": _blk(age_h), "fee": 10000}
    if liq is not None:
        c["liq"] = liq
    return c


def _watch(sym, liq, age_h):
    return {"sym": sym, "dex": "v3", "weth0": True, "liq": liq,
            "created_block": _blk(age_h), "seen": set()}


class TestRefillLiqQueueDefaultMode:
    def test_newest_first_and_cap_unchanged(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", False)
        monkeypatch.setattr(mod, "CAND_MAX", 3)
        f = _feed(monkeypatch)
        f.cand = {"p5h": _cand(5.0), "p1h": _cand(1.0), "p3h": _cand(3.0),
                  "p2h": _cand(2.0)}
        f._refill_liq_queue()
        assert set(f.cand) == {"p1h", "p2h", "p3h"}      # newest 3 kept
        assert f.liq_queue == ["p1h", "p2h", "p3h"]      # newest first

    def test_age_prune_at_env_ceiling(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", False)
        f = _feed(monkeypatch)
        f.cand = {"young": _cand(5.0), "old": _cand(25.0)}
        f.watch = {"wold": _watch("W", 50_000.0, 25.0)}
        f._refill_liq_queue()
        assert set(f.cand) == {"young"} and f.watch == {}


class TestRefillLiqQueueAgedMode:
    def test_liq_ranked_queue_and_prune(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", True)
        monkeypatch.setattr(mod, "MAX_AGE_H", 72.0)
        monkeypatch.setattr(mod, "CAND_MAX", 4)
        f = _feed(monkeypatch)
        f.cand = {"aged_promotable": _cand(30.0, liq=90_000.0),
                  "young_new": _cand(1.0),
                  "young_old": _cand(2.0),
                  "aged_unknown": _cand(40.0),
                  "aged_failed": _cand(30.0, liq=100.0),
                  "too_old": _cand(80.0)}                 # > 72h: age-pruned
        f._refill_liq_queue()
        # queue: promotable known -> young newest-first -> aged unknown;
        # cap 4 dropped the checked-and-failed aged pool first
        assert f.liq_queue == ["aged_promotable", "young_new", "young_old",
                               "aged_unknown"]
        assert set(f.cand) == {"aged_promotable", "young_new", "young_old",
                               "aged_unknown"}

    def test_watch_quota_protects_young_universe(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", True)
        monkeypatch.setattr(mod, "MAX_AGE_H", 72.0)
        monkeypatch.setattr(mod, "WATCH_MAX", 4)
        monkeypatch.setattr(mod, "WATCH_AGED_MAX", 2)
        f = _feed(monkeypatch)
        f.watch = {"a1": _watch("A1", 100_000.0, 30.0),
                   "a2": _watch("A2", 90_000.0, 40.0),
                   "a3": _watch("A3", 80_000.0, 50.0),
                   "y1": _watch("Y1", 10_000.0, 2.0),
                   "y2": _watch("Y2", 9_000.0, 3.0),
                   "y3": _watch("Y3", 8_000.0, 4.0)}
        f._refill_liq_queue()
        # aged high-liq pools would evict ALL young under pure liq ranking;
        # the quota keeps the young universe (scalp fleet) alive
        assert set(f.watch) == {"a1", "a2", "y1", "y2"}


class TestFreshPoolQueueJump:
    def _log(self):
        return {"topics": [TOPIC_POOL_CREATED, _pad_topic(WETH),
                           _pad_topic(TOKEN), "0x" + _u256(10000)],
                "data": "0x" + _u256(200) + "0" * 24 + POOL[2:],
                "blockNumber": hex(999_990)}

    def test_aged_mode_fresh_pool_jumps_queue(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", True)
        f = _feed(monkeypatch)
        f.liq_queue = ["0xexisting"]
        f._ingest_creation(self._log())
        assert POOL in f.cand
        assert f.liq_queue[0] == POOL         # checked next cycle

    def test_backfill_flood_does_not_insert(self, monkeypatch):
        # during startup backfill the queue is empty -> no O(n^2) inserts
        monkeypatch.setattr(mod, "AGED_MODE", True)
        f = _feed(monkeypatch)
        f.liq_queue = []
        f._ingest_creation(self._log())
        assert POOL in f.cand and f.liq_queue == []

    def test_default_mode_no_insert(self, monkeypatch):
        monkeypatch.setattr(mod, "AGED_MODE", False)
        f = _feed(monkeypatch)
        f.liq_queue = ["0xexisting"]
        f._ingest_creation(self._log())
        assert POOL in f.cand
        assert f.liq_queue == ["0xexisting"]  # legacy behavior untouched
