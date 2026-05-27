"""One-shot migration: scrub the 2026-05-27 EURC phantom-WIN profit.

no_filters bought EURC (a EUR-pegged stablecoin, real price ~$1.16) and a single
bad price tick read $6,199.37 — a 5,316x glitch — which tripped TP1+TP2 and booked
+$106,334 of phantom PROFIT on a ~$20 position, inflating the bot's balance to
$108,048 and corrupting the whole fleet leaderboard. (Root cause patched in
core/exit_price_guard.py — the drop-only guard now also rejects absurd upward
spikes via EXIT_GUARD_MAX_RISE + cross-source confirmation.) This migration
reverses the phantom profit in the trade ledger + per-bot capital snapshots.

Unlike the GIGA scrub (which repriced to breakeven), this reprices each phantom
EURC sell to the REAL exit price (~$1.16). EURC was essentially flat vs the
$1.1658 entry, so the corrected tranches book a tiny realistic loss (~-0.5%
price + the position never legitimately hit TP at all).

WHAT IT DOES (idempotent, sentinel-guarded, backs up first):
  1. Back up trades_multi.json, trades.json, bot_state/ to backup_eurc_<ts>/.
  2. Find EURC SELL records with pnl_pct > THRESHOLD_PCT (the phantom-win band; a
     real stablecoin cannot gain >1000%). Reprice each to the REAL exit: derive
     the tranche cost basis from the booked pnl/pnl_pct, then recompute pnl at
     REAL_EXIT. exit_price=REAL_EXIT, pnl/pnl_pct corrected, mark phantom_corrected.
  3. For each affected bot, apply the (new_pnl - old_pnl) delta (strongly
     negative) to its bot_state balance_usd + realized_pnl_total (+ daily_pnl).
  4. Write a sentinel so it never runs twice.

Run modes:
  - In-container boot/one-shot (real fix):   python scripts/scrub_eurc_phantom.py /data
  - Dry run (report only, no writes):        python scripts/scrub_eurc_phantom.py /data --dry-run
  - Local self-test (synthetic data):        python scripts/scrub_eurc_phantom.py --selftest
"""
from __future__ import annotations
import json
import shutil
import sys
import time
from pathlib import Path

THRESHOLD_PCT = 1000.0     # pnl_pct above this on EURC == phantom win (real ~flat)
TOKEN = "EURC"
REAL_EXIT = 1.16           # confirmed live EURC price ($670k liq) — stablecoin, flat
SENTINEL = ".eurc_phantom_scrub_v1"


def _reprice_records(records: list, restored: dict) -> int:
    """Reprice phantom EURC wins to the real exit in-place; accumulate per-bot
    delta (new_pnl - old_pnl, negative). Returns count repriced."""
    n = 0
    for r in records:
        if (r.get("type") == "sell" and r.get("token") == TOKEN
                and isinstance(r.get("pnl_pct"), (int, float)) and r["pnl_pct"] > THRESHOLD_PCT
                and not r.get("phantom_corrected")):
            old_pnl = r.get("pnl") or 0.0
            old_pct = r.get("pnl_pct") or 0.0
            ep = r.get("entry_price")
            # tranche cost basis implied by the booked (phantom) pnl & pct
            cost_basis = (old_pnl / (old_pct / 100.0)) if old_pct else 0.0
            if isinstance(ep, (int, float)) and ep > 0:
                new_pct = (REAL_EXIT / ep - 1.0) * 100.0
                new_pnl = cost_basis * (REAL_EXIT / ep - 1.0)
                r["exit_price"] = REAL_EXIT
                if "exit_mid_price" in r:
                    r["exit_mid_price"] = REAL_EXIT
            else:
                # no usable entry → fall back to breakeven (still reverses the win)
                new_pct = 0.0
                new_pnl = 0.0
            bot = r.get("bot_id") or "baseline_v1"
            restored[bot] = restored.get(bot, 0.0) + (new_pnl - old_pnl)
            r["pnl"] = new_pnl
            r["pnl_pct"] = new_pct
            r["phantom_corrected"] = True
            r["phantom_note"] = (
                f"EURC 2026-05-27 phantom +{old_pct:.0f}% tick ($6199 vs real ${REAL_EXIT}) "
                f"repriced to real exit"
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

    # backup
    if not dry_run:
        bk = data_dir / f"backup_eurc_{int(time.time())}"
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

    # apply per-bot capital deltas (negative — removes the phantom profit)
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
    # two phantom EURC wins (TP1+TP2, +531669%, mirrors prod) + one legit EURC
    # sell (+12%) + one other token
    trades = [
        {"type": "sell", "token": "EURC", "bot_id": "no_filters", "entry_price": 1.1658,
         "exit_price": 6199.37, "pnl": 79750.41, "pnl_pct": 531669.38},   # TP1
        {"type": "sell", "token": "EURC", "bot_id": "no_filters", "entry_price": 1.1658,
         "exit_price": 6199.37, "pnl": 26583.47, "pnl_pct": 531669.38},   # TP2
        {"type": "sell", "token": "EURC", "bot_id": "no_filters", "entry_price": 1.1658,
         "exit_price": 1.30, "pnl": 0.60, "pnl_pct": 11.5},   # legit small win, untouched
        {"type": "sell", "token": "WIF", "bot_id": "no_filters", "entry_price": 1.0,
         "exit_price": 1.1, "pnl": 2.0, "pnl_pct": 10.0},
    ]
    (d / "trades_multi.json").write_text(json.dumps(trades))
    (d / "bot_state" / "no_filters.json").write_text(json.dumps(
        {"balance_usd": 108047.98, "realized_pnl_total_usd": 106335.48, "daily_pnl_usd": 106346.87}))
    out = scrub(d)
    print("SELFTEST summary:", json.dumps(out, indent=2))
    after = json.loads((d / "trades_multi.json").read_text())
    st = json.loads((d / "bot_state" / "no_filters.json").read_text())
    # phantom repriced to real exit (~-0.5%), legit + other untouched
    assert after[0]["phantom_corrected"] is True and after[1]["phantom_corrected"] is True, "phantom(s) not repriced"
    assert after[0]["exit_price"] == REAL_EXIT, "exit not repriced to real"
    assert after[0]["pnl"] < 0 and after[0]["pnl"] > -1.0, f"new pnl unrealistic: {after[0]['pnl']}"
    assert "phantom_corrected" not in after[2], "legit EURC win wrongly touched"
    assert after[3]["pnl"] == 2.0, "other token touched"
    # balance corrected from 108047.98 to ~1714 (108047.98 + delta where delta ~= -79750.49)
    assert 1700 < st["balance_usd"] < 1720, f"balance not corrected: {st['balance_usd']}"
    # idempotency
    assert "skipped" in scrub(d), "not idempotent"
    print(f"SELFTEST PASS: balance {108047.98} -> {st['balance_usd']:.2f}; phantom repriced, legit untouched, idempotent.")
    shutil.rmtree(d)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        dd = next((a for a in sys.argv[1:] if not a.startswith("--")), "/data")
        print(json.dumps(scrub(Path(dd), dry_run="--dry-run" in sys.argv), indent=2))
