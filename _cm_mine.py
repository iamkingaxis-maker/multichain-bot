import json, math, statistics as st
from collections import defaultdict

BOT = "champion_minimal"
data = json.load(open(r"C:\Users\jcole\multichain-bot\_cm_trades.json"))
trades = data["trades"] if isinstance(data, dict) else data
print("total trades:", len(trades))

# filter to bot, sort by time within (bot,address)
bot_trades = [t for t in trades if t.get("bot_id") == BOT]
print("bot trades:", len(bot_trades))

# group by address, sort by time
by_addr = defaultdict(list)
for t in bot_trades:
    by_addr[t.get("address")].append(t)

pairs = []  # (buy, sell)
for addr, ts in by_addr.items():
    ts.sort(key=lambda x: x.get("time",""))
    # walk: each buy joins to the next sell after it
    pending_buy = None
    for t in ts:
        if t.get("type") == "buy":
            if pending_buy is None:
                pending_buy = t
        elif t.get("type") == "sell":
            if pending_buy is not None:
                pairs.append((pending_buy, t))
                pending_buy = None

print("joined pairs:", len(pairs))

def get_pnl(sell):
    for k in ("pnl_pct","pnl_percent","pct","return_pct"):
        if k in sell and sell[k] is not None:
            return sell[k]
    return None

clean = []
for b, s in pairs:
    p = get_pnl(s)
    if p is None:
        continue
    if abs(p) > 300:
        continue
    clean.append((b, s, p))

print("clean pairs (|pnl|<=300):", len(clean))
if clean:
    pls = [p for _,_,p in clean]
    print("pnl median:", round(st.median(pls),2), "mean:", round(st.mean(pls),2))
    wins = sum(1 for p in pls if p>0)
    print("win rate %:", round(100*wins/len(pls),1))

# extract entry_meta features
def emeta(buy):
    for k in ("entry_meta","raw_meta","meta"):
        if k in buy and isinstance(buy[k], dict):
            return buy[k]
    return {}

# collect all numeric feature keys
winners = [(b,s,p) for b,s,p in clean if p>0]
losers  = [(b,s,p) for b,s,p in clean if p<=0]
print("winners:", len(winners), "losers:", len(losers))

feat_keys = set()
for b,_,_ in clean:
    m = emeta(b)
    for k,v in m.items():
        if isinstance(v,(int,float)) and not isinstance(v,bool):
            feat_keys.add(k)

def vals(group, k):
    out=[]
    for b,_,_ in group:
        m=emeta(b)
        v=m.get(k)
        if isinstance(v,(int,float)) and not isinstance(v,bool) and not (isinstance(v,float) and math.isnan(v)):
            out.append(v)
    return out

HOLDER_HINTS = ("holder","top10","_hf")
rows=[]
n=len(clean)
for k in feat_keys:
    wv=vals(winners,k); lv=vals(losers,k)
    if len(wv)<5 or len(lv)<5:
        continue
    wmed=st.median(wv); lmed=st.median(lv)
    allv=vals(clean,k)
    try:
        psd=st.pstdev(allv)
    except: psd=0
    sep = abs(wmed-lmed)/psd if psd>0 else 0
    cov = 100*len(allv)/n
    gr = "false" if any(h in k.lower() for h in HOLDER_HINTS) else "true"
    rows.append((sep,k,wmed,lmed,cov,gr,len(wv),len(lv)))

rows.sort(reverse=True)
print("\n=== TOP SEPARATORS ===")
for sep,k,wmed,lmed,cov,gr,nw,nl in rows[:30]:
    print(f"{sep:6.3f}  {k:42s} wmed={wmed:12.4f} lmed={lmed:12.4f} cov={cov:5.1f}% gr={gr} nw={nw} nl={nl}")
