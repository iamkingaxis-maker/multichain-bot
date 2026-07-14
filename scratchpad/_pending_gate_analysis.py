import json, statistics
d=json.load(open('scratchpad/_full_trades.json'))
# pair sells to prior buy per (address,bot_id)
pending={}  # key -> last buy
pairs=[]
for t in d:
    key=(t.get('address'), t.get('bot_id'))
    if t['type']=='buy':
        pending[key]=t
    elif t['type']=='sell':
        b=pending.get(key)
        if b is not None:
            pairs.append((b,t))
            pending[key]=None  # consume
print("paired sells:", len(pairs))

# scrub trivial round-trips: drop ret>0 & hold<10s
def scrub(b,s):
    p=s.get('pnl_pct'); h=s.get('hold_secs')
    if p is not None and h is not None and p>0 and h<10:
        return True
    return False

clean=[(b,s) for (b,s) in pairs if not scrub(b,s) and s.get('pnl_pct') is not None]
print("after scrub w/ pnl:", len(clean))

def f(v):
    try:
        if v is None or isinstance(v,bool): return None
        return float(v)
    except: return None

def gate_stale_knife(em):
    sw=str(em.get('filter_stale_watch_verdict') or '').upper()
    if sw!='BLOCK': return False
    mtf=em.get('chart_mtf_verdicts')
    if not isinstance(mtf,dict): return False
    vals={tf:str(mtf.get(tf,'')).lower() for tf in ('1m','5m','15m')}
    if any(vals[tf]=='' for tf in vals): return False
    return all(vals[tf]=='bear' for tf in vals)

def gate_dev_not_dumped(em):
    v=f(em.get('dev_pct_remaining'))
    if v is None: return False
    return v < 20.0

def gate_oversold_held_pass(em):
    r=f(em.get('rsi_15m')); dv=f(em.get('dev_pct_remaining'))
    if r is None or dv is None: return None  # absent -> would be blocked (dark)
    return (r<=44.0 and dv>=10.0)

def stats(rows):
    if not rows: return None
    # rows = list of (token, pnl_pct)
    # distinct-token: aggregate mean pnl per token? report both trades and distinct
    pnls=[p for (_,p) in rows]
    toks={}
    for tok,p in rows:
        toks.setdefault(tok,[]).append(p)
    tok_means=[statistics.mean(v) for v in toks.values()]
    wr=100*sum(1 for p in pnls if p>0)/len(pnls)
    tok_wr=100*sum(1 for m in tok_means if m>0)/len(tok_means)
    return dict(trades=len(pnls), n_distinct=len(toks),
                trade_mean=round(statistics.mean(pnls),3),
                trade_med=round(statistics.median(pnls),3),
                trade_wr=round(wr,1),
                tok_mean=round(statistics.mean(tok_means),3),
                tok_med=round(statistics.median(tok_means),3),
                tok_wr=round(tok_wr,1))

for name,pred in [('stale_knife',gate_stale_knife),('dev_not_dumped',gate_dev_not_dumped)]:
    blocked=[]; passed=[]
    for b,s in clean:
        em=b.get('entry_meta') or {}
        tok=b.get('address'); p=s['pnl_pct']
        if pred(em): blocked.append((tok,p))
        else: passed.append((tok,p))
    print("\n===",name,"===")
    print("BLOCKED:",stats(blocked))
    print("PASSED :",stats(passed))

# oversold_held: pass cohort = kept; blocked = complement (incl absent -> dark)
blocked=[]; passed=[]; absent=0
for b,s in clean:
    em=b.get('entry_meta') or {}
    tok=b.get('address'); p=s['pnl_pct']
    r=gate_oversold_held_pass(em)
    if r is True: passed.append((tok,p))
    else:
        blocked.append((tok,p))
        if r is None: absent+=1
print("\n=== oversold_held (positive selector) ===")
print("KEPT (rsi<=44 & dev>=10):",stats(passed))
print("REJECTED (complement incl absent):",stats(blocked), "absent_in_rejected:",absent)
# rejected split: feature-present-but-fail vs absent
rej_present=[]; rej_absent=[]
for b,s in clean:
    em=b.get('entry_meta') or {}
    tok=b.get('address'); p=s['pnl_pct']
    r=gate_oversold_held_pass(em)
    if r is False: rej_present.append((tok,p))
    elif r is None: rej_absent.append((tok,p))
print("REJECTED-feature-present:",stats(rej_present))
print("REJECTED-feature-absent(dark):",stats(rej_absent))
