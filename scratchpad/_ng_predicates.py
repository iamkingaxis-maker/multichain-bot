"""Candidate entry-gate predicates: recall / winner-kill / net-pp, half-split honesty."""
import json
from datetime import datetime

E = json.load(open("scratchpad/_ng_dataset.json"))
for e in E:
    e["feat"] = e.get("feat") or {}
    e["tape"] = e["feat"].get("rt_buys_n") is not None
    e["day"] = e["sell_time"][:10]
    e["half"] = 1 if e["day"] in ("2026-07-02", "2026-07-03") else 2

def v(e, f):
    x = e["feat"].get(f)
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else (
        (1.0 if x else 0.0) if isinstance(x, bool) else None)

# ---- predicates (fail-open: fire only when required features present) ----
def p_whale(t):
    def f(e):
        w = v(e, "whale_max_buy_usd")
        return None if w is None else (w < t)
    f.__name__ = f"whale_max<{t}"
    return f

def p_whale_burst(t, b):
    def f(e):
        w = v(e, "whale_max_buy_usd")
        bb = v(e, "buy_burst_30s_count") if e["tape"] else None
        if w is None or bb is None: return None
        return w < t and bb <= b
    f.__name__ = f"whale<{t}&burst30<= {b}"
    return f

def p_whale_decay(t, d):
    def f(e):
        w = v(e, "whale_max_buy_usd"); dd = v(e, "1s_vol_decay_120s")
        if w is None or dd is None: return None
        return w < t and dd >= d
    f.__name__ = f"whale<{t}&voldecay>={d}"
    return f

def p_decay(d):
    def f(e):
        dd = v(e, "1s_vol_decay_120s")
        return None if dd is None else dd >= d
    f.__name__ = f"voldecay>={d}"
    return f

def p_red60(n):
    def f(e):
        r = v(e, "1s_red_count_60s")
        return None if r is None else r >= n
    f.__name__ = f"1s_red60>={n}"
    return f

def p_notape(e):
    return not e["tape"]
p_notape.__name__ = "no_tape"

def p_whale_or_notape(t):
    def f(e):
        if not e["tape"] and e["feat"].get("whale_max_buy_usd") is None:
            return True
        w = v(e, "whale_max_buy_usd")
        return None if w is None else (w < t)
    f.__name__ = f"whale<{t}|no_data"
    return f

def p_young_red(n, b):
    def f(e):
        r = v(e, "1s_red_count_60s"); bb = v(e, "buy_burst_30s_count") if e["tape"] else None
        if r is None: return None
        if bb is None: return r >= n
        return r >= n and bb <= b
    f.__name__ = f"red60>={n}&burst<={b}"
    return f

def evaluate(pred, subset, label=""):
    ng_b = ng_t = bn_b = bn_t = ot_b = ot_t = 0
    pp_ng = pp_b = pp_o = 0.0
    n_na = 0
    for e in subset:
        r = pred(e)
        if e["label"] == "NEVER_GREEN": ng_t += 1
        elif e["label"] == "BOUNCED": bn_t += 1
        else: ot_t += 1
        if r is None:
            n_na += 1; continue
        if r:
            if e["label"] == "NEVER_GREEN": ng_b += 1; pp_ng += e["pnl_pct"]
            elif e["label"] == "BOUNCED": bn_b += 1; pp_b += e["pnl_pct"]
            else: ot_b += 1; pp_o += e["pnl_pct"]
    net_pp = -(pp_ng + pp_b + pp_o)
    return {"ng_recall": ng_b / max(1, ng_t), "ng_b": ng_b, "ng_t": ng_t,
            "b_kill": bn_b / max(1, bn_t), "b_b": bn_b, "b_t": bn_t,
            "o_b": ot_b, "na": n_na, "net_pp": net_pp,
            "pp_ng": pp_ng, "pp_b": pp_b, "pp_o": pp_o}

PREDS = ([p_whale(t) for t in (100, 150, 200, 250, 300)] +
         [p_whale_burst(250, 1), p_whale_burst(300, 1), p_whale_burst(300, 2)] +
         [p_whale_decay(250, 0.9), p_whale_decay(300, 0.9), p_whale_decay(300, 0.8)] +
         [p_decay(0.9), p_decay(1.0), p_decay(1.1)] +
         [p_red60(5), p_red60(6), p_red60(8)] +
         [p_young_red(5, 1), p_young_red(5, 2)] +
         [p_notape] +
         [p_whale_or_notape(200), p_whale_or_notape(250)])

