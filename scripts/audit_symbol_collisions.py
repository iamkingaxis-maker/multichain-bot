"""AUDIT: same-symbol ≠ same-token collisions in the exit-pricing loop.

Built for the 2026-06-12 SPCX incident (six different mints all ticker'd
"SPCX"; the symbol-keyed price map + exit guard cross-poisoned two of them →
+$7.8k phantom TP booked to badday_flush_conviction). The keying fix is in
feeds/dip_scanner.py (_price_key); THIS script measures the blast radius in
the historical tape: every window where two different addresses sharing one
symbol were held concurrently, and every sell inside such a window whose
price action looks like it came from the sibling token.

Heuristics (flag, then eyeball — a flag is a LEAD, not a verdict):
  F1  |pnl_pct| > SUSPECT_PCT inside an overlap window (the incident shape).
  F2  exit_price is within 2x of the SIBLING token's concurrent price band
      but >3x away from this position's own entry (price-scale mismatch).

Usage:
  python scripts/audit_symbol_collisions.py [--cache _trades_cache.json] [--days 7]
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SUSPECT_PCT = 150.0


def _ts(t):
    dt = datetime.fromisoformat(str(t.get("time")).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="_trades_cache.json")
    ap.add_argument("--days", type=float, default=7.0)
    args = ap.parse_args()

    rows = json.load(open(args.cache))
    rows = rows if isinstance(rows, list) else rows.get("trades", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    # Build per-(bot, address) position episodes from buy→sells.
    # Episode = [buy_time, last_sell_time_or_now]; symbol from the records.
    episodes = []   # (symbol, address, bot, t0, t1)
    open_buys = {}  # (bot, address) -> (symbol, t0)
    for t in sorted(rows, key=lambda r: str(r.get("time") or "")):
        try:
            ts = _ts(t)
        except Exception:
            continue
        if ts < cutoff:
            continue
        sym = t.get("token") or t.get("symbol")
        addr = t.get("address") or t.get("pair_address")
        bot = t.get("bot_id") or t.get("strategy") or "?"
        if not sym or not addr:
            continue
        k = (bot, addr)
        if t.get("type") == "buy":
            open_buys.setdefault(k, (sym, ts))
        elif t.get("type") == "sell" and k in open_buys:
            s0, t0 = open_buys[k]
            if t.get("fully_closed") is not False:   # True or absent = closed
                episodes.append((s0, addr, bot, t0, ts))
                del open_buys[k]
    now = datetime.now(timezone.utc)
    for (bot, addr), (sym, t0) in open_buys.items():
        episodes.append((sym, addr, bot, t0, now))   # still open

    # Group by symbol; find overlapping episodes with DIFFERENT addresses.
    by_sym = defaultdict(list)
    for ep in episodes:
        by_sym[ep[0]].append(ep)
    overlaps = []   # (symbol, addrA, addrB, w0, w1)
    for sym, eps in by_sym.items():
        for i in range(len(eps)):
            for j in range(i + 1, len(eps)):
                _, a1, b1, s1, e1 = eps[i]
                _, a2, b2, s2, e2 = eps[j]
                if a1 == a2:
                    continue
                w0, w1 = max(s1, s2), min(e1, e2)
                if w0 < w1:
                    overlaps.append((sym, a1, a2, w0, w1))

    print(f"=== SYMBOL-COLLISION AUDIT (last {args.days:g}d) ===")
    print(f"episodes: {len(episodes)} | symbols with multi-mint concurrent holds: "
          f"{len({o[0] for o in overlaps})} | overlap windows: {len(overlaps)}")
    for sym in sorted({o[0] for o in overlaps}):
        ws = [o for o in overlaps if o[0] == sym]
        addrs = sorted({a for o in ws for a in (o[1], o[2])})
        tot_h = sum((o[4] - o[3]).total_seconds() for o in ws) / 3600
        print(f"  {sym}: {len(addrs)} mints, {len(ws)} windows, {tot_h:.1f}h total overlap")

    # Sells inside overlap windows → flag suspects.
    win_by_sym = defaultdict(list)
    for o in overlaps:
        win_by_sym[o[0]].append(o)
    # last-known price per address (entry/exit prints) for the F2 scale check
    px_by_addr = defaultdict(list)   # addr -> [(ts, price)]
    for t in rows:
        addr = t.get("address") or t.get("pair_address")
        if not addr:
            continue
        for f in ("entry_price", "exit_price", "price"):
            v = t.get(f)
            if isinstance(v, (int, float)) and v > 0:
                try:
                    px_by_addr[addr].append((_ts(t), float(v)))
                except Exception:
                    pass
                break

    flagged = []
    for t in rows:
        if t.get("type") != "sell":
            continue
        sym = t.get("token") or t.get("symbol")
        addr = t.get("address") or t.get("pair_address")
        if sym not in win_by_sym or not addr:
            continue
        try:
            ts = _ts(t)
        except Exception:
            continue
        hits = [o for o in win_by_sym[sym] if o[3] <= ts <= o[4] and addr in (o[1], o[2])]
        if not hits:
            continue
        pnl_pct = t.get("pnl_pct")
        reasons = []
        if isinstance(pnl_pct, (int, float)) and abs(pnl_pct) > SUSPECT_PCT:
            reasons.append(f"F1 |pnl_pct|={pnl_pct:.0f}%")
        ep_, xp_ = t.get("entry_price"), t.get("exit_price")
        if isinstance(ep_, (int, float)) and isinstance(xp_, (int, float)) and ep_ > 0 and xp_ > 0:
            ratio = xp_ / ep_
            if ratio > 3 or ratio < 1 / 3:
                # compare to sibling's concurrent price band
                for o in hits:
                    sib = o[2] if addr == o[1] else o[1]
                    near = [p for (pt, p) in px_by_addr.get(sib, [])
                            if abs((pt - ts).total_seconds()) < 6 * 3600]
                    if near and any(0.5 <= xp_ / p <= 2.0 for p in near):
                        reasons.append(f"F2 exit≈sibling {sib[:8]}… scale "
                                       f"(own ratio {ratio:.1f}x)")
                        break
        if reasons:
            flagged.append((ts, t, reasons))

    print(f"\nflagged sells inside overlap windows: {len(flagged)}")
    for ts, t, reasons in sorted(flagged, key=lambda x: x[0]):
        print(f"  {ts.strftime('%m-%d %H:%M')} {t.get('token'):8s} "
              f"bot={t.get('bot_id') or t.get('strategy')} pnl=${float(t.get('pnl') or 0):+.2f} "
              f"({float(t.get('pnl_pct') or 0):+.1f}%) addr={str(t.get('address'))[:10]}… "
              f"| {'; '.join(reasons)}"
              + ("  [already scrubbed]" if t.get("phantom_scrubbed") else ""))
    if not flagged:
        print("  (none — collisions existed but no sell shows cross-priced damage)")


if __name__ == "__main__":
    main()
