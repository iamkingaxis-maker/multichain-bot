#!/usr/bin/env python
"""Positive-Selection Scanner — reusable held-out + worst-day signal discovery.

Consolidates the pair-trades -> split-train/test -> worst-day-gate -> tercile/
quartile-bucket logic that was re-implemented ad-hoc across many one-off mining
scripts (and 5x in one 2026-05-29 session). One tool, parameterized.

A signal is a CANDIDATE only if it beats the fleet WR baseline in the TRAIN
window AND the TEST window AND on the single worst regime day — the discipline
that surfaced champion_whale_buyers (top_buy_makers_n<9) and champion_post_peak
(time_since_h24_peak_secs>=4h) while rejecting the good-day artifacts.

STATISTICAL HONESTY LAYER (folded in):
  * confound flags  — auto-marks candidates whose feature name signals a
    market-regime confound (sol_*) or token-identity proxy (absolute USD price
    levels: *_usd, mcap*, vwap*, *price*, support_level, 5m_high/low, 1h_low).
    These "win" by selecting an era/regime, not a tradeable signal.
  * --permute K     — shuffle win/loss labels WITHIN each window (preserving each
    window's base WR) and re-run the feature scan K times. Reports the mean #
    survivors under the null = expected false positives. With ~300 features x 2
    sides, some pass the 3-window gate by chance; this quantifies how many.

Usage:
    python scripts/ps_scan.py triggers
    python scripts/ps_scan.py features [--permute 30]
    python scripts/ps_scan.py feature time_since_h24_peak_secs
    python scripts/ps_scan.py feature top_buy_makers_n --quantiles 0.25,0.5,0.75

    # custom data / windows
    python scripts/ps_scan.py features --files a.json,b.json \
        --train 2026-05-16:2026-05-24 --test 2026-05-27:2026-05-29 --worst 2026-05-28

No Railway impact — pure local analysis over trade-dump JSON files.
"""
from __future__ import annotations
import argparse
import gc
import json
import os
import sys
from collections import Counter, defaultdict

# Reproducible label shuffles for --permute without Math.random()-style nondeterminism.
import random as _random

DEFAULT_FILES = ["trades_dump_candidates.json", ".watch7h/val_wide.json"]
DEFAULT_TRAIN = ("2026-05-16", "2026-05-24")
DEFAULT_TEST = ("2026-05-27", "2026-05-29")
DEFAULT_WORST = "2026-05-27"  # true worst fleet day in the 05-16..29 dumps (23% WR)
MIN_N = 20          # per-window minimum for a trigger/tercile to be scored
MIN_WORST_N = 15    # minimum trades on the worst day to score it

# Feature-name patterns that signal a confounded "signal" (regime / token-identity).
_CONFOUND_REGIME = ("sol_pc", "sol_macro", "btc_pc", "btc_macro", "regime_h", "_neg_pct",
                    "macro30", "macro60", "trades_today", "trades_per_sec")
_CONFOUND_IDENTITY = ("_usd", "mcap", "vwap", "price", "support_level",
                      "5m_high", "5m_low", "1h_low", "1h_high", "peak_h24",
                      "lifecycle_peak", "nearest_psych_level")
# Keys that are not features (bookkeeping / verdicts / match flags / text).
_SKIP_SUBSTR = ("verdict", "reasons", "_match", "block", "triggers_fired",
                "cnn_image", "_n_with_ts")


def _em(buy):
    """Return the feature dict for a buy record (entry_meta nested, else flat)."""
    em = buy.get("entry_meta")
    return em if isinstance(em, dict) else buy


