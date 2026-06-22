"""Tests for the solpump_neg_gate re-classification: TRADE-JOIN -> FORWARD-CANDLE.

solpump_neg_gate used to emit to core.shadow_gate_log.log_shadow_block (trade-join),
which STARVED (blocked candidates rarely become closed trades). It now emits to
feeds.filter_shadow_recorder.record_verdict (forward-candle), scored by
scripts.audit_filter_shadow_log.compute_filter_pnl — which scores the BLOCKED
token's forward return directly, no executed trade needed.

These are PURE / no-network tests:
  1. Scorer path: synthetic filter_shadow_log records with
     filter_name="solpump_neg_gate" + stubbed forward candles (winners + losers)
     run through compute_filter_pnl -> a "solpump_neg_gate" group with sane n/WR.
  2. AST: the dip_scanner gate site calls record_verdict("solpump_neg_gate", ...)
     and NO LONGER calls log_shadow_block for solpump_neg_gate.

ADDRESS-keyed; no network (forward candles stubbed via a fake DexScreener client).
"""
import ast
import asyncio
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── forward-candle stub (same fixture style as test_filter_shadow_unify) ──────

class _FakeBar:
    def __init__(self, open_time, close, high=None, low=None):
        self.open_time = open_time
        self.close = close
        self.high = high if high is not None else close
        self.low = low if low is not None else close


class _CountingClient:
    def __init__(self, bars_by_pair):
        self.bars_by_pair = bars_by_pair
        self.calls = []

    async def fetch_1m(self, pair, limit=60):
        self.calls.append(pair)
        return self.bars_by_pair.get(pair, [])


def _rec(filter_name, verdict, pair, ts, addr=None):
    return {
        "ts": ts, "filter_name": filter_name, "verdict": verdict,
        "pair_address": pair, "token_address": addr or ("addr_" + pair),
        "token_symbol": "S",
    }


# ── 1) scorer auto-discovers solpump_neg_gate, sane n/WR ──────────────────────

def test_scorer_groups_solpump_neg_gate_with_winners_and_losers():
    from scripts import audit_filter_shadow_log as af

    ts = "1970-01-01T00:16:40+00:00"  # block_ts = 1000s
    # WIN pair: +10% forward move; LOSS pairs: -10% forward move.
    bars = {
        "WIN1": [_FakeBar(1000, 100.0), _FakeBar(1060, 110.0, high=110.0)],
        "LOSS1": [_FakeBar(1000, 100.0), _FakeBar(1060, 90.0, low=90.0)],
        "LOSS2": [_FakeBar(1000, 100.0), _FakeBar(1060, 90.0, low=90.0)],
    }
    client = _CountingClient(bars)
    recs = [
        _rec("solpump_neg_gate", "BLOCK", "WIN1", ts),
        _rec("solpump_neg_gate", "BLOCK", "LOSS1", ts),
        _rec("solpump_neg_gate", "BLOCK", "LOSS2", ts),
    ]
    out = asyncio.run(af.compute_filter_pnl(
        records=recs, client=client, min_forward_min=0, now_ts=2000,
        pace_secs=0.0,
    ))
    assert "solpump_neg_gate" in out
    g = out["solpump_neg_gate"]
    # all 3 BLOCK records scored
    assert g["n"] == 3
    assert g["block_n"] == 3
    # 1 forward-winner (+10% -> capped realized > 0), 2 forward-losers
    assert g["wr"] is not None
    # WR is a sane percentage
    assert 0.0 <= g["wr"] <= 100.0
    # one winner of three -> ~33%
    assert abs(g["wr"] - (100.0 / 3.0)) < 1e-6


def test_scorer_solpump_pass_block_diff_computable():
    from scripts import audit_filter_shadow_log as af

    ts = "1970-01-01T00:16:40+00:00"
    bars = {
        "P1": [_FakeBar(1000, 100.0), _FakeBar(1060, 90.0, low=90.0)],   # block=loser
        "P2": [_FakeBar(1000, 100.0), _FakeBar(1060, 110.0, high=110.0)],  # pass=winner
    }
    client = _CountingClient(bars)
    recs = [
        _rec("solpump_neg_gate", "BLOCK", "P1", ts),
        _rec("solpump_neg_gate", "PASS", "P2", ts),
    ]
    out = asyncio.run(af.compute_filter_pnl(
        records=recs, client=client, min_forward_min=0, now_ts=2000,
        pace_secs=0.0,
    ))
    g = out["solpump_neg_gate"]
    assert g["block_n"] == 1
    assert g["pass_n"] == 1
    assert g["pass_block_diff"] is not None


# ── 2) AST: gate site uses record_verdict, not log_shadow_block ───────────────

def _gate_block_source():
    """Extract the source of the if _ng_mode == "shadow" block at the
    solpump gate site (the lines mentioning solpump_neg_gate)."""
    path = os.path.join(_REPO_ROOT, "feeds", "dip_scanner.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    return src


def test_gate_site_calls_record_verdict_for_solpump():
    src = _gate_block_source()
    # The forward-candle recorder is wired with the solpump filter name.
    assert 'filter_name="solpump_neg_gate"' in src
    assert "from feeds.filter_shadow_recorder import record_verdict" in src


def test_gate_site_no_longer_logs_shadow_block_for_solpump():
    src = _gate_block_source()
    # The old trade-join emit for solpump must be gone (string no longer paired
    # with log_shadow_block). We assert no log_shadow_block call carries the
    # "solpump_neg_gate" literal anywhere in the module.
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = getattr(fn, "id", None) or getattr(fn, "attr", None)
        if name not in ("log_shadow_block", "_sgl"):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and arg.value == "solpump_neg_gate":
                offenders.append(name)
    assert offenders == [], f"solpump still trade-join emitted via {offenders}"


def test_dip_scanner_module_parses():
    # Guards the edit (the file has non-cp1252 bytes; must read as utf-8).
    src = _gate_block_source()
    ast.parse(src)
