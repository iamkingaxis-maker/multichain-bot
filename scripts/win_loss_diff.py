#!/usr/bin/env python
"""Per-trigger WIN-vs-LOSS feature differential — multi-week, token-deduped, held-out.

For EVERY trigger that fired, split its firings into winning vs losing cohorts and
find the feature(s) that separate them. A separator only counts if it holds in the
SAME direction in BOTH the TRAIN and TEST windows (held-out) and survives token
dedup (one row per token, so a correlated cluster like TinyWorld can't manufacture
a separation). Implements feedback_win_loss_firing_differential: the edge is in the
DIFFERENCE between winning and losing fires, not in the loss alone.

Reuses ps_scan's pairing / dedup / window / confound machinery.

  WIN/LOSS token   = a token whose MEDIAN realized pnl over its fires is >0 / <=0.
  separator        = feature whose win-token median vs loss-token median differs by
                     >= EFFECT_MIN (standardized) with the SAME sign in train AND test,
                     and >= MIN_TOK tokens on each side in both windows.
  confound flag     = REGIME (market regime proxy) / IDENTITY (absolute USD level) —
                     a "separator" that is really selecting an era/token, not a signal.

Usage:
    python scripts/win_loss_diff.py
    python scripts/win_loss_diff.py --train 2026-05-12:2026-05-24 --test 2026-05-27:2026-05-30
    python scripts/win_loss_diff.py --files a.json,b.json --min-tokens 5 --effect 0.6
"""
from __future__ import annotations
import argparse
import statistics as st
from collections import defaultdict, Counter

from ps_scan import (
    load_completed, _in, confound_flag, _SKIP_SUBSTR,
)

# Multi-week meta-bearing dumps (05-12 -> 05-30). April dumps have no entry_meta.
DEFAULT_FILES = [
    "trades_local_dump.json",        # 05-12 .. 05-24
    "trades_dump_candidates.json",   # 05-16 .. 05-25
    ".watch7h/val_wide.json",        # 05-27 .. 05-29
    ".overnight_trades.json",        # 05-28 .. 05-30
    ".trades_now.json",              # 05-29 .. current (fresh pull)
]
DEFAULT_TRAIN = ("2026-05-12", "2026-05-24")
DEFAULT_TEST = ("2026-05-27", "2026-05-31")

# Known enforcement status (from dip_scanner.py comments + memory). Annotated only.
RETIRED = {"channel_hvn", "sweep_holder_liq", "two_pattern_demand",
           "chart_score_reversal"}
DEMOTED = {"whale_conviction"}
LOSER_SHADOW = {"low_buy_slip", "support_with_60s_flow", "support_big_buyer",
                "channel_pos_swing", "net_flow_5m_demand", "chart_channel_strong"}

MIN_TRIG_TOKENS = 8   # need >=8 distinct tokens total to analyze a trigger at all
MIN_TOK = 4           # >=4 tokens per side per window for a feature to be a separator
EFFECT_MIN = 0.6      # standardized median-diff threshold (both windows, same sign)


def _med(xs):
    return st.median(xs) if xs else None


def token_rows(rows):
    """Collapse trade-level rows to one row per token: median pnl + median feature."""
    by_tok = defaultdict(list)
    for c in rows:
        by_tok[c["tok"]].append(c)
    out = {}
    for tok, cs in by_tok.items():
        pnl_med = st.median([c["pnl"] for c in cs])
        feats = defaultdict(list)
        for c in cs:
            for k, v in c["f"].items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) \
                        and not any(s in k.lower() for s in _SKIP_SUBSTR):
                    feats[k].append(v)
        out[tok] = {
            "pnl": pnl_med,
            "win": pnl_med > 0,
            "f": {k: st.median(vs) for k, vs in feats.items() if vs},
        }
    return out


def standardized_diff(win_vals, loss_vals):
    """(median(win) - median(loss)) / pooled scale (MAD-based, robust)."""
    if len(win_vals) < 2 or len(loss_vals) < 2:
        return None, None, None
    wm, lm = st.median(win_vals), st.median(loss_vals)
    allv = win_vals + loss_vals
    med_all = st.median(allv)
    mad = st.median([abs(x - med_all) for x in allv]) or st.pstdev(allv)
    scale = (mad * 1.4826) if mad else 1e-9
    return (wm - lm) / scale, wm, lm


