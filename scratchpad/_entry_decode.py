"""Dip-vs-strength entry decode for in-pond wallets' pond buys (res=5 bars)."""
import json, time, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cr
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = "So11111111111111111111111111111111111111112"
cls = {r["wallet"]: r["class"] for r in json.load(open("scratchpad/_toptrader_class.json"))}
tok = json.load(open("scratchpad/_toptrader_tokens.json"))

# resolve full mirror address
mirror = [w for w in cls if w.startswith("YupUTKEj")]
MIRROR = set(mirror)

# gather pond buys per mint from in-pond operators (excluding mirror)
from collections import defaultdict
pond_buys = defaultdict(list)
for line in open("scratchpad/_toptrader_activity.jsonl"):
    d = json.loads(line)
    w = d["wallet"]
    if cls.get(w) != "IN-POND" or w in MIRROR:
        continue
    for t in d["trades"]:
        if t["side"] != "buy":
            continue
        info = tok.get(t["mint"])
        if not info:
            continue
        try:
            mc = float(t["price_usd"]) * float(t["supply"])
        except (TypeError, ValueError):
            continue
        if not (100_000 <= mc <= 5_000_000):
            continue
        pond_buys[t["mint"]].append({"w": w, "ts": t["ts"], "usd": t["usd"], "mc": mc})

mints = sorted(pond_buys, key=lambda m: -len(pond_buys[m]))[:80]
print(f"pond mints: {len(pond_buys)}; sampling {len(mints)}; "
      f"buys covered: {sum(len(pond_buys[m]) for m in mints)}")

sess = cr.Session(impersonate="chrome", timeout=20,
                  headers={"Origin": "https://dexscreener.com", "Referer": "https://dexscreener.com/"})

# pair resolution (need pairAddress + dexId)
pairs = {}
for i in range(0, len(mints), 30):
    batch = mints[i:i+30]
    r = sess.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(batch))
    best = {}
    for p in r.json() or []:
        m = p["baseToken"]["address"]
        liq = (p.get("liquidity") or {}).get("usd") or 0
        if m not in best or liq > best[m][2]:
            best[m] = (p["pairAddress"], p["dexId"].lower(), liq)
    pairs.update(best)
    time.sleep(1.4)
print("pairs resolved:", len(pairs))

SLUG = {"raydium": "solamm", "pumpswap": "pumpfundex", "pumpfun": "pumpfundex"}
results = []
nobar = 0
for mi, m in enumerate(mints):
    if m not in pairs:
        continue
    pair, dexid, _ = pairs[m]
    slug = SLUG.get(dexid, dexid)
    u = (f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}"
         f"?res=5&cb=1500&q={SOL}")
    try:
        r = sess.get(u)
        bars = parse_chart_bars(r.content) if r.status_code == 200 else []
    except Exception:
        bars = []
    time.sleep(1.3)
    if not bars:
        nobar += 1
        continue
    idx = {b["ts_ms"] // 300000: b for b in bars}
    info = tok.get(m) or {}
    created = info.get("created")
    for b in pond_buys[m]:
        slot = b["ts"] * 1000 // 300000
        if slot not in idx:
            continue
        prior = [idx[s] for s in range(slot - 12, slot) if s in idx]
        if len(prior) < 8:
            continue
        entry = idx[slot]
        hi60 = max(p["high"] for p in prior)
        dd = entry["close"] / hi60 - 1
        # local low timing
        lows = [(p["low"], p["ts_ms"]) for p in prior]
        lowv, lowts = min(lows)
        mins_since_low = (b["ts"] * 1000 - lowts) / 60000
        # 15m momentum
        c3 = prior[-3]["close"] if len(prior) >= 3 else prior[0]["close"]
        mom15 = entry["close"] / c3 - 1
        age_h = (b["ts"] - created / 1000) / 3600 if created else None
        results.append({"w": b["w"], "mint": m, "ts": b["ts"], "usd": b["usd"],
                        "mc": b["mc"], "dd60": dd, "mins_since_low": mins_since_low,
                        "mom15": mom15, "age_h": age_h})
    if mi % 20 == 0:
        print(f"  {mi}/{len(mints)} bars-missing={nobar} scored={len(results)}")

json.dump(results, open("scratchpad/_entry_scored.json", "w"))
print(f"scored buys: {len(results)}; tokens w/o bars: {nobar}")

def pct(v, p):
    v = sorted(v)
    return v[int(p / 100 * (len(v) - 1))] if v else None

dd = [r["dd60"] * 100 for r in results]
print(f"\ndd-from-60m-high at entry (%): n={len(dd)} p10={pct(dd,10):.1f} p25={pct(dd,25):.1f} "
      f"med={pct(dd,50):.1f} p75={pct(dd,75):.1f} p90={pct(dd,90):.1f}")
ndip = sum(1 for x in dd if x <= -15)
nstr = sum(1 for x in dd if x >= -5)
print(f"DIP(<=-15%): {ndip/len(dd):.0%}  NEAR-HIGH(>=-5%): {nstr/len(dd):.0%}  MID: {1-(ndip+nstr)/len(dd):.0%}")
mm = [r["mom15"] * 100 for r in results]
print(f"15m momentum at entry (%): med={pct(mm,50):.1f} p25={pct(mm,25):.1f} p75={pct(mm,75):.1f}  "
      f"neg-share={sum(1 for x in mm if x < 0)/len(mm):.0%}")
msl = [r["mins_since_low"] for r in results]
print(f"mins since 60m local low: p25={pct(msl,25):.0f} med={pct(msl,50):.0f} p75={pct(msl,75):.0f}")
ages = [r["age_h"] for r in results if r["age_h"] is not None]
print(f"token age at buy (h): n={len(ages)} p10={pct(ages,10):.1f} p25={pct(ages,25):.1f} "
      f"med={pct(ages,50):.1f} p75={pct(ages,75):.1f} p90={pct(ages,90):.1f}")
mcs = [r["mc"] for r in results]
print(f"mcap at buy ($k): p25={pct(mcs,25)/1e3:.0f} med={pct(mcs,50)/1e3:.0f} p75={pct(mcs,75)/1e3:.0f}")
