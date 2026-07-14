"""Paper twins on same token-window + selection features + rate + balance-truth."""
import json, collections, statistics as st
from datetime import datetime, timezone, timedelta

D = r"C:\Users\jcole\multichain-bot\scratchpad\live_fidelity"
trades = json.load(open(f"{D}/trades_full.json"))
trips = json.load(open(f"{D}/trips2.json"))
swaps = [r for r in json.load(open(f"{D}/live_swaps.json"))["recent"] if r["ts"] >= "2026-07-09"]

# ---- balance-truth per trip (eras C/D, single-position, ordered) ----
print("=== BALANCE-TRUTH PER TRIP (C/D) ===")
buys = [r for r in swaps if r["side"] == "buy" and r["ts"] >= "2026-07-11T23:50"]
seq = []
for i, b in enumerate(buys):
    seq.append(b)
sol_now = 2.114779
for i, b in enumerate(seq):
    nb = seq[i+1]["sol_before"] if i+1 < len(seq) else sol_now
    if b.get("sol_before") is None or nb is None: continue
    delta = nb - b["sol_before"]
    spent = b.get("size_sol") or 0
    print(f"{b['ts'][:19]} {b.get('bot_id')} {b.get('token_symbol')} ok={b.get('success')} "
          f"spent_sol={spent:.4f} delta_sol={delta:+.6f} ret={delta/spent*100 if spent else 0:+.2f}%")

# ---- paper twins ----
# live trip windows: (addr, buy_ts, sell_end). paper rows = sells with no live_signature
# from young-lane bots, same address, entry time within [buy_ts - 30min, buy_ts + 30min]
def pt(s): return datetime.fromisoformat(s)

paper_sells = [r for r in trades if r["type"] == "sell" and not r.get("live_signature")]
# SCRUB RULE (reference_spike_illusion_rebaseline_2026_07_01): drop ret>0 & hold<10s
n0 = len(paper_sells)
paper_sells = [r for r in paper_sells if not ((r["pnl_pct"] or 0) > 0 and (r["hold_secs"] or 0) < 10)]
print(f"\nSCRUB RULE dropped {n0 - len(paper_sells)} of {n0} paper sells (ret>0 & hold<10s)")
for r in paper_sells:
    r["_entry_ts"] = pt(r["time"]) - timedelta(seconds=r["hold_secs"] or 0)

YOUNG = lambda b: ("young" in b or "adolescent" in b)

print("\n=== PAPER TWINS per live trip (same token, paper entry within +/-30min of live entry) ===")
rows = []
for t in trips:
    bts = pt(t["buy_ts"] + "+00:00")
    twins = [r for r in paper_sells if r["address"] == t["addr"]
             and abs((r["_entry_ts"] - bts).total_seconds()) <= 1800
             and YOUNG(r["bot_id"])]
    # dedupe: one entry per bot per minute
    tw_pnls = [r["pnl_pct"] for r in twins]
    med = round(st.median(tw_pnls), 2) if tw_pnls else None
    rows.append((t, med, len(tw_pnls), sorted(collections.Counter(r['bot_id'] for r in twins).items())))
    print(f"{t['buy_ts']} {t['era']:9s} {t['bot']:22s} {str(t['sym']):12s} live_booked={t['booked_pnl_pct']}% "
          f"| paper twins n={len(tw_pnls)} med={med}%")
    for r in sorted(twins, key=lambda r: r["_entry_ts"]):
        print(f"    twin {r['bot_id']:28s} entry={r['_entry_ts'].isoformat()[11:19]} pnl={round(r['pnl_pct'],2)}% hold={round(r['hold_secs'])}s")

# era D gap summary
print("\n=== LIVE vs PAPER-TWIN gap by era (trips with >=1 twin) ===")
for era in sorted(set(t["era"] for t in trips)):
    g = [(t["booked_pnl_pct"] - m) for t, m, n, _ in rows if t["era"] == era and m is not None and t["booked_pnl_pct"] is not None]
    if g:
        print(f"{era:10s} n={len(g)} gap live-paper med={round(st.median(g),2)}pp mean={round(sum(g)/len(g),2)}pp")

# ---- selection: entry_meta join for live trips ----
print("\n=== SELECTION: live trip entry stamps ===")
paper_buys = [r for r in trades if r["type"] == "buy"]
feat_keys = None
sel = []
for t in trips:
    bts = pt(t["buy_ts"] + "+00:00")
    cands = [r for r in paper_buys if r["bot_id"] == t["bot"] and r["address"] == t["addr"]
             and abs((pt(r["time"]) - bts).total_seconds()) < 60]
    em = (cands[0].get("entry_meta") or {}) if cands else {}
    if feat_keys is None and em:
        feat_keys = [k for k in em if any(s in k for s in ("hidden", "top10", "holder", "liq", "hour", "age", "mcap"))]
        print("available keys sample:", sorted(em.keys())[:60])
    green = (t.get("sol_net_pct") or 0) > 0
    sel.append({"ts": t["buy_ts"], "bot": t["bot"], "sym": t["sym"], "era": t["era"],
                "green": green, "booked": t["booked_pnl_pct"], "entry_slip": t["entry_slip"],
                "hid": em.get("hidden_supply_pct"), "top10": em.get("top10_pct"),
                "liq": t.get("liq"), "hour": t["buy_ts"][11:13],
                "meta_found": bool(em)})
print()
for s in sel:
    print(f"{s['ts']} {s['era']:9s} {str(s['sym']):12s} green={s['green']} booked={s['booked']}% "
          f"slip={s['entry_slip']} hid={s['hid']} top10={s['top10']} liq={s['liq']} hr={s['hour']} meta={s['meta_found']}")

# ---- rate ----
print("\n=== RATE: closed live round trips per day ===")
byday = collections.Counter(t["buy_ts"][:10] for t in trips)
for d, n in sorted(byday.items()): print(d, n)
