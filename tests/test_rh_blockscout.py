# tests/test_rh_blockscout.py
"""Unit tests for core/rh_blockscout.py — the cheap Blockscout SHADOW source.

Network is fully mocked (the _get_json chokepoint is monkeypatched). Covers the
distribution math (top10 / hidden-supply / burn / pool classification), the
fail-open contract on API errors, and the per-token cache."""
import unittest

import core.rh_blockscout as bs

TOKEN = "0x" + "22" * 20
POOL = "0x" + "11" * 20
DEAD = bs.DEAD_ADDR


def _holder(addr, value, is_contract=False, is_scam=False, tags=None):
    md = {"tags": [{"slug": t} for t in (tags or [])]} if tags is not None else {}
    return {"value": value,
            "address": {"hash": addr, "is_contract": is_contract,
                        "is_scam": is_scam, "reputation": "ok",
                        "metadata": md}}


class TestCoercers(unittest.TestCase):
    def test_to_int_handles_str_and_int_and_bad(self):
        self.assertEqual(bs._to_int("1000000"), 1000000)
        self.assertEqual(bs._to_int(42), 42)
        self.assertEqual(bs._to_int(None), 0)
        self.assertEqual(bs._to_int("nope"), 0)

    def test_to_float(self):
        self.assertAlmostEqual(bs._to_float("2492439.83"), 2492439.83)
        self.assertIsNone(bs._to_float(None))
        self.assertIsNone(bs._to_float("x"))


class TestNormalizeRows(unittest.TestCase):
    def test_lowercases_and_skips_bad(self):
        items = [
            _holder("0xAbC" + "0" * 37, 100),
            {"value": 5},                       # no address
            "garbage",                          # not a dict
            _holder("0xDEF" + "0" * 37, 7, is_contract=True, tags=["lp"]),
        ]
        rows = bs.normalize_holder_rows(items)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["addr"], ("0xAbC" + "0" * 37).lower())
        self.assertTrue(rows[1]["is_contract"])
        self.assertEqual(rows[1]["tags"], ["lp"])


class TestDistribution(unittest.TestCase):
    def test_basic_split(self):
        # supply 1000: burn 50 (dead), pool 200 (is_contract) + 100 (known pool
        # addr), real holders 300/200/100/50.
        rows = bs.normalize_holder_rows([
            _holder(DEAD, 50),
            _holder("0x" + "cc" * 20, 200, is_contract=True),
            _holder(POOL, 100),                       # known pool addr
            _holder("0x" + "a1" * 20, 300),
            _holder("0x" + "a2" * 20, 200),
            _holder("0x" + "a3" * 20, 100),
            _holder("0x" + "a4" * 20, 50),
        ])
        d = bs.compute_distribution(rows, 1000, pool_addr=POOL)
        self.assertEqual(d["burn_pct"], 5.0)          # 50/1000
        self.assertEqual(d["pool_pct"], 30.0)         # (200+100)/1000
        self.assertEqual(d["top1_pct"], 30.0)         # 300/1000
        self.assertEqual(d["top10_pct"], 65.0)        # (300+200+100+50)/1000
        # hidden = 100 - pool(30) - top10(65) = 5
        self.assertEqual(d["hidden_supply_share_pct"], 5.0)
        self.assertEqual(d["n_holders_ranked"], 4)

    def test_shoulder_11_20(self):
        rows = bs.normalize_holder_rows(
            [_holder("0x" + f"{i:02x}" * 20, 100 - i) for i in range(1, 25)])
        d = bs.compute_distribution(rows, 10000)
        top10 = sum(100 - i for i in range(1, 11)) / 10000 * 100
        shoulder = sum(100 - i for i in range(11, 21)) / 10000 * 100
        self.assertAlmostEqual(d["top10_pct"], round(top10, 2))
        self.assertAlmostEqual(d["shoulder_11_20_pct"], round(shoulder, 2))

    def test_scam_count_and_pool_by_tag(self):
        rows = bs.normalize_holder_rows([
            _holder("0x" + "a1" * 20, 400, is_scam=True),
            _holder("0x" + "b2" * 20, 300, tags=["liquidity-pool"]),  # pool
            _holder("0x" + "a3" * 20, 300),
        ])
        d = bs.compute_distribution(rows, 1000)
        self.assertEqual(d["n_scam_flagged_holders"], 1)
        self.assertEqual(d["pool_pct"], 30.0)         # tagged holder -> pool
        self.assertEqual(d["top1_pct"], 40.0)         # scam still a real holder

    def test_none_on_bad_supply(self):
        rows = bs.normalize_holder_rows([_holder("0x" + "a1" * 20, 5)])
        self.assertIsNone(bs.compute_distribution(rows, 0))
        self.assertIsNone(bs.compute_distribution(rows, None))


