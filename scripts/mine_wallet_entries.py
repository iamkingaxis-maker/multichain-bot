"""Mine the 6 usable wallets' BUYS for entry-signal structure -> tune OUR gates.

For each wallet buy we reconstruct the token state AT entry from GeckoTerminal minute
OHLC and compare the distributions to our current entry gates:
  - dip depth off 90m high   (our pool_a_dipgate = -16%)
  - token age at entry        (our pool_a_goodpond = age >= 24h)
  - liquidity / mcap          (our goodpond = mcap 500k-10M)
  - time-of-day (CT)
  - forward outcome           (max % over next 6h from entry = was it a good entry)

Output: per-feature distribution (p25/median/p75) + a direct "shift X -> Y" readout vs
our current thresholds, plus the win-rate of their entries.

Usage: python scripts/mine_wallet_entries.py [sigs=80]  > out.txt 2> err.txt
"""
from __future__ import annotations
import json, os, sys, time, subprocess, statistics, collections
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
RPCS = ["https://solana.leorpc.com/?api_key=FREE", "https://api.mainnet-beta.solana.com"]
from curl_cffi import requests as cr  # noqa: E402
_S = cr.Session(impersonate="chrome")

# our current gates (for the comparison readout)
GATE_DIP = -16.0       # pool_a_dipgate: buy if <= -16% off 90m high
GATE_AGE_H = 24.0      # pool_a_goodpond: age >= 24h
GATE_MCAP_LO, GATE_MCAP_HI = 500_000, 10_000_000


def _rpc(method, params, tries=2):
    for rpc in RPCS:
        for t in range(tries):
            out = subprocess.run(["curl", "-s", "--max-time", "8", "-X", "POST", rpc,
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})],
                capture_output=True, text=True, errors="replace").stdout
            try:
                d = json.loads(out)
                if "result" in d:
                    return d["result"]
            except Exception:
                pass
            time.sleep(0.25)
    return None


def _gt(url, tries=4):
    for t in range(tries):
        try:
            r = _S.get(url, timeout=25)
            if r.status_code == 200:
                return r.json()
            time.sleep(8 if r.status_code == 429 else 3)
        except Exception:
            time.sleep(4)
    return None


def collect_buys(addr, sigs):
    """[(token_mint, blockTime, sol_size)] for this wallet's recent buys."""
    sl = _rpc("getSignaturesForAddress", [addr, {"limit": sigs}]) or []
    out = []
    for s in sl:
        sig = s.get("signature"); bt = s.get("blockTime")
        if not sig or s.get("err") or not bt:
            continue
        tx = _rpc("getTransaction", [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
        time.sleep(0.1)
        if not tx or not tx.get("meta"):
            continue
        meta = tx["meta"]
        pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
               for b in (meta.get("preTokenBalances") or []) if b.get("owner") == addr}
        post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                for b in (meta.get("postTokenBalances") or []) if b.get("owner") == addr}
        try:
            keys = [k if isinstance(k, str) else k.get("pubkey")
                    for k in tx["transaction"]["message"]["accountKeys"]]
            wi = keys.index(addr); sol_d = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
        except Exception:
            continue
        for m in set(list(pre) + list(post)):
            if m in STABLE:
                continue
            if post.get(m, 0) - pre.get(m, 0) > 0 and sol_d < 0:
                out.append((m, bt, -sol_d))
    return out


_pool_cache = {}


