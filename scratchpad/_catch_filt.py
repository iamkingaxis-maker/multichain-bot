import json, statistics as st
events=json.load(open('_catch_events.json'))
def m(e,k): 
    v=e.get(k); return v
# label each event
for e in events:
    e['is_held']= e['peak'] is not None and e['peak']>=8
    e['is_dead']= (e['peak'] is None or e['peak']<3) and e['pnl']<=-10
    e['is_gap'] = e['pnl']<=-15
    e['is_bad'] = e['is_dead'] or e['is_gap']
T=len(events)
TH=sum(e['is_held'] for e in events)
TB=sum(e['is_bad'] for e in events)
TG=sum(e['is_gap'] for e in events)
print('TOTAL events=%d held=%d bad(dead|gap)=%d gap=%d'%(T,TH,TB,TG))
def ev(name,pred):
    kept=[e for e in events if pred(e)]
    rem=[e for e in events if not pred(e)]
    if not kept: print(name,'kept 0'); return
    kh=sum(e['is_held'] for e in kept); kb=sum(e['is_bad'] for e in kept); kg=sum(e['is_gap'] for e in kept)
    winner_kill = (TH-kh)/TH
    bad_removed = (TB-kb)/TB
    gap_removed = (TG-kg)/TG
    medk=st.median([e['pnl'] for e in kept])
    print('%-42s keep=%3d(pairs %3d) held%%=%.3f bad%%=%.3f gap%%=%.3f medpnl=%5.2f | winnerKILL=%.3f badREMOVED=%.3f gapREMOVED=%.3f'%(
        name,len(kept),len(set(e['addr'] for e in kept)),kh/len(kept),kb/len(kept),kg/len(kept),medk,winner_kill,bad_removed,gap_removed))
print('\n--- single filters ---')
ev('ALL (baseline)', lambda e:True)
ev('liq>=30k', lambda e:e['liq']>=30000)
ev('liq>=30k & liq<80k', lambda e:30000<=e['liq']<80000)
ev('pc_h6<100', lambda e:e['pc_h6']<100)
ev('pc_h6<150', lambda e:e['pc_h6']<150)
ev('NOT(100<=pc_h6<300)  [cut poison band]', lambda e:not(100<=e['pc_h6']<300))
ev('pc_h24<150', lambda e:(e['pc_h24'] or 0)<150)
ev('NOT(150<=pc_h24<900)', lambda e:not(150<=(e['pc_h24'] or 0)<900))
ev('age>=12', lambda e:(e['age'] or 999)>=12)
ev('NOT(3<=age<12)', lambda e:not(3<=(e['age'] or 999)<12))
print('\n--- combos ---')
ev('liq>=30k & pc_h6<100', lambda e:e['liq']>=30000 and e['pc_h6']<100)
ev('liq>=30k & NOT(100<=pc_h6<300)', lambda e:e['liq']>=30000 and not(100<=e['pc_h6']<300))
ev('liq>=30k & NOT(3<=age<12)', lambda e:e['liq']>=30000 and not(3<=(e['age'] or 999)<12))
ev('liq>=30k & NOT poison_h6 & NOT young3-12', lambda e:e['liq']>=30000 and not(100<=e['pc_h6']<300) and not(3<=(e['age'] or 999)<12))
ev('liq>=30k & pc_h24<150', lambda e:e['liq']>=30000 and (e['pc_h24'] or 0)<150)
print('\n--- keep-the-mooners variants (allow pc_h6>=300 but only if liq>=50k) ---')
ev('liq>=30k & (pc_h6<100 OR pc_h6>=300)', lambda e:e['liq']>=30000 and (e['pc_h6']<100 or e['pc_h6']>=300))
ev('liq>=30k & (pc_h6<100 OR (pc_h6>=300 & liq>=50k))', lambda e:e['liq']>=30000 and (e['pc_h6']<100 or (e['pc_h6']>=300 and e['liq']>=50000)))
