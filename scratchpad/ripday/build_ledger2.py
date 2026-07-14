# Current-regime wallet ledger: union-of-entries P&L per (wallet, token) from all tapes
# Window: 2026-07-01T00:00Z .. end of tape. Realized = covered sells - buys; unrealized = leftover qty * last price.
import json, glob, os, bisect, sys
from datetime import datetime, timezone

RIP = os.path.dirname(os.path.abspath(__file__))
W_START = "2026-07-01T00:00:00+00:00"

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# ---------- load tapes ----------
seen = set()
trades_by_pair = {}   # pair -> list of trade dicts
pair_tok = {}         # pair -> (token, sym)
n_raw = 0
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        n_raw += 1
        if t["ts"] < W_START:
            continue
        key = (t["pair"], t["ts"], t["maker"], t["kind"], round(t["volume_usd"], 4))
        if key in seen:
            continue
        seen.add(key)
        p = t["pair"]
        trades_by_pair.setdefault(p, []).append(t)
        if p not in pair_tok:
            pair_tok[p] = (t.get("token", ""), t.get("sym", ""))

# token may be missing in live tapes; backfill from tape_index
try:
    idx = json.load(open(os.path.join(RIP, "tape_index.json")))
    for p, v in idx.items():
        if p in pair_tok and not pair_tok[p][0]:
            pair_tok[p] = (v.get("token", ""), pair_tok[p][1] or v.get("sym", ""))
        elif p not in pair_tok:
            pass
except Exception:
    pass

for p in trades_by_pair:
    trades_by_pair[p].sort(key=lambda t: t["ts"])

print("pairs with in-window trades:", len(trades_by_pair), "unique trades:", len(seen), "raw lines:", n_raw)

# ---------- load bars ----------
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
        p = d.get("pair")
        if p and d.get("bars"):
            bars_by_pair.setdefault(p, []).extend(d["bars"])
    except Exception:
        pass
# gt bars: filename = first 12 chars of pair
p12 = {}
for p in set(list(trades_by_pair) + list(bars_by_pair)):
    p12.setdefault(p[:12], p)
for f in glob.glob(os.path.join(RIP, "_gt_bars", "*.json")):
    stem = os.path.basename(f).split(".")[0]
    p = p12.get(stem)
    if not p:
        continue
    try:
        b = json.load(open(f))
        if isinstance(b, list):
            bars_by_pair.setdefault(p, []).extend(b)
        elif isinstance(b, dict) and b.get("bars"):
            bars_by_pair.setdefault(p, []).extend(b["bars"])
    except Exception:
        pass
for p in bars_by_pair:
    u = {}
    for b in bars_by_pair[p]:
        u[int(b[0])] = b
    bars_by_pair[p] = sorted(u.values(), key=lambda b: b[0])

cov = sum(1 for p in trades_by_pair if p in bars_by_pair and bars_by_pair[p])
print("pairs with bars:", cov, "/", len(trades_by_pair))

def price_at(pair, ep):
    bars = bars_by_pair.get(pair)
    if not bars:
        return None, None
    ts_list = [b[0] for b in bars]
    i = bisect.bisect_right(ts_list, ep) - 1
    if i < 0:
        b = bars[0]
        return b[1], abs(b[0] - ep)
    b = bars[i]
    gap = ep - b[0]
    # use close of containing/preceding bar
    return b[4], gap

# ---------- per (wallet, token-pair) ledger ----------
# episode keyed on (maker, pair)
eps = {}
for p, trades in trades_by_pair.items():
    for t in trades:
        k = (t["maker"], p)
        e = eps.get(k)
        if e is None:
            e = eps[k] = {"buy_usd": 0.0, "sell_usd": 0.0, "buy_qty": 0.0, "sell_qty_after": 0.0,
                          "sell_usd_after": 0.0, "sell_usd_before": 0.0,
                          "n_buys": 0, "n_sells": 0, "first_buy": None, "last_ts": None,
                          "buy_ts": [], "sell_ts": [], "px_missing": 0}
        ep_ts = iso2ep(t["ts"])
        px, gap = price_at(p, ep_ts)
        qty = (t["volume_usd"] / px) if (px and px > 0 and gap is not None and gap < 3600) else None
        if qty is None:
            e["px_missing"] += 1
        if t["kind"] == "buy":
            e["buy_usd"] += t["volume_usd"]
            e["n_buys"] += 1
            if qty:
                e["buy_qty"] += qty
            if e["first_buy"] is None:
                e["first_buy"] = t["ts"]
            e["buy_ts"].append(t["ts"])
        else:
            e["sell_usd"] += t["volume_usd"]
            e["n_sells"] += 1
            if e["first_buy"] is not None:
                e["sell_usd_after"] += t["volume_usd"]
                if qty:
                    e["sell_qty_after"] += qty
            else:
                e["sell_usd_before"] += t["volume_usd"]
            e["sell_ts"].append(t["ts"])
        e["last_ts"] = t["ts"]

# last price per pair (for marking)
last_px = {}
for p, bars in bars_by_pair.items():
    if bars:
        last_px[p] = bars[-1][4]

out = {}
for (w, p), e in eps.items():
    tok, sym = pair_tok.get(p, ("", ""))
    # realized/unrealized
    bq, sq = e["buy_qty"], e["sell_qty_after"]
    cov_sell = e["sell_usd_after"]
    capped = False
    if bq > 0 and sq > bq * 1.05:
        cov_sell = e["sell_usd_after"] * (bq / sq)  # scale: only the portion covering in-window buys
        capped = True
        rem_qty = 0.0
    else:
        rem_qty = max(0.0, bq - sq)
    realized = cov_sell - e["buy_usd"]
    lp = last_px.get(p)
    unreal = rem_qty * lp if (lp and rem_qty > 0) else 0.0
    rec = {"pair": p, "tok": tok, "sym": sym, "buy_usd": round(e["buy_usd"], 2), "sell_usd_after": round(e["sell_usd_after"], 2),
           "sell_usd_before": round(e["sell_usd_before"], 2), "n_buys": e["n_buys"], "n_sells": e["n_sells"],
           "realized": round(realized, 2), "unreal": round(unreal, 2), "net": round(realized + unreal, 2),
           "capped_preinv": capped, "px_missing": e["px_missing"],
           "first_buy": e["first_buy"], "last_ts": e["last_ts"],
           "buy_ts": e["buy_ts"], "sell_ts": e["sell_ts"]}
    out.setdefault(w, []).append(rec)

json.dump(out, open(os.path.join(RIP, "ledger2_wallets.json"), "w"))
print("wallets:", len(out), "episodes:", len(eps))

# quick stats
multi = {w: v for w, v in out.items() if sum(1 for r in v if r["buy_usd"] >= 20) >= 3}
print("wallets with buys>=20 on >=3 pairs:", len(multi))
