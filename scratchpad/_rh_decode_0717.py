#!/usr/bin/env python3
"""RH wallet decode — 2026-07-17 session tape (fresh collection window only).

Adapts the 07-11 decode method (maker-level ledgers, union-counting, the
sell_only extraction caveat) to TODAY's tape and TODAY's questions:
  1. winner behavior NOW (hold, sizing, ladder-vs-single-leg) vs the 07-11
     findings (aged pools + ~1m holds + all-out sells);
  2. extraction share (sell_only = invisible cost basis — NOT tradeable edge);
  3. per-pool tape health (context per the market-context rule);
  4. what the audited winners do that our bots don't.
P&L proxy = per (maker,pool) USD net flow. Open holdings UNPRICED — makers
with sells < 70% of buys are 'open' and excluded from realized (07-11 rule).
"""
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict

CUTOFF = sys.argv[1] if len(sys.argv) > 1 else "2026-07-17T02:10"

ROUTERS = {"0x8876789976decbfcbbbe364623c63652db8c0904",
           "0xcaf681a66d020601342297493863e78c959e5cb2"}
rows = []
for f in glob.glob("scratchpad/robinhood_tapes/tape_*.jsonl"):
    if os.path.getmtime(f) < 1752700000:          # untouched-old files skip fast
        pass
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if (str(r.get("ts", "")) >= CUTOFF
                        and (r.get("maker") or "").lower() not in ROUTERS):
                    rows.append(r)
    except OSError:
        continue

print(f"fresh rows since {CUTOFF}: {len(rows)}")
if not rows:
    sys.exit(0)
ts_all = sorted(r["ts"] for r in rows)
span_h = 0.0
try:
    from datetime import datetime
    span_h = (datetime.fromisoformat(ts_all[-1]) -
              datetime.fromisoformat(ts_all[0])).total_seconds() / 3600
except Exception:
    pass
pools = {r.get("pair") for r in rows}
makers = {r.get("maker") for r in rows}
print(f"span {span_h:.2f}h | pools {len(pools)} | distinct makers {len(makers)}")
print(f"HONEST N CAVEAT: single {span_h:.1f}h window — accrual-stage read, "
      f"not a regime verdict.\n")

# ── tape health (market context first) ─────────────────────────────────────
buys = [r for r in rows if r.get("kind") == "buy"]
sells = [r for r in rows if r.get("kind") == "sell"]
bv = sum(float(r.get("volume_usd") or 0) for r in buys)
sv = sum(float(r.get("volume_usd") or 0) for r in sells)
print(f"TAPE: {len(buys)} buys ${bv:,.0f} vs {len(sells)} sells ${sv:,.0f} "
      f"| net flow ${bv - sv:+,.0f} ({'inflow' if bv > sv else 'OUTFLOW'})")

# ── per (maker,pool) ledgers ────────────────────────────────────────────────
led = defaultdict(lambda: {"b": 0.0, "s": 0.0, "nb": 0, "ns": 0,
                           "t0": None, "t1": None})
for r in rows:
    k = (r.get("maker"), r.get("pair"))
    L = led[k]
    v = float(r.get("volume_usd") or 0)
    if r.get("kind") == "buy":
        L["b"] += v; L["nb"] += 1
    else:
        L["s"] += v; L["ns"] += 1
    ts = r.get("ts")
    L["t0"] = ts if L["t0"] is None or ts < L["t0"] else L["t0"]
    L["t1"] = ts if L["t1"] is None or ts > L["t1"] else L["t1"]

def _mins(a, b):
    try:
        from datetime import datetime
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)
                ).total_seconds() / 60
    except Exception:
        return None

closed, sell_only, open_pos = [], [], []
for (mk, pool), L in led.items():
    net = L["s"] - L["b"]
    row = {"maker": mk, "pool": pool, "net": net, **L,
           "hold_m": _mins(L["t0"], L["t1"])}
    if L["nb"] == 0 and L["ns"] > 0:
        sell_only.append(row)
    elif L["b"] > 0 and L["s"] >= 0.7 * L["b"]:
        closed.append(row)
    elif L["b"] > 0:
        open_pos.append(row)

print(f"\nledgers: {len(closed)} closed | {len(open_pos)} open (excluded) "
      f"| {len(sell_only)} sell_only")
ext = sum(r["net"] for r in sell_only)
print(f"sell_only extraction (cost basis INVISIBLE, not tradeable edge): "
      f"${ext:+,.0f} across {len({r['maker'] for r in sell_only})} makers")

# ── audited winners/losers (closed ledgers only, union-counted) ────────────
per_maker = defaultdict(list)
for r in closed:
    per_maker[r["maker"]].append(r)
audited = []
for mk, ls in per_maker.items():
    net = sum(x["net"] for x in ls)
    audited.append({
        "maker": mk, "net": net, "pools": len(ls),
        "wins": sum(1 for x in ls if x["net"] > 0),
        "med_hold_m": st.median([x["hold_m"] for x in ls
                                 if x["hold_m"] is not None] or [0]),
        "med_buy": st.median([x["b"] / max(1, x["nb"]) for x in ls]),
        "med_legs_sell": st.median([x["ns"] for x in ls]),
        "med_legs_buy": st.median([x["nb"] for x in ls]),
    })
