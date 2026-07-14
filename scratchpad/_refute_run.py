import ijson, statistics as st, json
from collections import defaultdict, Counter
from datetime import datetime, timedelta

def fl(x):
    try: return float(x)
    except: return None

buys={}   # (bot,addr,eprice_str) -> meta dict
buy_rows=[]
sells=[]
f=open('_trades_cache.json','rb')
for rec in ijson.items(f,'item'):
    t=rec.get('type'); b=rec.get('bot_id')
    ep=rec.get('entry_price')
    key=(b, rec.get('address'), str(ep))
    if t=='buy':
        m=rec.get('entry_meta') or {}
        buys[key]=dict(
            age=fl(m.get('lifecycle_age_hours')),
            age_top=fl(rec.get('entry_age_hours')),
            stage=m.get('lifecycle_stage'),
            nf5=fl(m.get('net_flow_5m_imbalance')),
            pc_h1=(fl(m.get('pc_h1_lookback')) or 0)+(fl(m.get('pc_h1_change_since_lookback')) or 0) if (m.get('pc_h1_lookback') is not None or m.get('pc_h1_change_since_lookback') is not None) else None,
            mcap=fl(rec.get('entry_market_cap_usd')),
            time=rec.get('time'),
        )
    elif t=='sell':
        sells.append(rec)

print('buys=%d sells=%d'%(len(buys),len(sells)))

# join
rows=[]
matched=0
for s in sells:
    b=s.get('bot_id'); key=(b, s.get('address'), str(s.get('entry_price')))
    bm=buys.get(key)
    if bm is None: continue
    matched+=1
    real=fl(s.get('pnl_pct')); peak=fl(s.get('peak_pnl_pct'))
    mae=fl(s.get('max_drawdown_pct')); hold=fl(s.get('hold_secs')) or 0.0
    peak_at=fl(s.get('peak_pnl_at_secs'))
    et=None
    try: et=datetime.fromisoformat(s['time'])-timedelta(seconds=hold)
    except: pass
    rows.append(dict(bot=b, addr=s.get('address'), age=bm['age'], age_top=bm['age_top'],
        stage=bm['stage'], nf5=bm['nf5'], pc_h1=bm['pc_h1'], mcap=bm['mcap'],
        real=real, peak=peak, mae=mae, hold=hold, peak_at=peak_at, ent=et))
print('matched completed positions=%d'%matched)

# SCRUB
before=len(rows)
rows=[r for r in rows if not (r['real'] is not None and r['real']>0 and r['hold']<10)]
print('scrub dropped %d -> %d'%(before-len(rows),len(rows)))
ents=[r['ent'] for r in rows if r['ent']]
print('span',min(ents),'->',max(ents))
print('age coverage: %d/%d have lifecycle_age'%(sum(1 for r in rows if r['age'] is not None),len(rows)))
print('age_top coverage: %d/%d'%(sum(1 for r in rows if r['age_top'] is not None),len(rows)))

json.dump([dict(bot=r['bot'],addr=r['addr'],age=r['age'],stage=r['stage'],nf5=r['nf5'],
    pc_h1=r['pc_h1'],mcap=r['mcap'],real=r['real'],peak=r['peak'],mae=r['mae'],
    hold=r['hold'],peak_at=r['peak_at'],ent=(r['ent'].isoformat() if r['ent'] else None)) for r in rows],
    open('scratchpad/_joined_rows.json','w'))
print('wrote scratchpad/_joined_rows.json')
