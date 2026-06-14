"""DEEP MINE — red-window winning-wallet ENTRY patterns (AxiS 2026-06-14:
"find more winning entry patterns from last night's red time from winning
wallets. they ARE out there — dig deep").

Pipeline: dig_drawdown_winners.py already ranked the net-positive wallets among
last night's runners (TURTLE/BLX/BOB/ZUL/SOCCER...) -> _drawdown_winners_ranked.json.
This reconstructs EVERY recent buy of the top winners from GeckoTerminal minute
OHLC and SPLITS winning entries (forward bounce >= +30%) vs losing (<= 0%) to
surface what the red-tape winners' entries had in common — dip depth off the 90m
high, token age, a capitulation VOLUME-SPIKE proxy, liquidity, mcap, time-of-day.
Reuses mine_wallet_entries' proven on-chain collectors.

Usage: python scripts/_red_winner_entry_mine.py [topN=12] [sigs=80]
"""
import sys, os, json, time, statistics, collections
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from mine_wallet_entries import collect_buys, token_pool_ohlc  # noqa: E402


def _vol_spike_at(entry_ts, ohlcv):
    """entry-bar volume / median(prior 10 bars) — the capitulation vol-burst proxy
    (the deep-flush signature gates 1m_volume_spike >= 3)."""
    if not ohlcv:
        return None
    rows = sorted(ohlcv, key=lambda r: r[0])
    ei = None
    for i, r in enumerate(rows):
        if r[0] <= entry_ts:
            ei = i
        else:
            break
    if ei is None or ei < 3:
        return None
    try:
        entry_vol = float(rows[ei][5])
        prior = [float(r[5]) for r in rows[max(0, ei - 10):ei] if len(r) > 5 and float(r[5]) > 0]
    except Exception:
        return None
    if not prior or entry_vol <= 0:
        return None
    med = statistics.median(prior)
    return (entry_vol / med) if med > 0 else None


def _entry_full(entry_ts, created, liq, fdv, ohlcv):
    if not ohlcv:
        return None
    rows = sorted(ohlcv, key=lambda r: r[0])
    entry_close = ei = None
    for i, r in enumerate(rows):
        if r[0] <= entry_ts:
            entry_close = r[4]; ei = i
        else:
            break
    if entry_close is None or ei is None or entry_close <= 0:
        return None
    prior = [r for r in rows[:ei + 1] if entry_ts - r[0] <= 5400]    # prior 90 min
    hi90 = max((r[2] for r in prior), default=entry_close)
    dip90 = (entry_close / hi90 - 1.0) * 100 if hi90 > 0 else 0.0
    fwd = [r for r in rows[ei + 1:] if r[0] - entry_ts <= 21600]     # next 6h
    fwd_max = (max((r[2] for r in fwd), default=entry_close) / entry_close - 1.0) * 100
    age_h = ((entry_ts - created) / 3600.0) if created else None
    return {"ts": entry_ts, "dip_90m": dip90, "age_h": age_h, "fwd_max": fwd_max,
            "liq": liq, "fdv": fdv, "vol_spike": _vol_spike_at(entry_ts, ohlcv)}


def main():
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 80
    winners = json.load(open("_drawdown_winners_ranked.json"))
    wallets = [w["wallet"] for w in winners[:topn]]
    print(f"mining entries for top {len(wallets)} red-window winning wallets (sigs={sigs})",
          file=sys.stderr)

    feats = []
    for w in wallets:
        try:
            buys = collect_buys(w, sigs)
        except Exception as e:
            print(f"  {w[:10]} collect ERR {e}", file=sys.stderr); continue
        for m, ts, sol in buys:
            meta = token_pool_ohlc(m)
            if not meta:
                continue
            created, liq, fdv, ohlcv = meta
            f = _entry_full(ts, created, liq, fdv, ohlcv)
            if f:
                f["sol"] = sol; f["wallet"] = w[:10]
                feats.append(f)
        print(f"  {w[:10]}: running total {len(feats)} entries", file=sys.stderr)
        time.sleep(0.3)

    if not feats:
        print("no entries reconstructed (raise sigs / rerun)"); return
    json.dump(feats, open("_red_winner_feats.json", "w"))

    WIN, LOSE = 30.0, 0.0
    win = [f for f in feats if f["fwd_max"] >= WIN]
    lose = [f for f in feats if f["fwd_max"] <= LOSE]
    print(f"\n=== {len(feats)} entries | {len(win)} WINNERS (fwd>=+{WIN:.0f}%) "
          f"vs {len(lose)} LOSERS (fwd<=0%) ===")

    def cmp(nm, key, fmt="{:.1f}"):
        wv = [f[key] for f in win if f.get(key) is not None]
        lv = [f[key] for f in lose if f.get(key) is not None]
        wm = statistics.median(wv) if wv else float("nan")
        lm = statistics.median(lv) if lv else float("nan")
        print(f"  {nm:14s} WINNERS median={fmt.format(wm)}   LOSERS median={fmt.format(lm)}")

    cmp("dip_90m %", "dip_90m")
    cmp("age_h", "age_h")
    cmp("vol_spike x", "vol_spike", "{:.1f}")
    cmp("liq $", "liq", "{:,.0f}")
    cmp("fdv/mcap $", "fdv", "{:,.0f}")
    cmp("buy_size SOL", "sol", "{:.3f}")

    tod = collections.Counter()
    for f in win:
        ct = datetime.fromtimestamp(f["ts"], tz=timezone.utc) - timedelta(hours=5)  # CDT (June)
        tod[ct.hour] += 1
    print(f"\n  WINNER entries time-of-day (CT) top: "
          f"{', '.join(f'{h}:00({n})' for h, n in tod.most_common(6))}")

    print("\n=== RED-WINNING ENTRY SIGNATURE (winner medians) ===")
    for nm, key, fmt in [("dip off 90m high", "dip_90m", "{:+.0f}%"), ("age", "age_h", "{:.1f}h"),
                         ("vol_spike", "vol_spike", "{:.1f}x"), ("liq", "liq", "${:,.0f}"),
                         ("mcap", "fdv", "${:,.0f}")]:
        wv = [f[key] for f in win if f.get(key) is not None]
        if wv:
            print(f"  {nm:18s}: {fmt.format(statistics.median(wv))}")
    print("\nwrote _red_winner_feats.json")


if __name__ == "__main__":
    main()
