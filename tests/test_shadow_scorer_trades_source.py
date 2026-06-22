"""Trades-source fallback for the shadow-P&L scorer.

The trade-join gate scorer matched ZERO would-blocks because
core/shadow_pnl_scorer.py::_load_trades_file() only read pre-dumped JSON files
that NOTHING on the server writes. The real trades live in the append-mode
ledger: DATA_DIR/trades_multi.json (frozen base array) + trades_multi.jsonl
(this-session sidecar, one JSON object per line). These tests pin the fallback:

  1) when no dump file exists, _load_trades_file() returns base + sidecar union;
  2) an explicit dump file STILL wins (tests / forced paths unchanged).
"""
import json
import os

import core.shadow_pnl_scorer as scorer


def _write_ledger(dd):
    base = [
        {"bot_id": "b1", "address": "AAA", "pnl_pct": 10.0, "type": "sell"},
        {"bot_id": "b1", "address": "BBB", "pnl_pct": -5.0, "type": "sell"},
    ]
    with open(os.path.join(dd, "trades_multi.json"), "w") as f:
        json.dump(base, f)
    side = [
        {"bot_id": "b2", "address": "CCC", "pnl_pct": 3.0, "type": "sell"},
        {"bot_id": "b2", "address": "DDD", "pnl_pct": -1.0, "type": "sell"},
    ]
    with open(os.path.join(dd, "trades_multi.jsonl"), "w") as f:
        for r in side:
            f.write(json.dumps(r) + "\n")
    return base, side


def test_loads_ledger_base_plus_sidecar_union(tmp_path, monkeypatch):
    dd = str(tmp_path)
    base, side = _write_ledger(dd)
    monkeypatch.setattr(scorer, "_data_dir", lambda: dd)
    monkeypatch.delenv("SHADOW_PNL_TRADES_PATH", raising=False)

    out = scorer._load_trades_file()

    assert isinstance(out, list)
    addrs = sorted(t.get("address") for t in out)
    assert addrs == ["AAA", "BBB", "CCC", "DDD"]
    assert len(out) == len(base) + len(side)


def test_explicit_dump_still_wins(tmp_path, monkeypatch):
    dd = str(tmp_path)
    _write_ledger(dd)
    dump = os.path.join(dd, "explicit_dump.json")
    with open(dump, "w") as f:
        json.dump([{"bot_id": "z", "address": "ZZZ", "pnl_pct": 99.0}], f)
    monkeypatch.setattr(scorer, "_data_dir", lambda: dd)
    monkeypatch.setenv("SHADOW_PNL_TRADES_PATH", dump)

    out = scorer._load_trades_file()

    assert [t.get("address") for t in out] == ["ZZZ"]


def test_blank_sidecar_lines_skipped(tmp_path, monkeypatch):
    dd = str(tmp_path)
    with open(os.path.join(dd, "trades_multi.json"), "w") as f:
        json.dump([{"bot_id": "b", "address": "AAA", "pnl_pct": 1.0}], f)
    with open(os.path.join(dd, "trades_multi.jsonl"), "w") as f:
        f.write("\n")
        f.write(json.dumps({"bot_id": "b", "address": "BBB", "pnl_pct": 2.0}) + "\n")
        f.write("   \n")
    monkeypatch.setattr(scorer, "_data_dir", lambda: dd)
    monkeypatch.delenv("SHADOW_PNL_TRADES_PATH", raising=False)

    out = scorer._load_trades_file()
    assert sorted(t.get("address") for t in out) == ["AAA", "BBB"]
