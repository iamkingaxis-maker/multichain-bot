#!/usr/bin/env python
"""Fresh-launch outcome accumulator.

The token-class divergence scan (2026-05-30) left ONE open question: does the
fresh-launch class (<2h old) need different filters than aged tokens? The trade
dumps are aged-skewed (fresh<2h had ~0 paired outcomes), so we can't answer it
from trade history. But the bot's universe-recorder ALREADY logs fresh tokens
WITH forward outcomes (peak_pct / exit_pct / won over +30min) — it just rolls at
1000 records (~8h), so fresh launches age off before a dataset accumulates.

This script persists them. Each run pulls /api/universe-recorder, keeps the
fresh<2h records, and appends new ones (dedup by event_id) to a growing local
store. Run it a few times a day (or wire as a Railway thread later) and over
1-2 weeks it builds the fresh-launch dataset we need. It also prints a current
fresh-vs-aged divergence read on the gates computable from recorder fields
(volume velocity = freshness proxy; bs_m5 = demand; liquidity tier).

Usage:  python scripts/fresh_launch_recorder.py
        python scripts/fresh_launch_recorder.py --report-only   # no pull, analyze store

Store: .fresh_launch_dataset.jsonl (gitignored). No deploy impact (read-only pull).
"""
from __future__ import annotations
import argparse, json, os, sys, io, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
URL = "https://gracious-inspiration-production.up.railway.app/api/universe-recorder"
STORE = ".fresh_launch_dataset.jsonl"
FRESH_MAX_AGE_H = 2.0


def pull():
    raw = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "flr"}), timeout=60).read()
    return json.loads(raw)


def load_store():
    if not os.path.exists(STORE):
        return {}
    out = {}
    for line in open(STORE, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            out[r.get("event_id")] = r
        except Exception:
            pass
    return out


def num(r, k):
    v = r.get(k)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def velocity(r):
    """m5 volume vs per-5min h1 baseline — the recorder's freshness proxy
    (analogue of 1m_volume_spike, which the recorder doesn't carry)."""
    vm5, vh1 = num(r, "vol_m5"), num(r, "vol_h1")
    if vm5 is None or not vh1:
        return None
    return vm5 / (vh1 / 12.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()

    store = load_store()
    before = len(store)
    if not args.report_only:
        try:
            recs = pull()
        except Exception as e:
            print(f"[warn] pull failed: {e}; reporting on existing store", file=sys.stderr)
            recs = []
        fresh = [r for r in recs if (num(r, "age_hours") or 99) < FRESH_MAX_AGE_H and r.get("event_id")]
        new = 0
        with open(STORE, "a", encoding="utf-8") as f:
            for r in fresh:
                if r["event_id"] not in store:
                    f.write(json.dumps(r) + "\n")
                    store[r["event_id"]] = r
                    new += 1
        print(f"pulled {len(recs)} universe records | fresh<2h in window: {len(fresh)} | "
              f"NEW persisted this run: {new} | store now: {len(store)} (was {before})")

    rows = list(store.values())
    done = [r for r in rows if num(r, "peak_pct") is not None]
    print(f"\n=== FRESH-LAUNCH DATASET: {len(rows)} tokens, {len(done)} with outcomes ===")
    if len(done) < 20:
        print(f"  (need ~50+ for a stable read — keep running; have {len(done)})")
    # core outcome distribution
    ng = sum(1 for r in done if num(r, "peak_pct") is not None and r["peak_pct"] < 1.0)
    won = sum(1 for r in done if r.get("won_5pct") or (num(r, "peak_pct") and r["peak_pct"] >= 5))
    if done:
        avg_exit = sum(r["exit_pct"] for r in done if num(r, "exit_pct") is not None) / len(done)
        print(f"  never-green (peak<+1%): {100*ng/len(done):.0f}%  |  reached +5%: {100*won/len(done):.0f}%  "
              f"|  avg exit_pct: {avg_exit:+.1f}%")

    # gate divergence on the fresh class (do these gates help fresh launches?)
    def split(rows, passfn):
        return [r for r in rows if passfn(r)], [r for r in rows if not passfn(r)]
    def ngr(rows):
        h = [r for r in rows if num(r, "peak_pct") is not None]
        return (100 * sum(1 for r in h if r["peak_pct"] < 1.0) / len(h)) if h else float("nan")
    def exitm(rows):
        h = [r for r in rows if num(r, "exit_pct") is not None]
        return (sum(r["exit_pct"] for r in h) / len(h)) if h else float("nan")
    GATES = {
        "velocity>=0.4 (fresh vol)": lambda r: (velocity(r) is None) or velocity(r) >= 0.4,
        "bs_m5>=1.0 (net buying)":   lambda r: (num(r, "bs_m5") is None) or num(r, "bs_m5") >= 1.0,
        "liq>=25k":                  lambda r: (num(r, "liq_usd") is None) or num(r, "liq_usd") >= 25000,
    }
    if len(done) >= 20:
        print("\n  gate effect on FRESH launches (Δ vs ungated):")
        print(f"    {'gate':28s}{'n_pass':>8}{'NG%pass':>9}{'NG%block':>10}{'exit%pass':>11}{'exit%block':>11}")
        for gn, gp in GATES.items():
            p, b = split(done, gp)
            print(f"    {gn:28s}{len(p):>8}{ngr(p):>9.0f}{ngr(b):>10.0f}{exitm(p):>+11.1f}{exitm(b):>+11.1f}")
        print("  (gate worth it for fresh if NG%pass << NG%block AND exit%pass > exit%block)")


if __name__ == "__main__":
    main()
