"""Persistence standard (2026-06-08): paper positions survive a restart instead of
being synthetic-closed at 0% ("cancelled on restart"). The tracker's orphan-flush
must EXEMPT any position the trader persisted to open_positions_paper.json, and only
clean up positions that are genuinely gone (not persisted).

Before this fix, every restart flushed open paper positions at breakeven — corrupting
strategy P&L and hiding real losses (loser-survivorship that inflated fleet WR)."""
import json
import dashboard.tracker as tk


def _setup(tmp_path, monkeypatch, persisted_addrs):
    monkeypatch.setattr(tk, "TRADE_LOG_FILE", str(tmp_path / "trades.json"))
    monkeypatch.setattr(tk, "DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PAPER_MODE", "true")
    monkeypatch.delenv("SOLANA_PRIVATE_KEY", raising=False)
    # two orphan buys (no matching sells): KEEP is persisted, GONE is not
    (tmp_path / "trades.json").write_text(json.dumps([
        {"type": "buy", "token": "KEEP", "address": "AAA", "amount_usd": 100.0},
        {"type": "buy", "token": "GONE", "address": "BBB", "amount_usd": 100.0},
    ]))
    if persisted_addrs is not None:
        (tmp_path / "open_positions_paper.json").write_text(json.dumps(
            {"positions": [{"token_address": a} for a in persisted_addrs]}))


def test_persisted_position_exempt_from_flush(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, persisted_addrs=["AAA"])
    t = tk.PerformanceTracker()                       # __init__ runs the flush
    sold = {x["address"].lower() for x in t.trades if x["type"] == "sell"}
    assert "bbb" in sold, "genuinely-gone orphan should still be flushed"
    assert "aaa" not in sold, "persisted paper position must NOT be synthetic-closed"


def test_no_paper_file_flushes_all_as_before(tmp_path, monkeypatch):
    # transition / first boot: no paper book yet -> behave as before (flush both)
    _setup(tmp_path, monkeypatch, persisted_addrs=None)
    t = tk.PerformanceTracker()
    sold = {x["address"].lower() for x in t.trades if x["type"] == "sell"}
    assert {"aaa", "bbb"} <= sold, "with no paper book, flush should clean both"


def test_all_persisted_no_flush(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, persisted_addrs=["AAA", "BBB"])
    t = tk.PerformanceTracker()
    sells = [x for x in t.trades if x["type"] == "sell"]
    assert sells == [], "all positions persisted -> zero synthetic closes"
