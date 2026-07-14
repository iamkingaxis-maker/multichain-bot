import sys,os,json,collections,time
sys.path.insert(0,'scripts')
import score_wallet_diversity as swd

def trade_map(addr,sigs):
    sl=swd._rpc("getSignaturesForAddress",[addr,{"limit":sigs}]) or []
    tok=collections.defaultdict(lambda:{"spent":0.0,"recv":0.0,"buys":0,"sells":0})
    for s in sl:
        sig,bt=s.get("signature"),s.get("blockTime")
        if not sig or s.get("err") or not bt: continue
        tx=swd._rpc("getTransaction",[sig,{"maxSupportedTransactionVersion":0,"encoding":"jsonParsed"}])
        time.sleep(0.04)
        if not tx or not tx.get("meta"): continue
        meta=tx["meta"]
        pre={b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (meta.get("preTokenBalances") or []) if b.get("owner")==addr}
        post={b.get("mint"):float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0) for b in (meta.get("postTokenBalances") or []) if b.get("owner")==addr}
        try:
            keys=[k if isinstance(k,str) else k.get("pubkey") for k in tx["transaction"]["message"]["accountKeys"]]
            wi=keys.index(addr); sol_d=(meta["postBalances"][wi]-meta["preBalances"][wi])/1e9
        except Exception: continue
        deltas={m:post.get(m,0)-pre.get(m,0) for m in set(list(pre)+list(post)) if m not in swd.STABLE}
        deltas={m:d for m,d in deltas.items() if abs(d)>0}
        if not deltas: continue
        mint=max(deltas,key=lambda m:abs(deltas[m])); d=deltas[mint]
        if d>0 and sol_d<0: tok[mint]["buys"]+=1; tok[mint]["spent"]+=-sol_d
        elif d<0 and sol_d>0: tok[mint]["sells"]+=1; tok[mint]["recv"]+=sol_d
    return tok

addr=sys.argv[1]; sigs=int(sys.argv[2]); out=sys.argv[3]
tok=trade_map(addr,sigs)
res={}
for m,v in tok.items():
    ret=(v["recv"]/v["spent"]-1)*100 if v["spent"]>0 and v["recv"]>0 else None
    res[m]={"spent":v["spent"],"recv":v["recv"],"buys":v["buys"],"sells":v["sells"],"ret_pct":ret,"profit":(v["recv"]>v["spent"]) if v["recv"]>0 else None}
json.dump(res,open(out,'w'))
print(addr[:8],"tokens",len(res))
