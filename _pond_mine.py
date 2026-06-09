"""Mine win/loss separators WITHIN the stack-passing pond (the only cohort that
matters now). Method per the proven playbook: rank numeric entry features by
win-vs-loss separation (Cohen's d), then validate the top features on a
TIME-SPLIT held-out half. Universe-wide mining is banned (conflates cohorts);
this is per-cohort by construction.
"""
import json, collections, statistics, math, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

d = json.load(open("_bleed_trades.json"))
trades = d if isinstance(d, list) else d.get("trades", [])

buys_by_key = collections.defaultdict(list)
for t in trades:
    if t.get("type") == "buy":
        k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        buys_by_key[k].append(t)
for k in buys_by_key:
    buys_by_key[k].sort(key=lambda b: b.get("time", ""))

def stack_pass(b):
    em = b.get("entry_meta") or {}
    v = em.get("shape_90m_drawdown_from_max_pct")
    if isinstance(v, (int, float)) and v > -16: return False
    v = em.get("net_flow_60s_usd")
    if isinstance(v, (int, float)) and v < 100: return False
    v = b.get("entry_age_hours")
    if isinstance(v, (int, float)) and 0 < v < 24: return False
    v = b.get("entry_market_cap_usd")
    if isinstance(v, (int, float)) and v > 0 and not (5e5 <= v <= 1e7): return False
    return True

rows = []  # (time, pnl_pct, pnl_usd, entry_meta, token)
for t in trades:
    if t.get("type") != "sell": continue
    r = (t.get("reason") or "").lower()
    if "cancelled on restart" in r: continue
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    cands = [b for b in buys_by_key.get(k, []) if b.get("time", "") < t.get("time", "")]
    if not cands: continue
    b = cands[-1]
    if not stack_pass(b): continue
    rows.append((t.get("time", ""), float(t.get("pnl_pct") or 0), float(t.get("pnl") or 0),
                 b.get("entry_meta") or {}, t.get("token") or ""))

rows.sort(key=lambda r: r[0])
half = len(rows) // 2
train, test = rows[:half], rows[half:]
print(f"stack-passing closed: {len(rows)} | train={len(train)} (to {train[-1][0][:10]}) "
      f"| test={len(test)} (from {test[0][0][:10]})")
wr_all = sum(1 for r in rows if r[1] > 0) / len(rows)
print(f"pond baseline: WR={wr_all*100:.0f}% | mean {statistics.mean(r[1] for r in rows):+.2f}%/tr "
      f"| ${statistics.mean(r[2] for r in rows):+.2f}/tr\n")

def cohens_d(rows_):
    """Rank numeric features by win-vs-loss separation on rows_."""
    feats = collections.defaultdict(lambda: {"w": [], "l": []})
    for _, pct, usd, em, _tok in rows_:
        side = "w" if pct > 0 else "l"
        for k, v in em.items():
            if isinstance(v, bool) or not isinstance(v, (int, float)): continue
            feats[k][side].append(float(v))
    out = []
    for k, v in feats.items():
        w, l = v["w"], v["l"]
        if len(w) < 80 or len(l) < 80: continue
        mw, ml = statistics.mean(w), statistics.mean(l)
        sw = statistics.pstdev(w) or 1e-9; sl = statistics.pstdev(l) or 1e-9
        sp = math.sqrt((sw * sw + sl * sl) / 2)
        if sp <= 0: continue
        out.append((abs(mw - ml) / sp, k, mw, ml))
    out.sort(reverse=True)
    return out

ranked = cohens_d(train)
print(f"TOP 25 separators on TRAIN (|d|, feature, win_mean, loss_mean):")
for dd, k, mw, ml in ranked[:25]:
    print(f"  d={dd:.3f}  {k:42s} W={mw:+.3g}  L={ml:+.3g}")

# held-out check: for top features, does a median-split threshold lift WR on TEST?
print(f"\nHELD-OUT validation (TEST half) — median-split WR lift:")
print(f"{'feature':42s} {'dir':>4s} {'trainWR+':>9s} {'testWR+':>8s} {'testN+':>7s} {'test$/tr+':>10s}  verdict")
base_wr_test = sum(1 for r in test if r[1] > 0) / len(test)
kept = []
for dd, k, mw, ml in ranked[:25]:
    tr_vals = [(em.get(k), pct, usd) for _, pct, usd, em, _ in train
               if isinstance(em.get(k), (int, float)) and not isinstance(em.get(k), bool)]
    if len(tr_vals) < 200: continue
    med = statistics.median(v for v, _, _ in tr_vals)
    direction = 1 if mw > ml else -1   # winners higher -> favor above-median
    def side_stats(data):
        sel = [(p, u) for v, p, u in data if (v > med if direction == 1 else v < med)]
        if len(sel) < 60: return None
        wr = sum(1 for p, _ in sel if p > 0) / len(sel)
        return wr, len(sel), statistics.mean(u for _, u in sel)
    tr_s = side_stats(tr_vals)
    te_vals = [(em.get(k), pct, usd) for _, pct, usd, em, _ in test
               if isinstance(em.get(k), (int, float)) and not isinstance(em.get(k), bool)]
    te_s = side_stats(te_vals)
    if not tr_s or not te_s: continue
    lift_tr = tr_s[0] - (sum(1 for r in train if r[1] > 0) / len(train))
    lift_te = te_s[0] - base_wr_test
    verdict = "HOLDS" if (lift_tr > 0.03 and lift_te > 0.03) else ("weak" if lift_te > 0 else "fails")
    if verdict == "HOLDS": kept.append((k, direction, med, lift_te, te_s))
    arrow = ">" if direction == 1 else "<"
    print(f"  {k:40s} {arrow}med {tr_s[0]*100:8.0f}% {te_s[0]*100:7.0f}% {te_s[1]:7d} {te_s[2]:+10.2f}  {verdict}")

print(f"\nbaseline test WR: {base_wr_test*100:.0f}%")
print(f"features that HOLD on held-out: {len(kept)}")
for k, direction, med, lift, te_s in kept:
    print(f"  {k} {'>' if direction==1 else '<'} {med:.4g}  -> test WR {te_s[0]*100:.0f}% (+{lift*100:.0f}pp), ${te_s[2]:+.2f}/tr, n={te_s[1]}")
