import urllib.request, gzip, json, time, datetime
from curl_cffi import requests as creq

def fetch(url, tries=4):
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
# Filter to REAL active pumpers: nonzero liq, has m5 activity
real = [p for p in pumpers if (p.get('liq') or 0) > 5000]
print('real active pumpers (liq>5k): %d' % len(real))

now = datetime.datetime.now(datetime.timezone.utc)

def minute_ohlc(pool, n=300):
    # GT OHLCV minute endpoint
    url=f'https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute?aggregate=1&limit={n}'
    d=fetch(url)
    if not d: return None
    lst=d.get('data',{}).get('attributes',{}).get('ohlcv_list',[])
    # list of [ts, o,h,l,c,v]; GT returns newest first
    return sorted(lst, key=lambda x:x[0])

print()
for p in real:
    pool=p['pool']; name=p['name']
    o=minute_ohlc(pool)
    time.sleep(3)
    if not o or len(o)<10:
        print('%-22s NO OHLC (%s bars)' % (name, len(o) if o else 0)); continue
    ts=[x[0] for x in o]; close=[x[4] for x in o]; high=[x[2] for x in o]; low=[x[3] for x in o]; vol=[x[5] for x in o]
    cur=close[-1]
    # Find all-window high and when it occurred
    wh=max(high); wh_i=high.index(wh)
    wl=min(low);
    # peak timing: how long ago was the high (minutes from now)
    peak_age_min = round((ts[-1]-ts[wh_i])/60,0)
    span_min = round((ts[-1]-ts[0])/60,0)
    # drawdown from window high right now
    dd_from_high = round((cur/wh-1)*100,1)
    # rise from window low
    rise_from_low = round((cur/wl-1)*100,1)
    # last 15m and 30m momentum
    def mom(mins):
        target=ts[-1]-mins*60
        idx=min(range(len(ts)), key=lambda i:abs(ts[i]-target))
        if close[idx]==0: return None
        return round((cur/close[idx]-1)*100,1)
    m15=mom(15); m30=mom(30); m60=mom(60)
    # is price near its high (within 5%)? => momentum/breakout shape. Or pulled back >10%? => dip shape
    shape = 'NEAR-HIGH(momo)' if dd_from_high>-6 else ('PULLBACK' if dd_from_high<-12 else 'mid')
    # volume trend: last 5m vol vs prior 5m
    v5=sum(vol[-5:]); vp5=sum(vol[-10:-5]) if len(vol)>=10 else 0
    vtrend = round(v5/vp5,2) if vp5>0 else None
    print('%-22s bars=%3d span=%4.0fm | cur_dd_from_high=%6.1f%% peak_was=%4.0fm_ago rise_from_low=%6.0f%% | m15=%5s m30=%5s m60=%5s | vtrend5=%5s | %s' % (
        name, len(o), span_min, dd_from_high, peak_age_min, rise_from_low,
        str(m15),str(m30),str(m60), str(vtrend), shape))
