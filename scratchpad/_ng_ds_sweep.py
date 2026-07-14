"""Live DS sweep: (a) retro trade-log retention vs our buy timestamps,
(b) DS-fallback answer for buyers=None (no-tape) entries: does DS serve
a valid maker-bearing trade log for those tokens right now?"""
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cr
from feeds.dexscreener_trades_format import parse_trades

SOL = "So11111111111111111111111111111111111111112"
HDR = {"Origin": "https://dexscreener.com", "Referer": "https://dexscreener.com/",
       "Accept": "*/*"}
sess = cr.Session(impersonate="chrome", headers=HDR)
SLUG_MAP = {"raydium": "solamm", "pumpswap": "pumpfundex", "pumpfun": "pumpfundex",
            "meteora": "meteora"}

E = json.load(open("scratchpad/_ng_dataset.json"))
for e in E:
    e["feat"] = e.get("feat") or {}
    e["tape"] = e["feat"].get("rt_buys_n") is not None

notape = [e for e in E if not e["tape"]]
tapeok = [e for e in E if e["tape"]]
notape.sort(key=lambda r: -r["buy_ts"])
tapeok.sort(key=lambda r: -r["buy_ts"])
# dedup by pair
def dedup(rows, cap):
    seen, out = set(), []
    for r in rows:
        if r["pair"] in seen: continue
        seen.add(r["pair"]); out.append(r)
        if len(out) >= cap: break
    return out
sample = [("notape", e) for e in dedup(notape, 40)] + [("tape", e) for e in dedup(tapeok, 20)]
print("sweep size:", len(sample))

def fetch_one(e):
    pair = e["pair"]
    out = {"pair": pair, "token": e["token"], "bot": e["bot"], "label": e["label"],
           "buy_ts": e["buy_ts"], "buy_time": e.get("buy_time")}
    try:
        r = sess.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair}", timeout=15)
        if r.status_code != 200:
            out["step"] = f"meta_http_{r.status_code}"; return out
        p = r.json().get("pairs") or r.json().get("pair") or []
        if isinstance(p, dict): p = [p]
        if not p:
            out["step"] = "meta_no_pair"; return out
        dexid = (p[0].get("dexId") or "").lower()
        slug = SLUG_MAP.get(dexid, dexid)
        quote = (p[0].get("quoteToken") or {}).get("address") or SOL
        out["dexid"] = dexid
    except Exception as ex:
        out["step"] = f"meta_err_{type(ex).__name__}"; return out
    time.sleep(1.2)
    try:
        u = f"https://io.dexscreener.com/dex/log/amm/v4/{slug}/all/solana/{pair}?q={quote}&c=1"
        r = sess.get(u, timeout=20)
        out["log_http"] = r.status_code
        if r.status_code != 200:
            out["step"] = f"log_http_{r.status_code}"; return out
        tr = parse_trades(r.content, max_records=10000)
        out["n_trades"] = len(tr)
        out["n_makers"] = len({t.get("maker") for t in tr if t.get("maker")})
        if tr:
            tss = [datetime.fromisoformat(t["ts"]).timestamp() for t in tr]
            out["oldest"] = min(tss); out["newest"] = max(tss)
            out["covers_pre120"] = min(tss) <= e["buy_ts"] - 120
        out["step"] = "ok" if tr else "log_empty"
    except Exception as ex:
        out["step"] = f"log_err_{type(ex).__name__}"
    return out

results = []
fails = 0
for i, (grp, e) in enumerate(sample):
    r = fetch_one(e)
    r["grp"] = grp
    results.append(r)
    ok = r["step"] == "ok"
    if not ok:
        fails += 1
        if fails >= 6:
            print("  fail streak — backing off 70s"); time.sleep(70); fails = 0
    else:
        fails = 0
    if i % 10 == 0:
        print(f"  {i}/{len(sample)} {grp} {r['step']} n={r.get('n_trades')} makers={r.get('n_makers')}")
    time.sleep(1.6)

json.dump(results, open("scratchpad/_ng_ds_sweep.json", "w"), indent=1)

from collections import Counter
for grp in ("notape", "tape"):
    rs = [r for r in results if r["grp"] == grp]
    c = Counter(r["step"] for r in rs)
    ok = [r for r in rs if r["step"] == "ok"]
    mk = [r for r in ok if (r.get("n_makers") or 0) > 0]
    cov = [r for r in ok if r.get("covers_pre120")]
    print(f"\n{grp}: n={len(rs)} steps={dict(c)}")
    print(f"  valid log now: {len(ok)}/{len(rs)}  with makers: {len(mk)}  retro pre-entry window covered: {len(cov)}")