def feature_diff(win_toks, loss_toks):
    """For each feature, standardized win-vs-loss separation across these tokens."""
    feats = set()
    for d in list(win_toks.values()) + list(loss_toks.values()):
        feats.update(d["f"].keys())
    res = {}
    for f in feats:
        wv = [d["f"][f] for d in win_toks.values() if f in d["f"]]
        lv = [d["f"][f] for d in loss_toks.values() if f in d["f"]]
        if len(wv) < MIN_TOK or len(lv) < MIN_TOK:
            continue
        eff, wm, lm = standardized_diff(wv, lv)
        if eff is None:
            continue
        res[f] = {"eff": eff, "win_med": wm, "loss_med": lm,
                  "n_w": len(wv), "n_l": len(lv)}
    return res


def analyze_trigger(sel, train, test):
    tr = [c for c in sel if _in(c, *train)]
    te = [c for c in sel if _in(c, *test)]
    tr_tok = token_rows(tr)
    te_tok = token_rows(te)
    all_tok = set(token_rows(sel).keys())
    if len(all_tok) < MIN_TRIG_TOKENS:
        return None

    def wr_tok(toks):
        return (100.0 * sum(1 for d in toks.values() if d["win"]) / len(toks)) if toks else float("nan")

    def dpt_trade(rows):
        return (sum(c["pnl"] for c in rows) / len(rows)) if rows else float("nan")

    tr_w = {k: v for k, v in tr_tok.items() if v["win"]}
    tr_l = {k: v for k, v in tr_tok.items() if not v["win"]}
    te_w = {k: v for k, v in te_tok.items() if v["win"]}
    te_l = {k: v for k, v in te_tok.items() if not v["win"]}

    d_tr = feature_diff(tr_w, tr_l)
    d_te = feature_diff(te_w, te_l)

    # A feature separates if present in both windows, same sign, |eff|>=EFFECT_MIN in both.
    separators = []
    for f in set(d_tr) & set(d_te):
        a, b = d_tr[f], d_te[f]
        if (abs(a["eff"]) >= EFFECT_MIN and abs(b["eff"]) >= EFFECT_MIN
                and (a["eff"] > 0) == (b["eff"] > 0)):
            separators.append({
                "feat": f, "confound": confound_flag(f),
                "eff_tr": a["eff"], "eff_te": b["eff"],
                "win_tr": a["win_med"], "loss_tr": a["loss_med"],
                "win_te": b["win_med"], "loss_te": b["loss_med"],
                "robust": min(abs(a["eff"]), abs(b["eff"])),
            })
    separators.sort(key=lambda s: -s["robust"])
    return {
        "n_tr": len(tr), "n_te": len(te),
        "tok_tr": len(tr_tok), "tok_te": len(te_tok),
        "win_tok_tr": len(tr_w), "loss_tok_tr": len(tr_l),
        "win_tok_te": len(te_w), "loss_tok_te": len(te_l),
        "wr_tok_tr": wr_tok(tr_tok), "wr_tok_te": wr_tok(te_tok),
        "dpt_tr": dpt_trade(tr), "dpt_te": dpt_trade(te),
        "separators": separators,
    }


def verdict(r):
    clean = [s for s in r["separators"] if not s["confound"]]
    pos_test = r["dpt_te"] > 0
    if clean and pos_test:
        return "KEEP+GATE"          # profitable & has a clean win/loss separator
    if clean and not pos_test:
        return "GATE-RESCUE"        # bleeding but a clean separator could rescue it
    if not r["separators"] and not pos_test and r["dpt_tr"] <= 0:
        return "RETIRE"             # bleeding both windows, no separator at all
    if not clean and r["separators"]:
        return "CONFOUND-ONLY"      # only regime/identity 'separators' -> not tradeable
    return "WEAK"


