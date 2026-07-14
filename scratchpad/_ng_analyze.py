"""NG vs BOUNCED separation analysis on decision-time features."""
import json
from collections import Counter

E = json.load(open("scratchpad/_ng_dataset.json"))
for e in E:
    e["feat"] = e.get("feat") or {}

# tape-known flag: rt features valid only when tape existed
for e in E:
    e["tape"] = e["feat"].get("rt_buys_n") is not None

FEATURES = [
    # sell-side
    "rt_sells_n", "rt_sells_usd", "rt_max_sell_usd", "rt_avg_sell_usd",
    "sell_burst_30s_count", "sell_volume_decay_ratio_30s", "sells_per_min_recent",
    # buy-side
    "rt_buys_n", "rt_buys_usd", "rt_max_buy_usd", "rt_avg_buy_usd",
    "buy_burst_30s_count", "buys_per_min_recent",
    "buy_size_max_last60s", "buy_size_max_trend", "buy_size_mean_trend",
    "buy_size_n_last60s", "buy_size_n_prior60s",
    "median_buy_size_usd", "p90_buy_size_usd", "whale_max_buy_usd",
    # ratios / flow
    "rt_dollar_imbalance", "buy_sell_volume_imbalance", "largest_buy_to_largest_sell",
    "net_flow_15s_usd", "net_flow_60s_usd", "net_flow_5m_usd",
    "net_flow_60s_imbalance", "net_flow_5m_imbalance", "buy_pressure_60s",
    "rt_consec_buys", "rt_consec_sells", "n_consecutive_buys_at_end",
    "trades_per_sec_last60s", "trades_per_sec_prior60s", "trade_density_30s_vs_5m",
    "rt_trades_per_sec",
    # makers
    "unique_buyers_n", "unique_buyer_ratio", "top5_buyer_volume_pct",
    "top10_buyer_within_60s_count", "top10_buyer_time_spread_sec", "top_buy_makers_n",
    "n_recurring_buyers_3plus",
    # 1m bars
    "1m_consec_red", "1m_max_drop", "1m_cum_5m_pct", "1m_cum_3min_pct",
    "1m_last_close_pct", "1m_volume_spike", "1m_red_count_5", "1m_green_in_last3",
    "1m_close_in_range",
    # 1s bars
    "1s_lower_wick_ratio_last", "1s_red_count_60s", "1s_red_pct_60s",
    "1s_close_pos_60s", "1s_vol_decay_120s", "1s_cascade_length", "1s_green_run_end",
    # context (already-gated axes, for comparison)
    "pc_h6", "pc_h1", "liquidity_usd", "avg_trade_size_h1_usd",
]
# features whose 0-default is fabricated when tape missing -> require tape
TAPE_DEP = {"buy_burst_30s_count", "sell_burst_30s_count", "rt_consec_buys",
            "rt_consec_sells", "n_consecutive_buys_at_end", "buys_per_min_recent",
            "sells_per_min_recent"}

def val(e, f):
    v = e["feat"].get(f)
    if v is None:
        return None
    if f in TAPE_DEP and not e["tape"]:
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None

def median(xs):
    s = sorted(xs); n = len(s)
    if not n: return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

def auc(neg, pos):
    """P(pos > neg) rank AUC; pos=NEVER_GREEN, neg=BOUNCED."""
    if not neg or not pos: return None
    wins = ties = 0
    for p in pos:
        for q in neg:
            if p > q: wins += 1
            elif p == q: ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))

def rows(bot=None, labels=("NEVER_GREEN", "BOUNCED")):
    return [e for e in E if (bot is None or e["bot"] == bot) and e["label"] in labels]

results = {}
print(f"{'feature':34s} {'n_NG':>4} {'n_B':>4} {'med_NG':>10} {'med_B':>10} {'AUC':>5}  {'fAUC':>5} {'yAUC':>5}")
for f in FEATURES:
    ng = [val(e, f) for e in rows() if e["label"] == "NEVER_GREEN"]
    b = [val(e, f) for e in rows() if e["label"] == "BOUNCED"]
    ng = [x for x in ng if x is not None]; b = [x for x in b if x is not None]
    if len(ng) < 8 or len(b) < 8:
        continue
    a = auc(b, ng)
    pb = {}
    for bot, tag in [("badday_flush", "f"), ("badday_young_absorb", "y")]:
        ngb = [val(e, f) for e in rows(bot) if e["label"] == "NEVER_GREEN"]
        bb = [val(e, f) for e in rows(bot) if e["label"] == "BOUNCED"]
        ngb = [x for x in ngb if x is not None]; bb = [x for x in bb if x is not None]
        pb[tag] = auc(bb, ngb) if len(ngb) >= 5 and len(bb) >= 5 else None
    results[f] = {"n_ng": len(ng), "n_b": len(b), "med_ng": median(ng), "med_b": median(b),
                  "auc": a, "auc_flush": pb["f"], "auc_young": pb["y"]}
    fa = f"{pb['f']:.2f}" if pb['f'] is not None else "  na"
    ya = f"{pb['y']:.2f}" if pb['y'] is not None else "  na"
    print(f"{f:34s} {len(ng):4d} {len(b):4d} {median(ng):10.3f} {median(b):10.3f} {a:.2f}  {fa:>5} {ya:>5}")

# tape-missing as signal
print("\n--- tape availability vs label ---")
for bot in [None, "badday_flush", "badday_young_absorb"]:
    r = rows(bot, ("NEVER_GREEN", "BOUNCED", "OTHER"))
    c = Counter((e["tape"], e["label"]) for e in r)
    tag = bot or "POOLED"
    ng_no = c[(False, "NEVER_GREEN")]; b_no = c[(False, "BOUNCED")]; o_no = c[(False, "OTHER")]
    ng_yes = c[(True, "NEVER_GREEN")]; b_yes = c[(True, "BOUNCED")]; o_yes = c[(True, "OTHER")]
    print(f"{tag}: no-tape NG={ng_no} B={b_no} O={o_no} (NG rate {ng_no/max(1,ng_no+b_no+o_no):.0%}) | "
          f"tape NG={ng_yes} B={b_yes} O={o_yes} (NG rate {ng_yes/max(1,ng_yes+b_yes+o_yes):.0%})")
    # pp of no-tape NG entries
    pp_no = sum(e["pnl_pct"] for e in r if not e["tape"])
    pp_no_ng = sum(e["pnl_pct"] for e in r if not e["tape"] and e["label"] == "NEVER_GREEN")
    print(f"   no-tape total pp={pp_no:.0f} (NG part {pp_no_ng:.0f})")

json.dump(results, open("scratchpad/_ng_separation.json", "w"), indent=1)
print("\nwrote scratchpad/_ng_separation.json")
