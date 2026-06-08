"""One-shot migration: scrub the 2026-06-08 CDOF phantom-WIN profit.

champion_defender_v3 and baseline_v1 each held CDOF (6uviLiu8BC7rGovoFG18Bp3W4Sk
ScozZstJR45ASbPaH) when a deploy restart (~15:20 UTC) cold-started the price feed.
The first post-restart exit tick read a 62x phantom price (entry ~0.000163 ->
"exit" 0.01028, +6135%) — both bots booked the IDENTICAL glitched exit price to 18
decimals (the tell), tripping TP1+TP2 for +$2,456 of phantom PROFIT across 4 sells,
corrupting both bots' balances + the fleet leaderboard.

Root cause patched in core/exit_price_guard.py: the SEED path (cold guard state,
i.e. first tick after a restart) blind-accepted the first print without consulting
the OHLC high_fn. It now seeds last_good=ENTRY and validates an extreme first print
against the OHLC bound, so a phantom can no longer walk through on restart.

This migration reverses the phantom profit in the trade ledger + per-bot capital
snapshots. CDOF is illiquid/dead now (no confirmable real exit), so — like the GIGA
scrub — each phantom tranche is repriced to BREAKEVEN (entry price): removes the
fake win without inventing a real loss.

WHAT IT DOES (idempotent, sentinel-guarded, backs up first):
  1. Back up trades_multi.json, trades.json, bot_state/ to backup_cdof_<ts>/.
  2. Find CDOF SELL records on the phantom address with pnl_pct > THRESHOLD_PCT.
     Reprice each to breakeven: exit_price=entry_price, pnl=0, pnl_pct=0, mark
     phantom_corrected.
  3. For each affected bot, apply the (0 - old_pnl) delta (negative) to its
     bot_state balance_usd + realized_pnl_total (+ daily_pnl).
  4. Write a sentinel so it never runs twice.

Run modes:
  - In-container boot/one-shot (real fix):   python scripts/scrub_cdof_phantom.py /data
  - Dry run (report only, no writes):        python scripts/scrub_cdof_phantom.py /data --dry-run
  - Local self-test (synthetic data):        python scripts/scrub_cdof_phantom.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

THRESHOLD_PCT = 1000.0   # pnl_pct above this on the CDOF phantom address == phantom win
TOKEN = "CDOF"
ADDRESS = "6uviLiu8BC7rGovoFG18Bp3W4SkScozZstJR45ASbPaH"
SENTINEL = ".cdof_phantom_scrub_v1"


def _reprice_records(records: list, restored: dict) -> int:
    """Reprice phantom CDOF wins to breakeven (entry) in-place; accumulate per-bot
    delta (0 - old_pnl, negative). Returns count repriced."""
    n = 0
    for r in records:
        if (r.get("type") == "sell" and r.get("token") == TOKEN
                and r.get("address") == ADDRESS
                and isinstance(r.get("pnl_pct"), (int, float)) and r["pnl_pct"] > THRESHOLD_PCT
                and not r.get("phantom_corrected")):
            old_pnl = r.get("pnl") or 0.0
            old_pct = r.get("pnl_pct") or 0.0
            ep = r.get("entry_price")
            # breakeven reversal — exit at entry, zero pnl (CDOF dead, no real exit)
            if isinstance(ep, (int, float)) and ep > 0:
                r["exit_price"] = ep
                if "exit_mid_price" in r:
                    r["exit_mid_price"] = ep
            new_pnl = 0.0
            new_pct = 0.0
            bot = r.get("bot_id") or "baseline_v1"
            restored[bot] = restored.get(bot, 0.0) + (new_pnl - old_pnl)
            r["pnl"] = new_pnl
            r["pnl_pct"] = new_pct
            r["phantom_corrected"] = True
            r["phantom_note"] = (
                f"CDOF 2026-06-08 phantom +{old_pct:.0f}% tick (62x, post-restart cold-feed "
                f"glitch) repriced to breakeven"
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
        bk = data_dir / f"backup_cdof_{int(time.time())}"
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
    fixed_states = {}
    for bot, delta in restored.items():
        sp = bs_dir / f"{bot}.json"
        if not sp.exists():
            fixed_states[bot] = "no bot_state file"
            continue
        st = json.loads(sp.read_text())
        for k in ("balance_usd", "realized_pnl_total_usd", "realized_pnl_total",
                  "total_pnl_realized", "daily_pnl_usd"):
            if isinstance(st.get(k), (int, float)):
                st[k] = st[k] + delta
        fixed_states[bot] = round(delta, 2)
        if not dry_run:
            sp.write_text(json.dumps(st, indent=2))
    summary["bot_state_fixed"] = fixed_states
    summary["total_delta"] = round(sum(restored.values()), 2)

    if not dry_run:
        sentinel.write_text(json.dumps(summary))
    return summary


def _selftest():
    import tempfile
    d = Path(tempfile.mkdtemp())
    (d / "bot_state").mkdir()
    trades = [
        {"type": "sell", "token": "CDOF", "address": ADDRESS, "bot_id": "baseline_v1",
         "entry_price": 0.000163475, "exit_price": 0.01028057, "pnl": 928.32, "pnl_pct": 6188.77},
        {"type": "sell", "token": "CDOF", "address": ADDRESS, "bot_id": "baseline_v1",
         "entry_price": 0.000163475, "exit_price": 0.01014244, "pnl": 305.21, "pnl_pct": 6104.28},
        {"type": "sell", "token": "CDOF", "address": ADDRESS, "bot_id": "champion_defender_v3",
         "entry_price": 0.0001648834, "exit_price": 0.01028057, "pnl": 920.26, "pnl_pct": 6135.06},
        {"type": "sell", "token": "CDOF", "address": ADDRESS, "bot_id": "champion_defender_v3",
         "entry_price": 0.0001648834, "exit_price": 0.01014244, "pnl": 302.56, "pnl_pct": 6051.28},
        # a legit small CDOF-symbol win on a DIFFERENT address -> must be untouched
        {"type": "sell", "token": "CDOF", "address": "OTHERaddr", "bot_id": "baseline_v1",
         "entry_price": 1.0, "exit_price": 1.1, "pnl": 2.0, "pnl_pct": 10.0},
        {"type": "sell", "token": "WIF", "address": "z", "bot_id": "baseline_v1",
         "entry_price": 1.0, "exit_price": 1.1, "pnl": 2.0, "pnl_pct": 10.0},
    ]
    (d / "trades_multi.json").write_text(json.dumps(trades))
    (d / "bot_state" / "baseline_v1.json").write_text(json.dumps(
        {"balance_usd": 3107.45, "realized_pnl_total_usd": 1165.64, "daily_pnl_usd": 1200.0}))
    (d / "bot_state" / "champion_defender_v3.json").write_text(json.dumps(
        {"balance_usd": 3128.31, "realized_pnl_total_usd": 1198.31, "daily_pnl_usd": 1220.0}))
    out = scrub(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    after = json.loads((d / "trades_multi.json").read_text())
    b = json.loads((d / "bot_state" / "baseline_v1.json").read_text())
    v3 = json.loads((d / "bot_state" / "champion_defender_v3.json").read_text())
    assert all(after[i].get("phantom_corrected") for i in range(4)), "phantoms not repriced"
    assert all(after[i]["pnl"] == 0.0 for i in range(4)), "not breakeven"
    assert "phantom_corrected" not in after[4], "legit CDOF (other addr) wrongly touched"
    assert after[5]["pnl"] == 2.0, "other token touched"
    # baseline: 3107.45 - (928.32+305.21) = ~1873.92
    assert 1870 < b["balance_usd"] < 1876, f"baseline balance not corrected: {b['balance_usd']}"
    # v3: 3128.31 - (920.26+302.56) = ~1905.49
    assert 1903 < v3["balance_usd"] < 1908, f"v3 balance not corrected: {v3['balance_usd']}"
    assert "skipped" in scrub(d), "not idempotent"
    print(f"SELFTEST PASS: baseline {3107.45}->{b['balance_usd']:.2f}, v3 {3128.31}->{v3['balance_usd']:.2f}; "
          f"phantoms->breakeven, legit untouched, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(scrub(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
