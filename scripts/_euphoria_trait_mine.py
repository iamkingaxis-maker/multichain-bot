#!/usr/bin/env python
"""Euphoria winner TRAIT mine (2026-06-14 SOL-pump regime).

Pull fleet closes (last Nh), aggregate per TOKEN, enrich each token with
CURRENT DexScreener market traits (mcap/fdv, liquidity, age, vol h24/h1,
priceChange h24/h6/h1, txn buy/sell ratio). Then split tokens into
winners (token net pnl>0) vs losers and compare trait distributions to
derive a NUMERIC entry filter for the euphoric tape.

Two unit levels:
  (1) per-TOKEN (winner token vs loser token) — what kind of token won
  (2) per-CLOSE  ($-weighted) — robustness check (a single big token can't
      carry a per-token verdict).
"""
from __future__ import annotations
import urllib.request, gzip, json, time, statistics as st, math, sys
from collections import defaultdict

API = "https://gracious-inspiration-production.up.railway.app/api/trades?hours=%d"
HOURS = int(sys.argv[1]) if len(sys.argv) > 1 else 6
UA = {"User-Agent": "Mozilla/5.0 (euphoria-trait-mine)"}
NOW = time.time()


def get(url, hdr=UA, timeout=90):
    req = urllib.request.Request(url, headers=hdr)
    r = urllib.request.urlopen(req, timeout=timeout)
    data = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return json.loads(data)


# ---- 1. fleet closes -> per-token agg ----
j = get(API % HOURS)
sells = [x for x in j if x.get("type") == "sell" and x.get("pnl") is not None]
agg = defaultdict(lambda: {"pnl": 0.0, "n": 0, "w": 0, "pcts": [], "peaks": [],
                           "tok": None, "closes": []})
for s in sells:
    a = s["address"]
    d = agg[a]
    d["pnl"] += s["pnl"]; d["n"] += 1; d["w"] += 1 if s["pnl"] > 0 else 0
    d["tok"] = s.get("token")
    if s.get("pnl_pct") is not None:
        d["pcts"].append(s["pnl_pct"])
    if s.get("peak_pnl_pct") is not None:
        d["peaks"].append(s["peak_pnl_pct"])
    d["closes"].append(s["pnl"])

print(f"window={HOURS}h  closed sells={len(sells)}  distinct tokens={len(agg)}", file=sys.stderr)

# ---- 2. enrich each token via DexScreener ----
def dex(addr):
    try:
        jj = get(f"https://api.dexscreener.com/latest/dex/tokens/{addr}",
                 hdr={"User-Agent": "Mozilla/5.0"}, timeout=30)
    except Exception as e:
        return None
    pairs = jj.get("pairs") or []
    if not pairs:
        return None
    # pick the highest-liquidity solana pair
    pairs = [p for p in pairs if p.get("chainId") == "solana"] or pairs
    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    liq = (p.get("liquidity") or {}).get("usd")
    vol = p.get("volume") or {}
    pc = p.get("priceChange") or {}
    tx = p.get("txns") or {}
    created = p.get("pairCreatedAt")
    age_h = (NOW - created / 1000.0) / 3600.0 if created else None
    def br(win):
        t = tx.get(win) or {}
        b, s = t.get("buys") or 0, t.get("sells") or 0
        return (b / (b + s)) if (b + s) else None
    return {
        "mcap": p.get("marketCap") or p.get("fdv"),
        "fdv": p.get("fdv"),
        "liq": liq,
        "age_h": age_h,
        "vol_h24": vol.get("h24"), "vol_h6": vol.get("h6"),
        "vol_h1": vol.get("h1"), "vol_m5": vol.get("m5"),
        "pc_h24": pc.get("h24"), "pc_h6": pc.get("h6"),
        "pc_h1": pc.get("h1"), "pc_m5": pc.get("m5"),
        "buyratio_h1": br("h1"), "buyratio_h6": br("h6"),
        "buyratio_m5": br("m5"),
        "vol_liq_ratio_h24": (vol.get("h24") / liq) if (liq and vol.get("h24")) else None,
    }


