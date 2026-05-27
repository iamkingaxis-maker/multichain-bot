"""One-shot: reconcile per-bot capital after the position-persistence fix.

Before the fix, the multi-bot harness orphaned open positions on every restart
(the lossy trades-reconstruction skipped any token with a sell), so their closing
sells never fired and their committed capital was never released. in_flight on many
bots was inflated by these unrecoverable phantom bags (no_filters ~$397.5; counts
ran 6-7x over max_concurrent).

The fixed harness persists the real position book in bot_state.open_positions and
restores from it. On the first boot with the fix, no bot has a persisted book yet,
so each starts flat — meaning the old in_flight is entirely stuck phantom capital.
This migration returns it to balance and zeroes the book:

    balance_usd     += in_flight_usd      # release the stuck capital
    in_flight_usd    = 0.0
    open_positions   = []                 # clean slate; new book persists going forward

This PRESERVES the per-bot invariant (balance + in_flight - realized == paper_capital)
and does NOT touch realized P&L (the research signal). Sentinel-guarded; backs up
bot_state first; never breaks boot.

Run modes:
  - In-container boot:   python scripts/reconcile_positions.py /data
  - Dry run:             python scripts/reconcile_positions.py /data --dry-run
  - Local self-test:     python scripts/reconcile_positions.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

SENTINEL = ".positions_reconciled_v1"


def reconcile(data_dir, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    bs_dir = data_dir / "bot_state"
    if not bs_dir.is_dir():
        return {"skipped": "no bot_state dir"}

    if not dry_run:
        bk = data_dir / f"backup_reconcile_{int(time.time())}"
        (bk / "bot_state").mkdir(parents=True, exist_ok=True)
        shutil.copytree(bs_dir, bk / "bot_state", dirs_exist_ok=True)

    fixed: dict = {}
    total_released = 0.0
    for sp in sorted(bs_dir.glob("*.json")):
        try:
            st = json.loads(sp.read_text())
        except Exception:
            continue
        inflight = st.get("in_flight_usd")
        if not isinstance(inflight, (int, float)):
            continue
        # Safety: if this bot already has a non-empty persisted position book, it's
        # a POST-fix state with real positions — never zero it (a sentinel-absent
        # disaster-recovery boot must not orphan real positions). 2026-05-27 audit.
        if st.get("open_positions"):
            continue
        if isinstance(st.get("balance_usd"), (int, float)):
            st["balance_usd"] = st["balance_usd"] + inflight
        st["in_flight_usd"] = 0.0
        st["open_positions"] = []
        fixed[st.get("bot_id", sp.stem)] = round(inflight, 2)
        total_released += inflight
        if not dry_run:
            sp.write_text(json.dumps(st, indent=2))
    summary = {"bots_reconciled": len(fixed), "total_released": round(total_released, 2),
               "per_bot": fixed}
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    # bot with leaked in_flight (balance+in_flight-realized == 2000)
    (d / "bot_state" / "b1.json").write_text(json.dumps({
        "bot_id": "b1", "balance_usd": 1606.0, "in_flight_usd": 397.5,
        "realized_pnl_total_usd": 3.5, "daily_pnl_usd": 3.5, "daily_pnl_date": "2026-05-27"}))
    out = reconcile(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    st = json.loads((d / "bot_state" / "b1.json").read_text())
    assert st["in_flight_usd"] == 0.0, "in_flight not zeroed"
    assert abs(st["balance_usd"] - 2003.5) < 1e-9, f"balance not released: {st['balance_usd']}"
    assert st["open_positions"] == [], "book not cleared"
    # invariant preserved: balance + in_flight - realized == 2000
    assert abs(st["balance_usd"] + st["in_flight_usd"] - st["realized_pnl_total_usd"] - 2000.0) < 1e-9
    assert "skipped" in reconcile(d), "not idempotent"
    print("SELFTEST PASS: in_flight released to balance, book cleared, invariant held, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(reconcile(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
