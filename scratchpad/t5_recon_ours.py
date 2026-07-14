"""Reconstruct OUR entries vs local flush-low at MINUTE resolution (GT), same metric as winners.
Uses entry_price (USD fill) AND entry_mid_price (USD decision) directly - no SOL conversion."""
import json,sys,time
from datetime import datetime,timezone
sys.path.insert(0,'scratchpad')
import importlib.util
spec=importlib.util.spec_from_file_location("rc","scratchpad/t5_recon.py")
rc=importlib.util.module_from_spec(spec); spec.loader.exec_module(rc)

def parse(t):
    try: return datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
    except: return None

def analyze_ours(addr,ts,fill_usd,decision_usd):
    meta=rc.pool_ohlc(addr)
    if not meta: return None
    created,liq,fdv,oh,pair=meta
    if not oh: return None
    rows=sorted(oh,key=lambda r:r[0])
    ei=None
    for i,r in enumerate(rows):
        if r[0]<=ts: ei=i
        else: break
    if ei is None: return None
    bar=rows[ei]; bl,bh=bar[3],bar[2]
    out={"addr":addr,"bt":ts,"fill_usd":fill_usd,"decision_usd":decision_usd,"liq":liq,"fdv":fdv}
    def winlow(sec):
        w=[r for r in rows[:ei+1] if ts-r[0]<=sec]
        if not w: return None,None
        lb=min(w,key=lambda r:r[3]); return lb[3],(ts-lb[0])
    for nm,sec in [("15m",900),("30m",1800),("90m",5400)]:
        lo,since=winlow(sec)
        if lo and lo>0:
            out[f"gap_{nm}"]=(fill_usd/lo-1)*100
            out[f"gapdec_{nm}"]=(decision_usd/lo-1)*100 if decision_usd else None
            out[f"since_{nm}"]=since/60.0
    if bh>bl:
        out["barpos"]=(fill_usd-bl)/(bh-bl)
    fwd=[r for r in rows[ei+1:] if r[0]-ts<=21600]
    if fwd: out["fwd_max"]=(max(r[2] for r in fwd)/fill_usd-1)*100
    return out

if __name__=="__main__":
    d=json.load(open('_full_trades.json'))
    buys=[r for r in d if r.get('type')=='buy']
    # distinct token (address), first buy, only those new enough for GT window (last ~16h)
    import time as _t
    cutoff=_t.time()-16*3600
    seen=set(); sample=[]
    for r in sorted(buys,key=lambda x:parse(x.get('time')) or 0):
        a=r.get('address'); ts=parse(r.get('time'))
        if not a or not ts or a in seen: continue
        seen.add(a)
        if ts>=cutoff:
            sample.append((a,ts,r.get('entry_price'),r.get('entry_mid_price')))
    print(f"our distinct-token buys in GT window: {len(sample)}",file=sys.stderr)
    res=[]
    for a,ts,ep,emp in sample:
        try: o=analyze_ours(a,ts,ep,emp)
        except Exception as e: print("err",e,file=sys.stderr); o=None
        if o: res.append(o)
    json.dump(res,open("scratchpad/ours_recon.json","w"),indent=0)
    print(f"reconstructed {len(res)} OUR distinct-token entries")
