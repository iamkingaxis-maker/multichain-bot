"""Deep-dive on MASCOTS — first winner since the filter fixes (2026-05-14 PM).

Entry: 2026-05-14T17:42:41 UTC at $0.0003432  (mcap=$343k)
TP1:   2026-05-14T18:30:23 UTC at +5.8% ($0.46 on 50%)
Trail: 2026-05-14T18:30:57 UTC at +0.6% ($0.06 on 50%)
Peak:  +5.8%  max DD -3.7%  hold 48 min

Triggers fired: patient_bottom + informed_cluster + whale_conviction
"""
import os
import sys
from datetime import datetime, timezone

# Force UTF-8
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from feeds.dexscreener_chart_format import parse_chart_bars

from curl_cffi import requests as cf_requests


PAIR = "D2g8AAUqBzLpX7mxp6yg8v3Psau6hVc2dRLqjuaQXZyP"
ADDR = "8GxLxKA8tf3h8JUkXFfP4dNyn6D2vvwyGif5wanRpump"
ENTRY_TS = datetime(2026, 5, 14, 17, 42, 41, tzinfo=timezone.utc)
TP1_TS = datetime(2026, 5, 14, 18, 30, 23, tzinfo=timezone.utc)
TRAIL_TS = datetime(2026, 5, 14, 18, 30, 57, tzinfo=timezone.utc)
ENTRY_PRICE = 0.0003432


def fetch_bars(sess, res, cb=300):
    """Fetch via DexScreener binary chart API. Returns list of bar dicts."""
    # MASCOTS is pumpfun token → pumpfundex slug
    slug = "pumpfundex"
    # First resolve quote mint
    meta_url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{PAIR}"
    try:
        r = sess.get(meta_url, timeout=10)
        data = r.json()
        pairs = data.get("pairs") or data.get("pair") or []
        if isinstance(pairs, dict):
            pairs = [pairs]
        if not pairs:
            return [], "no pair meta"
        p = pairs[0]
        dex_id = (p.get("dexId") or "").lower()
        slug = {"pumpswap": "pumpfundex", "raydium": "solamm"}.get(dex_id, slug)
        quote = (p.get("quoteToken") or {}).get("address") or ""
    except Exception as e:
        return [], f"meta err: {e}"

    url = (
        f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}/bars/solana/{PAIR}"
        f"?res={res}&cb={cb}&q={quote}"
    )
    try:
        r = sess.get(
            url, timeout=15,
            headers={
                "Origin": "https://dexscreener.com",
                "Referer": "https://dexscreener.com/",
                "Accept": "*/*",
            },
        )
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}"
        bars = parse_chart_bars(r.content)
        return bars, None
    except Exception as e:
        return [], str(e)


def fmt_pct(price: float, ref: float) -> str:
    if not ref:
        return "?"
    return f"{(price - ref) / ref * 100:+6.2f}%"


