"""Post-stop trajectory test (cooldown run ~02:00 UTC) — the exit-horizon verdict.

For each hard-stopped smart_follow position: did the token recover past the stop
price within 12h (grace period would have saved it) or keep falling/rug (stop was
right)? Hardened GT client: status-aware, 429 backoff 15s, 6s pacing, one process.
"""
import json, collections, time, sys
from datetime import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from curl_cffi import requests as cr
S = cr.Session(impersonate="chrome")

def gt(url, tries=5):
    for i in range(tries):
        try:
            r = S.get(url, timeout=25)
            if r.status_code == 200:
                return r.json()
            wait = 15 if r.status_code == 429 else 5
            print(f"    [gt {r.status_code} -> wait {wait}s]", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            print(f"    [gt err {e}]", file=sys.stderr); time.sleep(6)
    return None

tr = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "_bleed_trades.json"))
trades = tr if isinstance(tr, list) else tr.get("trades", [])
bb = collections.defaultdict(list)
for t in trades:
    if t.get("type") == "buy":
        k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        bb[k].append(t)
for k in bb:
    bb[k].sort(key=lambda b: b.get("time", ""))

stopped = []
for t in trades:
    if t.get("type") != "sell":
        continue
    r = (t.get("reason") or "").lower()
    if "stop" not in r or "faststop" in r or "pre-stop" in r:
        continue
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    c = [b for b in bb.get(k, []) if b.get("time", "") < t.get("time", "")]
    if not c or (c[-1].get("strategy") or "") != "smart_follow":
        continue
    buy = c[-1]
    ep = float(buy.get("entry_price") or 0)
    if ep <= 0:
        continue
    stopped.append((t.get("token"),
                    (buy.get("pair_address") or t.get("pair_address") or "").strip(),
                    (buy.get("address") or t.get("address") or "").strip(),
                    t.get("time"),
                    ep * (1 + float(t.get("pnl_pct") or 0) / 100), float(t.get("pnl") or 0)))

def resolve_pool(mint):
    """smart_follow buys carry no pair_address (external-signal path) — resolve the
    top pool from the token mint via DexScreener (also tells us if token is dead)."""
    j = gt(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", tries=3)
    pairs = [p for p in ((j or {}).get("pairs") or []) if (p.get("chainId") == "solana")]
    if not pairs:
        return None
    pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    return pairs[0].get("pairAddress")


print(f"hard-stopped smart_follow positions: {len(stopped)}")
rec = bounce = fell = dead = 0
for tok, pair, mint, ts, px, pnl in stopped:
    if not pair and mint:
        pair = resolve_pool(mint) or ""
        time.sleep(2)
    if not pair:
        print(f"  {str(tok)[:10]:10s} ${pnl:+6.0f} — NO POOL on DexScreener (token delisted/dead)")
        dead += 1
        continue
    ets = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    j = gt(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate=15&limit=1000")
    rows = (j or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", []) if j else []
    after = [x for x in rows if x[0] > ets and x[0] <= ets + 12 * 3600]
    if not after:
        status = "fetch-fail" if not rows else "DEAD post-stop (no bars after)"
        print(f"  {str(tok)[:10]:10s} ${pnl:+6.0f} — {status}")
        dead += 1
    else:
        hi = max(x[2] for x in after)
        rp = (hi / px - 1) * 100
        v = "RECOVERED" if rp > 15 else ("bounced" if rp > 5 else "kept falling")
        if rp > 15: rec += 1
        elif rp > 5: bounce += 1
        else: fell += 1
        print(f"  {str(tok)[:10]:10s} ${pnl:+6.0f} post-stop 12h max {rp:+7.1f}%  {v}")
    time.sleep(6)

print(f"\nVERDICT INPUTS — RECOVERED(>15%): {rec} | bounced(5-15%): {bounce} | kept falling: {fell} | dead/fail: {dead}")
print("If RECOVERED dominates -> grace-period exit fix for smart_follow justified.")
print("If kept-falling/dead dominates -> stops are RIGHT; exit-horizon fix is TP-side only (let winners run), not stop-side.")