audited.sort(key=lambda x: -x["net"])
winners = [a for a in audited if a["net"] > 1]
losers = [a for a in audited if a["net"] < -1]
print(f"\nAUDITED on-tape traders: {len(audited)} makers "
      f"| winners(> $1): {len(winners)} ${sum(a['net'] for a in winners):+,.0f} "
      f"| losers(<-$1): {len(losers)} ${sum(a['net'] for a in losers):+,.0f}")

print(f"\nTOP 10 audited winners (behavior):")
print(f"{'maker':14} {'net$':>8} {'pools':>5} {'win':>4} {'hold_m':>7} "
      f"{'buy$':>7} {'sellLegs':>8} {'buyLegs':>7}")
for a in audited[:10]:
    print(f"{a['maker'][:14]:14} {a['net']:>8.0f} {a['pools']:>5} "
          f"{a['wins']:>4} {a['med_hold_m']:>7.1f} {a['med_buy']:>7.0f} "
          f"{a['med_legs_sell']:>8.1f} {a['med_legs_buy']:>7.1f}")
print(f"\nTOP 5 losers (what NOT to do):")
for a in audited[-5:]:
    print(f"{a['maker'][:14]:14} {a['net']:>8.0f} {a['pools']:>5} "
          f"{a['wins']:>4} {a['med_hold_m']:>7.1f} {a['med_buy']:>7.0f} "
          f"{a['med_legs_sell']:>8.1f} {a['med_legs_buy']:>7.1f}")

# behavior contrast: winners vs losers medians
def med(f, xs):
    v = [f(x) for x in xs if f(x) is not None]
    return st.median(v) if v else None
if winners and losers:
    print(f"\nBEHAVIOR CONTRAST (medians)   winners     losers")
    for k, lbl in [("med_hold_m", "hold (min)"), ("med_buy", "buy size $"),
                   ("med_legs_sell", "sell legs"), ("pools", "pools traded")]:
        print(f"  {lbl:18} {med(lambda a: a[k], winners):>9.1f} "
              f"{med(lambda a: a[k], losers):>9.1f}")

# ── EXTRACTOR-FLOW HYPOTHESIS (AxiS: "we will figure out how to profit from
# it"). If extractor sells are INFORMED supply, buyers entering right after an
# extractor sell-burst should fare worse than baseline buyers. Test: classify
# extractor wallets (sell_only makers with >$500 extracted), find their sell
# events, then compare the realized nets of (maker,pool) ledgers whose FIRST
# BUY lands within 10min after an extractor sell on that pool vs all others.
# Survives -> build the entry veto + held-exit trigger. Fails -> drop it.
from datetime import datetime

def _t(ts):
    try:
        return datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None

extractors = {r["maker"] for r in sell_only if r["net"] > 500}
ex_sells = defaultdict(list)              # pool -> [ts]
for r in rows:
    if r.get("kind") == "sell" and r.get("maker") in extractors:
        t = _t(r.get("ts"))
        if t:
            ex_sells[r.get("pair")].append(t)
for v in ex_sells.values():
    v.sort()

def after_burst(pool, first_buy_ts, window_s=600):
    t = _t(first_buy_ts)
    if t is None:
        return False
    return any(0 <= t - s <= window_s for s in ex_sells.get(pool, ()))

tainted, clean = [], []
for r in closed:
    (tainted if after_burst(r["pool"], r["t0"]) else clean).append(r["net"])
print(f"\nEXTRACTOR-FLOW HYPOTHESIS (buyers entering <=10min after an "
      f"extractor sell, closed ledgers only):")
print(f"  extractor wallets (sell_only >$500): {len(extractors)}")
if tainted and clean:
    print(f"  post-extractor buyers: n={len(tainted)} median net "
          f"${st.median(tainted):+.2f}  mean ${st.mean(tainted):+.2f}")
    print(f"  baseline buyers:       n={len(clean)} median net "
          f"${st.median(clean):+.2f}  mean ${st.mean(clean):+.2f}")
    print(f"  win-rate: post-extractor "
          f"{100*sum(1 for x in tainted if x>0)/len(tainted):.0f}% vs baseline "
          f"{100*sum(1 for x in clean if x>0)/len(clean):.0f}%")
else:
    print(f"  insufficient split (tainted={len(tainted)}, clean={len(clean)})")

out = {"rows": len(rows), "span_h": round(span_h, 2),
       "winners": audited[:20], "losers": audited[-10:],
       "extraction_usd": round(ext), "closed": len(closed),
       "extractor_test": {"n_extractors": len(extractors),
                          "tainted": tainted, "clean_n": len(clean),
                          "clean_median": (st.median(clean) if clean else None)}}
with open("scratchpad/_rh_decode_0717.json", "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)
print("\nsaved -> scratchpad/_rh_decode_0717.json")
