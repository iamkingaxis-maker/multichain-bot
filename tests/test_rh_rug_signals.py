# tests/test_rh_rug_signals.py
"""Unit tests for the PURE parts of core/rh_rug_signals.py (no network).

The RPC-side compute_entry_stamp is exercised only for its fail-open contract
(bad rpc object -> stamp with err set, never a raise)."""
import os
import unittest

from core.rh_rug_signals import (
    TOPIC_TRANSFER, TOPIC_V3_MINT, TOPIC_V3_BURN, ZERO_ADDR, DEAD_ADDR,
    replay_transfers, holder_structure, lp_owners_from_events, summarize_lp,
    hidden_supply_readout, assemble_stamp, compute_entry_stamp,
    rug_gate_verdict,
)

A = "0x" + "aa" * 20
B = "0x" + "bb" * 20
C = "0x" + "cc" * 20
POOL = "0x" + "11" * 20
TOKEN = "0x" + "22" * 20


def _pad(addr):
    return "0x" + "0" * 24 + addr[2:].lower()


def _xfer(src, dst, value, block=1, idx=0):
    return {"topics": [TOPIC_TRANSFER, _pad(src), _pad(dst)],
            "data": hex(value), "blockNumber": hex(block),
            "logIndex": hex(idx)}


class TestReplayTransfers(unittest.TestCase):
    def test_mint_transfer_burn(self):
        logs = [
            _xfer(ZERO_ADDR, A, 1000, block=1),          # mint 1000 -> A
            _xfer(A, B, 400, block=2),                    # A -> B 400
            _xfer(B, ZERO_ADDR, 100, block=3),            # B burns 100
        ]
        bal, supply, first_mint = replay_transfers(logs)
        self.assertEqual(supply, 900)
        self.assertEqual(bal[A], 600)
        self.assertEqual(bal[B], 300)
        self.assertEqual(first_mint, {"to": A, "block": 1})

    def test_upto_block_cutoff(self):
        logs = [_xfer(ZERO_ADDR, A, 1000, block=1),
                _xfer(A, B, 999, block=5)]
        bal, supply, _ = replay_transfers(logs, upto_block=4)
        self.assertEqual(bal[A], 1000)
        self.assertNotIn(B, bal)
        self.assertEqual(supply, 1000)

    def test_unsorted_logs_replay_in_block_order(self):
        logs = [_xfer(A, B, 400, block=2),
                _xfer(ZERO_ADDR, A, 1000, block=1)]
        bal, supply, first_mint = replay_transfers(logs)
        self.assertEqual(first_mint["block"], 1)
        self.assertEqual(bal[A], 600)

    def test_skips_undecodable_and_foreign_topics(self):
        logs = [{"topics": [TOPIC_V3_MINT, _pad(A)], "data": "0x10",
                 "blockNumber": "0x1", "logIndex": "0x0"},
                {"topics": [TOPIC_TRANSFER, _pad(ZERO_ADDR)],  # too few topics
                 "data": "0x10", "blockNumber": "0x1", "logIndex": "0x1"},
                {"topics": [TOPIC_TRANSFER, _pad(ZERO_ADDR), _pad(A)],
                 "data": "zz", "blockNumber": "0x2", "logIndex": "0x0"},
                _xfer(ZERO_ADDR, A, 50, block=3)]
        bal, supply, first_mint = replay_transfers(logs)
        self.assertEqual(supply, 50)
        self.assertEqual(bal[A], 50)
        self.assertEqual(first_mint["block"], 3)

    def test_dead_address_keeps_balance_but_zero_reduces_supply(self):
        logs = [_xfer(ZERO_ADDR, A, 1000, block=1),
                _xfer(A, DEAD_ADDR, 250, block=2)]
        bal, supply, _ = replay_transfers(logs)
        self.assertEqual(supply, 1000)      # dead != burned-to-zero
        self.assertEqual(bal[DEAD_ADDR], 250)


