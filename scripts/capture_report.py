#!/usr/bin/env python3
"""Daily CAPTURE REPORT — detect the winners we never touched.

CAPTURE RATE = pond bounces traded / pond bounces available.

Scans a 1m-bars cache for BOUNCE EVENTS today (00:00Z -> latest bar):
  trough  = >=10% decline from the max high of the preceding 60min,
  bounce  = >=+10% rise from the trough low within 45min  (BASE tier),
  wide    = >=+20% rise                                    (WIDE tier, subset).

Joins fleet trades (from /api/trades?full=1 JSON dump):
  CAPTURED  = a badday-family bot BUY in that pair within [trough-15m, trough+15m]
  NEAR-MISS = any fleet BUY same pair same UTC day, outside the window
  UNTOUCHED = no fleet buy in that pair today

Bars cache dir: one JSON per pair, filename <pair8>.json, each a list of
{ts_ms, open, high, low, close, volume_usd} dicts (io.dexscreener bars format).
Optional metadata JSON (bars_ext_report.json style: {pair8: {sym: ...}}) names tokens.
Liq/mcap classification comes from entry_meta of any historical fleet buy of the
pair (best-effort; UNKNOWN is stated honestly). Stdlib only. Medians, not means.

Usage:
  python scripts/capture_report.py [--bars DIR] [--trades FILE] [--meta FILE]
                                   [--day YYYY-MM-DD] [--top N]
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone

# --- today's defaults (2026-07-06 session) ---
_SESSION_SP = (r"C:\Users\jcole\AppData\Local\Temp\claude"
               r"\C--Users-jcole-multichain-bot"
               r"\ecbaef77-2f98-4dc5-9231-4bd9a529e92c\scratchpad")
DEFAULT_BARS = os.path.join(_SESSION_SP, "bars_ext")
DEFAULT_META = os.path.join(_SESSION_SP, "bars_ext_report.json")
DEFAULT_TRADES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "scratchpad", "_trades_full_2026_07_06.json")

DECLINE_PCT = -10.0      # trough qualifier: decline vs max high of preceding 60m
DECLINE_WIN_S = 3600
RISE_BASE = 10.0         # BASE tier bounce
RISE_WIDE = 20.0         # WIDE tier
RISE_WIN_S = 45 * 60
DEDUPE_S = 45 * 60       # troughs closer than this collapse (keep lower low)
CAPTURE_WIN_S = 15 * 60  # +/- around trough for CAPTURED
MIN_PRIOR_BARS = 5       # need some tape in the lookback to trust the decline


def parse_iso(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def load_bars(bars_dir):
    out = {}
    for fn in sorted(os.listdir(bars_dir)):
        if not fn.endswith(".json"):
            continue
        key = fn[:-5]
        try:
            with open(os.path.join(bars_dir, fn), encoding="utf-8") as f:
                bars = json.load(f)
        except Exception as e:
            print(f"  [warn] unreadable bars {fn}: {e}", file=sys.stderr)
            continue
        rows = []
        for b in bars or []:
            try:
                rows.append((b["ts_ms"] / 1000.0, float(b["high"]), float(b["low"])))
            except (KeyError, TypeError, ValueError):
                continue
        rows.sort()
        if rows:
            out[key] = rows
    return out


def find_bounces(rows, day0, day1):
    """Return (events, unresolved_count). Event = dict per resolved trough today."""
    n = len(rows)
    last_ts = rows[-1][0]
    # candidate trough bars
    cands = []
    j0 = 0
    for i, (ts, hi, lo) in enumerate(rows):
        if ts < day0 or ts >= day1:
            continue
        while j0 < i and rows[j0][0] < ts - DECLINE_WIN_S:
            j0 += 1
        prior = rows[j0:i]
        if len(prior) < MIN_PRIOR_BARS:
            continue
        pmax = max(h for _, h, _ in prior)
        if pmax <= 0 or lo <= 0:
            continue
        decline = (lo / pmax - 1.0) * 100.0
        if decline <= DECLINE_PCT:
            cands.append((ts, lo, decline, i))
    if not cands:
        return [], 0
    # dedupe: keep-lower within DEDUPE_S
    cands.sort(key=lambda c: c[1])  # by low, ascending
    kept = []
    for c in cands:
        if all(abs(c[0] - k[0]) >= DEDUPE_S for k in kept):
            kept.append(c)
    kept.sort()
    events, unresolved = [], 0
    for ts, lo, decline, i in kept:
        hi_max, hit_base_ts = 0.0, None
        j = i + 1
        while j < n and rows[j][0] <= ts + RISE_WIN_S:
            hi_max = max(hi_max, rows[j][1])
            j += 1
        rise = (hi_max / lo - 1.0) * 100.0 if hi_max > 0 else 0.0
        window_complete = last_ts >= ts + RISE_WIN_S
        if rise < RISE_BASE:
            if not window_complete:
                unresolved += 1  # can't call it a non-bounce yet
            continue
        events.append({"trough_ts": ts, "trough_low": lo, "decline_pct": decline,
                       "rise_pct": rise, "wide": rise >= RISE_WIDE,
                       "window_complete": window_complete})
    return events, unresolved


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bars", default=DEFAULT_BARS, help="bars cache dir (<pair8>.json files)")
    ap.add_argument("--trades", default=DEFAULT_TRADES, help="trades JSON (/api/trades?full=1 dump)")
    ap.add_argument("--meta", default=DEFAULT_META, help="optional {pair8:{sym}} metadata JSON")
    ap.add_argument("--day", default=None, help="UTC day YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--top", type=int, default=10, help="top-N untouched bounces to list")
    args = ap.parse_args()

    day = args.day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day0, day1 = d.timestamp(), d.timestamp() + 86400

    # --- metadata (symbols) ---
    meta = {}
    if args.meta and os.path.exists(args.meta):
        try:
            raw = json.load(open(args.meta, encoding="utf-8"))
            meta = {k: (v.get("sym") if isinstance(v, dict) else str(v)) for k, v in raw.items()}
        except Exception as e:
            print(f"[warn] meta unreadable: {e}", file=sys.stderr)

    # --- bars ---
    if not os.path.isdir(args.bars):
        sys.exit(f"bars dir not found: {args.bars}")
    bars = load_bars(args.bars)
    if not bars:
        sys.exit("no usable bars files")
    latest = max(rows[-1][0] for rows in bars.values())
    print(f"# CAPTURE REPORT — {day} (UTC)")
    print(f"bars: {len(bars)} pairs, latest bar "
          f"{datetime.fromtimestamp(latest, timezone.utc).strftime('%H:%M')}Z; "
          f"trades: {os.path.basename(args.trades)}")

    # --- trades ---
    try:
        trades = json.load(open(args.trades, encoding="utf-8"))
    except Exception as e:
        sys.exit(f"trades unreadable: {e}")
    buys_today = {}          # pair8 -> [(ts, bot_id)]
    any_buy_meta = {}        # pair8 -> latest entry_meta (any day, for liq/mcap)
    sym_from_trades = {}
    n_buys_today = 0
    for t in trades:
        if t.get("type") != "buy":
            continue
        pa = t.get("pair_address") or ""
        if not pa:
            continue
        k = pa[:8]
        ts = parse_iso(t.get("time") or "")
        if ts is None:
            continue
        sym_from_trades.setdefault(k, t.get("token"))
        em = t.get("entry_meta")
        if isinstance(em, dict) and (em.get("liquidity_usd") or em.get("mcap_usd")):
            prev = any_buy_meta.get(k)
            if prev is None or ts > prev[0]:
                any_buy_meta[k] = (ts, em.get("liquidity_usd"), em.get("mcap_usd"))
        if day0 <= ts < day1:
            buys_today.setdefault(k, []).append((ts, t.get("bot_id") or "?"))
            n_buys_today += 1
    print(f"fleet buys today: {n_buys_today} across {len(buys_today)} pairs")

    # --- scan bounces + join ---
    all_events, tot_unresolved = [], 0
    for k, rows in bars.items():
        evs, unres = find_bounces(rows, day0, day1)
        tot_unresolved += unres
        for e in evs:
            e["pair8"] = k
            e["sym"] = meta.get(k) or sym_from_trades.get(k) or "?"
            blist = buys_today.get(k, [])
            in_win = [(ts, b) for ts, b in blist
                      if abs(ts - e["trough_ts"]) <= CAPTURE_WIN_S and b.startswith("badday")]
            if in_win:
                e["status"] = "CAPTURED"
                e["bots"] = sorted({b for _, b in in_win})
            elif blist:
                e["status"] = "NEAR-MISS"
                e["bots"] = sorted({b for _, b in blist})
            else:
                e["status"] = "UNTOUCHED"
                e["bots"] = []
            m = any_buy_meta.get(k)
            e["liq_usd"], e["mcap_usd"] = (m[1], m[2]) if m else (None, None)
            all_events.append(e)

    def summarize(evs, label):
        n = len(evs)
        c = sum(1 for e in evs if e["status"] == "CAPTURED")
        nm = sum(1 for e in evs if e["status"] == "NEAR-MISS")
        u = n - c - nm
        rate = f"{100.0 * c / n:.1f}%" if n else "n/a"
        rises = [e["rise_pct"] for e in evs]
        med = f"{statistics.median(rises):.1f}%" if rises else "n/a"
        print(f"{label}: capture {rate}  (captured {c} / near-miss {nm} / untouched {u}; "
              f"n={n}, median rise {med})")

    print(f"\nbounce events today (resolved): n={len(all_events)}  "
          f"[unresolved troughs near tape end: {tot_unresolved}]")
    summarize(all_events, "BASE tier (rise >= +10%)")
    summarize([e for e in all_events if e["wide"]], "WIDE tier (rise >= +20%)")

    untouched = sorted((e for e in all_events if e["status"] == "UNTOUCHED"),
                       key=lambda e: -e["rise_pct"])
    print(f"\n## Top-{args.top} UNTOUCHED bounces by rise% (n untouched = {len(untouched)})")
    print(f"{'sym':<14}{'pair8':<10}{'trough(UTC)':<13}{'rise%':>7}{'decl%':>8}  liq/mcap (last fleet-buy meta)")
    for e in untouched[:args.top]:
        tt = datetime.fromtimestamp(e["trough_ts"], timezone.utc).strftime("%H:%M")
        lm = (f"${e['liq_usd']/1000:.0f}k/${e['mcap_usd']/1000:.0f}k"
              if e["liq_usd"] or e["mcap_usd"] else "UNKNOWN (never bought)")
        star = "" if e["window_complete"] else " (window open)"
        print(f"{str(e['sym'])[:13]:<14}{e['pair8']:<10}{tt:<13}{e['rise_pct']:>6.1f}{e['decline_pct']:>8.1f}  {lm}{star}")

    nms = [e for e in all_events if e["status"] == "NEAR-MISS"]
    if nms:
        print(f"\n## NEAR-MISS detail (n={len(nms)})")
        for e in sorted(nms, key=lambda x: -x["rise_pct"])[:args.top]:
            tt = datetime.fromtimestamp(e["trough_ts"], timezone.utc).strftime("%H:%M")
            print(f"  {str(e['sym'])[:13]:<14}{tt}Z rise {e['rise_pct']:.1f}%  bots: {', '.join(e['bots'])[:80]}")

    print("\ncaveats: universe = cached-bars pairs only (not the full scanner universe); "
          "liq/mcap only known for pairs the fleet ever bought; bounces whose 45m window "
          "ran past the last bar and had not yet bounced are excluded from the denominator.")
    return all_events


if __name__ == "__main__":
    main()
