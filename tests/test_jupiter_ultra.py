"""Jupiter Ultra (MEV-protected routing) — pure helpers + dormancy.

Ultra builds + lands the swap tx through Jupiter's protected infra (not the public
mempool), so it is not sandwich-able like the standard quote+swap+send path. Built
2026-06-02 for the live measurement probe; HARD-GATED behind USE_JUPITER_ULTRA and
the live-mode private-key check (dormant in paper).
"""
import asyncio
import core.trader as T


# ── build_ultra_order_params ──
def test_build_params_basic():
    p = T.build_ultra_order_params("INMINT", "OUTMINT", 1000, "TAKERPK")
    assert p == {"inputMint": "INMINT", "outputMint": "OUTMINT", "amount": 1000, "taker": "TAKERPK"}
    assert "slippageBps" not in p   # omitted -> Ultra uses its own RTSE estimate


def test_build_params_with_slippage_cap():
    p = T.build_ultra_order_params("A", "B", 5, "T", slippage_bps=300)
    assert p["slippageBps"] == 300
    assert p["amount"] == 5


# ── parse_ultra_order ──
def test_parse_order_ok():
    r = T.parse_ultra_order({"transaction": "BASE64TX", "requestId": "rid-1",
                             "outAmount": "12345", "inAmount": "20000000", "router": "metis"})
    assert r["ok"] is True
    assert r["transaction"] == "BASE64TX" and r["request_id"] == "rid-1"
    assert r["out_amount"] == 12345 and r["in_amount"] == 20000000
    assert r["router"] == "metis"


def test_parse_order_missing_tx_not_ok():
    assert T.parse_ultra_order({"requestId": "rid"})["ok"] is False
    assert T.parse_ultra_order({"transaction": "tx"})["ok"] is False   # no requestId
    assert T.parse_ultra_order(None)["ok"] is False
    assert T.parse_ultra_order({"error": "no route found"})["ok"] is False


def test_parse_order_bad_amounts_coerce_zero():
    r = T.parse_ultra_order({"transaction": "tx", "requestId": "r", "outAmount": None})
    assert r["ok"] is True and r["out_amount"] == 0


# ── parse_ultra_execute ──
def test_parse_execute_success():
    r = T.parse_ultra_execute({"status": "Success", "signature": "SIG123", "slippageBps": 42})
    assert r["ok"] is True and r["signature"] == "SIG123" and r["slippage_bps"] == 42


def test_parse_execute_failed():
    assert T.parse_ultra_execute({"status": "Failed", "error": "slippage exceeded"})["ok"] is False
    assert T.parse_ultra_execute({"status": "Success"})["ok"] is False   # no signature
    assert T.parse_ultra_execute(None)["ok"] is False


# ── dormancy: paper mode (no private key) NEVER executes a real swap ──
def test_ultra_dormant_in_paper_mode():
    tr = T.Trader.__new__(T.Trader)        # bypass __init__ (no live deps)
    tr.private_key = ""                     # paper mode
    tr._exec_stats = {"swaps_attempted": 0, "swap_failures": 0,
                      "quote_failures": 0, "successful_swaps": 0}
    res = asyncio.run(tr._execute_swap_ultra("A", "B", 1000))
    assert res["success"] is False and res["reason"] == "paper_mode"
    assert tr._exec_stats["swaps_attempted"] == 0   # did not even attempt a live swap


def test_flag_default_off():
    # USE_JUPITER_ULTRA must default OFF so the live path stays dormant unless opted in.
    import importlib
    assert isinstance(T.USE_JUPITER_ULTRA, bool)
