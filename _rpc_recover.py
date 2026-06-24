import json, urllib.request, time
RPC="https://api.mainnet-beta.solana.com"
def call(body):
    req=urllib.request.Request(RPC,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
    try:
        r=urllib.request.urlopen(req,timeout=15); return r.getcode()
    except urllib.error.HTTPError as e: return e.code
    except Exception as e: return -1

# measure recovery: poll getHealth once/sec until 200 returns
print("recovery probe:")
start=time.time()
for i in range(30):
    c=call({"jsonrpc":"2.0","id":1,"method":"getHealth"})
    el=time.time()-start
    print(f"  t={el:.1f}s code={c}")
    if c==200:
        print(f"RECOVERED after ~{el:.1f}s of cooldown")
        break
    time.sleep(1)
