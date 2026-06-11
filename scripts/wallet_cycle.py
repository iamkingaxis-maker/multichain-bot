"""WALLET CYCLE (2026-06-11, AxiS: "correct cycling of new wallets will be the key").

The sustainability engine: one daily command that runs the full wallet
lifecycle and outputs evidence-backed ACTIONS.

  RECRUIT -> VET -> BENCH -> ACTIVE -> (PODS) -> CUT, continuously.

Checks per run:
  1. DORMANCY  - active wallets silent > DORMANT_H (rotation signal; the
                 operators-rotate-wallets lesson) -> CUT candidate
  2. COPY TAX  - copyability verdicts on post-overhaul fires:
                 TOXIC at n>=10 our-closes -> CUT candidate
  3. RECRUITS  - vetted daily-positive keepers from the harvest funnels not
                 yet seated -> PROMOTE candidates (consensus-only seats)
  4. ROSTER FLOOR - keep ACTIVE size in [MIN_ROSTER, MAX_ROSTER]

Usage:
  python scripts/wallet_cycle.py            # report + recommended actions
  python scripts/wallet_cycle.py --apply    # apply the MECHANICAL rules
        (dormancy cuts, TOXIC cuts, keeper promotions) with backups.
Pod seats are NEVER auto-assigned - earned via the board, human-approved.
"""
from __future__ import annotations
import gzip
import io
import json
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://gracious-inspiration-production.up.railway.app"
WATCHLIST = "config/follow_watchlist.json"
DORMANT_H = 36.0
TOXIC_MIN_CLOSES = 10
MIN_ROSTER, MAX_ROSTER = 6, 12
try:
    from core.rpc_pool import rpc_pool as _rpc_pool
    RPCS = _rpc_pool()
except Exception:
    RPCS = ["https://api.mainnet-beta.solana.com",
            "https://solana-rpc.publicnode.com",
            "https://solana.drpc.org"]


def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def last_activity_hours(addr):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getSignaturesForAddress",
                       "params": [addr, {"limit": 1}]})
    for rpc in RPCS:
        out = subprocess.run(["curl", "-s", "--max-time", "8", "-X", "POST", rpc,
                              "-H", "Content-Type: application/json", "-d", body],
                             capture_output=True, text=True, errors="replace").stdout
        try:
            res = json.loads(out).get("result") or []
            if res and res[0].get("blockTime"):
                return (time.time() - res[0]["blockTime"]) / 3600
        except Exception:
            pass
        time.sleep(0.3)
    return None


def copy_tax_verdicts(watch):
    """our $/close per wallet on its fires (post-overhaul) via follow logs."""
    fl = _get(f"{BASE}/api/follow-logs")
    sigs = [s for s in (fl.get("signals") or [])
            if s.get("wallets") and (s.get("ts") or 0) >= 1781108400]
    trades = json.load(open("_trades_cache.json"))
    buy_strat = {}
    for t in trades:
        if t.get("type") == "buy" and t.get("strategy"):
            buy_strat[(t.get("pair_address") or t.get("address") or "").lower()] = t["strategy"]
    ours = defaultdict(list)
    for t in trades:
        if t.get("type") != "sell":
            continue
        if "cancelled on restart" in (t.get("reason") or "").lower():
            continue
        k = (t.get("pair_address") or t.get("address") or "").lower()
        if str(buy_strat.get(k, "")).startswith("smart_follow"):
            ours[(t.get("address") or "").lower()].append(float(t.get("pnl") or 0))
    # FRACTIONAL attribution (2026-06-11 Deniz lesson): consensus fires
    # multi-counted — one bad token charged EVERY voter full price, flipping
    # several wallets TOXIC on shared variance. Each token's P&L now splits
    # across its voters, so toxicity must be a wallet's OWN.
    per = defaultdict(lambda: [0.0, 0.0])
    seen = set()
    for s in sigs:
        tok = (s.get("token") or "").lower()
        if tok not in ours or tok in seen:
            continue
        seen.add(tok)
        voters = list(s.get("wallets") or [])
        if not voters:
            continue
        share = 1.0 / len(voters)
        for w in voters:
            per[w][0] += sum(ours[tok]) * share
            per[w][1] += len(ours[tok]) * share
    out = {}
    for w in watch:
        pnl, n = per.get(w, [0.0, 0.0])
        if n >= TOXIC_MIN_CLOSES:
            out[w] = ("TOXIC" if pnl / n < -1.0 else
                      "COPYABLE" if pnl / n > 0 else "TAXED", round(pnl / n, 2), n)
        else:
            out[w] = ("thin", round(pnl / n, 2) if n else None, n)
    return out


