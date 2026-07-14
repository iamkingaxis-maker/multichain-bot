import json, statistics
d=json.load(open('scratchpad/_full_trades.json'))
pending={}; pairs=[]
for t in d:
    key=(t.get('address'), t.get('bot_id'))
    if t['type']=='buy': pending[key]=t
    elif t['type']=='sell':
        b=pending.get(key)
        if b is not None: pairs.append((b,t)); pending[key]=None
def scrub(s): 
    p=s.get('pnl_pct'); h=s.get('hold_secs')
    return p is not None and h is not None and p>0 and h<10
clean=[(b,s) for b,s in pairs if s.get('pnl_pct') is not None and not scrub(s)]
def f(v):
    try:
        if v is None or isinstance(v,bool): return None
        return float(v)
    except: return None
def stale(em):
    if str(em.get('filter_stale_watch_verdict') or '').upper()!='BLOCK': return False
    mtf=em.get('chart_mtf_verdicts')
    if not isinstance(mtf,dict): return False
    vals={tf:str(mtf.get(tf,'')).lower() for tf in ('1m','5m','15m')}
    if any(v=='' for v in vals.values()): return False
    return all(v=='bear' for v in vals.values())
def dev(em):
    v=f(em.get('dev_pct_remaining')); return v is not None and v<20.0
def tmean(rows):
    toks={}
    for tok,p in rows: toks.setdefault(tok,[]).append(p)
    return statistics.mean([statistics.mean(v) for v in toks.values()]), len(toks)
for name,pred in [('stale_knife',stale),('dev_not_dumped',dev)]:
    print("\n===",name,"per-bot (blocked tok_mean vs passed tok_mean, distinct n)===")
    bybot={}
    for b,s in clean:
        bot=b.get('bot_id'); em=b.get('entry_meta') or {}
        bybot.setdefault(bot,{'bl':[],'pa':[]})
        (bybot[bot]['bl'] if pred(em) else bybot[bot]['pa']).append((b.get('address'),s['pnl_pct']))
    consistent=0; total=0
    for bot,g in sorted(bybot.items()):
        if len(g['bl'])<5 or len(g['pa'])<5: continue
        bm,bn=tmean(g['bl']); pm,pn=tmean(g['pa'])
        total+=1; ok = bm<pm
        if ok: consistent+=1
        print(f"  {bot:32s} blk {bm:+.2f}(n={bn:2d}) pass {pm:+.2f}(n={pn:2d}) {'blk<pass OK' if ok else 'INVERTED'}")
    print(f"  --> blocked-worse in {consistent}/{total} bots with >=5 each side")