rows = []
for a, d in agg.items():
    info = dex(a)
    time.sleep(0.25)
    if info is None:
        print(f"  no dex data: {d['tok']} {a}", file=sys.stderr)
        info = {}
    r = {"addr": a, "tok": d["tok"], "pnl": d["pnl"], "n": d["n"],
         "wr": d["w"] / d["n"], "closes": d["closes"],
         "medpct": st.median(d["pcts"]) if d["pcts"] else None,
         "medpeak": st.median(d["peaks"]) if d["peaks"] else None,
         "win": d["pnl"] > 0}
    r.update(info)
    rows.append(r)

json.dump(rows, open("_euphoria_traits.json", "w"), indent=0)
print(f"enriched {sum(1 for r in rows if r.get('mcap'))}/{len(rows)} tokens", file=sys.stderr)

# ---- 3. per-token winner vs loser trait table ----
W = [r for r in rows if r["win"]]
L = [r for r in rows if not r["win"]]
print()
print(f"PER-TOKEN  winners={len(W)} losers={len(L)}  "
      f"winner-net=${sum(r['pnl'] for r in W):+.1f}  loser-net=${sum(r['pnl'] for r in L):+.1f}")


def vals(rs, f):
    return [r[f] for r in rs if isinstance(r.get(f), (int, float)) and not isinstance(r.get(f), bool)]


def report(feats):
    print(f"\n{'trait':20s}{'nW':>4}{'nL':>4}{'win_med':>14}{'loss_med':>14}{'W/L':>8}")
    out = []
    for f in feats:
        wv, lv = vals(W, f), vals(L, f)
        if len(wv) < 3 or len(lv) < 3:
            print(f"{f:20s}{len(wv):>4}{len(lv):>4}   (thin)")
            continue
        wm, lm = st.median(wv), st.median(lv)
        ratio = (wm / lm) if lm else float('nan')
        out.append((f, wm, lm, ratio, len(wv), len(lv)))
        print(f"{f:20s}{len(wv):>4}{len(lv):>4}{wm:>14.4g}{lm:>14.4g}{ratio:>8.2f}")
    return out


FEATS = ["mcap", "liq", "age_h", "vol_h24", "vol_h1", "vol_liq_ratio_h24",
         "pc_h24", "pc_h6", "pc_h1", "pc_m5",
         "buyratio_h1", "buyratio_h6", "buyratio_m5"]
report(FEATS)

# ---- 4. threshold sweep: for each trait, WR & $/close above/below candidate cuts ----
# Use per-CLOSE weighting so a single token can't dominate.
def per_close():
    cl = []
    for r in rows:
        for pnl in r["closes"]:
            row = dict(r); row["__pnl"] = pnl; row["__win"] = pnl > 0
            cl.append(row)
    return cl


CL = per_close()
print(f"\nPER-CLOSE rows={len(CL)}  WR={100*sum(1 for c in CL if c['__win'])/len(CL):.0f}%  "
      f"$/close={sum(c['__pnl'] for c in CL)/len(CL):+.2f}")


def sweep(f, cuts):
    print(f"\n--- {f} threshold sweep (per-close) ---")
    fv = [c for c in CL if isinstance(c.get(f), (int, float)) and not isinstance(c.get(f), bool)]
    cov = len(fv)
    for cut in cuts:
        hi = [c for c in fv if c[f] >= cut]
        lo = [c for c in fv if c[f] < cut]
        def stat(g):
            if not g:
                return "  --"
            w = sum(1 for c in g if c["__win"])
            return f"n={len(g):3d} WR={100*w/len(g):3.0f}% $/cl={sum(c['__pnl'] for c in g)/len(g):+6.2f}"
        print(f"  cut {f}>={cut:<10g}  HI[{stat(hi)}]   LO[{stat(lo)}]   (cov {cov})")


sweep("mcap", [200e3, 400e3, 600e3, 1e6, 2e6])
sweep("liq", [20e3, 40e3, 60e3, 100e3])
sweep("age_h", [6, 24, 72, 168])
sweep("vol_h24", [100e3, 300e3, 600e3, 1e6])
sweep("pc_h24", [0, 30, 60, 100, 150])
sweep("pc_h6", [0, 20, 50])
sweep("pc_h1", [-2, 0, 2, 5])
sweep("buyratio_h1", [0.45, 0.50, 0.55])
sweep("vol_liq_ratio_h24", [2, 4, 8])