BOTS = ["badday_flush", "badday_young_absorb"]
print(f"{'predicate':26s} {'scope':8s} {'NGrec':>6} {'Bkill':>6} {'netpp':>7} {'ppNG':>7} {'ppB':>6} {'ppO':>6} {'na':>3}  h1net h2net h1dir h2dir")
report = []
for pred in PREDS:
    for scope in ["POOLED"] + BOTS:
        sub = [e for e in E if scope == "POOLED" or e["bot"] == scope]
        r = evaluate(pred, sub)
        # half-split: net pp and direction (NG block-rate > B block-rate)
        halves = []
        for h in (1, 2):
            rh = evaluate(pred, [e for e in sub if e["half"] == h])
            halves.append(rh)
        h1, h2 = halves
        dir1 = h1["ng_recall"] > h1["b_kill"]
        dir2 = h2["ng_recall"] > h2["b_kill"]
        tag = scope[:8]
        print(f"{pred.__name__:26s} {tag:8s} {r['ng_recall']:5.0%} {r['b_kill']:5.0%} "
              f"{r['net_pp']:7.1f} {-r['pp_ng']:7.1f} {r['pp_b']:6.1f} {r['pp_o']:6.1f} {r['na']:3d}  "
              f"{h1['net_pp']:5.0f} {h2['net_pp']:5.0f}  {'Y' if dir1 else 'n'}    {'Y' if dir2 else 'n'}")
        report.append({"pred": pred.__name__, "scope": scope, **r,
                       "h1_net": h1["net_pp"], "h2_net": h2["net_pp"],
                       "h1_dir": dir1, "h2_dir": dir2,
                       "h1": h1, "h2": h2})
    print()

json.dump(report, open("scratchpad/_ng_predicate_report.json", "w"), indent=1)

# independence check: whale_max vs existing gates
print("--- independence: whale_max among entries passing existing crowd gates ---")
def auc(neg, pos):
    if not neg or not pos: return None
    w = t = 0
    for p in pos:
        for q in neg:
            if p > q: w += 1
            elif p == q: t += 1
    return (w + 0.5 * t) / (len(pos) * len(neg))
sub = [e for e in E if e["label"] in ("NEVER_GREEN", "BOUNCED")
       and isinstance(e["feat"].get("median_buy_size_usd"), (int, float))
       and e["feat"]["median_buy_size_usd"] >= 8
       and isinstance(e["feat"].get("whale_max_buy_usd"), (int, float))]
ng = [e["feat"]["whale_max_buy_usd"] for e in sub if e["label"] == "NEVER_GREEN"]
b = [e["feat"]["whale_max_buy_usd"] for e in sub if e["label"] == "BOUNCED"]
print(f"medbuy>=8 subset n={len(sub)} (NG {len(ng)} / B {len(b)}): whale_max AUC={auc(b, ng):.2f}")
sub2 = [e for e in E if e["label"] in ("NEVER_GREEN", "BOUNCED")
        and isinstance(e["feat"].get("unique_buyers_n"), (int, float))
        and e["feat"]["unique_buyers_n"] >= 20
        and isinstance(e["feat"].get("whale_max_buy_usd"), (int, float))]
ng2 = [e["feat"]["whale_max_buy_usd"] for e in sub2 if e["label"] == "NEVER_GREEN"]
b2 = [e["feat"]["whale_max_buy_usd"] for e in sub2 if e["label"] == "BOUNCED"]
print(f"buyers>=20 subset n={len(sub2)} (NG {len(ng2)} / B {len(b2)}): whale_max AUC={auc(b2, ng2):.2f}")
# spearman-ish rank corr whale_max vs median_buy
import statistics
pairs = [(e["feat"]["whale_max_buy_usd"], e["feat"]["median_buy_size_usd"]) for e in E
         if isinstance(e["feat"].get("whale_max_buy_usd"), (int, float))
         and isinstance(e["feat"].get("median_buy_size_usd"), (int, float))]
def rank(xs):
    s = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0] * len(xs)
    for i, j in enumerate(s): r[j] = i
    return r
if len(pairs) > 10:
    ra = rank([p[0] for p in pairs]); rb = rank([p[1] for p in pairs])
    print(f"rank-corr(whale_max, median_buy) n={len(pairs)}: "
          f"{statistics.correlation(ra, rb):.2f}")