class TestFetchFailOpen(unittest.TestCase):
    def setUp(self):
        bs.clear_cache()
        self._orig = bs._get_json

    def tearDown(self):
        bs._get_json = self._orig
        bs.clear_cache()

    def test_meta_fail_open(self):
        def boom(path):
            raise RuntimeError("timeout")
        bs._get_json = boom
        self.assertEqual(bs.fetch_token_meta(TOKEN), {})

    def test_holder_dist_fail_open(self):
        def boom(path):
            raise ValueError("500")
        bs._get_json = boom
        self.assertEqual(
            bs.fetch_holder_distribution(TOKEN, total_supply=1000), {})

    def test_stamp_never_raises_returns_null(self):
        def boom(path):
            raise RuntimeError("down")
        bs._get_json = boom
        stamp = bs.blockscout_stamp(TOKEN, pool_addr=POOL)
        self.assertFalse(stamp["bs_source_ok"])
        self.assertIsNone(stamp["bs_top10_pct"])
        # full key set present even on total failure
        self.assertIn("bs_hidden_supply_share_pct", stamp)

    def test_malformed_json_fail_open(self):
        bs._get_json = lambda path: ["not", "a", "dict"]
        self.assertEqual(bs.fetch_token_meta(TOKEN), {})
        self.assertFalse(bs.blockscout_stamp(TOKEN)["bs_source_ok"])


class TestStampAndCache(unittest.TestCase):
    def setUp(self):
        bs.clear_cache()
        self._orig = bs._get_json
        self.calls = []

        def fake(path):
            self.calls.append(path)
            if path.endswith("/holders"):
                return {"items": [
                    _holder(DEAD, 100_000),
                    _holder("0x" + "cc" * 20, 200_000, is_contract=True),
                    _holder("0x" + "a1" * 20, 300_000),
                    _holder("0x" + "a2" * 20, 100_000),
                ]}
            return {"holders_count": "1788", "total_supply": "1000000",
                    "decimals": "18", "volume_24h": "2492439.83",
                    "circulating_market_cap": "1519420.2",
                    "exchange_rate": "0.00158", "reputation": "ok",
                    "symbol": "seedcoin", "name": "watch it grow"}
        bs._get_json = fake

    def tearDown(self):
        bs._get_json = self._orig
        bs.clear_cache()

    def test_full_stamp(self):
        s = bs.blockscout_stamp(TOKEN, pool_addr=POOL)
        self.assertTrue(s["bs_source_ok"])
        self.assertEqual(s["bs_holders_count"], 1788)
        self.assertEqual(s["bs_reputation"], "ok")
        self.assertEqual(s["bs_total_supply"], "1000000")
        self.assertEqual(s["bs_burn_pct"], 10.0)       # 100k/1M
        self.assertEqual(s["bs_pool_pct"], 20.0)       # 200k contract
        self.assertEqual(s["bs_top1_pct"], 30.0)       # 300k
        self.assertEqual(s["bs_top10_pct"], 40.0)      # 300k+100k
        self.assertEqual(s["bs_hidden_supply_share_pct"], 40.0)  # 100-20-40
        self.assertEqual(s["bs_n_scam"], 0)

    def test_cache_reuses_within_ttl(self):
        bs.blockscout_stamp(TOKEN, pool_addr=POOL)
        n_after_first = len(self.calls)
        self.assertGreaterEqual(n_after_first, 2)      # meta + holders
        bs.blockscout_stamp(TOKEN, pool_addr=POOL)
        self.assertEqual(len(self.calls), n_after_first)  # no new network

    def test_cache_bypass(self):
        bs.blockscout_stamp(TOKEN, pool_addr=POOL, use_cache=False)
        n = len(self.calls)
        bs.blockscout_stamp(TOKEN, pool_addr=POOL, use_cache=False)
        self.assertGreater(len(self.calls), n)

    def test_empty_token(self):
        s = bs.blockscout_stamp("")
        self.assertFalse(s["bs_source_ok"])


if __name__ == "__main__":
    unittest.main()
