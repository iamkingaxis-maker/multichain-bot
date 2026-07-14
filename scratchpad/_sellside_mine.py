import json, os, glob
from collections import defaultdict, Counter
from datetime import datetime

EP = r'C:\Users\jcole\AppData\Local\Temp\claude\C--Users-jcole-multichain-bot\ecbaef77-2f98-4dc5-9231-4bd9a529e92c\scratchpad\LABELED_pump_retrace_episodes.json'
TAPES = r'C:\Users\jcole\multichain-bot\scratchpad\ripday\live_tapes'

eps = json.load(open(EP))
eps = [e for e in eps if e.get('has_microstructure')]

# group by tid
by_tid = defaultdict(list)
for e in eps:
    by_tid[e['tid']].append(e)

def parse_ts(s):
    # '2026-07-02T15:30:17+00:00'
    return datetime.fromisoformat(s).timestamp()

def load_tape(tid):
    fn = os.path.join(TAPES, f'tape_{tid}.jsonl')
    if not os.path.exists(fn): return None
    rows=[]
    with open(fn) as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except: continue
            try: t=parse_ts(r['ts'])
            except: continue
            rows.append((t, r['kind'], float(r.get('volume_usd',0) or 0), r.get('maker','')))
    rows.sort(key=lambda x:x[0])
    return rows

def peak_epoch(e):
    try: return parse_ts(e['pump_peak_ts'].replace('Z','+00:00'))
    except: return None

def metrics(rows, low_ep, peak_ep, win):
    # window [low_ep-win, low_ep], only past data (<= low_ep)
    lo = low_ep - win
    sub = [r for r in rows if lo <= r[0] <= low_ep]
    sv=sum(r[2] for r in sub if r[1]=='sell')
    bv=sum(r[2] for r in sub if r[1]=='buy')
    sells=[r[2] for r in sub if r[1]=='sell']
    buys=[r[2] for r in sub if r[1]=='buy']
    sc=len(sells); bc=len(buys)
    max_sell=max(sells) if sells else 0.0
    dsm=len(set(r[3] for r in sub if r[1]=='sell'))
    # buy:sell ratio
    bsr = bv/sv if sv>0 else (float('inf') if bv>0 else 0.0)
    # trajectory: split window into halves, sell vol early vs late
    mid=low_ep-win/2
    sv_early=sum(r[2] for r in sub if r[1]=='sell' and r[0]<mid)
    sv_late =sum(r[2] for r in sub if r[1]=='sell' and r[0]>=mid)
    ms_early=max([r[2] for r in sub if r[1]=='sell' and r[0]<mid], default=0.0)
    ms_late =max([r[2] for r in sub if r[1]=='sell' and r[0]>=mid], default=0.0)
    return dict(sv=sv,bv=bv,sc=sc,bc=bc,max_sell=max_sell,dsm=dsm,bsr=bsr,
                sv_early=sv_early,sv_late=sv_late,ms_early=ms_early,ms_late=ms_late,n=len(sub))

OUT=[]
missing=0
for tid, elist in by_tid.items():
    rows=load_tape(tid)
    if not rows:
        missing+=len(elist); continue
    for e in elist:
        low=e['retrace_low_epoch']; pk=peak_epoch(e)
        m60=metrics(rows, low, pk, 60)
        m120=metrics(rows, low, pk, 120)
        # retrace-leg window (peak->low) if peak known and before low
        leg_win = max(30, low-pk) if (pk and pk<low) else 60
        mleg=metrics(rows, low, pk, leg_win)
        OUT.append(dict(tid=tid,sym=e['sym'],label=e['label'],
                        pump_gain=e['pump_gain'],retr_depth=e['retr_depth'],
                        m60=m60,m120=m120,mleg=mleg,leg_win=leg_win))

json.dump(OUT, open(r'C:\Users\jcole\multichain-bot\scratchpad\_sellside_metrics.json','w'))
print('episodes computed:',len(OUT),'missing tape:',missing)
from collections import Counter
print('labels:',Counter(o['label'] for o in OUT))
print('tokens:',len(set(o['tid'] for o in OUT)))
