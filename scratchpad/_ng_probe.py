"""Probe DS internal endpoints: trade-log reach, c param, bars retro 'to' param."""
import json, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from curl_cffi import requests as cr
from feeds.dexscreener_trades_format import parse_trades
from feeds.dexscreener_chart_format import parse_chart_bars

SOL = "So11111111111111111111111111111111111111112"
HDR = {"Origin": "https://dexscreener.com", "Referer": "https://dexscreener.com/",
       "Accept": "*/*"}
sess = cr.Session(impersonate="chrome", headers=HDR)

entries = json.load(open("scratchpad/_ng_entries.json"))
entries.sort(key=lambda r: -r["buy_ts"])

def get_slug(pair):
    r = sess.get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{pair}", timeout=15)
    if r.status_code != 200:
        return None, None
    p = (r.json().get("pairs") or r.json().get("pair") or [])
    if isinstance(p, dict): p = [p]
    if not p: return None, None
    dexid = (p[0].get("dexId") or "").lower()
    slug = {"raydium": "solamm", "pumpswap": "pumpfundex", "pumpfun": "pumpfundex",
            "meteora": "meteora"}.get(dexid, dexid)
    quote = (p[0].get("quoteToken") or {}).get("address") or SOL
    return slug, quote

# probe on: newest NG entry, an old (07-02) NG entry, a newest BOUNCED
picks = []
for want, key in [("NEVER_GREEN", "new"), ("BOUNCED", "new")]:
    for e in entries:
        if e["label"] == want:
            picks.append((key + "_" + want, e)); break
for e in reversed(entries):
    if e["label"] == "NEVER_GREEN":
        picks.append(("old_NEVER_GREEN", e)); break

for tag, e in picks:
    pair = e["pair"]
    slug, quote = get_slug(pair)
    print("=" * 60)
    print(tag, e["token"], e["bot"], "buy", e.get("buy_time"), "slug", slug)
    if not slug: continue
    buy_ts = e["buy_ts"]
    time.sleep(1.2)
    for c in ("1", "400"):
        u = f"https://io.dexscreener.com/dex/log/amm/v4/{slug}/all/solana/{pair}?q={quote}&c={c}"
        r = sess.get(u, timeout=20)
        tr = parse_trades(r.content, max_records=10000) if r.status_code == 200 else []
        if tr:
            tss = [datetime.fromisoformat(t["ts"]).timestamp() for t in tr]
            print(f" log c={c}: HTTP {r.status_code} n={len(tr)} oldest={datetime.fromtimestamp(min(tss),timezone.utc)} newest={datetime.fromtimestamp(max(tss),timezone.utc)} covers_pre120={'YES' if min(tss) <= buy_ts-120 else 'no'} (buy-oldest={buy_ts-min(tss):.0f}s)")
        else:
            print(f" log c={c}: HTTP {r.status_code} n=0 bytes={len(r.content)}")
        time.sleep(1.5)
    # bars retro probe: to = buy_ts ms
    for extra in ("", f"&to={int(buy_ts*1000)}", f"&tb={int(buy_ts*1000)}"):
        u = (f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{pair}"
             f"?res=1&cb=30&q={quote}" + extra)
        r = sess.get(u, timeout=20)
        bars = parse_chart_bars(r.content) if r.status_code == 200 else []
        if bars:
            print(f" bars {extra or 'plain'}: HTTP {r.status_code} n={len(bars)} first={datetime.fromtimestamp(bars[0]['ts_ms']/1000,timezone.utc)} last={datetime.fromtimestamp(bars[-1]['ts_ms']/1000,timezone.utc)}")
        else:
            print(f" bars {extra or 'plain'}: HTTP {r.status_code} n=0 bytes={len(r.content)}")
        time.sleep(1.5)
