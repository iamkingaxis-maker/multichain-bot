"""Legacy trades.json phantom scrub (2026-06-13, RAGEGUY +$242,668). The
multi-bot self-heal reads only trades_multi.json; the legacy path writes
phantoms to trades.json. This scrub covers that file."""
import json, tempfile, pathlib
from scripts.scrub_phantom_pnl import scrub_legacy_trades_phantoms


def test_scrubs_legacy_phantom_leaves_real_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        (d / "trades.json").write_text(json.dumps([
            {"type": "sell", "token": "RAGEGUY", "bot_id": "baseline_v1",
             "pnl": 242668.0, "pnl_pct": 485337.0, "reason": "tp"},          # phantom
            {"type": "sell", "token": "OK", "pnl": 12.0, "pnl_pct": 24.0},     # real win
            {"type": "sell", "token": "LOSS", "pnl": -8.0, "pnl_pct": -16.0},  # real loss
            {"type": "buy",  "token": "RAGEGUY", "pnl": None},                 # buy ignored
        ]))
        n = scrub_legacy_trades_phantoms(d)
        assert n == 1
        out = json.loads((d / "trades.json").read_text())
        rage = next(t for t in out if t.get("token") == "RAGEGUY" and t["type"] == "sell")
        assert rage["pnl"] == 0.0 and rage["pnl_pct"] == 0.0 and rage["phantom_scrubbed"]
        assert rage["orig_pnl"] == 242668.0           # original preserved for audit
        assert next(t for t in out if t["token"] == "OK")["pnl"] == 12.0      # real untouched
        assert next(t for t in out if t["token"] == "LOSS")["pnl"] == -8.0
        # backup written + idempotent
        assert (d / "trades.json.pre-legacy-scrub").exists()
        assert scrub_legacy_trades_phantoms(d) == 0    # second run: nothing to do


def test_no_file_is_noop():
    with tempfile.TemporaryDirectory() as tmp:
        assert scrub_legacy_trades_phantoms(pathlib.Path(tmp)) == 0
