"""For each winner buy: reconstruct fill position vs local flush-low at MINUTE resolution (GT)."""
import json,sys,time,statistics as st
from datetime import datetime,timezone,timedelta
from curl_cffi import requests as cr
S=cr.Session(impersonate="chrome")
def gt(url,tries=4):
    for t in range(tries):
        try:
            r=S.get(url,timeout=25)
            if r.status_code==200: return r.json()
            time.sleep(9 if r.status_code==429 else 3)
        except: time.sleep(4)
    return None
_pool={}
def pool_ohlc(mint):
    if mint in _pool: return _pool[mint]
    j=gt(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools"); time.sleep(2.5)
    if not j or not j.get("data"): _pool[mint]=None; return None
    best=max(j["data"],key=lambda p:float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
    a=best.get("attributes",{}); pair=best.get("id","").replace("solana_","")
    try: created=datetime.fromisoformat(a.get("pool_created_at").replace("Z","+00:00")).timestamp()
    except: created=None
    liq=float(a.get("reserve_in_usd") or 0); fdv=float(a.get("fdv_usd") or 0)
    oj=gt(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate=1&limit=1000"); time.sleep(2.5)
    oh=(oj or {}).get("data",{}).get("attributes",{}).get("ohlcv_list",[]) if oj else []
    _pool[mint]=(created,liq,fdv,oh,pair); return _pool[mint]

# SOL/USD minute price (Raydium SOL/USDC top pool)
_sol=None
def sol_price(ts):
    global _sol
    if _sol is None:
        j=gt("https://api.geckoterminal.com/api/v2/networks/solana/tokens/So11111111111111111111111111111111111111112/pools"); time.sleep(2.5)
        best=max(j["data"],key=lambda p:float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
        pair=best.get("id","").replace("solana_","")
        oj=gt(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate=1&limit=1000"); time.sleep(2.5)
        oh=(oj or {}).get("data",{}).get("attributes",{}).get("ohlcv_list",[])
        _sol=sorted(oh,key=lambda r:r[0])
    # nearest bar <= ts
    best=None
    for r in _sol:
        if r[0]<=ts: best=r[4]
        else: break
    return best or (_sol[-1][4] if _sol else 150.0)

def analyze(buy):
    meta=pool_ohlc(buy["mint"])
    if not meta: return None
    created,liq,fdv,oh,pair=meta
    if not oh: return None
    rows=sorted(oh,key=lambda r:r[0])
    ts=buy["bt"]
    # fill price in USD
    sp=sol_price(ts)
    fill_usd=buy["price_sol"]*sp
    # entry bar = last bar <= ts
    ei=None
    for i,r in enumerate(rows):
        if r[0]<=ts: ei=i
        else: break
    if ei is None: return None
    bar=rows[ei]  # [ts,o,h,l,c,v]
    # validate fill within bar range (sanity)
    bl,bh=bar[3],bar[2]
    in_bar = bl*0.9 <= fill_usd <= bh*1.1
    # trailing windows
    def winlow(sec):
        w=[r for r in rows[:ei+1] if ts-r[0]<=sec]
        if not w: return None,None
        lowbar=min(w,key=lambda r:r[3])
        return lowbar[3], (ts-lowbar[0])
    out={"mint":buy["mint"],"bt":ts,"sol":buy["sol"],"fill_usd":fill_usd,
         "liq":liq,"fdv":fdv,"in_bar":in_bar,"bar_lo":bl,"bar_hi":bh}
    for nm,sec in [("15m",900),("30m",1800),("90m",5400)]:
        lo,since=winlow(sec)
        if lo and lo>0:
            out[f"gap_{nm}"]=(fill_usd/lo-1)*100
            out[f"since_{nm}"]=since/60.0  # minutes since window low
    # position within entry bar
    if bh>bl: out["barpos"]=(fill_usd-bl)/(bh-bl)
    # forward 6h max
    fwd=[r for r in rows[ei+1:] if r[0]-ts<=21600]
    if fwd: out["fwd_max"]=(max(r[2] for r in fwd)/fill_usd-1)*100
    # dip off 90m high at entry
    prior=[r for r in rows[:ei+1] if ts-r[0]<=5400]
    hi90=max((r[2] for r in prior),default=bh)
    if hi90>0: out["dip_90m"]=(fill_usd/hi90-1)*100
    return out

if __name__=="__main__":
    allb=json.load(open("scratchpad/all_winner_buys.json"))
    res=[]
    for k,blist in allb.items():
        # dedup distinct tokens per wallet (first buy per token)
        seen=set(); db=[]
        for b in sorted(blist,key=lambda x:x["bt"]):
            if b["mint"] in seen: continue
            seen.add(b["mint"]); db.append(b)
        print(f"{k}: {len(db)} distinct-token buys",file=sys.stderr)
        for b in db:
            try:
                r=analyze(b)
            except Exception as e:
                print("err",e,file=sys.stderr); r=None
            if r: r["wallet"]=k; res.append(r)
    json.dump(res,open("scratchpad/winner_recon.json","w"),indent=0)
    print(f"\nreconstructed {len(res)} distinct-token winner entries")
