"""Scrub phantom-win P&L from bot_state (overload-era price-glitch fills).

2026-05-31: the PAPER_UNCAPPED overload degraded the realtime exit feed; a bad
SPCX tick booked +1180% phantom "wins" (exit ~12.8x entry) on a few bots,
inflating the leaderboard (~+$470 fake). This migration subtracts the phantom
sells' fake pnl from each affected bot's balance + realized (and today's
daily_pnl) in bot_state, so /api/leaderboard is accurate again.

Phantom = a SELL with pnl_pct > 200% (far above ANY real move — memecoin upside
caps ~+15-35%, so a real GACHA +35% pump is NOT touched) OR exit/entry > 3x.

bot_state-ONLY: the capital managers load the bot_state snapshot on restart
(they don't replay trades), so correcting bot_state durably fixes the leaderboard.
trades_multi.json (the canonical record) is left INTACT for audit — the glitch
sells remain visible as what actually happened. Backed up to
bot_state.pre-phantom-scrub/ + sentinel'd (runs exactly once).
"""
from __future__ import annotations
import json
from pathlib import Path

PHANTOM_PCT = 200.0      # realized pnl% above this = price-glitch phantom
PHANTOM_RATIO = 3.0      # exit/entry above this = phantom (secondary check)


def _is_phantom(s: dict) -> bool:
    if s.get("type") != "sell":
        return False
    p = s.get("pnl_pct")
    if isinstance(p, (int, float)) and not isinstance(p, bool) and p > PHANTOM_PCT:
        return True
    ep, xp = s.get("entry_price"), s.get("exit_price")
    try:
        if ep and xp and float(ep) > 0 and float(xp) / float(ep) > PHANTOM_RATIO:
            return True
    except Exception:
        pass
    return False


def migrate(data_dir, force: bool = False) -> int:
    """Subtract phantom pnl from affected bots' bot_state. Returns # bots fixed.
    Idempotent via data_dir/phantom_scrub_done.json sentinel."""
    data_dir = Path(data_dir)
    sentinel = data_dir / "phantom_scrub_done.json"
    if sentinel.exists() and not force:
        print(f"[phantom_scrub] sentinel exists at {sentinel} — skipping")
        return 0

    trades_path = data_dir / "trades_multi.json"
    if not trades_path.exists():
        print(f"[phantom_scrub] no {trades_path} — nothing to scrub")
        return 0
    try:
        trades = json.loads(trades_path.read_text())
    except Exception as e:
        print(f"[phantom_scrub] could not read trades: {e} — aborting (safe)")
        return 0

    phantom_by_bot: dict[str, float] = {}
    details = []
    for s in trades:
        if isinstance(s, dict) and _is_phantom(s):
            b = s.get("bot_id", "baseline_v1")
            pnl = float(s.get("pnl", 0.0) or 0.0)
            phantom_by_bot[b] = phantom_by_bot.get(b, 0.0) + pnl
            details.append({"bot": b, "token": s.get("token"),
                            "pnl": pnl, "pnl_pct": s.get("pnl_pct"),
                            "time": s.get("time")})

    if not phantom_by_bot:
        print("[phantom_scrub] no phantom sells found")
        sentinel.write_text(json.dumps({"scrubbed_bots": [], "total_removed": 0.0}))
        return 0

    bot_state_dir = data_dir / "bot_state"
    if not bot_state_dir.exists():
        print(f"[phantom_scrub] no bot_state dir — aborting (safe)")
        return 0

    # Backup before any mutation (reversible).
    backup = data_dir / "bot_state.pre-phantom-scrub"
    if not backup.exists():
        backup.mkdir()
        for p in bot_state_dir.glob("*.json"):
            (backup / p.name).write_text(p.read_text())
        print(f"[phantom_scrub] backed up bot_state to {backup}")

    fixed = 0
    for bot, fake in phantom_by_bot.items():
        sp = bot_state_dir / f"{bot}.json"
        if not sp.exists():
            print(f"[phantom_scrub] WARN no bot_state for {bot} (skipped ${fake:.2f})")
            continue
        try:
            st = json.loads(sp.read_text())
        except Exception:
            print(f"[phantom_scrub] WARN unreadable bot_state {bot} — skipped")
            continue
        st["balance_usd"] = float(st.get("balance_usd", 0.0)) - fake
        st["realized_pnl_total_usd"] = float(st.get("realized_pnl_total_usd", 0.0)) - fake
        if "daily_pnl_usd" in st:
            st["daily_pnl_usd"] = float(st.get("daily_pnl_usd", 0.0)) - fake
        sp.write_text(json.dumps(st, indent=2))
        fixed += 1
        print(f"[phantom_scrub] {bot}: -${fake:.2f} (balance + realized + daily)")

    sentinel.write_text(json.dumps({
        "scrubbed_bots": sorted(phantom_by_bot),
        "total_removed": round(sum(phantom_by_bot.values()), 2),
        "threshold_pct": PHANTOM_PCT, "details": details[:50],
    }, indent=2))
    print(f"[phantom_scrub] scrubbed {fixed} bots, removed "
          f"${sum(phantom_by_bot.values()):.2f} phantom P&L")
    return fixed


def mark_phantom_trades(data_dir, force: bool = False) -> int:
    """Mark phantom SELL records in trades_multi.json so the trade list AND any
    future recompute are clean: zero pnl/pnl_pct, set phantom_scrubbed=True, keep
    orig_pnl/orig_pnl_pct for audit. INDEPENDENT of the bot_state scrub (which
    already corrected realized) — this only touches the trade records, so there
    is no double-correction. Idempotent (skips already-flagged records). Backed
    up + sentinel'd. Runs at startup BEFORE the trade store loads, so the cleaned
    records persist.
    """
    data_dir = Path(data_dir)
    sentinel = data_dir / "phantom_trades_marked.json"
    if sentinel.exists() and not force:
        print(f"[phantom_mark] sentinel exists at {sentinel} — skipping")
        return 0
    trades_path = data_dir / "trades_multi.json"
    if not trades_path.exists():
        print(f"[phantom_mark] no {trades_path} — nothing to mark")
        return 0
    try:
        trades = json.loads(trades_path.read_text())
    except Exception as e:
        print(f"[phantom_mark] could not read trades: {e} — aborting (safe)")
        return 0
    if not isinstance(trades, list):
        print("[phantom_mark] trades_multi.json is not a list — aborting (safe)")
        return 0

    marked = 0
    for s in trades:
        if isinstance(s, dict) and _is_phantom(s) and not s.get("phantom_scrubbed"):
            s["orig_pnl"] = s.get("pnl")
            s["orig_pnl_pct"] = s.get("pnl_pct")
            s["pnl"] = 0.0
            s["pnl_pct"] = 0.0
            s["phantom_scrubbed"] = True
            marked += 1

    if marked == 0:
        print("[phantom_mark] no unmarked phantom records")
        sentinel.write_text(json.dumps({"marked": 0}))
        return 0

    backup = data_dir / "trades_multi.pre-phantom-mark.json"
    if not backup.exists():
        backup.write_text(trades_path.read_text())
        print(f"[phantom_mark] backed up trades to {backup}")
    trades_path.write_text(json.dumps(trades))
    sentinel.write_text(json.dumps({"marked": marked}))
    print(f"[phantom_mark] marked {marked} phantom trade records (pnl zeroed, "
          f"orig kept, phantom_scrubbed=True)")
    return marked


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="/data")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    migrate(Path(a.data_dir), force=a.force)
    mark_phantom_trades(Path(a.data_dir), force=a.force)
