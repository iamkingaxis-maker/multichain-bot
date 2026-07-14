"""Steep-fall entry analysis for winner wallets.

For each followable winner: get full mint + buy timestamp via RPC trade_map,
resolve pool (DexScreener), fetch GT 1m candles ending at the buy minute,
compute the 3-min cumulative % change BEFORE entry (the filter_1m_steep_fall
signal: BLOCK when 1m_cum_3min_pct < -1.5). Classify each buy:
  steep-flush buy  = cum_3min <= -1.5  (what the filter would BLOCK)
  + extras: max single-1m drop in window, consec-red (still-falling knife).
"""
import json, sys, time, urllib.request, urllib.error, datetime, statistics, os
sys.path.insert(0, "scripts"); sys.path.insert(0, ".")
import score_wallet_diversity as swd
import collections

UA = {"User-Agent": "Mozilla/5.0"}

def trade_buys(addr, sigs=200):
    """Return {mint: [buy_unix_ts,...]} for buys (token up, sol down)."""
    sl = swd._rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    tok = collections.defaultdict(list)
    for s in sl:
        sig, bt = s.get("signature"), s.get("blockTime")
        if not sig or s.get("err") or not bt: continue
        tx = swd._rpc("getTransaction", [sig, {"maxSupportedTransactionVersion":0,"encoding":"jsonParsed"}])
        time.sleep(0.05)
        if not tx or not tx.get("meta"): continue
        meta = tx["meta"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner")==addr}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner")==addr}
        try:
            keys=[k if isinstance(k,str) else k.get("pubkey") for k in tx["transaction"]["message"]["accountKeys"]]
            wi=keys.index(addr); sol_d=(meta["postBalances"][wi]-meta["preBalances"][wi])/1e9
        except Exception: continue
        deltas={m:post.get(m,0)-pre.get(m,0) for m in set(list(pre)+list(post)) if m not in swd.STABLE}
        deltas={m:d for m,d in deltas.items() if abs(d)>0}
        if not deltas: continue
        mint=max(deltas,key=lambda m:abs(deltas[m])); d=deltas[mint]
        if d>0 and sol_d<0:
            tok[mint].append(bt)
    return tok

_pool_cache={}
def resolve_pool(mint):
    if mint in _pool_cache: return _pool_cache[mint]
    url=f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
    try:
        d=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=15))
        pairs=d if isinstance(d,list) else d.get("pairs",[])
        sol=[p for p in pairs if p.get("chainId")=="solana"]
        sol.sort(key=lambda p:-(float((p.get("liquidity") or {}).get("usd") or 0)))
        pa=sol[0].get("pairAddress") if sol else None
    except Exception as e:
        pa=None
    _pool_cache[mint]=pa
    return pa

def gt_1m(pool, before_ts, limit=8):
    url=(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute"
         f"?aggregate=1&before_timestamp={before_ts}&limit={limit}&currency=usd")
    for attempt in range(3):
        try:
            d=json.load(urllib.request.urlopen(urllib.request.Request(url,headers=UA),timeout=15))
            rows=d["data"]["attributes"]["ohlcv_list"]
            rows.sort(key=lambda r:r[0])
            return rows  # [ts,o,h,l,c,v]
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(4); continue
            return []
        except Exception:
            return []
    return []

def analyze_buy(pool, buy_ts):
    """3min cum %, max 1m drop, consec-red ending at buy minute."""
    rows=gt_1m(pool, buy_ts+90, limit=8)  # candles up to ~buy minute
    time.sleep(2.4)
    if len(rows)<4: return None
    # keep candles whose open_time <= buy_ts (the minute being entered + prior)
    rows=[r for r in rows if r[0] <= buy_ts+60]
    if len(rows)<4: return None
    last4=rows[-4:]  # need close 3min before and at entry
    c_entry=last4[-1][4]
    c_3ago=last4[-4][4]
    if not c_3ago: return None
    cum3=(c_entry/c_3ago-1)*100
    # max single 1m drop (close vs open) within last 3 candles
    drops=[ (r[4]/r[1]-1)*100 for r in last4[-3:] if r[1] ]
    maxdrop=min(drops) if drops else 0
    # consec red: how many of trailing candles closed below open
    cr=0
    for r in reversed(last4):
        if r[1] and r[4] < r[1]: cr+=1
        else: break
    return dict(cum3=cum3, maxdrop=maxdrop, consec_red=cr, n=len(rows))

def run(addr, label, sigs=200, max_tokens=40):
    print(f"\n##### {label} {addr} #####", flush=True)
    tok=trade_buys(addr, sigs)
    print(f"tokens with buys: {len(tok)}", flush=True)
    results=[]
    for mint, ts_list in tok.items():
        bt=min(ts_list)  # first entry
        pool=resolve_pool(mint)
        if not pool:
            print(f"  {mint[:8]} buy {datetime.datetime.utcfromtimestamp(bt):%m-%d %H:%M}  NO POOL", flush=True)
            continue
        a=analyze_buy(pool, bt)
        if a is None:
            print(f"  {mint[:8]} buy {datetime.datetime.utcfromtimestamp(bt):%m-%d %H:%M}  no candles", flush=True)
            continue
        flush = a["cum3"] <= -1.5
        knife = a["consec_red"]>=3
        steep = a["maxdrop"] <= -10
        print(f"  {mint[:8]} {datetime.datetime.utcfromtimestamp(bt):%m-%d %H:%M} "
              f"cum3={a['cum3']:+6.1f}% maxdrop={a['maxdrop']:+6.1f}% cr={a['consec_red']} "
              f"{'FLUSH-BUY' if flush else 'stable '} {'KNIFE' if knife else ''} {'STEEP' if steep else ''}", flush=True)
        results.append((mint,bt,a,flush,knife,steep))
    if results:
        fl=sum(1 for r in results if r[3])
        kn=sum(1 for r in results if r[4])
        st=sum(1 for r in results if r[5])
        cums=[r[2]["cum3"] for r in results]
        print(f"  SUMMARY {label}: n={len(results)} flush-buys(cum3<=-1.5)={fl} ({fl/len(results):.0%}) "
              f"knife(cr>=3)={kn} steep(maxdrop<=-10)={st} | median cum3={statistics.median(cums):+.1f}%", flush=True)
    return results

if __name__=="__main__":
    addr=sys.argv[1]; label=sys.argv[2] if len(sys.argv)>2 else addr[:8]
    sigs=int(sys.argv[3]) if len(sys.argv)>3 else 200
    run(addr, label, sigs)
