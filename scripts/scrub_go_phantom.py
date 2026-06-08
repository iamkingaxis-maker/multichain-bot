"""One-shot migration: scrub the 2026-06-08 GO phantom-LOSS prints.

GO (CujZ5W6GWYb5XYe3hsTJ6kjiaw5MdZjbKQEuGA6jpump) has a BROKEN price feed — it
printed garbage (2.5e-7 and $0.07 on the down side, 30.0/71.0 on the up side) for a
token actually trading at ~$0.0004-0.0006. The legacy realtime exit path was UNGUARDED
(fixed in core/pool_price_feed.py), so the ~0 prints booked phantom -99.9% "rug" stops:
  - 14:18 young_probe  -$99.95 (-99.95%)  -> token traded at 0.00043 again 38min later
  - 17:55 scanner      -$133.93 (-99.94%) -> "Dip stop -15% via pm_synced+model" at 5% impact
GO did NOT rug (price recovered after every -99.9%) — these are phantom losses.

Reprices each GO-address SELL with pnl_pct <= THRESHOLD_PCT to BREAKEVEN (entry), and
restores the (positive) delta to the affected bots' capital. Mirrors the CDOF scrub.

Modes: python scripts/scrub_go_phantom.py /data [--dry-run] | --selftest
"""
from __future__ import annotations
import json, shutil, sys, time
from pathlib import Path

THRESHOLD_PCT = -50.0   # pnl_pct at/below this on the GO phantom address == phantom loss
TOKEN = "GO"
ADDRESS = "CujZ5W6GWYb5XYe3hsTJ6kjiaw5MdZjbKQEuGA6jpump"
SENTINEL = ".go_phantom_scrub_v1"


def _reprice_records(records: list, restored: dict) -> int:
    n = 0
    for r in records:
        if (r.get("type") == "sell" and r.get("token") == TOKEN
                and r.get("address") == ADDRESS
                and isinstance(r.get("pnl_pct"), (int, float)) and r["pnl_pct"] <= THRESHOLD_PCT
                and not r.get("phantom_corrected")):
            old_pnl = r.get("pnl") or 0.0
            old_pct = r.get("pnl_pct") or 0.0
            ep = r.get("entry_price")
            if isinstance(ep, (int, float)) and ep > 0:
                r["exit_price"] = ep
                if "exit_mid_price" in r:
                    r["exit_mid_price"] = ep
            new_pnl = 0.0
            bot = r.get("bot_id") or "scanner"
            restored[bot] = restored.get(bot, 0.0) + (new_pnl - old_pnl)  # positive: restore loss
            r["pnl"] = new_pnl
            r["pnl_pct"] = 0.0
            r["phantom_corrected"] = True
            r["phantom_note"] = (
                f"GO 2026-06-08 phantom {old_pct:.0f}% print (broken feed, price recovered) "
                f"repriced to breakeven"
            )
            n += 1
    return n


def scrub(data_dir: Path, dry_run: bool = False) -> dict:
    data_dir = Path(data_dir)
    sentinel = data_dir / SENTINEL
    if sentinel.exists():
        return {"skipped": "sentinel exists"}
    restored: dict = {}
    summary = {"files": {}, "restored_per_bot": restored}
    if not dry_run:
        bk = data_dir / f"backup_go_{int(time.time())}"
        bk.mkdir(parents=True, exist_ok=True)
        for name in ("trades_multi.json", "trades.json"):
            p = data_dir / name
            if p.exists():
                shutil.copy2(p, bk / name)
        bs = data_dir / "bot_state"
        if bs.exists():
            shutil.copytree(bs, bk / "bot_state")
        summary["backup"] = str(bk)
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
    bs_dir = data_dir / "bot_state"
    fixed = {}
    for bot, delta in restored.items():
        sp = bs_dir / f"{bot}.json"
        if not sp.exists():
            fixed[bot] = "no bot_state (legacy capital — trades ledger repriced)"
            continue
        st = json.loads(sp.read_text())
        for k in ("balance_usd", "realized_pnl_total_usd", "realized_pnl_total",
                  "total_pnl_realized", "daily_pnl_usd"):
            if isinstance(st.get(k), (int, float)):
                st[k] = st[k] + delta
        fixed[bot] = round(delta, 2)
        if not dry_run:
            sp.write_text(json.dumps(st, indent=2))
    summary["bot_state_fixed"] = fixed
    summary["total_restored"] = round(sum(restored.values()), 2)
    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    trades = [
        {"type": "sell", "token": "GO", "address": ADDRESS, "bot_id": "young_probe_candidate",
         "entry_price": 0.0004885752, "exit_price": 2.5e-07, "pnl": -99.95, "pnl_pct": -99.95},
        {"type": "sell", "token": "GO", "address": ADDRESS, "bot_id": None,
         "entry_price": 0.000642, "exit_price": 0.07, "pnl": -133.93, "pnl_pct": -99.94},
        {"type": "sell", "token": "GO", "address": ADDRESS, "bot_id": "scanner",
         "entry_price": 0.0004585, "exit_price": 0.00049, "pnl": 0.11, "pnl_pct": 3.38},  # real TP, untouched
        {"type": "sell", "token": "GO", "address": "OTHERaddr", "bot_id": "x",
         "entry_price": 1.0, "exit_price": 0.0, "pnl": -50.0, "pnl_pct": -99.0},  # diff addr, untouched
    ]
    (d / "trades.json").write_text(json.dumps(trades))
    (d / "bot_state" / "young_probe_candidate.json").write_text(json.dumps(
        {"balance_usd": 1900.05, "realized_pnl_total_usd": -99.95, "daily_pnl_usd": -99.95}))
    out = scrub(d)
    print("SELFTEST:", json.dumps(out, indent=2))
    after = json.loads((d / "trades.json").read_text())
    yp = json.loads((d / "bot_state" / "young_probe_candidate.json").read_text())
    assert after[0]["pnl"] == 0.0 and after[0]["phantom_corrected"], "morning phantom not repriced"
    assert after[1]["pnl"] == 0.0 and after[1]["phantom_corrected"], "afternoon phantom not repriced"
    assert "phantom_corrected" not in after[2], "real TP wrongly touched"
    assert "phantom_corrected" not in after[3], "different-address GO wrongly touched"
    assert abs(yp["balance_usd"] - 2000.0) < 0.1, f"young_probe capital not restored: {yp['balance_usd']}"
    assert "skipped" in scrub(d), "not idempotent"
    print(f"SELFTEST PASS: restored ${out['total_restored']}; phantoms->breakeven, real+other untouched, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(scrub(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
