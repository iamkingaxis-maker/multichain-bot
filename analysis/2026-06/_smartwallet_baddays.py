"""Smart-wallet bad-day study (2026-06-10, AxiS): what do our 10 elite wallets
do on bad days — do they keep winning, stand down, or change profile?

Per wallet: last ~80 swaps via free RPC -> buys/sells by CT day -> per-day
realized round-trips (FIFO by token) + activity. Bad days: 06-08, 06-10
(fleet net-$ bottom third); 06-09 = comparison. Then DexScreener lookup on
the tokens they bought on bad days (age now ~ age then + days; mcap is
CURRENT, caveat noted).
"""
import json, sys, time, collections
from datetime import datetime, timezone, timedelta
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

WATCH = json.load(open("config/follow_watchlist.json"))
RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]
STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
BAD = {"2026-06-08", "2026-06-10"}
_rr = [0]


def rpc(method, params, tries=4):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    for t in range(tries):
        url = RPCS[_rr[0] % len(RPCS)]; _rr[0] += 1
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                j = json.loads(r.read())
            if "result" in j:
                return j["result"]
        except Exception:
            pass
        time.sleep(1.2 * (t + 1))
    return None


def ct_day(bt):
    return (datetime.fromtimestamp(bt, tz=timezone.utc)
            - timedelta(hours=5)).strftime("%Y-%m-%d")


per_day = collections.defaultdict(lambda: collections.defaultdict(
    lambda: {"buys": 0, "sells": 0, "rt_n": 0, "rt_win": 0, "rt_sol": 0.0}))
bad_buy_mints = collections.Counter()

for w in WATCH:
    sigs = rpc("getSignaturesForAddress", [w, {"limit": 80}]) or []
    open_pos = {}   # mint -> (bt, sol_in)
    events = []     # (bt, mint, side, sol)
    for s in sigs:
        sig = s.get("signature"); bt = s.get("blockTime")
        if not sig or s.get("err") or not bt:
            continue
        tx = rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0,
                                          "encoding": "jsonParsed"}])
        time.sleep(0.35)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner") == w}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner") == w}
        try:
            keys = [k if isinstance(k, str) else k.get("pubkey")
                    for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(w)
            sol_delta = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            continue
        for mint in set(list(pre) + list(post)):
            if mint in STABLE:
                continue
            delta = post.get(mint, 0) - pre.get(mint, 0)
            if delta > 0 and sol_delta < 0:
                events.append((bt, mint, "buy", -sol_delta))
            elif delta < 0 and sol_delta > 0:
                events.append((bt, mint, "sell", sol_delta))
    events.sort()
    for bt, mint, side, sol in events:
        d = ct_day(bt)
        rec = per_day[d][w]
        if side == "buy":
            rec["buys"] += 1
            open_pos[mint] = (bt, sol)
            if d in BAD:
                bad_buy_mints[mint] += 1
        else:
            rec["sells"] += 1
            ent = open_pos.pop(mint, None)
            if ent:
                rec["rt_n"] += 1
                rec["rt_win"] += sol > ent[1]
                rec["rt_sol"] += sol - ent[1]
    print(f"wallet {w[:8]} done: {len(events)} swap events", file=sys.stderr)

print("\nPER-DAY ELITE ACTIVITY + REALIZED (SOL):")
days = sorted(per_day)
print(f"{'day':12s}{'buys':>6s}{'sells':>6s}{'rtrips':>7s}{'rtWR':>6s}{'rtSOL':>8s}  bad?")
for d in days:
    b = sum(r['buys'] for r in per_day[d].values())
    s = sum(r['sells'] for r in per_day[d].values())
    rn = sum(r['rt_n'] for r in per_day[d].values())
    rw = sum(r['rt_win'] for r in per_day[d].values())
    rs = sum(r['rt_sol'] for r in per_day[d].values())
    wr = f"{rw/rn:.0%}" if rn else "--"
    print(f"{d:12s}{b:6d}{s:6d}{rn:7d}{wr:>6s}{rs:+8.2f}  {'BAD' if d in BAD else ''}")

print("\nPER-WALLET on BAD days:")
for w in WATCH:
    b = sum(per_day[d][w]['buys'] for d in BAD if w in per_day[d])
    rn = sum(per_day[d][w]['rt_n'] for d in BAD if w in per_day[d])
    rw = sum(per_day[d][w]['rt_win'] for d in BAD if w in per_day[d])
    rs = sum(per_day[d][w]['rt_sol'] for d in BAD if w in per_day[d])
    print(f"  {w[:10]} bad-day buys={b} rtrips={rn} winrate={rw}/{rn} sol={rs:+.2f}")

json.dump(sorted(bad_buy_mints), open("_elite_badday_mints.json", "w"))
print(f"\nbad-day buy mints saved: {len(bad_buy_mints)} -> _elite_badday_mints.json")
