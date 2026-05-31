#!/usr/bin/env python
"""Cross-check the universe-mined runner signal on REALIZED trade outcomes.

The universe mine (universe_remine.py) surfaced: young (age<=4h) + small-cap
(mcap<=650k / liq<=74k) + positive momentum (pc_h1>0) selects 30-min PEAK
runners at ~1.8x base, held-out-stable. But peak is a known mirage
(feedback_validate_on_realized). This is the mandatory realized check: among
trades the fleet ACTUALLY took (entry_meta joined to realized pnl), does the
signal raise realized WR + EV? And does the smallest-cap pocket (where peak-WR
was highest) HOLD or COLLAPSE (confirming round-trip mirage)?

Token-deduped (FCM gate). Read-only.
"""
from __future__ import annotations
import json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def feat(em, *names):
    for n in names:
        v = em.get(n)
        if v is not None:
            try:
                return float(v)
            except Exception:
                pass
    return None


def main():
    trades = _get("/api/trades?limit=5000&full=1")
    buys = [x for x in trades if x.get("type") == "buy" and x.get("entry_meta")]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)

    rows = []
    for b in buys:
        em = b["entry_meta"]
        age = feat(em, "lifecycle_age_hours", "age_hours", "dev_baseline_age_hours")
        mcap = feat(em, "fdv", "mcap", "market_cap_usd")
        liq = feat(em, "liquidity_usd", "liq_usd")
        pch1 = feat(em, "pc_h1", "price_change_h1", "pc_h1_pct")
        scand = sidx.get((b.get("bot_id"), b.get("address")))
        if not scand:
            continue
        s = scand[0]
        if s.get("pnl_pct") is None:
            continue
        rows.append(dict(token=b.get("token"), age=age, mcap=mcap, liq=liq,
                         pch1=pch1, pnl=float(s["pnl_pct"]),
                         peak=s.get("peak_pnl_pct")))
    print(f"joined realized trades w/ entry_meta: {len(rows)}")

    # require the signal features present
    have = [r for r in rows if r["age"] is not None and r["liq"] is not None and r["pch1"] is not None]
    print(f"with age+liq+pc_h1 present: {len(have)} (unique tokens {len({r['token'] for r in have})})")

    def report(rs, label):
        if not rs:
            print(f"  {label:46} n=0"); return
        # token-dedup: median pnl per token
        bytok = {}
        for r in rs:
            bytok.setdefault(r["token"], []).append(r)
        ded = []
        for t, g in bytok.items():
            g2 = sorted(g, key=lambda x: x["pnl"])
            ded.append(g2[len(g2)//2])
        pnls = [r["pnl"] for r in ded]
        wr = 100 * np.mean([p > 0 for p in pnls])
        ev = np.mean(pnls)
        print(f"  {label:46} tok={len(ded):>3} (raw {len(rs):>4}) | realized WR {wr:>3.0f}% EV {ev:+5.2f}%")

    report(have, "ALL (baseline)")
    # mid-frontier operating point
    sig = [r for r in have if r["age"] <= 168 and r["liq"] <= 74000 and r["pch1"] >= 2.8]
    nonsig = [r for r in have if not (r["age"] <= 168 and r["liq"] <= 74000 and r["pch1"] >= 2.8)]
    report(sig, "YOUNG_MOM (age<=168h, liq<=74k, pc_h1>=2.8)")
    report(nonsig, "  complement (everything else)")
    # the suspect small-cap pocket (highest peak-WR) — does it hold on realized?
    tiny = [r for r in have if r["age"] <= 168 and r["liq"] <= 40000 and r["pch1"] >= 22]
    report(tiny, "TINY pocket (liq<=40k, pc_h1>=22) [mirage?]")
    # decompose each leg
    report([r for r in have if r["age"] <= 168], "age<=168h (7d) only")
    report([r for r in have if r["age"] > 168], "age>168h only")
    report([r for r in have if r["age"] <= 24], "age<=24h only")
    report([r for r in have if r["liq"] <= 74000], "liq<=74k only")
    report([r for r in have if r["pch1"] is not None and r["pch1"] >= 2.8], "pc_h1>=2.8 only")
    report([r for r in have if r["pch1"] is not None and r["pch1"] < 0], "pc_h1<0 (dip) only")

    print("\nNOTE: realized WR/EV is the truth metric. If TINY collapses vs YOUNG_MOM,")
    print("the small-cap peak-WR was a round-trip mirage. Token-deduped throughout.")


if __name__ == "__main__":
    main()
