import json, collections, statistics, math

d = json.load(open('_full_trades.json'))
t = d.get('trades', d) if isinstance(d, dict) else d
BADDAY = {'badday_flush','badday_flush_nf15','badday_flush_conviction','badday_flush_conviction_demand'}
bot = lambda x: x.get('bot_id') or x.get('strategy')
buys = {(bot(x),(x.get('address') or x.get('token') or '').lower()): x
        for x in t if x.get('type')=='buy' and bot(x) in BADDAY}
sells = [x for x in t if x.get('type')=='sell' and bot(x) in BADDAY and isinstance(x.get('pnl_pct'),(int,float))]

def day(ts):
    return (ts or '')[:10][5:]  # 'MM-DD'

rows = []
for s in sells:
    k = (bot(s),(s.get('address') or s.get('token') or '').lower())
    b = buys.get(k)
    if not b: continue
    em = b.get('entry_meta') or {}
    pnl = s.get('pnl_pct'); mae = s.get('mae_pct')
    real = pnl
    if isinstance(mae,(int,float)) and pnl < mae: real = mae
    rows.append({'day':day(s.get('time')),'real':real,'em':em,
                 'addr':(b.get('address') or b.get('token') or '').lower(),'bot':bot(s)})

def stats(rs):
    if not rs: return (0,0,0)
    rr=[r['real'] for r in rs]
    return (len(rr), sum(1 for x in rr if x>0)/len(rr), statistics.mean(rr))

if __name__=='__main__':
    dd=collections.Counter(r['day'] for r in rows)
    print('joined rows',len(rows),'days',dict(dd))
    for dy in sorted(dd):
        n,wr,m=stats([r for r in rows if r['day']==dy])
        print(f'{dy}: n={n} WR={wr:.2f} mean_real={m:+.2f}')

def num(em,k):
    v=em.get(k)
    return v if isinstance(v,(int,float)) and not isinstance(v,bool) else None

def scan_threshold(feat, lo_is_falling=True):
    """For a numeric path feature, find separation. Report per-day WR for pass/block split at median."""
    vals=[(r,num(r['em'],feat)) for r in rows]
    vals=[(r,v) for r,v in vals if v is not None]
    if len(vals)<30: 
        print(f'{feat}: only n={len(vals)} nonnull -- THIN'); return
    med=statistics.median(v for _,v in vals)
    # try a few quantile cuts
    import numpy as np
    arr=sorted(v for _,v in vals)
    for q in [0.25,0.5,0.75]:
        thr=arr[int(q*(len(arr)-1))]
        hi=[r for r,v in vals if v>=thr]; lo=[r for r,v in vals if v<thr]
        nh,wh,mh=stats(hi); nl,wl,ml=stats(lo)
        print(f'{feat} q{q} thr={thr:.4g}  HI n={nh} WR={wh:.2f} m={mh:+.2f} | LO n={nl} WR={wl:.2f} m={ml:+.2f}')

def perday_pred(name, predfn):
    """predfn(em)->True means PASS (we'd allow). Report per-day WR/mean for PASS vs BLOCK, token conc."""
    print(f'=== {name} ===')
    by={}
    for r in rows:
        v=predfn(r['em'])
        if v is None: continue
        by.setdefault(('PASS' if v else 'BLOCK'),[]).append(r)
    for grp in ['PASS','BLOCK']:
        rs=by.get(grp,[])
        n,wr,m=stats(rs)
        # per day
        pd={}
        for dy in ['06-21','06-22','06-23']:
            sd=[r for r in rs if r['day']==dy]; pd[dy]=stats(sd)
        # token concentration: top token share of group
        tc=collections.Counter(r['addr'] for r in rs)
        topshare = (tc.most_common(1)[0][1]/n) if n else 0
        ntok=len(tc)
        print(f' {grp}: n={n} WR={wr:.2f} m={m:+.2f} ntok={ntok} topTokShare={topshare:.0%}')
        for dy in ['06-21','06-22','06-23']:
            dn,dw,dm=pd[dy]; print(f'    {dy}: n={dn} WR={dw:.2f} m={dm:+.2f}')
    print()
