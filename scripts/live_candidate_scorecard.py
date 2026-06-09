"""Live-candidate scorecard — rank EVERY bot individually as a go-live candidate.

The fleet is a bot-SELECTION instrument: the deliverable is the config closest to
live-ready, judged per-bot (never fleet-aggregate). Criteria per bot:
  n closed, DISTINCT tokens (pseudo-replication guard), WR, $/tr, %/tr, total $,
  and stability: full-window vs recent-7d (a candidate must be positive in BOTH).

Verdicts:
  LIVE-CANDIDATE  n>=50, tokens>=30, $/tr>0 in full AND recent windows
  PROMISING       n>=50, tokens>=20, $/tr>0 in full window (recent thin/mixed)
  ACCUMULATING    n<50 (no verdict yet — includes the new pond clones)
  NEGATIVE        n>=50 and $/tr<=0

Usage:
  python scripts/live_candidate_scorecard.py                  # pull fresh from API
  python scripts/live_candidate_scorecard.py --cache FILE     # use a cached dump
  python scripts/live_candidate_scorecard.py --since 2026-05-26
"""
from __future__ import annotations
import argparse, json, sys, urllib.request, gzip, io
from collections import defaultdict
from statistics import mean, median

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://gracious-inspiration-production.up.railway.app/api/trades?all=1&full=1"


def load(cache):
    if cache:
        d = json.load(open(cache))
    else:
        req = urllib.request.Request(API, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        d = json.loads(raw)
    return d if isinstance(d, list) else d.get("trades", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None)
    ap.add_argument("--since", default="2026-05-26")  # post-orphan-flush era
    args = ap.parse_args()
    trades = load(args.cache)

    sells = [t for t in trades if t.get("type") == "sell"
             and "cancelled on restart" not in (t.get("reason") or "").lower()
             and (t.get("time") or "") >= args.since
             and t.get("bot_id")]
    if not sells:
        print("no sells in window"); return
    latest = max(t.get("time", "") for t in sells)
    recent_cut = latest[:10]  # crude: last calendar week boundary
    from datetime import datetime, timedelta
    try:
        rc = (datetime.fromisoformat(latest.replace("Z", "+00:00")) - timedelta(days=7)).isoformat()
    except Exception:
        rc = args.since

    bots = defaultdict(lambda: {"p": [], "u": [], "tok": set(), "ru": [], "rp": []})
    for t in sells:
        b = bots[t["bot_id"]]
        pct = float(t.get("pnl_pct") or 0); usd = float(t.get("pnl") or 0)
        b["p"].append(pct); b["u"].append(usd)
        b["tok"].add(t.get("token") or t.get("address") or "")
        if (t.get("time") or "") >= rc:
            b["rp"].append(pct); b["ru"].append(usd)

    rows = []
    for bot_id, b in bots.items():
        n = len(b["p"])
        wr = sum(1 for x in b["p"] if x > 0) / n
        dpt = mean(b["u"]); ppt = mean(b["p"]); tot = sum(b["u"])
        ntok = len(b["tok"])
        rn = len(b["ru"]); rd = mean(b["ru"]) if rn else None
        if n < 50:
            v = "ACCUMULATING"
        elif dpt <= 0:
            v = "NEGATIVE"
        elif ntok >= 30 and rn >= 15 and rd is not None and rd > 0:
            v = "LIVE-CANDIDATE"
        elif ntok >= 20:
            v = "PROMISING"
        else:
            v = "PROMISING(lowtok)"
        rows.append((bot_id, n, ntok, wr, dpt, ppt, tot, rn, rd, v))

    order = {"LIVE-CANDIDATE": 0, "PROMISING": 1, "PROMISING(lowtok)": 2,
             "ACCUMULATING": 3, "NEGATIVE": 4}
    rows.sort(key=lambda r: (order.get(r[9], 9), -(r[4])))

    print(f"window: {args.since} -> {latest[:16]}  |  recent = last 7d  |  bots: {len(rows)}")
    print(f"\n{'bot':32s}{'n':>6s}{'tok':>5s}{'WR':>5s}{'$/tr':>8s}{'%/tr':>7s}{'total$':>9s}{'r7n':>5s}{'r7$/tr':>8s}  verdict")
    print("-" * 106)
    for bot_id, n, ntok, wr, dpt, ppt, tot, rn, rd, v in rows:
        rds = f"{rd:+8.2f}" if rd is not None else "     n/a"
        print(f"  {bot_id:30s}{n:6d}{ntok:5d}{wr*100:4.0f}%{dpt:+8.2f}{ppt:+7.2f}{tot:+9.0f}{rn:5d}{rds}  {v}")

    lc = [r for r in rows if r[9] == "LIVE-CANDIDATE"]
    print(f"\n=== LIVE-CANDIDATES: {len(lc)} ===")
    for r in lc[:10]:
        print(f"  {r[0]}  ${r[4]:+.2f}/tr over n={r[1]}/{r[2]} tokens, recent-7d ${r[8]:+.2f}/tr (n={r[7]})")
    print("\nReminder: pond clones + entry-stack era started 2026-06-09 — their verdicts need "
          "their own forward window. Go-live still gated by tests/test_pre_live_invariants.py "
          "+ explicit approval.")


if __name__ == "__main__":
    main()