def pair_file(path):
    """Pair buys->sells WITHIN one dump file into completed positions.

    A position closes on fully_closed or when cumulative sell_fraction >= ~1.0.
    Returns dicts: {bot, tok, t (buy ISO time), pnl, peak (max peak_pnl_pct),
    trig (set of triggers_fired), f (feature dict from entry_meta)}.
    """
    with open(path) as fh:
        d = json.load(fh)
    d.sort(key=lambda t: t.get("time", ""))
    ob = defaultdict(list)
    out = []
    for t in d:
        bot, tok, ty = t.get("bot_id"), t.get("token"), (t.get("type") or "").lower()
        if not bot or not tok:
            continue
        k = (bot, tok)
        if ty == "buy":
            ob[k].append({"buy": t, "pnl": 0.0, "rem": 1.0, "peak": None})
        elif ty == "sell" and ob[k]:
            x = ob[k][0]
            x["pnl"] += float(t.get("pnl") or 0)
            fr = t.get("sell_fraction")
            x["rem"] -= float(fr) if fr is not None else x["rem"]
            pk = t.get("peak_pnl_pct")
            if pk is not None and (x["peak"] is None or float(pk) > x["peak"]):
                x["peak"] = float(pk)
            if t.get("fully_closed") or x["rem"] <= 0.01:
                em = _em(x["buy"])
                out.append({
                    "bot": bot, "tok": tok,
                    "t": x["buy"].get("time", "") or "",
                    "pnl": x["pnl"], "peak": x["peak"],
                    "trig": set(x["buy"].get("triggers_fired")
                               or em.get("triggers_fired") or []),
                    "f": em,
                })
                ob[k].pop(0)
    del d
    gc.collect()
    return out


def load_completed(files):
    """Pair every file and dedup completed positions by (bot, token, buy_minute)."""
    seen = set()
    comp = []
    for f in files:
        if not os.path.exists(f):
            print(f"  [warn] missing file: {f}", file=sys.stderr)
            continue
        for c in pair_file(f):
            key = (c["bot"], c["tok"], c["t"][:16])
            if key in seen:
                continue
            seen.add(key)
            comp.append(c)
    return comp


def wr(rows):
    return (100.0 * sum(1 for c in rows if c["pnl"] > 0) / len(rows)) if rows else float("nan")


def dpt(rows):
    return (sum(c["pnl"] for c in rows) / len(rows)) if rows else float("nan")


def ng(rows):
    """Never-green fraction (peak_pnl_pct < 1.0)."""
    have = [c for c in rows if c["peak"] is not None]
    return (100.0 * sum(1 for c in have if c["peak"] < 1.0) / len(have)) if have else float("nan")


def _in(c, lo, hi):
    return lo <= c["t"][:10] <= hi


def split(comp, train, test, worst):
    tr = [c for c in comp if _in(c, *train)]
    te = [c for c in comp if _in(c, *test)]
    bad = [c for c in comp if c["t"][:10] == worst]
    return tr, te, bad


def confound_flag(name):
    n = name.lower()
    if any(s in n for s in _CONFOUND_REGIME):
        return "REGIME"
    if any(s in n for s in _CONFOUND_IDENTITY):
        return "IDENTITY"
    return ""


def fleet_baselines(tr, te, bad):
    return wr(tr), wr(te), wr(bad)


# ---------------------------------------------------------------- triggers ---
def scan_triggers(comp, train, test, worst):
    tr_all, te_all, bad_all = split(comp, train, test, worst)
    f_tr, f_te, f_bad = fleet_baselines(tr_all, te_all, bad_all)
    counts = Counter()
    for c in comp:
        for t in c["trig"]:
            counts[t] += 1
    rows = []
    for trig in counts:
        sel = [c for c in comp if trig in c["trig"]]
        tr = [c for c in sel if _in(c, *train)]
        te = [c for c in sel if _in(c, *test)]
        bad = [c for c in sel if c["t"][:10] == worst]
        if len(tr) < MIN_N or len(te) < MIN_N:
            continue
        durable = (wr(tr) > f_tr and wr(te) > f_te
                   and (len(bad) < MIN_WORST_N or wr(bad) > f_bad))
        worst_robust = len(bad) >= MIN_WORST_N and wr(bad) > f_bad
        rows.append({
            "name": trig, "n_tr": len(tr), "wr_tr": wr(tr), "d_tr": dpt(tr),
            "n_te": len(te), "wr_te": wr(te), "d_te": dpt(te),
            "n_bad": len(bad), "wr_bad": wr(bad),
            "durable": durable, "worst_robust": worst_robust,
        })
    return rows, (f_tr, f_te, f_bad)


