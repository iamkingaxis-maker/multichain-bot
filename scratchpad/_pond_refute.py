import ijson, statistics as st
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

f=open('_trades_cache.json','rb')
trips=[]
nbuys=0
for rec in ijson.items(f,'item'):
    if rec.get('bot_id')!='baseline_v1': continue
    if rec['type']=='buy': nbuys+=1; continue
    if rec['type']!='sell': continue
    m=rec.get('entry_meta') or {}
    def fl(x):
        try: return float(x)
        except: return None
    exit_t=datetime.fromisoformat(rec['time'])
    hold=fl(rec.get('hold_secs')) or 0.0
    ent_t=exit_t - timedelta(seconds=hold)
    trips.append(dict(
        addr=rec.get('address'), tok=rec.get('token'),
        mcap=fl(rec.get('entry_market_cap_usd')),
        age=fl(rec.get('entry_age_hours')),
        vol=fl(rec.get('entry_volume_h1_usd')),
        liq=fl(m.get('liquidity_usd')),
        proto=m.get('protocol'),
        peak=fl(rec.get('peak_pnl_pct')),
        peak_at=fl(rec.get('peak_pnl_at_secs')),
        real=fl(rec.get('pnl_pct')),
        mae=fl(rec.get('max_drawdown_pct')),
        hold=hold, ent=ent_t, month=ent_t.strftime('%Y-%m'), hour=ent_t.hour,
    ))
print('baseline_v1 buys=%d sells(trips)=%d'%(nbuys,len(trips)))

# SCRUB: drop ret>0 & hold<10s
before=len(trips)
trips=[t for t in trips if not (t['real'] is not None and t['real']>0 and t['hold']<10)]
print('scrub dropped %d -> %d trips'%(before-len(trips),len(trips)))
print('date span:', min(t['ent'] for t in trips), '->', max(t['ent'] for t in trips))
print('months:', Counter(t['month'] for t in trips))

# per-token aggregate (first trip -> band features; peak=max; realized=median; mae=min)
bytok=defaultdict(list)
for t in trips: bytok[t['addr']].append(t)
toks=[]
for addr,ts in bytok.items():
    ts=sorted(ts,key=lambda x:x['ent'])
    first=ts[0]
    peaks=[x['peak'] for x in ts if x['peak'] is not None]
    reals=[x['real'] for x in ts if x['real'] is not None]
    maes=[x['mae'] for x in ts if x['mae'] is not None]
    toks.append(dict(addr=addr, mcap=first['mcap'], age=first['age'], vol=first['vol'],
        liq=first['liq'], proto=first['proto'], hour=first['hour'], month=first['month'],
        peak=max(peaks) if peaks else None, real=st.median(reals) if reals else None,
        mae=min(maes) if maes else None, ntrips=len(ts),
        peak_at=first['peak_at']))
print('unique tokens:', len(toks))

def extop2_med(vals):
    v=sorted([x for x in vals if x is not None])
    if len(v)<=2: return None
    v=v[:-2]  # drop 2 best
    return st.median(v)

def winrate(vals,thr):
    v=[x for x in vals if x is not None]
    if not v: return None
    return 100.0*sum(1 for x in v if x>=thr)/len(v)

