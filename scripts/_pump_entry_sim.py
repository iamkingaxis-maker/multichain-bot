import urllib.request, gzip, json, time, datetime

def fetch(url, tries=5):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
            r = urllib.request.urlopen(req, timeout=30)
            data = r.read()
            if r.headers.get('Content-Encoding')=='gzip': data=gzip.decompress(data)
            return json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(8*(i+1)); continue
            return None
        except Exception: time.sleep(3)
    return None

pumpers = json.load(open(r'C:\Users\jcole\multichain-bot\_pumpers.json'))
real = [p for p in pumpers if (p.get('liq') or 0) > 5000]

def minute_ohlc(pool, n=350):
    url=f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit={n}'
    d=fetch(url)
    if not d: return None
    lst=d.get('data',{}).get('attributes',{}).get('ohlcv_list',[])
    return sorted(lst, key=lambda x:x[0])

# Entry simulation. For each minute bar i (the DECISION bar), we observe trailing features
# and compute FORWARD return over horizon H (max favorable & close at H).
# Two entry archetypes:
#   DIP: price pulled back >=X% from a recent (last 60m) local high
#   BREAKOUT: price made a new 30m high on rising volume (momentum)
# We measure forward best (next 30m max) and forward close (+30m) for each trigger.

H = 30  # forward horizon minutes
results = {'DIP':[], 'BREAKOUT':[], 'ANY':[]}
detail = []

for p in real:
    o = minute_ohlc(p['pool']); time.sleep(3)
    if not o or len(o) < 80: continue
    ts=[x[0] for x in o]; opn=[x[1] for x in o]; high=[x[2] for x in o]; low=[x[3] for x in o]; close=[x[4] for x in o]; vol=[x[5] for x in o]
    n=len(o)
    name=p['name']
    dip_trigs=brk_trigs=0
    for i in range(60, n-H):
        c=close[i]
        if c<=0: continue
        # trailing 60m window
        w_hi=max(high[i-60:i+1]); w_lo=min(low[i-60:i+1])
        # 30m window for breakout
        prior30_hi=max(high[i-30:i])  # high of prior 30 bars (excl current)
        # volume: current 5m vs prior 5m
        v5=sum(vol[i-4:i+1]); vp5=sum(vol[i-9:i-4]) if i>=9 else 0
        vtrend = v5/vp5 if vp5>0 else 0
        # momentum last 15m
        c15 = close[i-15] if close[i-15]>0 else c
        mom15 = c/c15-1
        # forward
        fwd_max = max(high[i+1:i+1+H])/c - 1
        fwd_close = close[i+H]/c - 1

        dd_from_hi = c/w_hi - 1
        # DIP trigger: pulled back 12-30% from 60m high, but still in uptrend (above 60m low by a lot), modest recovery starting
        is_dip = (dd_from_hi <= -0.12 and dd_from_hi >= -0.35 and c > w_lo*1.10)
        # BREAKOUT: new 30m high (c >= prior30_hi*0.999) on rising vol and positive 15m momentum
        is_brk = (c >= prior30_hi*0.998 and vtrend >= 1.3 and mom15 >= 0.03)

        if is_dip:
            results['DIP'].append((fwd_max, fwd_close)); dip_trigs+=1
        if is_brk:
            results['BREAKOUT'].append((fwd_max, fwd_close)); brk_trigs+=1
    detail.append((name, dip_trigs, brk_trigs))

def summ(lst, tp=0.10):
    if not lst: return 'n=0'
    n=len(lst)
    fmax=[x[0] for x in lst]; fclose=[x[1] for x in lst]
    import statistics as st
    # WR with a +10% TP touch before -15% (approx via fwd_max touching tp); proxy
    tp_hit = sum(1 for x in fmax if x>=tp)/n
    med_close=st.median(fclose); med_max=st.median(fmax)
    win_close = sum(1 for x in fclose if x>0)/n
    return 'n=%4d | tp%+.0f%%_touch=%.0f%% | med_fwd_max=%+.1f%% | med_fwd_close=%+.1f%% | close>0=%.0f%%' % (
        n, tp*100, tp_hit*100, med_max*100, med_close*100, win_close*100)

print('\n=== FORWARD-RETURN BY ENTRY ARCHETYPE (horizon=%dm, across %d pump tokens) ===' % (H, len(detail)))
for k in ['DIP','BREAKOUT']:
    print('%-9s %s' % (k, summ(results[k])))
print('\nPer-token trigger counts (name, dip_trigs, brk_trigs):')
for d in detail: print('  %-22s dip=%3d brk=%3d' % d)

json.dump({k:results[k] for k in results}, open(r'C:\Users\jcole\multichain-bot\_entry_sim.json','w'))