def to_dt(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def filter_window(bars, start, end):
    """Return bars whose ts falls in [start, end]."""
    return [b for b in bars if start <= to_dt(b["ts_ms"]) <= end]


def main():
    sess = cf_requests.Session(impersonate="chrome")

    # === 5m candles — fetch large window ===
    print("=" * 90)
    print("5m bars — entry-relative")
    print("=" * 90)
    bars_5m, err = fetch_bars(sess, "5", cb=300)
    if err:
        print(f"5m fetch error: {err}")
    else:
        print(f"Got {len(bars_5m)} 5m bars")
        # Filter to entry-6h → exit+30min
        from datetime import timedelta
        win_start = ENTRY_TS - timedelta(hours=6)
        win_end = TRAIL_TS + timedelta(minutes=30)
        windowed = filter_window(bars_5m, win_start, win_end)
        print(f"In window: {len(windowed)} 5m bars")
        print()
        peak_pre = 0
        trough_pre = 9e9
        for b in windowed:
            t = to_dt(b["ts_ms"])
            o = b["open"]; h = b["high"]; l = b["low"]; c = b["close"]; v = b["volume_usd"]
            color = "G" if c >= o else "R"
            marker = ""
            if abs((t - ENTRY_TS).total_seconds()) < 300:
                marker = " <<< ENTRY"
            elif abs((t - TP1_TS).total_seconds()) < 300:
                marker = " <<< TP1"
            body_pct = (c - o) / o * 100 if o else 0
            range_pct = (h - l) / l * 100 if l else 0
            print(
                f"  {t.strftime('%H:%M')} {color} O={o:.7f} C={c:.7f} "
                f"body={body_pct:+5.2f}% rng={range_pct:5.2f}% "
                f"vol=${v:>7.0f} vs_entry={fmt_pct(c, ENTRY_PRICE)}{marker}"
            )
            if t < ENTRY_TS:
                peak_pre = max(peak_pre, h)
                trough_pre = min(trough_pre, l)
        if peak_pre > 0 and trough_pre < 9e9 and peak_pre > trough_pre:
            pct_in_range = (ENTRY_PRICE - trough_pre) / (peak_pre - trough_pre) * 100
            print()
            print(f"  Pre-entry 6h peak high: ${peak_pre:.7f}")
            print(f"  Pre-entry 6h trough low: ${trough_pre:.7f}")
            print(f"  Entry at {pct_in_range:.1f}% of range above trough")

    # === 1m candles ===
    print()
    print("=" * 90)
    print("1m bars — -30m before entry → +30m after exit")
    print("=" * 90)
    bars_1m, err = fetch_bars(sess, "1", cb=300)
    if err:
        print(f"1m fetch error: {err}")
    else:
        print(f"Got {len(bars_1m)} 1m bars")
        from datetime import timedelta
        win_start = ENTRY_TS - timedelta(minutes=30)
        win_end = TRAIL_TS + timedelta(minutes=30)
        windowed = filter_window(bars_1m, win_start, win_end)
        print(f"In window: {len(windowed)} 1m bars")
        print()
        for b in windowed:
            t = to_dt(b["ts_ms"])
            o = b["open"]; c = b["close"]; v = b["volume_usd"]
            color = "G" if c >= o else "R"
            marker = ""
            if abs((t - ENTRY_TS).total_seconds()) < 60:
                marker = " <<< ENTRY"
            elif abs((t - TP1_TS).total_seconds()) < 60:
                marker = " <<< TP1"
            elif abs((t - TRAIL_TS).total_seconds()) < 60:
                marker = " <<< TRAIL"
            body_pct = (c - o) / o * 100 if o else 0
            print(
                f"  {t.strftime('%H:%M:%S')} {color} body={body_pct:+5.2f}% "
                f"vol=${v:>6.0f} vs_entry={fmt_pct(c, ENTRY_PRICE)}{marker}"
            )

    # === Pattern summary ===
    print()
    print("=" * 90)
    print("PATTERN SUMMARY")
    print("=" * 90)
    if bars_5m:
        from datetime import timedelta
        # Pre-entry 5m breakdown by 1h chunks
        for hours_back in (1, 2, 3, 4, 6):
            win = filter_window(bars_5m, ENTRY_TS - timedelta(hours=hours_back), ENTRY_TS)
            if not win:
                continue
            greens = sum(1 for b in win if b["close"] >= b["open"])
            reds = len(win) - greens
            total_body_pct = sum((b["close"] - b["open"]) / b["open"] * 100 if b["open"] else 0 for b in win)
            print(f"  last {hours_back}h pre-entry: {len(win)} bars | greens={greens} reds={reds} | cum_body={total_body_pct:+.1f}%")

        # Post-entry → TP1 breakdown
        win = filter_window(bars_5m, ENTRY_TS, TP1_TS)
        if win:
            greens = sum(1 for b in win if b["close"] >= b["open"])
            reds = len(win) - greens
            tot = sum((b["close"] - b["open"]) / b["open"] * 100 if b["open"] else 0 for b in win)
            print(f"\n  entry→TP1 ({len(win)} bars): greens={greens} reds={reds} cum_body={tot:+.1f}%")


if __name__ == "__main__":
    main()
