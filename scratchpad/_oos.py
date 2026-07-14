import json, statistics
pos=json.load(open('scratchpad/_overgating_pos.json'))
pos=[p for p in pos if p['ppct'] is not None]
# scrub
pos=[p for p in pos if not (p['ppct']>0 and p['hold'] is not None and p['hold']<10)]
young=[p for p in pos if ('young' in p['bot'] or 'absorb' in p['bot'])]
young.sort(key=lambda p:p['time'])

def extop2(rows):
    v=sorted((p['ppct'] for p in rows), reverse=True)
    if len(v)<3: return None,len(v)
    return round(statistics.median(v[2:]),2), len(v)

def hit6(rows):
    pk=[p['peak'] for p in rows if p['peak'] is not None]
    return round(sum(1 for x in pk if x>=6)/len(pk),3) if pk else None

def quarters(rows):
    # chrono early/late by median index, then odd/even interleave
    n=len(rows); mid=n//2
    early=rows[:mid]; late=rows[mid:]
    q={}
    q['Q1 early-odd']=early[1::2]  # odd indices
    q['Q2 early-even']=early[0::2]
    q['Q3 late-odd']=late[1::2]
    q['Q4 late-even']=late[0::2]
    return q

for fname, sk in [('KNIFE', lambda p:p['kv']=='BLOCK'), ('RETRACE', lambda p:bool(p['rb']))]:
    print('\n########## %s — YOUNG family four-half OOS ##########'%fname)
    print('%-14s | %-22s | %-22s | winnerkill?'%('quarter','BLOCK extop2 (n)','PASS extop2 (n)'))
    qs=quarters(young)
    wins=0; valid=0
    for qn,rows in qs.items():
        blk=[p for p in rows if sk(p)]; pas=[p for p in rows if not sk(p)]
        be,bn=extop2(blk); pe,pn=extop2(pas)
        verdict=''
        if be is not None and pe is not None:
            valid+=1
            if be>pe: verdict='BLOCK>PASS (kill)'; wins+=1
            else: verdict='protective'
        else:
            verdict='n<3'
        print('%-14s | %-8s (%2s,hit6=%s) | %-8s (%2s,hit6=%s) | %s'%(
            qn, be, bn, hit6(blk), pe, pn, hit6(pas), verdict))
    print('  --> BLOCK beats PASS ex-top2 in %d/%d valid quarters'%(wins,valid))
