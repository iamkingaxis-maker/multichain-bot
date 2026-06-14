"""GREEN-VALIDATION (AxiS 2026-06-14): does the red-winner signature (deep dip +
decent liq + mid mcap + modest vol) generalize beyond the red night, and what's
the GREEN-tape winning pattern? Re-uses the 305 already-decoded entries in
_red_winner_feats.json (no re-mining), tags each with the SOL macro-regime at its
entry ts (CoinGecko hourly SOL -> sol_pc_h24), and splits winner/loser by regime.

Strategic purpose (wallets-as-intelligence pivot): the GREEN winner signature
becomes the chameleon's green-mode mined pattern (vs the failing panel-copy).
"""
import sys, os, json, time, statistics, urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def sol_price_series():
    """[(ts_secs, price_usd)] hourly for SOL, newest data. Try CoinGecko, then GT."""
    try:
        d = _get_json("https://api.coingecko.com/api/v3/coins/solana/market_chart?vs_currency=usd&days=7&interval=hourly")
        pr = [(int(p[0] / 1000), float(p[1])) for p in d.get("prices", [])]
        if pr:
            return sorted(pr)
    except Exception as e:
        print(f"  coingecko failed: {e}", file=sys.stderr)
    # GT fallback: a major SOL/USDC pool hourly
    try:
        pool = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"  # Orca SOL/USDC
        d = _get_json(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/hour?aggregate=1&limit=200")
        rows = d.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        return sorted((int(r[0]), float(r[4])) for r in rows)
    except Exception as e:
        print(f"  GT fallback failed: {e}", file=sys.stderr)
        return []


def _price_at(series, ts):
    """nearest hourly price at/just-before ts."""
    lo, hi, best = 0, len(series) - 1, None
    for t, p in series:
        if t <= ts:
            best = p
        else:
            break
    return best


def med(v):
    return statistics.median(v) if v else float("nan")


def main():
    feats = json.load(open("_red_winner_feats.json"))
    print(f"loaded {len(feats)} decoded entries", file=sys.stderr)
    series = sol_price_series()
    if not series:
        print("no SOL series — cannot tag regime"); return
    print(f"SOL series: {len(series)} hourly pts "
          f"({datetime.fromtimestamp(series[0][0], timezone.utc):%m-%d %H:%M} -> "
          f"{datetime.fromtimestamp(series[-1][0], timezone.utc):%m-%d %H:%M})", file=sys.stderr)

    for f in feats:
        ts = f.get("ts")
        p_now = _price_at(series, ts)
        p_24 = _price_at(series, ts - 86400)
        f["sol_pc_h24"] = ((p_now / p_24 - 1) * 100) if (p_now and p_24) else None

    tagged = [f for f in feats if f.get("sol_pc_h24") is not None]
    print(f"\n{len(tagged)}/{len(feats)} entries SOL-regime-tagged "
          f"(rest predate the 7d SOL window)")

    # SOL-regime buckets (approx; true regime also uses breadth which isn't reconstructable)
    buckets = {
        "SOL-RED (h24<=-2)":   [f for f in tagged if f["sol_pc_h24"] <= -2],
        "SOL-FLAT (-2..0)":    [f for f in tagged if -2 < f["sol_pc_h24"] <= 0],
        "SOL-GREEN (h24>0)":   [f for f in tagged if f["sol_pc_h24"] > 0],
    }
    for name, fs in buckets.items():
        win = [f for f in fs if f["fwd_max"] >= 30]
        lose = [f for f in fs if f["fwd_max"] <= 0]
        print(f"\n=== {name}: {len(fs)} entries | {len(win)} winners / {len(lose)} losers ===")
        if not win or not lose:
            print("  (insufficient winners/losers to split)"); continue
        def row(nm, key, fmt="{:.1f}"):
            wv = [f[key] for f in win if f.get(key) is not None]
            lv = [f[key] for f in lose if f.get(key) is not None]
            print(f"  {nm:13s} WIN={fmt.format(med(wv))}  LOSE={fmt.format(med(lv))}")
        row("dip_90m %", "dip_90m")
        row("age_h", "age_h")
        row("vol_spike x", "vol_spike", "{:.1f}")
        row("liq $", "liq", "{:,.0f}")
        row("mcap $", "fdv", "{:,.0f}")

    # headline: does the mcap/liq floor hold in the non-red (FLAT+GREEN) slice?
    nonred = [f for f in tagged if f["sol_pc_h24"] > -2]
    nw = [f for f in nonred if f["fwd_max"] >= 30]
    nl = [f for f in nonred if f["fwd_max"] <= 0]
    print(f"\n=== NON-RED slice (SOL h24 > -2): {len(nonred)} entries | {len(nw)} win / {len(nl)} lose ===")
    if nw and nl:
        for nm, key, fmt in [("dip_90m %", "dip_90m", "{:+.0f}"), ("liq $", "liq", "{:,.0f}"),
                             ("mcap $", "fdv", "{:,.0f}"), ("vol_spike", "vol_spike", "{:.1f}")]:
            wv = [f[key] for f in nw if f.get(key) is not None]
            lv = [f[key] for f in nl if f.get(key) is not None]
            print(f"  {nm:12s} WIN={fmt.format(med(wv))}  LOSE={fmt.format(med(lv))}")
        print("\n  => mcap/liq floor GENERALIZES if WIN mcap/liq >> LOSE here too.")


if __name__ == "__main__":
    main()