def report(name, groups):
    print('\n=== AXIS:',name,'===')
    print('%-14s %5s %8s %8s %7s %7s %7s | %10s %9s %9s %8s'%(
        'band','nTok','medPeak','exT2Real','w@20','w@30','w@50','medMAE','medReal','meanReal','deepLoss%'))
    for label,sel in groups:
        g=[t for t in toks if sel(t)]
        if not g:
            print('%-14s  (empty)'%label); continue
        peaks=[t['peak'] for t in g]
        reals=[t['real'] for t in g]
        maes=[t['mae'] for t in g if t['mae'] is not None]
        # deep loss / rug proxy: token realized <= -50 OR mae <= -50
        deep=sum(1 for t in g if (t['real'] is not None and t['real']<=-50) or (t['mae'] is not None and t['mae']<=-50))
        medpk=st.median([p for p in peaks if p is not None]) if any(p is not None for p in peaks) else None
        et2=extop2_med(reals)
        medreal=st.median([r for r in reals if r is not None]) if any(r is not None for r in reals) else None
        meanreal=st.mean([r for r in reals if r is not None]) if any(r is not None for r in reals) else None
        medmae=st.median(maes) if maes else None
        def fmt(x): return ('%8.2f'%x) if x is not None else '     -  '
        print('%-14s %5d %s %s %s %s %s | %s %s %s %7.1f'%(
            label,len(g),fmt(medpk),fmt(et2),
            ('%7.1f'%winrate(peaks,20)) if winrate(peaks,20) is not None else '   -   ',
            ('%7.1f'%winrate(peaks,30)) if winrate(peaks,30) is not None else '   -   ',
            ('%7.1f'%winrate(peaks,50)) if winrate(peaks,50) is not None else '   -   ',
            fmt(medmae),fmt(medreal),fmt(meanreal),100.0*deep/len(g)))

mcap_g=[('<100k',lambda t:t['mcap'] is not None and t['mcap']<100e3),
    ('100-300k',lambda t:t['mcap'] is not None and 100e3<=t['mcap']<300e3),
    ('300k-1M',lambda t:t['mcap'] is not None and 300e3<=t['mcap']<1e6),
    ('1-3M',lambda t:t['mcap'] is not None and 1e6<=t['mcap']<3e6),
    ('3-10M',lambda t:t['mcap'] is not None and 3e6<=t['mcap']<10e6),
    ('>=10M',lambda t:t['mcap'] is not None and t['mcap']>=10e6)]
age_g=[('<1h',lambda t:t['age'] is not None and t['age']<1),
    ('1-2h',lambda t:t['age'] is not None and 1<=t['age']<2),
    ('2-6h',lambda t:t['age'] is not None and 2<=t['age']<6),
    ('6-24h',lambda t:t['age'] is not None and 6<=t['age']<24),
    ('>=24h',lambda t:t['age'] is not None and t['age']>=24)]
vol_g=[('<5k',lambda t:t['vol'] is not None and t['vol']<5e3),
    ('5-20k',lambda t:t['vol'] is not None and 5e3<=t['vol']<20e3),
    ('20-100k',lambda t:t['vol'] is not None and 20e3<=t['vol']<100e3),
    ('>=100k',lambda t:t['vol'] is not None and t['vol']>=100e3)]
liq_g=[('<50k',lambda t:t['liq'] is not None and t['liq']<50e3),
    ('50-100k',lambda t:t['liq'] is not None and 50e3<=t['liq']<100e3),
    ('100-200k',lambda t:t['liq'] is not None and 100e3<=t['liq']<200e3),
    ('>=200k',lambda t:t['liq'] is not None and t['liq']>=200e3)]
protos=Counter(t['proto'] for t in toks)
proto_g=[(str(p),(lambda pp: (lambda t:t['proto']==pp))(p)) for p,_ in protos.most_common(6)]
hour_g=[('%02d-%02d'%(h,h+2),(lambda a,b:(lambda t:a<=t['hour']<b))(h,h+2)) for h in range(0,24,2)]

report('MCAP',mcap_g)
report('AGE',age_g)
report('VOL_H1',vol_g)
report('LIQ',liq_g)
report('PROTOCOL',proto_g)
report('HOUR_UTC',hour_g)

# allocation: where does lane deploy
print('\n=== ALLOCATION (%% of tokens) ===')
def alloc(name,groups):
    tot=len(toks)
    print(name, {lab:round(100.0*sum(1 for t in toks if sel(t))/tot,1) for lab,sel in groups})
alloc('mcap',mcap_g); alloc('age',age_g); alloc('vol',vol_g); alloc('liq',liq_g)
print('proto', {p:round(100.0*c/len(toks),1) for p,c in protos.most_common(6)})