# ---------------------------------------------------------------- features ---
def numeric_features(comp, min_cov):
    cov = Counter()
    for c in comp:
        for k, v in c["f"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
               and not any(s in k.lower() for s in _SKIP_SUBSTR):
                cov[k] += 1
    return {k: n for k, n in cov.items() if n >= min_cov}


def _tercile_cuts(vals):
    vals = sorted(vals)
    return vals[len(vals) // 3], vals[2 * len(vals) // 3]


def scan_features(comp, train, test, worst, min_cov=1800, _pnl=None):
    """Tercile scan. Returns durable (feat, side) candidates.

    _pnl: optional dict id(c)->pnl override for permutation nulls. When given,
    BOTH wr and $/tr are computed from the shuffled pnl so the null is consistent
    across both metrics.
    """
    tr_all, te_all, bad_all = split(comp, train, test, worst)

    def pnl_of(c):
        return _pnl[id(c)] if _pnl is not None else c["pnl"]

    def wr2(rows):
        return (100.0 * sum(1 for c in rows if pnl_of(c) > 0) / len(rows)) if rows else float("nan")

    def dpt2(rows):
        return (sum(pnl_of(c) for c in rows) / len(rows)) if rows else float("nan")

    f_tr, f_te, f_bad = wr2(tr_all), wr2(te_all), wr2(bad_all)
    feats = numeric_features(comp, min_cov)
    out = []
    for feat in feats:
        vals = [c["f"][feat] for c in comp if c["f"].get(feat) is not None]
        if len(set(vals)) < 5:
            continue
        lo, hi = _tercile_cuts(vals)
        if lo == hi:
            continue
        low = [c for c in comp if c["f"].get(feat) is not None and c["f"][feat] <= lo]
        high = [c for c in comp if c["f"].get(feat) is not None and c["f"][feat] >= hi]
        for side, rows in (("LOW", low), ("HIGH", high)):
            tr = [c for c in rows if _in(c, *train)]
            te = [c for c in rows if _in(c, *test)]
            bad = [c for c in rows if c["t"][:10] == worst]
            if len(tr) < 40 or len(te) < 40 or len(bad) < MIN_WORST_N:
                continue
            if wr2(tr) > f_tr and wr2(te) > f_te and wr2(bad) > f_bad:
                out.append({
                    "name": feat, "side": side, "cut": (lo if side == "LOW" else hi),
                    "n_tr": len(tr), "wr_tr": wr2(tr), "d_tr": dpt2(tr),
                    "n_te": len(te), "wr_te": wr2(te), "d_te": dpt2(te),
                    "n_bad": len(bad), "wr_bad": wr2(bad),
                    "confound": confound_flag(feat),
                })
    return out, (f_tr, f_te, f_bad)


def _dual_pos(surv):
    """Survivors that are CLEAN (no confound) AND +$/tr in BOTH windows — the
    strict, trustworthy subset that the WR-only gate alone can't establish."""
    return [s for s in surv if not s["confound"] and s["d_tr"] > 0 and s["d_te"] > 0]


def permutation_null(comp, train, test, worst, k, min_cov, seed=12345):
    """Shuffle actual P&L values WITHIN each window k times; re-run the scan.
    Nulls BOTH wr and $/tr consistently. Reports the null distribution of total
    survivors AND of the strict dual-+$/tr-clean subset, so a real signal is one
    whose observed dual-+$/tr count exceeds the null."""
    rng = _random.Random(seed)
    buckets = {"train": [], "test": [], "worst": [], "other": []}
    for c in comp:
        d = c["t"][:10]
        if d == worst:
            buckets["worst"].append(c)
        elif _in(c, *train):
            buckets["train"].append(c)
        elif _in(c, *test):
            buckets["test"].append(c)
        else:
            buckets["other"].append(c)
    counts, dual_counts = [], []
    for _ in range(k):
        pnl = {}
        for rows in buckets.values():
            vals = [c["pnl"] for c in rows]
            rng.shuffle(vals)
            for c, v in zip(rows, vals):
                pnl[id(c)] = v
        surv, _ = scan_features(comp, train, test, worst, min_cov, _pnl=pnl)
        counts.append(len(surv))
        dual_counts.append(len(_dual_pos(surv)))
    return counts, dual_counts


# ----------------------------------------------------------------- feature ---
def quartile_dive(comp, feat, train, test, worst, quantiles):
    vals = sorted(c["f"][feat] for c in comp if c["f"].get(feat) is not None)
    if len(vals) < 40:
        print(f"  {feat}: low coverage ({len(vals)})")
        return
    q = [vals[int(len(vals) * p)] for p in quantiles]
    tr_all, te_all, bad_all = split(comp, train, test, worst)
    print(f"  {feat}: cov {len(vals)}/{len(comp)}  cuts {[round(x, 4) for x in q]}  "
          f"confound={confound_flag(feat) or 'none'}")
    print(f"  fleet baseline: TR wr={wr(tr_all):.0f} | TE wr={wr(te_all):.0f} | worst wr={wr(bad_all):.0f}")
    edges = [(-float('inf'), q[0], f"Q1 <={q[0]:.4g}")]
    for i in range(len(q) - 1):
        edges.append((q[i], q[i + 1], f"Q{i+2} {q[i]:.4g}..{q[i+1]:.4g}"))
    edges.append((q[-1], float('inf'), f"Q{len(q)+1} >{q[-1]:.4g}"))
    for loq, hiq, lab in edges:
        rows = [c for c in comp if c["f"].get(feat) is not None and loq < c["f"][feat] <= hiq] \
            if loq != -float('inf') else \
            [c for c in comp if c["f"].get(feat) is not None and c["f"][feat] <= hiq]
        tr = [c for c in rows if _in(c, *train)]
        te = [c for c in rows if _in(c, *test)]
        bad = [c for c in rows if c["t"][:10] == worst]
        print(f"    {lab:26s} n={len(rows):4d} | TR n={len(tr):4d} wr={wr(tr):3.0f} ${dpt(tr):+6.2f}"
              f" | TE n={len(te):4d} wr={wr(te):3.0f} ${dpt(te):+6.2f}"
              f" | worst n={len(bad):3d} wr={wr(bad):3.0f}")


# --------------------------------------------------------------------- cli ---
def _win(s):
    a, b = s.split(":")
    return (a, b)


def main():
    ap = argparse.ArgumentParser(description="Positive-selection signal scanner")
    ap.add_argument("mode", choices=["triggers", "features", "feature"])
    ap.add_argument("feature_name", nargs="?", default=None)
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--train", type=_win, default=DEFAULT_TRAIN)
    ap.add_argument("--test", type=_win, default=DEFAULT_TEST)
    ap.add_argument("--worst", default=DEFAULT_WORST)
    ap.add_argument("--min-cov", type=int, default=1800)
    ap.add_argument("--permute", type=int, default=0, help="run K permutation nulls (features mode)")
    ap.add_argument("--quantiles", default="0.25,0.5,0.75")
    args = ap.parse_args()

    files = [f.strip() for f in args.files.split(",") if f.strip()]
    print(f"Loading {files} ...", file=sys.stderr)
    comp = load_completed(files)
    print(f"completed positions: {len(comp)} | windows: train {args.train} "
          f"test {args.test} worst {args.worst}")

    if args.mode == "triggers":
        rows, (f_tr, f_te, f_bad) = scan_triggers(comp, args.train, args.test, args.worst)
        print(f"fleet baseline WR: train={f_tr:.0f} test={f_te:.0f} worst={f_bad:.0f}\n")
        print(f"{'trigger':32s}{'nTR':>5}{'TRwr':>5}{'TR$':>7}{'nTE':>5}{'TEwr':>5}{'TE$':>7}"
              f"{'nBAD':>5}{'BADwr':>6}  flag")
        rows.sort(key=lambda r: -(min(r["wr_tr"] - f_tr, r["wr_te"] - f_te)))
        for r in rows:
            flag = "DURABLE" if r["durable"] else ""
            if r["worst_robust"] and r["durable"]:
                flag = "** DURABLE+WORST"
            print(f"{r['name']:32s}{r['n_tr']:5d}{r['wr_tr']:5.0f}{r['d_tr']:+7.2f}"
                  f"{r['n_te']:5d}{r['wr_te']:5.0f}{r['d_te']:+7.2f}"
                  f"{r['n_bad']:5d}{r['wr_bad']:6.0f}  {flag}")

    elif args.mode == "features":
        rows, (f_tr, f_te, f_bad) = scan_features(comp, args.train, args.test, args.worst, args.min_cov)
        n_feats = len(numeric_features(comp, args.min_cov))
        print(f"fleet baseline WR: train={f_tr:.0f} test={f_te:.0f} worst={f_bad:.0f}")
        print(f"features scanned (cov>={args.min_cov}): {n_feats} | survivors: {len(rows)} "
              f"({sum(1 for r in rows if not r['confound'])} clean, "
              f"{sum(1 for r in rows if r['confound'])} confounded)\n")
        print(f"{'feature':34s}{'side':5s}{'nTR':>4}{'TRwr':>5}{'TR$':>7}"
              f"{'nTE':>4}{'TEwr':>5}{'TE$':>7}{'nBAD':>5}{'BADwr':>6}  flag")
        rows.sort(key=lambda r: -(min(r["wr_tr"] - f_tr, r["wr_te"] - f_te, r["wr_bad"] - f_bad)))
        for r in rows:
            print(f"{r['name']:34s}{r['side']:5s}{r['n_tr']:4d}{r['wr_tr']:5.0f}{r['d_tr']:+7.2f}"
                  f"{r['n_te']:4d}{r['wr_te']:5.0f}{r['d_te']:+7.2f}"
                  f"{r['n_bad']:5d}{r['wr_bad']:6.0f}  {r['confound']}")
        obs_dual = _dual_pos(rows)
        if args.permute:
            print(f"\n[honesty] running {args.permute} permutation nulls "
                  f"(shuffle actual P&L within window)...", file=sys.stderr)
            counts, dual_counts = permutation_null(comp, args.train, args.test, args.worst,
                                                   args.permute, args.min_cov)
            mean = sum(counts) / len(counts)
            dmean = sum(dual_counts) / len(dual_counts)
            real_clean = sum(1 for r in rows if not r["confound"])
            print(f"\n=== MULTIPLE-COMPARISON HONESTY ===")
            print(f"  WR-gate survivors:    observed {len(rows)} ({real_clean} clean)  vs  "
                  f"null mean {mean:.1f} (range {min(counts)}-{max(counts)})")
            print(f"    -> the WR-beats-fleet-in-3-windows gate is NOISE-DOMINATED; do not trust it alone.")
            print(f"  STRICT (clean + +$/tr BOTH windows):  observed {len(obs_dual)}  vs  "
                  f"null mean {dmean:.2f}")
            print(f"    -> est. real signals: ~{max(0, len(obs_dual) - dmean):.1f}. "
                  f"These survive a metric the null almost never passes:")
            for s in sorted(obs_dual, key=lambda s: -(s["d_tr"] + s["d_te"])):
                print(f"       {s['name']} {s['side']} (cut {s['cut']:.4g}): "
                      f"TR {s['wr_tr']:.0f}%/${s['d_tr']:+.2f}  TE {s['wr_te']:.0f}%/${s['d_te']:+.2f}")

    elif args.mode == "feature":
        if not args.feature_name:
            ap.error("feature mode needs a feature name")
        qs = [float(x) for x in args.quantiles.split(",")]
        quartile_dive(comp, args.feature_name, args.train, args.test, args.worst, qs)


if __name__ == "__main__":
    main()
