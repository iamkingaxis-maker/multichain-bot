"""Which gates/filters are REDUNDANT now that the entry stack is enforced?

A filter earns its keep only if it adds discrimination ON TOP of the stack.
Method: among closed trades whose entries PASS the validated entry stack
(dip<=-16, flow>=100, age>=24h, mcap in band), compare P&L of trades the
filter would have BLOCKED vs PASSED (verdicts are recorded even when not
enforced). A filter whose BLOCK no longer separates losers within
stack-passers is dead weight. Also: filters that almost never fire within
passers are inert.
"""
import json, collections, statistics, sys
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

# join closed sells to buys; keep only stack-passers
rows = []   # (pnl, entry_meta)
n_join = 0
for t in trades:
    if t.get("type") != "sell": continue
    r = (t.get("reason") or "").lower()
    if "cancelled on restart" in r: continue
    k = ((t.get("pair_address") or t.get("address") or "").lower(), t.get("bot_id") or "")
    cands = [b for b in buys_by_key.get(k, []) if b.get("time", "") < t.get("time", "")]
    if not cands: continue
    b = cands[-1]; n_join += 1
    if not stack_pass(b): continue
    rows.append((float(t.get("pnl") or 0), b.get("entry_meta") or {}))

print(f"joined sells: {n_join} | STACK-PASSING closed trades: {len(rows)}\n")

# evaluate every recorded filter verdict within stack-passers
filters = collections.defaultdict(lambda: {"blk": [], "ok": []})
for pnl, em in rows:
    for key, val in em.items():
        if not key.endswith("_verdict"): continue
        nm = key[:-8]
        v = str(val).upper()
        if v == "BLOCK":
            filters[nm]["blk"].append(pnl)
        elif v in ("PASS", "OK", "ALLOW"):
            filters[nm]["ok"].append(pnl)

print(f"{'filter':38s}{'n_blk':>6s}{'n_ok':>7s}{'blk$/tr':>9s}{'ok$/tr':>8s}{'verdict':>22s}")
print("-" * 92)
out = []
for nm, v in sorted(filters.items()):
    nb, no = len(v["blk"]), len(v["ok"])
    if nb + no < 50: continue
    mb = statistics.mean(v["blk"]) if nb else None
    mo = statistics.mean(v["ok"]) if no else None
    if nb < 10:
        verdict = "INERT in stack (cut?)"
    elif mb is not None and mo is not None:
        # additive if its blocks lose meaningfully more than its passes
        if mb < mo - 0.25 and mb < 0:
            verdict = "ADDITIVE (keep)"
        elif mb > mo + 0.25:
            verdict = "HARMFUL (blocks winners)"
        else:
            verdict = "redundant (cut?)"
    else:
        verdict = "?"
    out.append((nm, nb, no, mb, mo, verdict))
    print(f"  {nm:36s}{nb:6d}{no:7d}"
          f"{(f'{mb:+9.2f}' if mb is not None else '      n/a')}"
          f"{(f'{mo:+8.2f}' if mo is not None else '     n/a')}  {verdict:>20s}")

cuts = [o for o in out if "cut?" in o[5]]
keeps = [o for o in out if "keep" in o[5]]
harm = [o for o in out if "HARMFUL" in o[5]]
print(f"\nsummary within stack-passers: {len(keeps)} additive | {len(cuts)} inert/redundant | {len(harm)} harmful")