class TestHolderStructure(unittest.TestCase):
    def test_excludes_pool_token_dead_zero(self):
        bal = {POOL: 500, TOKEN: 100, DEAD_ADDR: 50, ZERO_ADDR: 5,
               A: 200, B: 100, C: 50}
        hs = holder_structure(bal, 1000, POOL, TOKEN)
        self.assertEqual(hs["pool_pct_of_supply"], 50.0)
        self.assertEqual(hs["token_contract_pct"], 10.0)
        self.assertEqual(hs["dead_pct"], 5.0)
        self.assertEqual(hs["n_holders"], 3)
        self.assertEqual(hs["top1_pct"], 20.0)
        self.assertEqual(hs["top10_pct"], 35.0)
        self.assertEqual(hs["shoulder_11_20_pct"], 0.0)
        self.assertEqual(hs["top1_addr"], A)

    def test_shoulder_11_20(self):
        # i+40 so no generated address collides with POOL ("11"*20) / TOKEN
        bal = {("0x" + f"{i + 40:02d}" * 20): 100 - i for i in range(1, 25)}
        hs = holder_structure(bal, 2000, POOL, TOKEN)
        self.assertEqual(hs["n_holders"], 24)
        top10 = sum(100 - i for i in range(1, 11)) / 2000 * 100
        shoulder = sum(100 - i for i in range(11, 21)) / 2000 * 100
        self.assertAlmostEqual(hs["top10_pct"], round(top10, 2))
        self.assertAlmostEqual(hs["shoulder_11_20_pct"], round(shoulder, 2))

    def test_none_on_bad_supply(self):
        self.assertIsNone(holder_structure({A: 5}, 0, POOL, TOKEN))
        self.assertIsNone(holder_structure({A: 5}, None, POOL, TOKEN))

    def test_negative_balances_ignored(self):
        # replay of a partial log window can leave negative balances
        bal = {A: -10, B: 100}
        hs = holder_structure(bal, 1000, POOL, TOKEN)
        self.assertEqual(hs["n_holders"], 1)
        self.assertEqual(hs["top1_pct"], 10.0)


class TestLpOwners(unittest.TestCase):
    def _mint(self, owner, liq, block=1):
        # Mint data = (sender, amount, amount0, amount1); amount = word 1
        data = "0x" + _pad(A)[2:] + f"{liq:064x}" + "0" * 64 + "0" * 64
        return {"topics": [TOPIC_V3_MINT, _pad(owner)], "data": data,
                "blockNumber": hex(block), "logIndex": "0x0"}

    def _burn(self, owner, liq, block=2):
        # Burn data = (amount, amount0, amount1); amount = word 0
        data = "0x" + f"{liq:064x}" + "0" * 64 + "0" * 64
        return {"topics": [TOPIC_V3_BURN, _pad(owner)], "data": data,
                "blockNumber": hex(block), "logIndex": "0x0"}

    def test_net_liquidity(self):
        logs = [self._mint(A, 1000), self._mint(B, 500), self._burn(A, 400)]
        liq = lp_owners_from_events(logs)
        self.assertEqual(liq[A], 600)
        self.assertEqual(liq[B], 500)

    def test_summarize_lp(self):
        s = summarize_lp({A: 600, B: 400, C: 0}, {A: True, B: False})
        self.assertEqual(s["lp_n_owners"], 2)
        self.assertEqual(s["lp_top_owner"], A)
        self.assertEqual(s["lp_top_owner_share_pct"], 60.0)
        self.assertTrue(s["lp_top_owner_is_contract"])
        self.assertTrue(s["lp_any_eoa_owner"])      # B is a pull-ready EOA
        self.assertEqual(len(s["lp_owners"]), 2)

    def test_summarize_lp_empty(self):
        s = summarize_lp({})
        self.assertEqual(s["lp_n_owners"], 0)
        self.assertIsNone(s["lp_top_owner"])
        self.assertIsNone(s["lp_any_eoa_owner"])


class TestReadoutAndAssemble(unittest.TestCase):
    def test_hidden_supply_readout(self):
        r = hidden_supply_readout(pool_pct=15.86, top10_pct=31.94,
                                  shoulder_pct=5.41, n_holders=435,
                                  top1_pct=25.44)
        self.assertAlmostEqual(r["visible_float_pct"], 52.2)
        self.assertEqual(r["whale_overhang_pct"], 25.44)
        self.assertAlmostEqual(r["shoulder_to_top10_ratio"], 0.169)

    def test_readout_none_tolerant(self):
        r = hidden_supply_readout(None, None, None, None)
        self.assertIsNone(r["visible_float_pct"])
        self.assertIsNone(r["shoulder_to_top10_ratio"])

    def test_assemble_stamp_partial(self):
        stamp = assemble_stamp(POOL, TOKEN, quick={"pool_pct_of_supply": 90.0},
                               holders=None, lp=None, creator=None,
                               creator_pct=None, cost={"rpc_calls": 3},
                               truncated=True, err=None)
        self.assertEqual(stamp["pool_pct_of_supply"], 90.0)
        self.assertIsNone(stamp["top10_pct"])
        self.assertIsNone(stamp["lp_top_owner"])
        self.assertTrue(stamp["truncated"])
        self.assertEqual(stamp["v"], 1)
        # tier-A-only stamps still carry the hidden-supply readout fields
        self.assertIn("visible_float_pct", stamp)


