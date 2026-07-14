# Day-scoped wallet ledger for the 07-04 daily decode.
# Same union-of-entries + matched-covered-sell accounting as build_ledger2, but each
# (wallet, pair) episode is attributed to the UTC date of its first in-window buy, and
# sells are matched from first buy through tape end. Realized = covered sells - buys
# (sells > bought qty scaled down = pre-window/pre-buy inventory cap); leftover marked
# at last bar close = unrealized.
import json, glob, os, bisect
from datetime import datetime

RIP = os.path.dirname(os.path.abspath(__file__))
W_START = "2026-07-01T00:00:00+00:00"


def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()


# ---------- load tapes ----------
seen = set()
trades_by_pair = {}
pair_tok = {}
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

for p in trades_by_pair:
    trades_by_pair[p].sort(key=lambda t: t["ts"])
print("pairs with in-window trades:", len(trades_by_pair), "unique trades:", len(seen), "raw:", n_raw)

# ---------- load bars ----------
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    p = d.get("pair")
    if p and d.get("bars"):
        bars_by_pair.setdefault(p, []).extend(d["bars"])
p12 = {}
for p in set(list(trades_by_pair) + list(bars_by_pair)):
    p12.setdefault(p[:12], p)
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        stem = os.path.basename(f).split(".")[0]
        p = p12.get(stem)
        if not p:
            continue
        try:
            b = json.load(open(f))
        except Exception:
            continue
        bl = b if isinstance(b, list) else (b.get("bars") or [])
        if bl:
            bars_by_pair.setdefault(p, []).extend(bl)
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda b: b[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}
cov = sum(1 for p in trades_by_pair if bars_by_pair.get(p))
print("pairs with bars:", cov, "/", len(trades_by_pair))
# coverage of 07-03+ trade pairs specifically
d3pairs = {p for p, tl in trades_by_pair.items() if any(t["ts"] >= "2026-07-03" for t in tl)}
d3cov = sum(1 for p in d3pairs if bars_by_pair.get(p))
print("07-03+ pairs with bars:", d3cov, "/", len(d3pairs))


def price_at(pair, ep):
    ts = bar_ts.get(pair)
    if not ts:
        return None, None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0:
        b = bars_by_pair[pair][0]
        return b[1], abs(b[0] - ep)
    b = bars_by_pair[pair][i]
    return b[4], ep - b[0]


# ---------- episodes keyed (maker, pair); day = date of first buy ----------
eps = {}
for p, trades in trades_by_pair.items():
    for t in trades:
        k = (t["maker"], p)
        e = eps.get(k)
        if e is None:
            e = eps[k] = {"buy_usd": 0.0, "sell_usd_after": 0.0, "sell_usd_before": 0.0,
                          "buy_qty": 0.0, "sell_qty_after": 0.0,
                          "n_buys": 0, "n_sells": 0, "first_buy": None, "last_ts": None,
                          "buy_ts": [], "buy_usd_list": [], "sell_ts": [], "sell_usd_list": [],
                          "sell_px": [], "px_missing": 0}
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
            e["buy_usd_list"].append(t["volume_usd"])
        else:
            e["n_sells"] += 1
            if e["first_buy"] is not None:
                e["sell_usd_after"] += t["volume_usd"]
                if qty:
                    e["sell_qty_after"] += qty
                e["sell_ts"].append(t["ts"])
                e["sell_usd_list"].append(t["volume_usd"])
                e["sell_px"].append(px if (px and gap is not None and gap < 3600) else None)
            else:
                e["sell_usd_before"] += t["volume_usd"]
        e["last_ts"] = t["ts"]

last_px = {p: bl[-1][4] for p, bl in bars_by_pair.items() if bl}

out = {}
for (w, p), e in eps.items():
    if e["first_buy"] is None:
        continue  # sell-only: no in-window buy -> excluded entirely (matched accounting)
    tok, sym = pair_tok.get(p, ("", ""))
    bq, sq = e["buy_qty"], e["sell_qty_after"]
    cov_sell = e["sell_usd_after"]
    capped = False
    if bq > 0 and sq > bq * 1.05:
        cov_sell = e["sell_usd_after"] * (bq / sq)
        capped = True
        rem_qty = 0.0
    else:
        rem_qty = max(0.0, bq - sq)
    realized = cov_sell - e["buy_usd"]
    lp = last_px.get(p)
    unreal = rem_qty * lp if (lp and rem_qty > 0) else 0.0
    frac_sold = (sq / bq) if bq > 0 else 0.0
    rec = {"pair": p, "sym": sym, "day": e["first_buy"][:10],
           "buy_usd": round(e["buy_usd"], 2), "sell_usd_after": round(e["sell_usd_after"], 2),
           "sell_usd_before": round(e["sell_usd_before"], 2),
           "n_buys": e["n_buys"], "n_sells": e["n_sells"],
           "realized": round(realized, 2), "unreal": round(unreal, 2),
           "net": round(realized + unreal, 2), "capped_preinv": capped,
           "no_px": bool(bq == 0 and e["buy_usd"] > 0),
           "frac_sold": round(min(1.0, frac_sold), 3), "px_missing": e["px_missing"],
           "first_buy": e["first_buy"], "last_ts": e["last_ts"],
           "buy_ts": e["buy_ts"], "buy_usd_list": e["buy_usd_list"],
           "sell_ts": e["sell_ts"], "sell_usd_list": e["sell_usd_list"], "sell_px": e["sell_px"],
           "buy_vwap": round(e["buy_usd"] / bq, 12) if bq > 0 else None}
    out.setdefault(w, []).append(rec)

json.dump(out, open(os.path.join(RIP, "ledger3_wallets.json"), "w"))
n_eps = sum(len(v) for v in out.values())
from collections import Counter
dc = Counter(r["day"] for v in out.values() for r in v)
print("wallets:", len(out), "episodes:", n_eps, "by day:", dict(sorted(dc.items())))
