"""Union all /api/trades caches -> closed-position rows with age band + UTC hour.

Output: scratchpad/sol_young_regime/positions.jsonl
One row per closed position (buy joined to its sells) with:
entry_time, utc_hour, day, bot_id, token, address, age_h, pnl_pct_w (sell-fraction
weighted), pnl_usd, amount_usd, liq, sol_pc_h1, sol_pc_h6, bs_h1, peak_max.
"""
import json, os, sys
from collections import defaultdict

ROOT = r"C:\Users\jcole\multichain-bot"
OUT = os.path.join(ROOT, "scratchpad", "sol_young_regime", "positions.jsonl")

CACHES = [
    "trades_dump_candidates.json", ".t_off.json", ".hist_trades_0.json",
    ".watch7h/val_wide.json", ".mining_overnight_0528/trades_full.json",
    "_all.json", "_all2.json", "_adv_tr.json", "_cpf_trades.json",
    "_cdv3_trades.json", "_nf_trades.json", "_cd_full.json",
    "_full_trades.json", "_fro.json", "_balloon.json", "_hl_trades.json",
    "scratchpad/_tcond_trades.json", "scratchpad/_trades_full.json",
    "scratchpad/_vf_trades.json", "scratchpad/_ev_trades.json",
    "scratchpad/_trades_now.json", "scratchpad/_trades_full_2026_07_06.json",
    "scratchpad/ripday/_fleet_trades_0704.json", "scratchpad/_full_trades.json",
    "scratchpad/rug_cohort_v2/_trades_today.json",
]

def main():
    seen_buy, seen_sell = {}, {}
    for rel in CACHES:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            print("MISSING", rel); continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:
            print("ERR", rel, str(e)[:80]); continue
        if isinstance(d, dict):
            d = d.get("trades", [])
        nb = ns = 0
        for t in d:
            if not isinstance(t, dict) or not t.get("time"):
                continue
            ty = t.get("type")
            key = (t.get("bot_id"), t.get("address"), t.get("time"))
            if ty == "buy":
                if key not in seen_buy:
                    em = t.get("entry_meta") or {}
                    seen_buy[key] = {
                        "bot_id": t.get("bot_id"), "address": t.get("address"),
                        "token": t.get("token"), "time": t["time"],
                        "entry_price": t.get("entry_price"),
                        "amount_usd": t.get("amount_usd"),
                        "age_h": em.get("lifecycle_age_hours"),
                        "age_h2": em.get("hours_since_graduation"),
                        "liq": em.get("liquidity_usd"),
                        "sol_pc_h1": em.get("sol_pc_h1"),
                        "sol_pc_h6": em.get("sol_pc_h6"),
                        "bs_h1": em.get("bs_h1"),
                    }
                    nb += 1
            elif ty == "sell":
                if key + (t.get("entry_price"),) not in seen_sell:
                    seen_sell[key + (t.get("entry_price"),)] = {
                        "bot_id": t.get("bot_id"), "address": t.get("address"),
                        "time": t["time"], "entry_price": t.get("entry_price"),
                        "pnl": t.get("pnl"), "pnl_pct": t.get("pnl_pct"),
                        "sell_fraction": t.get("sell_fraction"),
                        "fully_closed": t.get("fully_closed"),
                        "hold_secs": t.get("hold_secs"),
                        "peak": t.get("peak_pnl_pct"),
                    }
                    ns += 1
        print(f"{rel:55s} +buys {nb:5d} +sells {ns:5d}")

    buys = sorted(seen_buy.values(), key=lambda b: b["time"])
    sells = sorted(seen_sell.values(), key=lambda s: s["time"])
    print("union buys", len(buys), "sells", len(sells))

    # index buys by (bot_id, address, round(entry_price,12)); fallback (bot_id, address)
    bidx = defaultdict(list)
    for b in buys:
        bidx[(b["bot_id"], b["address"])].append(b)

    positions = defaultdict(lambda: {"sells": []})
    unmatched = 0
    for s in sells:
        cand = bidx.get((s["bot_id"], s["address"]))
        if not cand:
            unmatched += 1; continue
        # nearest prior buy; prefer entry_price match
        prior = [b for b in cand if b["time"] <= s["time"]]
        pool = prior or cand
        ep = s.get("entry_price")
        exact = [b for b in pool if ep is not None and b.get("entry_price") is not None
                 and abs(b["entry_price"] - ep) <= 1e-15 + 1e-9 * abs(ep)]
        b = (exact or pool)[-1]
        pid = (b["bot_id"], b["address"], b["time"])
        positions[pid]["buy"] = b
        positions[pid]["sells"].append(s)

    out, skipped = [], 0
    for pid, pos in positions.items():
        b = pos.get("buy")
        if not b:
            skipped += 1; continue
        ss = pos["sells"]
        # weighted pnl_pct by sell_fraction (default 1.0); fallback simple mean
        tw = 0.0; wp = 0.0; usd = 0.0
        for s in ss:
            f = s.get("sell_fraction")
            f = float(f) if f is not None else 1.0
            if s.get("pnl_pct") is not None:
                wp += f * float(s["pnl_pct"]); tw += f
            if s.get("pnl") is not None:
                usd += float(s["pnl"])
        if tw <= 0:
            skipped += 1; continue
        pnl_pct_w = wp / max(tw, 1.0) if tw > 1.0 else wp / tw * min(tw, 1.0)
        # normalize: if fractions sum <1 (partial data) use weighted mean
        pnl_pct_w = wp / tw
        # but full position return = sum(f*pnl) when fractions sum to ~1
        if 0.9 <= tw <= 1.1:
            pnl_pct_w = wp
        fully = any(s.get("fully_closed") for s in ss)
        last = max(ss, key=lambda s: s["time"])
        out.append({
            "entry_time": b["time"], "utc_hour": int(b["time"][11:13]),
            "day": b["time"][:10], "bot_id": b["bot_id"], "token": b["token"],
            "address": b["address"], "age_h": b.get("age_h"),
            "age_h2": b.get("age_h2"), "liq": b.get("liq"),
            "sol_pc_h1": b.get("sol_pc_h1"), "sol_pc_h6": b.get("sol_pc_h6"),
            "bs_h1": b.get("bs_h1"), "amount_usd": b.get("amount_usd"),
            "pnl_pct": round(pnl_pct_w, 4), "pnl_usd": round(usd, 4),
            "n_sells": len(ss), "fully_closed": fully,
            "frac_sum": round(tw, 3),
            "hold_secs": last.get("hold_secs"),
            "peak": max((s.get("peak") or 0.0) for s in ss),
        })
    out.sort(key=lambda r: r["entry_time"])
    with open(OUT, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    print("positions", len(out), "unmatched sells", unmatched, "skipped", skipped)
    days = sorted({r["day"] for r in out})
    print("days", days[0], "->", days[-1], f"({len(days)} days)")
    n_age = sum(1 for r in out if r["age_h"] is not None)
    print(f"age_h present: {n_age}/{len(out)}")

if __name__ == "__main__":
    main()
