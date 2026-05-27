"""One-shot migration: scrub the 2026-05-27 GIGA phantom-stop losses.

GIGA's real price was ~flat (−3.5% h24, $1.8M liq) but a single bad price tick
read ~−32% and tripped the −15% hard stop across ~56 bots in one cycle, booking
~$452 of phantom losses. (Root cause patched in core/exit_price_guard.py, commit
81728f0 — threshold 0.40→0.22.) This migration reverses the already-booked
phantom losses in the trade ledger + per-bot capital snapshots.

WHAT IT DOES (idempotent, sentinel-guarded, backs up first):
  1. Back up trades_multi.json, trades.json, bot_state/ to backup_giga_<ts>/.
  2. Find GIGA SELL records with pnl_pct <= THRESHOLD (the phantom band; real
     GIGA was flat so a −25%+ stop is definitionally phantom). Reprice each to
     breakeven: exit_price = entry_price, pnl = 0, pnl_pct = 0, mark corrected.
  3. For each affected bot, add the restored amount back to its bot_state
     balance_usd + realized_pnl_total (+ daily_pnl).
  4. Write a sentinel so it never runs twice.

Run modes:
  - In-container boot/one-shot (real fix):   python scripts/scrub_giga_phantom.py /data
  - Dry run (report only, no writes):        python scripts/scrub_giga_phantom.py /data --dry-run
  - Local self-test (synthetic data):        python scripts/scrub_giga_phantom.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

THRESHOLD = -25.0          # pnl_pct at/below this on GIGA == phantom (real was flat)
TOKEN = "GIGA"
SENTINEL = ".giga_phantom_scrub_v1"


def _reprice_records(records: list, restored: dict) -> int:
    """Reprice phantom GIGA sells in-place; accumulate per-bot restored $. Returns count."""
    n = 0
    for r in records:
        if (r.get("type") == "sell" and r.get("token") == TOKEN
                and isinstance(r.get("pnl_pct"), (int, float)) and r["pnl_pct"] <= THRESHOLD
                and not r.get("phantom_corrected")):
            pnl = r.get("pnl") or 0.0
            bot = r.get("bot_id") or "baseline_v1"
            restored[bot] = restored.get(bot, 0.0) + (-pnl)
            ep = r.get("entry_price")
            if isinstance(ep, (int, float)) and ep > 0:
                r["exit_price"] = ep
                if "exit_mid_price" in r:
                    r["exit_mid_price"] = ep
            r["pnl"] = 0.0
            r["pnl_pct"] = 0.0
            r["phantom_corrected"] = True
            r["phantom_note"] = "GIGA 2026-05-27 phantom -32% tick repriced to breakeven"
            n += 1
    return n


def scrub(data_dir: Path, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    restored: dict = {}
    summary = {"files": {}, "restored_per_bot": restored}

    # backup
    if not dry_run:
        bk = data_dir / f"backup_giga_{int(time.time())}"
        bk.mkdir(parents=True, exist_ok=True)
        for name in ("trades_multi.json", "trades.json"):
            p = data_dir / name
            if p.exists():
                shutil.copy2(p, bk / name)
        bs = data_dir / "bot_state"
        if bs.exists():
            shutil.copytree(bs, bk / "bot_state")
        summary["backup"] = str(bk)

    # reprice both ledgers
    for name in ("trades_multi.json", "trades.json"):
        p = data_dir / name
        if not p.exists():
            continue
        recs = json.loads(p.read_text())
        if not isinstance(recs, list):
            continue
        cnt = _reprice_records(recs, restored)
        summary["files"][name] = cnt
        if cnt and not dry_run:
            p.write_text(json.dumps(recs))

    # restore per-bot capital snapshots
    bs_dir = data_dir / "bot_state"
    fixed_states = {}
    for bot, amt in restored.items():
        sp = bs_dir / f"{bot}.json"
        if not sp.exists():
            fixed_states[bot] = "no bot_state file"
            continue
        st = json.loads(sp.read_text())
        for k in ("balance_usd", "realized_pnl_total_usd", "realized_pnl_total",
                  "total_pnl_realized", "daily_pnl_usd"):
            if isinstance(st.get(k), (int, float)):
                st[k] = st[k] + amt
        fixed_states[bot] = round(amt, 2)
        if not dry_run:
            sp.write_text(json.dumps(st, indent=2))
    summary["bot_state_fixed"] = fixed_states
    summary["total_restored"] = round(sum(restored.values()), 2)

    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    # one phantom GIGA sell (-32%) + one legit GIGA sell (-3%) + one other token
    trades = [
        {"type": "sell", "token": "GIGA", "bot_id": "botA", "entry_price": 0.0037,
         "exit_price": 0.00249, "pnl": -25.8, "pnl_pct": -32.3},
        {"type": "sell", "token": "GIGA", "bot_id": "botB", "entry_price": 0.0040,
         "exit_price": 0.00388, "pnl": -0.6, "pnl_pct": -3.0},  # legit, untouched
        {"type": "sell", "token": "WIF", "bot_id": "botA", "entry_price": 1.0,
         "exit_price": 1.1, "pnl": 2.0, "pnl_pct": 10.0},
    ]
    (d / "trades_multi.json").write_text(json.dumps(trades))
    (d / "bot_state" / "botA.json").write_text(json.dumps(
        {"balance_usd": 1974.2, "realized_pnl_total_usd": -23.8, "daily_pnl_usd": -23.8}))
    out = scrub(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    after = json.loads((d / "trades_multi.json").read_text())
    a_state = json.loads((d / "bot_state" / "botA.json").read_text())
    assert after[0]["pnl"] == 0.0 and after[0]["phantom_corrected"] is True, "phantom not repriced"
    assert after[1]["pnl"] == -0.6 and "phantom_corrected" not in after[1], "legit GIGA wrongly touched"
    assert after[2]["pnl"] == 2.0, "other token touched"
    assert abs(a_state["balance_usd"] - (1974.2 + 25.8)) < 1e-6, "balance not restored"
    assert abs(a_state["realized_pnl_total_usd"] - (-23.8 + 25.8)) < 1e-6, "realized not restored"
    # idempotency: second run skips
    assert "skipped" in scrub(d), "not idempotent"
    print("SELFTEST PASS: phantom repriced, legit untouched, balance restored, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(scrub(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