def token_pool_ohlc(mint):
    """Return (pool_created_ts, liq_usd, fdv, ohlcv_list[[ts,o,h,l,c,v],...]) or None."""
    if mint in _pool_cache:
        return _pool_cache[mint]
    j = _gt(f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools")
    time.sleep(2.6)
    if not j or not j.get("data"):
        _pool_cache[mint] = None; return None
    # highest-liquidity pool
    best = max(j["data"], key=lambda p: float((p.get("attributes") or {}).get("reserve_in_usd") or 0))
    a = best.get("attributes", {})
    pair = best.get("id", "").replace("solana_", "")
    try:
        created = datetime.fromisoformat(a.get("pool_created_at").replace("Z", "+00:00")).timestamp()
    except Exception:
        created = None
    liq = float(a.get("reserve_in_usd") or 0)
    fdv = float(a.get("fdv_usd") or 0)
    oj = _gt(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pair}/ohlcv/minute?aggregate=1&limit=1000")
    time.sleep(2.6)
    ohlcv = (oj or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", []) if oj else []
    res = (created, liq, fdv, ohlcv)
    _pool_cache[mint] = res
    return res


def entry_features(entry_ts, created, liq, fdv, ohlcv):
    """dip_90m (%), age_h, fwd_max (%), all from minute OHLC around entry."""
    if not ohlcv:
        return None
    # ohlcv rows = [ts, o, h, l, c, v]; sort ascending
    rows = sorted(ohlcv, key=lambda r: r[0])
    # entry bar = last bar at or before entry_ts
    entry_close = None; ei = None
    for i, r in enumerate(rows):
        if r[0] <= entry_ts:
            entry_close = r[4]; ei = i
        else:
            break
    if entry_close is None or ei is None or entry_close <= 0:
        return None
    prior = [r for r in rows[:ei + 1] if entry_ts - r[0] <= 5400]   # prior 90 min
    hi_90 = max((r[2] for r in prior), default=entry_close)
    dip_90 = (entry_close / hi_90 - 1.0) * 100 if hi_90 > 0 else 0.0
    fwd = [r for r in rows[ei + 1:] if r[0] - entry_ts <= 21600]    # next 6h
    fwd_max = (max((r[2] for r in fwd), default=entry_close) / entry_close - 1.0) * 100
    age_h = ((entry_ts - created) / 3600.0) if created else None
    return {"dip_90m": dip_90, "age_h": age_h, "fwd_max": fwd_max, "liq": liq, "fdv": fdv}


def pctl(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals); k = int(round((len(s) - 1) * p))
    return s[k]


def main():
    sigs = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    wallets = json.load(open("_usable_wallets.json"))
    print(f"mining entries for {len(wallets)} usable wallets (sigs={sigs})\n", file=sys.stderr)

    all_buys = []   # (wallet, mint, ts, sol)
    for w in wallets:
        try:
            b = collect_buys(w, sigs)
        except Exception as e:
            print(f"  {w[:12]} collect ERR {e}", file=sys.stderr); b = []
        for m, ts, sol in b:
            all_buys.append((w, m, ts, sol))
        print(f"  {w[:12]}: {len(b)} buys", file=sys.stderr)
        time.sleep(0.3)

    distinct = sorted({b[1] for b in all_buys})
    print(f"\ntotal buys={len(all_buys)} | distinct tokens={len(distinct)}", flush=True)

    feats = []
    tod = collections.Counter()
    for i, (w, m, ts, sol) in enumerate(all_buys):
        meta = token_pool_ohlc(m)
        if not meta:
            continue
        created, liq, fdv, ohlcv = meta
        f = entry_features(ts, created, liq, fdv, ohlcv)
        if not f:
            continue
        f["sol"] = sol
        feats.append(f)
        ct = datetime.fromtimestamp(ts, tz=timezone.utc) - timedelta(hours=5)  # CDT (June)
        tod[ct.hour] += 1
        if (i + 1) % 20 == 0:
            print(f"  ...reconstructed {len(feats)} entries", file=sys.stderr)

    if not feats:
        print("no entries reconstructable (buys older than OHLC window?) — raise sigs or rerun")
        return

    dips = [f["dip_90m"] for f in feats]
    ages = [f["age_h"] for f in feats if f["age_h"] is not None]
    fwds = [f["fwd_max"] for f in feats]
    liqs = [f["liq"] for f in feats if f["liq"] > 0]
    fdvs = [f["fdv"] for f in feats if f["fdv"] > 0]
    sols = [f["sol"] for f in feats]

    print(f"\n=== ENTRY-STATE DISTRIBUTIONS ({len(feats)} reconstructed entries) ===")
    def line(nm, v, fmt="{:.1f}"):
        if not v:
            print(f"  {nm:14s} (none)"); return
        print(f"  {nm:14s} p25={fmt.format(pctl(v,.25))}  median={fmt.format(statistics.median(v))}  "
              f"p75={fmt.format(pctl(v,.75))}")
    line("dip_90m %", dips)
    line("age_h", ages)
    line("fwd_max %", fwds)
    line("liq $", liqs, "{:,.0f}")
    line("fdv/mcap $", fdvs, "{:,.0f}")
    line("buy_size SOL", sols, "{:.3f}")

    wins = sum(1 for f in feats if f["fwd_max"] >= 15)
    print(f"\n  entry win-rate (fwd_max>=+15%): {wins}/{len(feats)} = {wins/len(feats)*100:.0f}%")

    print("\n=== vs OUR CURRENT GATES ===")
    med_dip = statistics.median(dips)
    print(f"  DIP: our gate buys at <= {GATE_DIP:.0f}% off 90m high. "
          f"They buy at median {med_dip:+.1f}% "
          f"({sum(1 for d in dips if d<=GATE_DIP)}/{len(dips)} = "
          f"{sum(1 for d in dips if d<=GATE_DIP)/len(dips)*100:.0f}% would pass our gate).")
    if ages:
        med_age = statistics.median(ages)
        young = sum(1 for a in ages if a < GATE_AGE_H)
        print(f"  AGE: our gate requires >= {GATE_AGE_H:.0f}h. They buy median {med_age:.1f}h; "
              f"{young}/{len(ages)} = {young/len(ages)*100:.0f}% are YOUNGER than our floor "
              f"(would be BLOCKED).")
    if fdvs:
        inband = sum(1 for x in fdvs if GATE_MCAP_LO <= x <= GATE_MCAP_HI)
        print(f"  MCAP: our band {GATE_MCAP_LO/1e3:.0f}k-{GATE_MCAP_HI/1e6:.0f}M. "
              f"{inband}/{len(fdvs)} = {inband/len(fdvs)*100:.0f}% of their entries fall in-band.")
    top_h = tod.most_common(5)
    print(f"  TIME-OF-DAY (CT) top hours: {', '.join(f'{h}:00({n})' for h,n in top_h)}")

    json.dump(feats, open("_wallet_entry_feats.json", "w"))
    print("\nwrote _wallet_entry_feats.json")


if __name__ == "__main__":
    main()
