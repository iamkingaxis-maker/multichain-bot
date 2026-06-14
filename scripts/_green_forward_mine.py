"""FORWARD GREEN-MINE (AxiS 2026-06-14): auto-capture the winning entry pattern
when the memecoin tape is CALM-GREEN (low downside breadth), to define the
chameleon's green-mode pattern. This is the "wallets-as-INTELLIGENCE" pivot —
mine the pattern, never copy the wallet.

SELF-CONTAINED (GeckoTerminal + DexScreener + RPC + CoinGecko) so a scheduled
REMOTE agent can run it daily with NO access to the bot's local recorder. It is
REGIME-GATED: it only mines on a calm-green window (breadth h1neg <= 25, SOL not
euphoric); otherwise it logs a skip and exits. Each green run appends a dated
signature to _green_pattern_log.json — over green days the green-mode pattern
accumulates (recurrence: one window can't define it).

Flow: regime check -> (if green) GT runners -> DexScreener early buyers ->
on-chain P&L (keep net-positive winners) -> reconstruct their entry-state from
minute OHLC -> winner/loser split -> append signature.

Usage: python scripts/_green_forward_mine.py   (exits early unless calm-green)
"""
import sys, os, json, time, asyncio, statistics, urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import discover_wallets_dexscreener as dw          # find_runners (GT) + harvest (DexScreener), self-contained
import wallet_decode as wd                          # trade_map on-chain P&L
from mine_wallet_entries import collect_buys, token_pool_ohlc
from _red_winner_entry_mine import _entry_full      # entry-state incl vol-spike

GREEN_BREADTH_MAX = 25.0     # calm-green: <=25% of active pools down on h1 (regime_size_dial GOOD ~23)
SOL_EUPHORIA_MIN = 2.0       # SOL melt-up = chasing tops = not a clean green-buy tape
LOG_PATH = "_green_pattern_log.json"


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _sol_h24():
    try:
        d = _get_json("https://api.coingecko.com/api/v3/coins/solana/market_chart?vs_currency=usd&days=2&interval=hourly")
        pr = [p[1] for p in d.get("prices", [])]
        if len(pr) >= 25:
            return (pr[-1] / pr[-25] - 1) * 100
    except Exception as e:
        print(f"  SOL h24 fetch failed: {e}", file=sys.stderr)
    return None


def current_regime():
    """breadth (% of top-volume Solana pools down on h1) + SOL h24 — the same
    signals the bot's regime_size_dial uses, computed from public data."""
    h1s = []
    for pg in range(1, 6):
        j = dw._gt(f"https://api.geckoterminal.com/api/v2/networks/solana/pools?sort=h24_volume_usd_desc&page={pg}")
        for it in (j or {}).get("data", []):
            pcp = (it.get("attributes", {}).get("price_change_percentage") or {})
            try:
                h1s.append(float(pcp.get("h1") or 0))
            except Exception:
                pass
        time.sleep(3.8)
    if not h1s:
        return None
    h1neg = 100.0 * sum(1 for x in h1s if x < 0) / len(h1s)
    return {"h1neg_pct": round(h1neg, 1), "sol_pc_h24": _sol_h24(), "n_pools": len(h1s)}


def is_calm_green(reg):
    if not reg:
        return False
    if reg["h1neg_pct"] > GREEN_BREADTH_MAX:
        return False
    sol = reg.get("sol_pc_h24")
    if sol is not None and sol >= SOL_EUPHORIA_MIN:
        return False
    return True


def _append_log(entry):
    log = []
    if os.path.exists(LOG_PATH):
        try:
            log = json.load(open(LOG_PATH))
        except Exception:
            log = []
    log.append(entry)
    json.dump(log, open(LOG_PATH, "w"), indent=1)


def _med(v):
    return statistics.median(v) if v else None


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    reg = current_regime()
    print(f"[{stamp}] REGIME: {reg}")
    if not is_calm_green(reg):
        print(f"SKIP: not calm-green (need h1neg<={GREEN_BREADTH_MAX:.0f} and SOL<{SOL_EUPHORIA_MIN}). No mine.")
        _append_log({"ts": stamp, "regime": reg, "mined": False, "reason": "not_calm_green"})
        return
    print("CALM-GREEN tape — mining the green winner pattern", flush=True)

    runners = dw.find_runners()
    print(f"runners: {len(runners)}", flush=True)
    if not runners:
        _append_log({"ts": stamp, "regime": reg, "mined": False, "reason": "no_runners"}); return
    maker_hits, _vol = asyncio.run(dw.harvest(runners[:30]))
    cands = sorted(maker_hits.items(), key=lambda kv: -len(kv[1]))[:40]
    print(f"early-buyer candidates: {len(cands)}", flush=True)

    winners = []
    for w, toks in cands:
        try:
            tok = wd.trade_map(w, 100)
        except Exception:
            continue
        net = trips = 0.0
        for m, r in tok.items():
            if r.get("buys") and r.get("sells") and r.get("spent"):
                net += (r["recv"] - r["spent"]); trips += 1
        if trips >= 3 and net > 0:
            winners.append(w)
        time.sleep(0.1)
    print(f"net-positive green winners: {len(winners)}", flush=True)

    feats = []
    for w in winners[:10]:
        try:
            buys = collect_buys(w, 70)
        except Exception:
            continue
        for m, ts, sol in buys:
            meta = token_pool_ohlc(m)
            if not meta:
                continue
            created, liq, fdv, ohlcv = meta
            f = _entry_full(ts, created, liq, fdv, ohlcv)
            if f:
                feats.append(f)
        time.sleep(0.3)

    win = [f for f in feats if f["fwd_max"] >= 30]
    lose = [f for f in feats if f["fwd_max"] <= 0]
    sig = {k: {"win": _med([f[k] for f in win if f.get(k) is not None]),
               "lose": _med([f[k] for f in lose if f.get(k) is not None])}
           for k in ("dip_90m", "age_h", "vol_spike", "liq", "fdv")}
    entry = {"ts": stamp, "regime": reg, "mined": True,
             "n_winners_wallets": len(winners), "n_entries": len(feats),
             "n_win": len(win), "n_lose": len(lose), "signature": sig}
    _append_log(entry)
    print("\n=== GREEN-WINDOW SIGNATURE (winner vs loser medians) ===")
    for k, v in sig.items():
        print(f"  {k:10s} WIN={v['win']}  LOSE={v['lose']}")
    print(f"\nappended to {LOG_PATH} ({len(feats)} entries, {len(win)} winners)")


if __name__ == "__main__":
    main()
