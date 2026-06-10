"""Bleed-week decomposition: are bleed weeks ENTRY-decision failures or SIZE failures?

Joins sells->buys, gate-audits every entry against the validated stack:
  A dip:  shape_90m_drawdown_from_max_pct <= -16   (deep-dip gate)
  B age:  entry_age_hours >= 24                    (goodpond age)
  C mcap: 500k <= entry_market_cap_usd <= 10M      (goodpond band)
  D flow: net_flow_60s_usd >= 100                  (buy-side flow)

Outputs per day-class (green/bleed): $ loss share from gate-violating entries,
counterfactual P&L keeping only gate-passing entries (actual size), and
per-token concentration on bleed days.
"""
import json, collections
from datetime import datetime, timedelta

d = json.load(open("_bleed_trades.json"))
trades = d if isinstance(d, list) else d.get("trades", [])

def ct_day(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
    except Exception:
        return None

# ---- join sells to their buys (pair+bot, last buy before sell) ----
buys_by_key = collections.defaultdict(list)
for t in trades:
    if t.get("type") == "buy":
        key = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
        buys_by_key[key].append(t)
for k in buys_by_key:
    buys_by_key[k].sort(key=lambda b: b.get("time", ""))

closed = []
for t in trades:
    if t.get("type") != "sell":
        continue
    r = (t.get("reason") or "").lower()
    if "cancelled on restart" in r:
        continue
    key = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    cands = [b for b in buys_by_key.get(key, []) if b.get("time", "") < t.get("time", "")]
    b = cands[-1] if cands else None
    closed.append((t, b))

print(f"closed sells: {len(closed)} | joined to a buy: {sum(1 for _, b in closed if b)}")

# ---- gate evaluation ----
def gates(b):
    """Returns (n_evaluable, n_passed, detail) for the 4 validated gates."""
    if not b:
        return 0, 0, {}
    em = b.get("entry_meta") or {}
    out = {}
    v = em.get("shape_90m_drawdown_from_max_pct")
    if v is not None:
        try: out["dip"] = float(v) <= -16.0
        except Exception: pass
    v = b.get("entry_age_hours", em.get("lifecycle_age_hours"))
    if v is not None:
        try: out["age"] = float(v) >= 24.0
        except Exception: pass
    v = b.get("entry_market_cap_usd")
    if v is not None:
        try: out["mcap"] = 500_000 <= float(v) <= 10_000_000
        except Exception: pass
    v = em.get("net_flow_60s_usd")
    if v is not None:
        try: out["flow"] = float(v) >= 100.0
        except Exception: pass
    return len(out), sum(out.values()), out

# sanity: sign convention of drawdown field
vals = []
for t, b in closed[:4000]:
    if b:
        v = (b.get("entry_meta") or {}).get("shape_90m_drawdown_from_max_pct")
        if v is not None:
            try: vals.append(float(v))
            except Exception: pass
if vals:
    import statistics
    print(f"drawdown_90m sign check: min={min(vals):.1f} med={statistics.median(vals):.1f} max={max(vals):.1f} (expect <=0)")

# ---- per-day aggregates with gate split ----
daystats = collections.defaultdict(lambda: {
    "n": 0, "usd": 0.0, "eq": 0.0, "w": 0,
    "viol_usd": 0.0, "pass_usd": 0.0, "viol_n": 0, "pass_n": 0,
    "loss_usd": 0.0, "viol_loss_usd": 0.0,
    "uneval_usd": 0.0, "uneval_n": 0,
})
tokday_loss = collections.defaultdict(float)   # (day, token) -> $ loss

for t, b in closed:
    day = ct_day(t.get("time"))
    if not day:
        continue
    pnl = float(t.get("pnl") or 0); pct = float(t.get("pnl_pct") or 0)
    s = daystats[day]
    s["n"] += 1; s["usd"] += pnl; s["eq"] += pct
    if pct > 0: s["w"] += 1
    if pnl < 0:
        s["loss_usd"] += pnl
        tok = (t.get("token") or t.get("symbol") or (t.get("pair_address") or "")[:10])
        tokday_loss[(day, tok)] += pnl
    n_eval, n_pass, det = gates(b)
    if n_eval == 0:
        s["uneval_usd"] += pnl; s["uneval_n"] += 1
        continue
    # "gate-passing" = passes ALL evaluable validated gates
    if n_pass == n_eval:
        s["pass_usd"] += pnl; s["pass_n"] += 1
    else:
        s["viol_usd"] += pnl; s["viol_n"] += 1
        if pnl < 0:
            s["viol_loss_usd"] += pnl

# ---- classify days: bleed = fleet < -$400; green = fleet > +$100 ----
print(f"\n{'day':12s}{'n':>6s}{'fleet$':>9s}{'eqw%':>7s}{'WR':>5s} | {'PASS$':>8s}{'(n)':>6s} {'VIOL$':>9s}{'(n)':>6s} {'viol%ofLoss':>12s}")
bleed_days, green_days = [], []
for day in sorted(daystats):
    s = daystats[day]
    if s["n"] < 30:
        continue
    cls = "BLEED" if s["usd"] < -400 else ("GREEN" if s["usd"] > 100 else "")
    if cls == "BLEED": bleed_days.append(day)
    if cls == "GREEN": green_days.append(day)
    vshare = (s["viol_loss_usd"] / s["loss_usd"] * 100) if s["loss_usd"] < 0 else 0
    print(f"  {day} {s['n']:5d} {s['usd']:+9.0f} {s['eq']/max(s['n'],1):+6.2f} {s['w']/max(s['n'],1)*100:4.0f}% | "
          f"{s['pass_usd']:+8.0f} {s['pass_n']:5d} {s['viol_usd']:+9.0f} {s['viol_n']:5d} {vshare:11.0f}%  {cls}")

# ---- the verdict numbers ----
def agg(days):
    a = collections.Counter()
    for day in days:
        s = daystats[day]
        for k in ("usd", "pass_usd", "viol_usd", "loss_usd", "viol_loss_usd", "pass_n", "viol_n", "n", "uneval_usd"):
            a[k] += s[k]
    return a

for label, dayset in (("GREEN days", green_days), ("BLEED days", bleed_days)):
    a = agg(dayset)
    print(f"\n=== {label} ({len(dayset)}) ===")
    print(f"  fleet:        {a['usd']:+10.0f}  (n={a['n']})")
    print(f"  gate-PASSING: {a['pass_usd']:+10.0f}  (n={a['pass_n']})")
    print(f"  gate-VIOLATING:{a['viol_usd']:+9.0f}  (n={a['viol_n']})")
    print(f"  unevaluable:  {a['uneval_usd']:+10.0f}")
    if a["loss_usd"] < 0:
        print(f"  share of $ LOSS from violators: {a['viol_loss_usd']/a['loss_usd']*100:.0f}%")

# counterfactual: bleed days keeping ONLY gate-passing entries
a = agg(bleed_days)
print(f"\nCOUNTERFACTUAL bleed days, only gate-passing entries: {a['pass_usd']:+.0f} vs actual {a['usd']:+.0f}")
print(f"  -> entry discipline alone would have changed bleed-day P&L by {a['pass_usd']-a['usd']:+.0f}")

# ---- death clusters on bleed days ----
print("\nTop token death-clusters on bleed days (entry-correlation):")
worst = sorted(((dl, day, tok) for (day, tok), dl in tokday_loss.items() if day in bleed_days))[:12]
for dl, day, tok in worst:
    print(f"  {day}  {str(tok)[:14]:14s} {dl:+9.0f}")