def main():
    global MIN_TOK, EFFECT_MIN
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", default=",".join(DEFAULT_FILES))
    ap.add_argument("--train", default=f"{DEFAULT_TRAIN[0]}:{DEFAULT_TRAIN[1]}")
    ap.add_argument("--test", default=f"{DEFAULT_TEST[0]}:{DEFAULT_TEST[1]}")
    ap.add_argument("--min-tokens", type=int, default=MIN_TOK)
    ap.add_argument("--effect", type=float, default=EFFECT_MIN)
    ap.add_argument("--out", default=".win_loss_diff_report.txt")
    ap.add_argument("--top", type=int, default=8, help="separators to print per trigger")
    args = ap.parse_args()

    MIN_TOK = args.min_tokens
    EFFECT_MIN = args.effect
    train = tuple(args.train.split(":"))
    test = tuple(args.test.split(":"))
    files = [f.strip() for f in args.files.split(",") if f.strip()]

    print(f"loading {len(files)} files ...")
    comp = load_completed(files)
    print(f"completed positions (deduped): {len(comp)}  span "
          f"{min(c['t'][:10] for c in comp)}..{max(c['t'][:10] for c in comp)}")

    counts = Counter()
    for c in comp:
        for t in c["trig"]:
            counts[t] += 1

    results = {}
    for trig in counts:
        sel = [c for c in comp if trig in c["trig"]]
        r = analyze_trigger(sel, train, test)
        if r:
            r["verdict"] = verdict(r)
            results[trig] = r

    lines = []
    def emit(s=""):
        lines.append(s)
        print(s)

    emit("=" * 100)
    emit(f"PER-TRIGGER WIN-vs-LOSS DIFFERENTIAL  | TRAIN {train[0]}..{train[1]}  TEST {test[0]}..{test[1]}")
    emit(f"token-deduped | separator: |eff|>={EFFECT_MIN} same-sign both windows, >={MIN_TOK} tok/side")
    emit("=" * 100)

    # Summary table sorted by test $/tr
    emit("")
    emit("SUMMARY (sorted by TEST $/tr)")
    emit(f"{'trigger':30}{'stat':9}{'tokTR':>6}{'tokTE':>6}{'wrTR':>6}{'wrTE':>6}{'$/trTR':>8}{'$/trTE':>8}  {'verdict':13} sep")
    for trig, r in sorted(results.items(), key=lambda kv: (kv[1]['dpt_te'] if kv[1]['dpt_te']==kv[1]['dpt_te'] else -999)):
        stat = "RETIRED" if trig in RETIRED else ("DEMOTED" if trig in DEMOTED else ("shadow" if trig in LOSER_SHADOW else "live"))
        nclean = sum(1 for s in r["separators"] if not s["confound"])
        emit(f"{trig:30}{stat:9}{r['tok_tr']:>6}{r['tok_te']:>6}{r['wr_tok_tr']:>6.0f}{r['wr_tok_te']:>6.0f}"
             f"{r['dpt_tr']:>8.2f}{r['dpt_te']:>8.2f}  {r['verdict']:13} {nclean}clean/{len(r['separators'])}")

    # Full per-trigger separator detail
    emit("")
    emit("=" * 100)
    emit("DETAIL — separators per trigger (CLEAN first, then confounded)")
    emit("=" * 100)
    for trig, r in sorted(results.items(), key=lambda kv: kv[0]):
        stat = "RETIRED" if trig in RETIRED else ("DEMOTED" if trig in DEMOTED else ("SHADOW-loser" if trig in LOSER_SHADOW else "live"))
        emit("")
        emit(f"### {trig}  [{stat}]  verdict={r['verdict']}")
        emit(f"    TRAIN: {r['tok_tr']} tok ({r['win_tok_tr']}W/{r['loss_tok_tr']}L) tokWR={r['wr_tok_tr']:.0f}% $/tr={r['dpt_tr']:+.2f} (n={r['n_tr']})")
        emit(f"    TEST : {r['tok_te']} tok ({r['win_tok_te']}W/{r['loss_tok_te']}L) tokWR={r['wr_tok_te']:.0f}% $/tr={r['dpt_te']:+.2f} (n={r['n_te']})")
        clean = [s for s in r["separators"] if not s["confound"]]
        conf = [s for s in r["separators"] if s["confound"]]
        if not r["separators"]:
            emit("    NO SEPARATOR — winners and losers indistinguishable on all features (both windows).")
        for s in clean[:args.top]:
            emit(f"      + {s['feat']:30} eff tr={s['eff_tr']:+.2f} te={s['eff_te']:+.2f} | "
                 f"win(tr/te)={s['win_tr']:.3g}/{s['win_te']:.3g}  loss(tr/te)={s['loss_tr']:.3g}/{s['loss_te']:.3g}")
        if conf:
            emit(f"      [confounded, ignore: {', '.join(s['feat']+'('+s['confound'][:3]+')' for s in conf[:6])}]")

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    emit("")
    emit(f"[full report written to {args.out}]")


if __name__ == "__main__":
    main()
