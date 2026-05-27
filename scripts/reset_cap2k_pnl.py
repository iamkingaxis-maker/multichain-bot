"""One-shot: reset the cap2k bots' P&L after their entry logic was changed.

The 5 cap2k_* bots were created with broad (all-triggers) entry, took a handful of
losing trades, then were re-modeled on the proven dip family (classic_dip union
deep_dip triggers + 4 filter relaxations, 2026-05-27). Their pre-change trades no
longer represent the bot, so we reset them to a clean slate so the $2k sizing/exit
experiment measures the NEW entry config only.

WHAT IT DOES (idempotent, sentinel-guarded, backs up first):
  1. Back up trades_multi.json, trades.json, bot_state/ to backup_cap2k_reset_<ts>/.
  2. Drop all trade records with a cap2k_ bot_id from both ledgers (so the
     dashboard's total_trades / wins / total_pnl_realized reset to 0).
  3. Reset each cap2k_ bot_state to fresh: balance=$2000, in_flight=0, realized=0,
     daily=0, open_positions=[].
  4. Write a sentinel so it never runs twice.

Run as a boot hook (race-free — before the trading loop writes), like the scrubs.
Run modes:
  - In-container boot:   python scripts/reset_cap2k_pnl.py /data
  - Dry run:             python scripts/reset_cap2k_pnl.py /data --dry-run
  - Local self-test:     python scripts/reset_cap2k_pnl.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

CAP2K_PREFIX = "cap2k_"
PAPER_CAPITAL = 2000.0
SENTINEL = ".cap2k_pnl_reset_v2"  # v2: re-reset after the true-replica entry reconfig
                                  # (2026-05-27) — clears the few wrong-gated trades.


def _is_cap2k(bid) -> bool:
    return str(bid or "").startswith(CAP2K_PREFIX)


def reset(data_dir, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}

    if not dry_run:
        bk = data_dir / f"backup_cap2k_reset_{int(time.time())}"
        bk.mkdir(parents=True, exist_ok=True)
        for name in ("trades_multi.json", "trades.json"):
            p = data_dir / name
            if p.exists():
                shutil.copy2(p, bk / name)
        bs = data_dir / "bot_state"
        if bs.exists():
            shutil.copytree(bs, bk / "bot_state", dirs_exist_ok=True)

    # 1. drop cap2k trade records from both ledgers
    dropped: dict = {}
    for name in ("trades_multi.json", "trades.json"):
        p = data_dir / name
        if not p.exists():
            continue
        recs = json.loads(p.read_text())
        if not isinstance(recs, list):
            continue
        kept = [r for r in recs if not _is_cap2k(r.get("bot_id"))]
        dropped[name] = len(recs) - len(kept)
        if dropped[name] and not dry_run:
            p.write_text(json.dumps(kept))

    # 2. reset cap2k bot_state to fresh
    bs_dir = data_dir / "bot_state"
    reset_bots: list = []
    if bs_dir.is_dir():
        for sp in sorted(bs_dir.glob("*.json")):
            try:
                st = json.loads(sp.read_text())
            except Exception:
                continue
            if not _is_cap2k(st.get("bot_id")):
                continue
            st["balance_usd"] = PAPER_CAPITAL
            st["in_flight_usd"] = 0.0
            st["realized_pnl_total_usd"] = 0.0
            st["daily_pnl_usd"] = 0.0
            st["open_positions"] = []
            reset_bots.append(st.get("bot_id"))
            if not dry_run:
                sp.write_text(json.dumps(st, indent=2))

    summary = {"reset_bots": reset_bots, "dropped": dropped}
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    trades = [
        {"type": "sell", "bot_id": "cap2k_scalp", "token": "A", "pnl": -41.0, "pnl_pct": -10},
        {"type": "buy", "bot_id": "cap2k_scalp", "token": "A"},
        {"type": "sell", "bot_id": "baseline_v1", "token": "B", "pnl": 5.0, "pnl_pct": 10},  # keep
    ]
    (d / "trades_multi.json").write_text(json.dumps(trades))
    (d / "bot_state" / "cap2k_scalp.json").write_text(json.dumps({
        "bot_id": "cap2k_scalp", "balance_usd": 1958.91, "in_flight_usd": 0.0,
        "realized_pnl_total_usd": -41.09, "daily_pnl_usd": -41.09, "daily_pnl_date": "2026-05-27"}))
    (d / "bot_state" / "baseline_v1.json").write_text(json.dumps({
        "bot_id": "baseline_v1", "balance_usd": 2010.0, "in_flight_usd": 0.0,
        "realized_pnl_total_usd": 10.0, "daily_pnl_usd": 10.0, "daily_pnl_date": "2026-05-27"}))
    out = reset(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    tm = json.loads((d / "trades_multi.json").read_text())
    assert all(not r.get("bot_id", "").startswith("cap2k_") for r in tm), "cap2k trades not dropped"
    assert any(r.get("bot_id") == "baseline_v1" for r in tm), "baseline trade wrongly dropped"
    st = json.loads((d / "bot_state" / "cap2k_scalp.json").read_text())
    assert st["balance_usd"] == 2000.0 and st["realized_pnl_total_usd"] == 0.0 and st["open_positions"] == []
    bl = json.loads((d / "bot_state" / "baseline_v1.json").read_text())
    assert bl["balance_usd"] == 2010.0, "baseline state wrongly reset"
    assert "skipped" in reset(d), "not idempotent"
    print("SELFTEST PASS: cap2k reset to $2000/0, trades dropped, baseline untouched, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(reset(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
