# scripts/robinhood_tape_recorder.py
"""Robinhood Chain memecoin tape recorder — Phase 0 of the RH-chain infra
(2026-07-10 recon fleet: chain live 7/1, permissionless, ~16.6k tokens/day,
young-pool liq $28k-463k = our Solana habitat, NO major sniper bots yet).

KEYLESS v0: GeckoTerminal public API only (fleet-verified live for
network=robinhood; 30 req/min free -> we pace ~3s). Discovers new pools,
watches the young ones, and appends per-trade tape in the exact Solana
rip_tape schema so every existing analysis script works unchanged:
    {"kind": "buy"|"sell", "maker": <wallet>, "volume_usd": <f>,
     "ts": <ISO8601>, "pair": <pool>, "sym": <name>}
plus periodic pool-meta snapshots (liq/price/fdv) to pools_meta.jsonl.

Read-only, no keys, no wallet. Runs per-session (no local 24/7 — tape gaps
are normal per project rules). Upgrade path: Alchemy WSS Swap-event logs
(Phase 0.5, fresh session).

Usage: python scripts/robinhood_tape_recorder.py [max_minutes]
"""
import json
import os
import sys
import time
import urllib.request

GT = "https://api.geckoterminal.com/api/v2"
NET = "robinhood"
OUT_DIR = os.path.join("scratchpad", "robinhood_tapes")
PACE_SECS = 7.0          # GT throttles hard on trades endpoint; ~8/min sustained
WATCH_MAX = 6            # pools per cycle (429-safe budget)
MAX_AGE_H = 24.0         # young pools only (the habitat)
MIN_LIQ = 5000.0         # skip dust
META_EVERY = 5           # snapshot pool meta every N cycles


def _get(path):
    req = urllib.request.Request(GT + path, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _pace():
    time.sleep(PACE_SECS)


def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


def discover():
    """New pools -> [(pool_addr, sym, liq_usd, age_h)] young + liquid."""
    out = []
    try:
        d = _get(f"/networks/{NET}/new_pools?page=1")
        now = time.time()
        for p in d.get("data", []):
            a = p.get("attributes", {})
            addr = (p.get("id", "") or "").split("_")[-1]
            try:
                liq = float(a.get("reserve_in_usd") or 0)
            except (TypeError, ValueError):
                liq = 0.0
            created = a.get("pool_created_at")
            age_h = None
            if created:
                try:
                    from datetime import datetime
                    age_h = (now - datetime.fromisoformat(
                        created.replace("Z", "+00:00")).timestamp()) / 3600
                except Exception:
                    pass
            if addr and liq >= MIN_LIQ and (age_h is None or age_h <= MAX_AGE_H):
                out.append((addr, a.get("name", "?").split(" /")[0], liq, age_h))
    except Exception as e:
        print(f"[disc] {type(e).__name__}: {e}", flush=True)
    return out


def fetch_trades(pool):
    """GT trades -> rip_tape rows (fleet-verified: maker/side/size/ts present)."""
    rows = []
    try:
        d = _get(f"/networks/{NET}/pools/{pool}/trades")
        for t in d.get("data", []):
            a = t.get("attributes", {})
            kind = str(a.get("kind", "")).lower()          # buy | sell
            if kind not in ("buy", "sell"):
                continue
            try:
                vol = float(a.get("volume_in_usd") or 0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "kind": kind,
                "volume_usd": round(vol, 2),
                "ts": a.get("block_timestamp"),
                "maker": a.get("tx_from_address") or "",
                "pair": pool,
                "sym": "",                                  # filled by caller
                "tx": a.get("tx_hash") or "",
            })
    except Exception as e:
        print(f"[trades {pool[:10]}] {type(e).__name__}: {e}", flush=True)
    return rows


def main():
    max_minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    os.makedirs(OUT_DIR, exist_ok=True)
    meta_path = os.path.join(OUT_DIR, "pools_meta.jsonl")
    watch = {}          # pool -> {"sym":, "liq":, "seen_tx": set}
    t_end = time.time() + max_minutes * 60
    cycle = 0
    total = 0
    print(f"[rh-tape] recording network={NET} for {max_minutes:.0f}min "
          f"(watch<= {WATCH_MAX}, liq>=${MIN_LIQ:.0f}, age<={MAX_AGE_H}h)", flush=True)
    while time.time() < t_end:
        cycle += 1
        # 1. discovery (1 call)
        for addr, sym, liq, age_h in discover():
            if addr not in watch:
                watch[addr] = {"sym": sym, "liq": liq, "seen_tx": set()}
                _append(meta_path, {"ev": "discovered", "pool": addr, "sym": sym,
                                    "liq": liq, "age_h": age_h,
                                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())})
                print(f"[disc] +{sym} liq=${liq:,.0f} age={age_h if age_h is None else round(age_h,1)}h", flush=True)
        _pace()
        # keep the freshest/most-liquid WATCH_MAX
        if len(watch) > WATCH_MAX:
            keep = sorted(watch.items(), key=lambda kv: -kv[1]["liq"])[:WATCH_MAX]
            watch = dict(keep)
        # 2. trades per watched pool
        for pool, st in list(watch.items()):
            rows = fetch_trades(pool)
            fresh = 0
            tape = os.path.join(OUT_DIR, f"tape_{pool[:12]}.jsonl")
            for r in rows:
                if r["tx"] in st["seen_tx"]:
                    continue
                st["seen_tx"].add(r["tx"])
                r["sym"] = st["sym"]
                r.pop("tx", None)
                _append(tape, r)
                fresh += 1
            total += fresh
            if fresh:
                print(f"[tape] {st['sym']:<14} +{fresh:3d} trades (total {total})", flush=True)
            if len(st["seen_tx"]) > 5000:
                st["seen_tx"] = set(list(st["seen_tx"])[-2000:])
            _pace()
        # 3. periodic meta snapshot
        if cycle % META_EVERY == 0:
            for pool, st in watch.items():
                try:
                    d = _get(f"/networks/{NET}/pools/{pool}")
                    a = (d.get("data", {}) or {}).get("attributes", {})
                    _append(meta_path, {"ev": "snapshot", "pool": pool, "sym": st["sym"],
                                        "liq": a.get("reserve_in_usd"),
                                        "price": a.get("base_token_price_usd"),
                                        "fdv": a.get("fdv_usd"),
                                        "vol_h1": (a.get("volume_usd") or {}).get("h1"),
                                        "ts": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())})
                except Exception:
                    pass
                _pace()
        print(f"[cycle {cycle}] watching {len(watch)} pools | trades total {total}", flush=True)
    print(f"[rh-tape] done: {total} trades across {len(watch)} pools -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