class TestFailOpen(unittest.TestCase):
    def setUp(self):
        # keep this pure-RPC test network-free: the Blockscout shadow merge
        # (RH_BLOCKSCOUT default on) would otherwise fire a real HTTP call.
        self._prev = os.environ.get("RH_BLOCKSCOUT")
        os.environ["RH_BLOCKSCOUT"] = "off"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("RH_BLOCKSCOUT", None)
        else:
            os.environ["RH_BLOCKSCOUT"] = self._prev

    def test_compute_entry_stamp_never_raises(self):
        class BoomRpc:
            def call(self, method, params, tries=2):
                raise RuntimeError("rpc down")
        stamp = compute_entry_stamp(BoomRpc(), POOL, TOKEN,
                                    created_block=100, head_block=200,
                                    max_secs=1.0)
        self.assertIsNotNone(stamp["err"])
        self.assertIn("rpc down", stamp["err"])
        self.assertEqual(stamp["pool"], POOL)
        self.assertIn("cost", stamp)

    def test_blockscout_merge_off_is_byte_identical(self):
        # off -> no bs_ keys leak into the stamp
        class BoomRpc:
            def call(self, method, params, tries=2):
                raise RuntimeError("rpc down")
        stamp = compute_entry_stamp(BoomRpc(), POOL, TOKEN,
                                    created_block=100, head_block=200,
                                    max_secs=1.0)
        self.assertFalse(any(k.startswith("bs_") for k in stamp))


class TestRugGateVerdict(unittest.TestCase):
    """The concentration rug gate (2026-07-13, SHADOW): PURE verdict logic."""

    def test_top1_over_threshold_blocks(self):
        # CASHCATGAME-shape: top1 11.9 >= 9
        v = rug_gate_verdict({"top1_pct": 11.9, "top10_pct": 22.7})
        self.assertTrue(v["rug_gate_block"])
        self.assertIn("top1", v["rug_gate_reason"])
        self.assertEqual(v["rug_gate_source"], "recon")

    def test_top10_over_threshold_blocks(self):
        # CASHCATWIF-shape: top10 50.56 >= 30
        v = rug_gate_verdict({"top1_pct": 10.61, "top10_pct": 50.56})
        self.assertTrue(v["rug_gate_block"])

    def test_clean_winner_shape_passes(self):
        # typical survivor: top1 ~3 / top10 ~20 -> no block, no winner-kill
        v = rug_gate_verdict({"top1_pct": 3.19, "top10_pct": 19.4})
        self.assertFalse(v["rug_gate_block"])
        self.assertIsNone(v["rug_gate_reason"])

    def test_low_concentration_lp_pull_class_passes(self):
        # Halp-shape (top1 1.6 / top10 12.1) is INTENTIONALLY not caught by
        # concentration — that class is left to the LP-custody stamp.
        v = rug_gate_verdict({"top1_pct": 1.6, "top10_pct": 12.1})
        self.assertFalse(v["rug_gate_block"])

    def test_blockscout_source_preferred(self):
        v = rug_gate_verdict({"bs_top1_pct": 12.0, "bs_top10_pct": 15.0,
                              "top1_pct": 2.0, "top10_pct": 18.0})
        self.assertEqual(v["rug_gate_source"], "bs")
        self.assertTrue(v["rug_gate_block"])   # bs_top1 12 >= 9

    def test_fail_open_when_no_features(self):
        v = rug_gate_verdict({})
        self.assertFalse(v["rug_gate_block"])
        self.assertEqual(v["rug_gate_source"], "none")

    def test_custom_thresholds(self):
        v = rug_gate_verdict({"top1_pct": 8.0}, top1_thr=8.0, top10_thr=99.0)
        self.assertTrue(v["rug_gate_block"])


if __name__ == "__main__":
    unittest.main()
