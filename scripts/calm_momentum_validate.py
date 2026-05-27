"""Forward validation for the CALM-MOMENTUM strategy candidate (found 2026-05-27).

The universe recorder (/api/universe-recorder) already logs, for every scanned
token, the detection features (pc_h1, vol_m5, cum_pct_5m, liq_usd, ...) AND the
30-min forward outcome (peak_pct, exit_pct). So it IS the shadow for this
candidate — no scanner stamp needed. This tool reads it and evaluates the
calm-momentum predicate on records detected AFTER a cutoff (true out-of-sample,
since the strategy was mined on 2026-05-16..27 data).

Calm-momentum (decorrelated from dip-buy; buys calm strength):
    pc_h1 >= 13%  AND  vol_m5 <= 1510  AND  cum_pct_5m >= -7%   (liq >= 50k)

Discovery stats (in-sample 05-16..27, holdout 05-23..27):
    holdout mean realized exit +8.5%, WR 58%, n=24 (full n=75).
    Tail-driven: full mean +4.9% vs median +0.1% — "let winners run", not a grind.
    Robust theme: calm (vol_m5<=1510) beats frenzied (>1510) ~5x with half the
    round-trips. THIS forward run is the real test.

Usage:
    python scripts/calm_momentum_validate.py                 # forward (since 2026-05-27)
    python scripts/calm_momentum_validate.py 2026-05-20      # custom cutoff
"""
import sys
import time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
SINCE = sys.argv[1] if len(sys.argv) > 1 else "2026-05-27"


def _get(url):
    from curl_cffi import requests as r
    for _ in range(5):
        try:
            return r.get(url, impersonate="chrome", timeout=90).json()
        except Exception:
            time.sleep(3)
    return None


def col(recs, k):
    return np.array([x.get(k) if isinstance(x.get(k), (int, float)) else np.nan for x in recs], float)


def summ(exitp, peak, mask, label):
    m = mask & ~np.isnan(exitp)
    n = int(m.sum())
    if n < 3:
        print(f"  {label:<22} n={n}  (too few — keep waiting)")
        return
    e = exitp[m]
    print(f"  {label:<22} n={n:<4} mean={np.mean(e):+5.1f}%  median={np.median(e):+5.1f}%  "
          f"WR={np.mean(e>0):.2f}  hit+5%={np.mean(e>=5):.2f}  rt(<=-5)={np.mean(e<=-5):.2f}  "
          f"meanpeak={np.nanmean(peak[m]):.0f}%")


def main():
    d = _get(f"{BASE}/api/universe-recorder?limit=50000")
    recs = d if isinstance(d, list) else (d or {}).get("events", [])
    fwd = [x for x in recs if x.get("detected_at_iso", "") >= SINCE]
    print(f"universe records: {len(recs)} total | {len(fwd)} detected since {SINCE} (out-of-sample)\n")
    if len(fwd) < 20:
        print(f"Only {len(fwd)} forward records so far — re-run in a few days as data accrues.")
        if len(fwd) == 0:
            return
    exitp = col(fwd, "exit_pct"); peak = col(fwd, "peak_pct")
    pc_h1 = col(fwd, "pc_h1"); vol_m5 = col(fwd, "vol_m5")
    cum5 = col(fwd, "cum_pct_5m"); liq = col(fwd, "liq_usd")
    tradeable = liq >= 50000

    calm_mom = (pc_h1 >= 12.88) & (vol_m5 <= 1510) & (cum5 >= -6.989) & tradeable
    print("CALM-MOMENTUM candidate (forward / out-of-sample):")
    summ(exitp, peak, calm_mom, "calm-momentum")
    print("\n  Robustness — the durable theme (calm vs frenzied, pc_h1>=13, liq>=50k):")
    summ(exitp, peak, (pc_h1 >= 12.88) & (vol_m5 <= 1510) & tradeable, "calm momentum")
    summ(exitp, peak, (pc_h1 >= 12.88) & (vol_m5 > 1510) & tradeable, "frenzied momentum")
    print(f"\n  baseline (all tradeable): mean {np.nanmean(exitp[tradeable]):+.1f}% "
          f"WR {np.nanmean(exitp[tradeable] > 0):.2f}")
    print("\nVERDICT GUIDE: calm-momentum holds forward if mean stays clearly +ve (>~+3%) and")
    print("calm keeps beating frenzied. If it collapses to ~0/neg, it was a small-n artifact.")


if __name__ == "__main__":
    main()
