#!/usr/bin/env python3
"""rh_fleet_fidelity.py — fleet-wide RH paper FIDELITY correction (read-only).

Paper booked SELLS at the QuoterV2 quote regardless of whether a real sell would
execute. So for every bot, a paper "win" on a token that is a honeypot / rug is
an ILLUSION. This re-books each bot's closed positions: if the token is NOT
sellable now (quote reverts / ~0), the paper realized pnl is discarded and the
position is marked a TOTAL LOSS (-entry cost) — the live-faithful result.

Caveat (honest): "sellable now" is a retrospective proxy. A token that was
genuinely sold then died later is over-penalized; but on RH the dominant error
is the opposite (paper counted honeypot/rug sells as wins), so this is the right
direction and a conservative lower bound on each bot's REAL edge.

Reads the fleet ledger from the dashboard (per-bot raw rows), checks each unique
token's live sellability once (cached), prints raw-paper vs fidelity-corrected
realized P&L per bot, and flags bots that flip from "profit" to loss.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.environ.get("RH_DASH_BASE",
                      "https://gracious-inspiration-production.up.railway.app")
# ENV-FIRST (argv only as a CLI fallback): this module is imported by the RH lane
# for its periodic auto-refresh, where sys.argv belongs to the LANE — reading argv
# first would smuggle the lane's own flags in as dashboard creds. Reads are public
# anyway (only the WRITE needs DASH_AUTH), so these are effectively optional.
_ARGV = sys.argv[1:] if (sys.argv and sys.argv[0].endswith("rh_fleet_fidelity.py")) else []
USER = os.environ.get("RH_DASH_USER", "") or (_ARGV[0] if len(_ARGV) > 0 else "")
PW = os.environ.get("RH_DASH_PW", "") or (_ARGV[1] if len(_ARGV) > 1 else "")
ENTRY_USD = 25.0

BOTS = ["rh_young_v1","rh_deep_only","rh_first_touch","rh_bites2","rh_wide_ladder",
    "rh_moonbag","rh_demand_heavy","rh_liq40","rh_prime_hours","rh_launch_scalp",
    "rh_aged_hold","rh_aged_derisk","rh_aged_deep","rh_f_pullback","rh_f_arc_scalp",
    "rh_f_popret","rh_f_reload24","rh_f_reload_mid","rh_deep_barbell",
    "rh_deep_barbell_capped","rh_fill_probe","rh_lowvar_catstop","rh_lowvar_box",
    "rh_deep_consolidated","rh_strength_trail","rh_deepdemand","rh_demand_broad",
    "rh_deepdemand_capped","rh_bankfast","rh_stable_demand","rh_stable_deep",
    "rh_stable_ageddeep"]


def dash_rows(bot):
    r = subprocess.run(["curl","-s","--max-time","30","-u",f"{USER}:{PW}",
        f"{BASE}/api/rh-paper?bot={bot}&raw=1"], capture_output=True, text=True)
    try:
        return json.loads(r.stdout).get("rows", [])
    except Exception:
        return []


_sell_cache = {}
def token_dead(token) -> bool:
    """True if the token cannot be sold now (rug/honeypot) — quote reverts/~0."""
    if token in _sell_cache:
        return _sell_cache[token]
    dead = True
    try:
        from core.rh_execution import RhExecutor
        ex = RhExecutor()
        # quote a nominal 1000-token sell; alive pool -> >0 out, dead -> 0/raise
        q = ex.quote_sell(token, int(1000 * 1e18))
        out = getattr(q, "amount_out", None)
        dead = not (out and out > 0)
    except Exception:
        dead = True
    _sell_cache[token] = dead
    time.sleep(0.4)
    return dead


def main():
    print("=== RH FLEET FIDELITY (paper vs sellability-corrected) ===")
    # gather all bot ledgers + unique tokens
    per_bot = {}
    tokens = set()
    for b in BOTS:
        rows = dash_rows(b)
        if rows:
            per_bot[b] = rows
            for r in rows:
                if r.get("ev") == "buy" and r.get("token"):
                    tokens.add(r["token"])
    print(f"bots with data: {len(per_bot)}   unique tokens to sellability-check: {len(tokens)}")
    # sellability of each unique token (cached, paced)
    dead_tokens = set()
    for i, t in enumerate(tokens):
        if token_dead(t):
            dead_tokens.add(t)
        if (i + 1) % 25 == 0:
            print(f"  checked {i+1}/{len(tokens)}  dead-so-far={len(dead_tokens)}")
    print(f"DEAD/unsellable tokens: {len(dead_tokens)}/{len(tokens)} "
          f"({len(dead_tokens)/max(1,len(tokens))*100:.0f}%)\n")
    # per-bot re-book
    print(f"{'bot':26} {'raw_paper$':>11} {'fidelity$':>11} {'gap$':>9}  {'flip?':>6}")
    results = []
    for b, rows in per_bot.items():
        # RE-ENTRY FIX (2026-07-17, 07-15 audit finding): this keyed by pool
        # and OVERWROTE the buy on every re-entry, so a dead token was booked
        # as ONE -$25 no matter how many times the bot re-bought it — which
        # flattered exactly the re-entry-heavy bots. Track the buy COUNT and
        # TOTAL $ deployed per pool: N re-buys into a dead token = N stakes
        # lost, not one.
        pos = defaultdict(lambda: {"buy": None, "n_buys": 0, "usd": 0.0,
                                   "sp": []})

        def _ts(row):
            try:
                from datetime import datetime
                return datetime.fromisoformat(
                    str(row.get("ts")).replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        for r in rows:
            k = r.get("pool")
            if r.get("ev") == "buy":
                pos[k]["buy"] = r
                pos[k]["n_buys"] += 1
                pos[k]["usd"] += abs(r.get("usd") or ENTRY_USD)
            elif r.get("ev") == "sell" and isinstance(r.get("pnl_usd"), (int, float)):
                # SOL SCRUB RULE ported (2026-07-17, audit finding #4 /
                # reference_spike_illusion_rebaseline_2026_07_01): a POSITIVE
                # sell within 10s of the buy is a stale-price phantom fill
                # (the spike-illusion class), not an executable win — SOL's
                # live=paper reconciliation proved these never fill at the
                # booked price. Drop the WIN legs only; fast losses are real
                # (you can always sell into a dump, just not into a phantom
                # spike).
                bts, sts = _ts(pos[k]["buy"] or {}), _ts(r)
                if (r["pnl_usd"] > 0 and bts is not None and sts is not None
                        and (sts - bts) < 10.0):
                    continue
                pos[k]["sp"].append(r["pnl_usd"])
        raw = fid = 0.0
        for k, v in pos.items():
            if not (v["buy"] and v["sp"]):
                continue
            paper_pnl = sum(v["sp"])
            raw += paper_pnl
            tok = v["buy"].get("token")
            # fidelity: dead token -> EVERY stake deployed into it was an
            # illusion-booked loss, not just the last one
            fid += (-v["usd"] if tok in dead_tokens else paper_pnl)
        flip = "FLIP" if (raw > 0 and fid <= 0) else ""
        results.append((b, raw, fid, fid - raw, flip))
    for b, raw, fid, gap, flip in sorted(results, key=lambda x: x[1], reverse=True):
        print(f"{b:26} {raw:+11.2f} {fid:+11.2f} {gap:+9.2f}  {flip:>6}")
    traw = sum(r[1] for r in results); tfid = sum(r[2] for r in results)
    print(f"\nFLEET TOTAL: raw_paper=${traw:+.2f}  fidelity-corrected=${tfid:+.2f}  "
          f"illusion=${tfid-traw:+.2f}")
    print(f"bots that FLIP profit->loss: {sum(1 for r in results if r[4])}/{len(results)}")

    # push the fidelity-corrected per-bot P&L to the dashboard so its DISPLAYED
    # numbers are honest, not the quote-illusion paper P&L.
    # dashboard READS are public but WRITES need the DASH_AUTH cred (not the
    # read user/pass) — that's why an earlier push got 'Invalid credentials'.
    dash_auth = os.environ.get(
        "DASH_AUTH", "jcole:pMIwPSmRmoPfteWViuGgjaTdnx5JfO-g-e6-_zjdlmo")
    payload = {b: round(fid, 2) for b, raw, fid, gap, flip in results}
    payload["_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload["_dead_rate"] = round(len(dead_tokens) / max(1, len(tokens)), 3)
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "20", "-u", dash_auth,
            "-X", "POST", "-H", "Content-Type: application/json",
            "-d", json.dumps(payload),
            f"{BASE}/api/rh-fidelity/ingest"], capture_output=True, text=True)
        print(f"pushed fidelity to dashboard: {r.stdout[:140]}")
    except Exception as e:
        print(f"dashboard push failed: {e}")


if __name__ == "__main__":
    main()
