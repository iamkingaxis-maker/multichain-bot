import urllib.request, gzip, json, time, datetime

def fetch(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
            r = urllib.request.urlopen(req, timeout=30)
            data = r.read()
            if r.headers.get('Content-Encoding')=='gzip': data=gzip.decompress(data)
            return json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code==429:
                time.sleep(8*(i+1)); continue
            if e.code==400:
                return None
            time.sleep(3)
        except Exception:
            time.sleep(3)
    return None

urls = [
 'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1',
 'https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=2',
 'https://api.geckoterminal.com/api/v2/networks/solana/pools?page=1&sort=h6_volume_usd_desc',
 'https://api.geckoterminal.com/api/v2/networks/solana/pools?page=2&sort=h6_volume_usd_desc',
 'https://api.geckoterminal.com/api/v2/networks/solana/pools?page=1&sort=h24_volume_usd_desc',
]
seen={}
for u in urls:
    d = fetch(u)
    if not d:
        print('skip', u); continue
    for p in d.get('data',[]):
        a=p['attributes']
        rel=p.get('relationships',{})
        bt=rel.get('base_token',{}).get('data',{}).get('id','')
        mint = bt.split('_')[-1] if bt else None
        a['_mint']=mint
        seen[a['address']]=a
    time.sleep(3)

print('total unique pools', len(seen))
now = datetime.datetime.now(datetime.timezone.utc)
rows=[]
for addr,a in seen.items():
    pc=a.get('price_change_percentage') or {}
    def f(x):
        try: return float(x)
        except: return None
    h6f=f(pc.get('h6')); h1f=f(pc.get('h1')); h24f=f(pc.get('h24')); m5f=f(pc.get('m5')); m15f=f(pc.get('m15'))
    fdv=f(a.get('fdv_usd')); liq=f(a.get('reserve_in_usd'))
    created=a.get('pool_created_at'); age_h=None
    if created:
        ct=datetime.datetime.fromisoformat(created.replace('Z','+00:00'))
        age_h=round((now-ct).total_seconds()/3600,1)
    vol=a.get('volume_usd') or {}
    txn=a.get('transactions') or {}
    h1tx=txn.get('h1',{}); m5tx=txn.get('m5',{})
    rows.append(dict(h6=h6f,h1=h1f,h24=h24f,m5=m5f,m15=m15f,name=a['name'],fdv=fdv,liq=liq,age_h=age_h,
                     mint=a.get('_mint'),pool=addr,
                     vol_h1=f(vol.get('h1')),vol_h6=f(vol.get('h6')),
                     h1_buys=h1tx.get('buys'),h1_sells=h1tx.get('sells'),
                     m5_buys=m5tx.get('buys'),m5_sells=m5tx.get('sells')))

pumpers=[r for r in rows if r['h6'] is not None and r['h6']>=30]
pumpers.sort(key=lambda r:-r['h6'])
print('\n=== H6 >= +30%% PUMPERS (n=%d) ===' % len(pumpers))
print('%6s %6s %6s %6s %9s %8s %6s  %s' % ('h6%','h1%','m5%','m15%','fdv','liq','age_h','name'))
for r in pumpers:
    print('%6.0f %6s %6s %6s %9s %8s %6s  %s' % (
        r['h6'],
        ('%.0f'%r['h1']) if r['h1'] is not None else '-',
        ('%.0f'%r['m5']) if r['m5'] is not None else '-',
        ('%.0f'%r['m15']) if r['m15'] is not None else '-',
        ('%.0fk'%(r['fdv']/1000)) if r['fdv'] else '-',
        ('%.0fk'%(r['liq']/1000)) if r['liq'] else '-',
        ('%.1f'%r['age_h']) if r['age_h'] is not None else '-',
        r['name']))

# save for downstream minute analysis
with open(r'C:\Users\jcole\multichain-bot\_pumpers.json','w') as fh:
    json.dump(pumpers, fh)
print('\nsaved', len(pumpers),'pumpers -> _pumpers.json')
