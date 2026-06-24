import json, time, urllib.request, gzip, io
BASE="https://gracious-inspiration-production.up.railway.app"
def get(p,t=30):
    req=urllib.request.Request(BASE+p,headers={"Accept-Encoding":"gzip"})
    with urllib.request.urlopen(req,timeout=t) as r:
        raw=r.read()
        if r.headers.get("Content-Encoding")=="gzip": raw=gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)
seen=set(); th_losses=0
for i in range(10):  # ~40 min @ 4min
    try:
        ms=get("/api/meta-sensor")
        ch=(ms.get("chameleon") or {}).get("meta_chameleon") or {}
        for c in (ch.get("recent_closes") or []):
            if (c.get("archetype")=="thesis_holder"):
                k=(c.get("ts"),c.get("net"))
                if k not in seen:
                    seen.add(k)
                    w="WIN" if c.get("win") else "LOSS"
                    if not c.get("win"): th_losses+=1
                    print(f"THESIS_HOLDER close: {w} net=${c.get('net'):+.2f} (cumulative TH losses={th_losses})",flush=True)
        if th_losses>=2:
            print(f"** 2+ thesis_holder LOSSES — transfer risk materializing; assess copy stop-floor **",flush=True)
            break
    except Exception: pass
    time.sleep(240)
print(f"thesis-watch window done: {len(seen)} TH closes seen, {th_losses} losses",flush=True)
