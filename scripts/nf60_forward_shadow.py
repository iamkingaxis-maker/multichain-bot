"""nf60 forward-shadow (2026-06-03).

Fleet-wide FORWARD shadow of net_flow_60s_imbalance as an entry BLOCK gate.

WHY THIS IS READ-ONLY (no production change): net_flow_60s_imbalance is already
stamped into entry_meta on every fleet buy. So measuring "what would a
nf60 < threshold block have done" requires NO change to the trading path -- it is
pure analysis over /api/trades. This script reads actual fleet buys placed AFTER a
cutoff (default = the validation date, so the sample is genuinely OUT-OF-SAMPLE vs
the 2026-06-03 9-agent held-out study) and reports the live winner-kill ratio,
$/trade, and per-token concentration of the would-block set.

The 2026-06-03 study (reference_nf60_fleet_gate_falsified) found the gate FAILS
held-out as a fleet-wide hard block (kill-ratio ~0.68 ~= random; $ benefit was a
3-token mirage; NS at the true n=84 tokens). The ONLY missing piece was a LIVE
out-of-sample kill-ratio over a window wider than the 3-day in-sample cohort. This
script accumulates exactly that. KILL CRITERION: if the live kill-ratio stays >=
~0.6 (kills winners nearly as fast as a random block) over a wider window, the
fleet-wide block is dead and nf60 stays scoped to the fresh-graduation cohort
(momentum_grad_probe) + sizing only.

Usage:
  python scripts/nf60_forward_shadow.py                # report on forward window
  python scripts/nf60_forward_shadow.py --since 2026-06-04T00:00:00
  python scripts/nf60_forward_shadow.py --snapshot     # also append a dated json snapshot
  python scripts/nf60_forward_shadow.py --all          # ignore cutoff (whole recent cohort)

Snapshots: .nf60_forward_shadow/{date}.json (rolling forward history, since
/api/trades full=1 caps at 5000 recent records).
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent.parent
SNAP_DIR = ROOT / ".nf60_forward_shadow"
API = os.environ.get(
    "BOT_API_BASE", "https://gracious-inspiration-production.up.railway.app"
)
# Validation cutoff: the held-out study ran on data up to ~2026-06-03. Trades after
# this are forward / out-of-sample relative to that study.
DEFAULT_SINCE = "2026-06-04T00:00:00"


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def fetch_trades(limit: int = 5000) -> list:
    r = requests.get(f"{API}/api/trades", params={"full": 1, "limit": limit}, timeout=60)
    r.raise_for_status()
    return r.json()


def build_episodes(trades: list) -> list:
    """FIFO buy->sell join into closed episodes with blended P&L. Mirrors the
    validated _nf_episodes extraction: multi-bot format (sell_fraction present),
    blended pnl_pct, phantom exclusion (>200%), only buys carrying nf60."""
    buys = [t for t in trades if t.get("type") == "buy"]
    sells = [t for t in trades if t.get("type") == "sell"]
    sells_by = defaultdict(list)
    for s in sells:
        sells_by[(s.get("bot_id"), s.get("address"))].append(s)
    for k in sells_by:
        sells_by[k].sort(key=lambda x: x.get("time") or "")

    used = defaultdict(int)
    rows = []
    for b in sorted(buys, key=lambda x: x.get("time") or ""):
        key = (b.get("bot_id"), b.get("address"))
        legs = sells_by.get(key, [])
        bt = b.get("time") or ""
        frac = 0.0
        blended = 0.0
        peak = 0.0
        nlegs = 0
        i = used[key]
        while i < len(legs) and frac < 0.999:
            leg = legs[i]
            if (leg.get("time") or "") < bt:
                i += 1
                continue
            f = leg.get("sell_fraction") or 0.0
            p = leg.get("pnl_pct")
            if p is None:
                i += 1
                continue
            take = min(f, 1.0 - frac)
            blended += p * take
            frac += f
            peak = max(peak, leg.get("peak_pnl_pct") or 0.0)
            nlegs += 1
            i += 1
        used[key] = i
        if nlegs == 0:
            continue
        em = b.get("entry_meta")
        if not isinstance(em, dict) or "net_flow_60s_imbalance" not in em:
            continue
        if blended > 200:  # phantom
            continue
        rows.append({
            "bot_id": b.get("bot_id"), "token": b.get("token"), "address": b.get("address"),
            "time": bt, "amount_usd": b.get("amount_usd"),
            "pnl_pct": round(blended, 4), "peak_pnl_pct": round(peak, 4),
            "win": 1 if blended > 0 else 0,
            "nf60": _num(em.get("net_flow_60s_imbalance")),
        })
    return rows


def shadow_stats(episodes: list, threshold: float) -> dict:
    """PURE. Given closed episodes (each with nf60, win, pnl_pct, amount_usd, address),
    compute what a `nf60 < threshold` BLOCK gate would do. Returns the winner-kill
    ratio (winners blocked per loser blocked), the realized $ of the blocked set
    (negative = blocking saves money), and per-token concentration. Episodes with
    nf60 is None are never blocked (fail-open), matching production gate semantics."""
    blocked = [e for e in episodes if e.get("nf60") is not None and e["nf60"] < threshold]
    kept = [e for e in episodes if not (e.get("nf60") is not None and e["nf60"] < threshold)]
    bw = sum(e["win"] for e in blocked)
    bl = len(blocked) - bw

    def dollars(rs):
        return sum((e.get("amount_usd") or 0) * (e["pnl_pct"] / 100.0) for e in rs)

    by_token = defaultdict(float)
    for e in blocked:
        by_token[e["address"]] += (e.get("amount_usd") or 0) * (e["pnl_pct"] / 100.0)
    top = sorted(by_token.items(), key=lambda kv: kv[1])[:5]  # most-negative = most "saved"
    blocked_dollars = dollars(blocked)
    return {
        "threshold": threshold,
        "n_total": len(episodes),
        "n_blocked": len(blocked),
        "blocked_wins": bw,
        "blocked_losses": bl,
        "kill_ratio": round(bw / bl, 3) if bl else None,  # winners killed per loser blocked
        "blocked_dollars": round(blocked_dollars, 2),  # negative => blocking saves $
        "blocked_wr": round(bw / len(blocked), 3) if blocked else None,
        "kept_wr": round(sum(e["win"] for e in kept) / len(kept), 3) if kept else None,
        "top_token_saved": [(a[:6], round(-d, 1)) for a, d in top],  # $ saved per token
        "top1_share": (round(min(by_token.values()) / blocked_dollars, 2)
                       if blocked and blocked_dollars < 0 else None),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE,
                    help="ISO cutoff; only buys at/after this are forward/out-of-sample")
    ap.add_argument("--all", action="store_true", help="ignore cutoff (whole recent cohort)")
    ap.add_argument("--snapshot", action="store_true", help="append a dated json snapshot")
    args = ap.parse_args()

    trades = fetch_trades()
    episodes = build_episodes(trades)
    if not args.all:
        episodes = [e for e in episodes if (e["time"] or "") >= args.since]

    cutoff_label = "ALL recent cohort" if args.all else f">= {args.since} (forward/OOS)"
    n_tokens = len(set(e["address"] for e in episodes))
    print(f"\n=== nf60 forward-shadow ===  {cutoff_label}")
    print(f"closed episodes: {len(episodes)}  |  unique tokens: {n_tokens}", end="")
    if episodes:
        wr = sum(e["win"] for e in episodes) / len(episodes)
        print(f"  |  fleet WR {wr*100:.1f}%")
    else:
        print("\n  (no forward episodes yet -- re-run after the fleet trades past the cutoff)")
        return

    print("\n  KILL CRITERION: kill_ratio >= ~0.6 over a wide window = block stays dead "
          "(fresh-grad scope + sizing only).\n")
    stats = {}
    for thr in (-0.2, -0.3):
        s = shadow_stats(episodes, thr)
        stats[str(thr)] = s
        kr = s["kill_ratio"]
        verdict = "(barely beats random -> DEAD)" if (kr is not None and kr >= 0.6) else \
                  "(<0.6 winner-safe -> WATCH)" if kr is not None else ""
        print(f"  nf60 < {thr}: block {s['n_blocked']:>4} "
              f"({s['blocked_wins']}w/{s['blocked_losses']}l, kill={kr} {verdict}) "
              f"| $blocked={s['blocked_dollars']:+.1f} "
              f"| blocked_WR={s['blocked_wr']} vs kept_WR={s['kept_wr']}")
        if s["top_token_saved"]:
            print(f"        top tokens saved: {s['top_token_saved']}  top1_share={s['top1_share']}")

    if args.snapshot:
        SNAP_DIR.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        out = SNAP_DIR / f"{stamp}.json"
        out.write_text(json.dumps({
            "stamp": stamp, "since": (None if args.all else args.since),
            "n_episodes": len(episodes), "n_tokens": n_tokens, "stats": stats,
        }, indent=2))
        print(f"\n  snapshot -> {out}")


if __name__ == "__main__":
    main()