print('\n\n############ OOS: MCAP band by MONTH ############')
for mo in ['2026-05','2026-06']:
    sub=[t for t in toks if t['month']==mo]
    print('\n--- month',mo,'n_tok=',len(sub),'---')
    print('%-10s %5s %8s %8s %7s %7s'%('band','nTok','medPeak','exT2Real','w@30','w@50'))
    for label,sel in mcap_g:
        g=[t for t in sub if sel(t)]
        if not g: 
            print('%-10s  (empty)'%label); continue
        peaks=[t['peak'] for t in g]
        reals=[t['real'] for t in g]
        medpk=st.median([p for p in peaks if p is not None]) if any(p is not None for p in peaks) else None
        et2=extop2_med(reals)
        w30=winrate(peaks,30); w50=winrate(peaks,50)
        print('%-10s %5d %8s %8s %7s %7s'%(label,len(g),
            ('%.2f'%medpk) if medpk is not None else '-',
            ('%.2f'%et2) if et2 is not None else '-',
            ('%.1f'%w30) if w30 is not None else '-',
            ('%.1f'%w50) if w50 is not None else '-'))

# realized peak-to-capture ratio: even at PEAK, is rich pond realizable? median (real/peak) where peak>0
print('\n############ CAPTURE: median realized, median peak, and realized@peak-hold by mcap ############')
for label,sel in mcap_g:
    g=[t for t in toks if sel(t)]
    if len(g)<3: 
        print('%-10s n<3'%label); continue
    reals=[t['real'] for t in g if t['real'] is not None]
    peaks=[t['peak'] for t in g if t['peak'] is not None]
    # frac of tokens whose REALIZED (median trip) ended positive
    pos=100.0*sum(1 for r in reals if r>0)/len(reals)
    print('%-10s nTok=%3d medReal=%6.2f medPeak=%6.2f  frac_tokens_realized>0=%.1f%%'%(
        label,len(g),st.median(reals),st.median(peaks),pos))

print('\n\n############ SIMULATE PROPOSED rich_pond GATE ############')
def gate(t):
    return (t['mcap'] is not None and t['mcap']>=1e6 and
            t['age'] is not None and 2<=t['age']<=24 and
            t['vol'] is not None and t['vol']>=20e3 and
            (t['liq'] is None or t['liq']>=50e3))
for name,pool in [('ALL baseline (control)',toks),
                  ('rich_pond gate',[t for t in toks if gate(t)]),
                  ('rich_pond MAY',[t for t in toks if gate(t) and t['month']=='2026-05']),
                  ('rich_pond JUN',[t for t in toks if gate(t) and t['month']=='2026-06'])]:
    reals=[t['real'] for t in pool if t['real'] is not None]
    peaks=[t['peak'] for t in pool if t['peak'] is not None]
    maes=[t['mae'] for t in pool if t['mae'] is not None]
    if not reals: 
        print('%-24s n=0'%name); continue
    et2=extop2_med(reals)
    posreal=100.0*sum(1 for r in reals if r>0)/len(reals)
    green_tok=100.0*sum(1 for r in reals if r>0)/len(reals)
    deep=100.0*sum(1 for t in pool if (t['real'] is not None and t['real']<=-50) or (t['mae'] is not None and t['mae']<=-50))/len(pool)
    print('%-24s nTok=%3d exT2Real=%s medReal=%6.2f meanReal=%6.2f fracGreen=%4.1f%% medMAE=%6.2f deepLoss=%4.1f%%'%(
        name,len(pool),('%6.2f'%et2) if et2 is not None else '  n/a ',
        st.median(reals),st.mean(reals),posreal,st.median(maes),deep))

# Also: does gate beat control on REALIZED mean/median? And peak vs realize gap.
print('\n############ PEAK vs REALIZED gap (the capture leak) by pool ############')
for name,pool in [('control',toks),('rich_pond',[t for t in toks if gate(t)])]:
    both=[(t['peak'],t['real']) for t in pool if t['peak'] is not None and t['real'] is not None]
    medpk=st.median([p for p,_ in both]); medrl=st.median([r for _,r in both])
    print('%-12s medPeak=%6.2f medReal=%6.2f  UNREALIZED_GAP=%6.2f (%.0f%% of peak left on table)'%(
        name,medpk,medrl,medpk-medrl, 100*(medpk-medrl)/medpk if medpk else 0))