def tombstones():
    """Wallets cut for cause (config/follow_cuts.json) — never auto re-seat.
    A cut wallet may only return via explicit human decision after its
    copy-tax record is confronted (2tYcXQCf resurfaced at 78% rWR hours
    after being cut as a bleed-engine: quality != copyable)."""
    try:
        return set(json.load(open("config/follow_cuts.json")))
    except Exception:
        return set()


def record_cut(wallet, reason):
    try:
        cuts = json.load(open("config/follow_cuts.json"))
    except Exception:
        cuts = {}
    cuts[wallet] = f"{reason} (cycle cut {datetime.now(timezone.utc).strftime('%Y-%m-%d')})"
    json.dump(cuts, open("config/follow_cuts.json", "w"), indent=2)


def recruits():
    cands = []
    for f in ("_wide_harvest_results.json", "_daily_positive_candidates.json",
              "_wallet_diversity_scores.json"):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        rows = d.get("keepers", d) if isinstance(d, dict) else d
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            w = r.get("wallet")
            cls = r.get("class") or ("SELECTOR" if "runner_hits" in r else None)
            if (w and cls == "SELECTOR" and (r.get("net_realized") or 0) > 0
                    and (r.get("roundtrips") or 0) >= 1):
                cands.append({"wallet": w, "net": r.get("net_realized"),
                              "rWR": r.get("realized_wr"), "ndist": r.get("n_distinct")})
    seen, out = set(), []
    for c in sorted(cands, key=lambda x: -(x.get("net") or 0)):
        if c["wallet"] not in seen:
            seen.add(c["wallet"])
            out.append(c)
    return out


def main():
    apply = "--apply" in sys.argv
    watch = json.load(open(WATCHLIST))
    print(f"WALLET CYCLE - {datetime.now(timezone.utc).isoformat()[:16]}Z | active={len(watch)}")
    cuts, why = [], {}

    print("\n1) DORMANCY")
    for w in watch:
        h = last_activity_hours(w)
        flag = ""
        if h is not None and h > DORMANT_H:
            cuts.append(w)
            why[w] = f"dormant {h:.0f}h (rotation)"
            flag = "  -> CUT (rotation)"
        print(f"   {w[:10]} last activity {h if h is None else round(h, 1)}h{flag}")
        time.sleep(0.3)

    print("\n2) COPY TAX (post-overhaul fires)")
    verdicts = copy_tax_verdicts(watch)
    for w, (v, avg, n) in verdicts.items():
        flag = ""
        if v == "TOXIC" and w not in cuts:
            cuts.append(w)
            why[w] = f"copy-tax TOXIC (${avg}/close, n={n})"
            flag = "  -> CUT (toxic)"
        print(f"   {w[:10]} {v:9s} avg/close={avg} n={n}{flag}")

    print("\n3) RECRUITS (vetted, daily-positive, unseated)")
    dead = tombstones()
    rec = [r for r in recruits() if r["wallet"] not in watch and r["wallet"] not in dead]
    for r in rec[:8]:
        print(f"   {r['wallet'][:10]} net={r['net']} rWR={r['rWR']} ndist={r['ndist']}")

    survivors = [w for w in watch if w not in cuts]
    room = max(0, MAX_ROSTER - len(survivors))
    need = max(0, MIN_ROSTER - len(survivors))
    promote = [r["wallet"] for r in rec[:max(need, min(room, len(cuts)))]]

    print(f"\nACTIONS: cut {len(cuts)} | promote {len(promote)} "
          f"| roster {len(watch)} -> {len(survivors) + len(promote)}")
    for w in cuts:
        print(f"   CUT {w}  ({why[w]})")
    for w in promote:
        print(f"   PROMOTE {w}  (consensus seat; board judges at n>={TOXIC_MIN_CLOSES})")

    if apply and (cuts or promote):
        bak = f"config/follow_watchlist_precycle_{datetime.now(timezone.utc).strftime('%m%d%H%M')}.bak"
        json.dump(watch, open(bak, "w"), indent=2)
        new = survivors + promote
        json.dump(new, open(WATCHLIST, "w"), indent=2)
        for w in cuts:
            record_cut(w, why.get(w, "cycle cut"))
        print(f"\nAPPLIED. backup={bak} | new roster={len(new)} | "
              f"commit+deploy required for the strategy to reload.")
    elif cuts or promote:
        print("\n(report only - rerun with --apply to execute)")
    else:
        print("\nno actions - roster healthy.")


if __name__ == "__main__":
    main()
