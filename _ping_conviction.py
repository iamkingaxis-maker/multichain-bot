import json, time, urllib.request
BASE="https://gracious-inspiration-production.up.railway.app"
def get(p):
    return json.load(urllib.request.urlopen(BASE+p, timeout=25))
worn_seen=False
for _ in range(60):  # ~50 min
    try:
        ms=get("/api/meta-sensor")
        ch=(ms.get("chameleon") or {}).get("meta_chameleon") or {}
        worn=ch.get("archetype")
        if worn=="conviction" and not worn_seen:
            worn_seen=True
            t=ch.get("tune") or {}
            print(f"WEARING CONVICTION: ts={t.get('time_stop_minutes')}m tp1={t.get('tp1_pct')}% stop={t.get('hard_stop_pct')}%", flush=True)
        tr=get("/api/trades?limit=300")
        rows=tr if isinstance(tr,list) else tr.get("trades",[])
        buys=sum(1 for x in rows if x.get("bot_id")=="meta_chameleon" and x.get("type")=="buy")
        if worn=="conviction" and buys>6:
            nb=[x for x in rows if x.get("bot_id")=="meta_chameleon" and x.get("type")=="buy"]
            nb.sort(key=lambda x:str(x.get("time")),reverse=True)
            print(f"FIRST CONVICTION TRADE: buy #{buys} token={nb[0].get('token')} @ {nb[0].get('time','')[11:19]}",flush=True)
            break
    except Exception as e:
        pass
    time.sleep(60)
else:
    print("poller window elapsed without first trade — re-check manually",flush=True)
