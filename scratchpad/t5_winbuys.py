"""Collect winner BUYS with mint, blockTime, sol_spent, tokens_received -> fill price in SOL."""
import json,sys,time,subprocess,datetime
STABLE={"So11111111111111111111111111111111111111112",
"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
"Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
RPCS=["https://solana.leorpc.com/?api_key=FREE","https://api.mainnet-beta.solana.com","https://solana-rpc.publicnode.com"]
def rpc(method,params,tries=2):
    for r in RPCS:
        for t in range(tries):
            out=subprocess.run(["curl","-s","--max-time","8","-X","POST",r,"-H","Content-Type: application/json",
                "-d",json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params})],
                capture_output=True,text=True,errors="replace").stdout
            try:
                d=json.loads(out)
                if "result" in d: return d["result"]
            except: pass
            time.sleep(0.2)
    return None
def buys(addr,sigs=120):
    sl=rpc("getSignaturesForAddress",[addr,{"limit":sigs}]) or []
    out=[]
    for s in sl:
        sig=s.get("signature");bt=s.get("blockTime")
        if not sig or s.get("err") or not bt: continue
        tx=rpc("getTransaction",[sig,{"maxSupportedTransactionVersion":0,"encoding":"jsonParsed"}])
        time.sleep(0.05)
        if not tx or not tx.get("meta"): continue
        meta=tx["meta"]
        pre={b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (meta.get("preTokenBalances") or []) if b.get("owner")==addr}
        post={b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (meta.get("postTokenBalances") or []) if b.get("owner")==addr}
        try:
            keys=[k if isinstance(k,str) else k.get("pubkey") for k in tx["transaction"]["message"]["accountKeys"]]
            wi=keys.index(addr); sol_d=(meta["postBalances"][wi]-meta["preBalances"][wi])/1e9
        except: continue
        deltas={m:post.get(m,0)-pre.get(m,0) for m in set(list(pre)+list(post)) if m not in STABLE}
        deltas={m:d for m,d in deltas.items() if abs(d)>0}
        if not deltas: continue
        mint=max(deltas,key=lambda m:abs(deltas[m])); d=deltas[mint]
        if d>0 and sol_d<0:
            out.append({"mint":mint,"bt":bt,"sol":-sol_d,"tok":d,"price_sol":(-sol_d)/d})
    return out
if __name__=="__main__":
    w=sys.argv[1]
    b=buys(w,int(sys.argv[2]) if len(sys.argv)>2 else 120)
    for x in b:
        print(datetime.datetime.fromtimestamp(x["bt"],datetime.UTC).strftime("%m-%d %H:%M:%S"),x["mint"][:8],f'sol={x["sol"]:.3f}',f'price_sol={x["price_sol"]:.3e}')
    print("TOTAL buys",len(b))
    json.dump(b,open(f'scratchpad/buys_{w[:8]}.json',"w"))
