"""Forward-tracker for the newly-covered fresh-mover cohort.

The 2026-05-27 discovery fix (commit 62a9781, deployed 2026-05-26T22:06 CT /
2026-05-27T03:06Z) widened the scanner's universe by adding GeckoTerminal
new_pools + volume-sorted discovery feeds. This tracker measures the FORWARD,
SURVIVORSHIP-FREE dip-buy EV of the cohort that fix unlocked.

WHY THIS EXISTS / WHAT IT FIXES
-------------------------------
The retroactive peek (`.newcohort_mine.py`) sampled tokens from the CURRENT
GT trending/new/volume feeds. By construction those are survivors — tokens
that pumped, rugged, or died are no longer in today's feed — so its +EV read
(WR 65-71%, +100% mean at 6h) was survivorship-biased and tail-inflated.

This tracker removes that bias by sampling tokens AT DETECTION TIME from the
universe recorder (`/api/universe-recorder`). Every scanned token is logged
the moment the scanner saw it, with a timestamp — including the ones that
subsequently died. Anchoring the entry at the recorded detection timestamp and
measuring forward candle outcomes gives an unbiased estimate of "what would a
dip-buy of the newly-covered pond have realized."

COHORT SPLIT
------------
  pre-deploy  : detected_at  <  DEPLOY_TS   (old narrow universe = baseline)
  post-deploy : detected_at  >= DEPLOY_TS   (widened universe)
The newly-covered fresh-mover cohort = post-deploy detections with
liq_usd >= DISCOVERY_FRESH_MIN_LIQ (40k, the discovery floor) — what the
gt_volume / gt_new_pool feeds surface.

NOTE ON THE RECORDER: the universe recorder logs the scanner's DIP-CANDIDATE
stream, not the full scanned universe — every record has cum_pct_5m < 0
(median ~-7%), i.e. tokens already pulling back at detection. So entry-at-
detection here is itself a dip-buy proxy, and pre-vs-post is an apples-to-apples
comparison of dip-buy pond quality before vs after the coverage fix.

WHEN TO RUN
-----------
Re-run a few days after the deploy. A record is only measurable once >=6h of
forward candles exist (now - detected_at >= 6h) AND it's recent enough that
DexScreener still serves candles back to detection (<= MAX_LOOKBACK_DAYS).
On the first run (hours after deploy) the post-deploy cohort will be too young
— the script reports that and exits cleanly. The verdict comes from later runs.

USAGE
-----
  python scripts/fresh_mover_forward_tracker.py              # pull recorder live
  python scripts/fresh_mover_forward_tracker.py .cache.json  # use a local cache
"""
import sys
import json
import time
from datetime import datetime, timezone

import numpy as np
from curl_cffi import requests as cf

from feeds.dexscreener_chart_format import parse_chart_bars

SOL = "So11111111111111111111111111111111111111112"
RECORDER_URL = (
    "https://gracious-inspiration-production.up.railway.app"
    "/api/universe-recorder?limit=50000"
)
DEPLOY_TS = datetime(2026, 5, 27, 3, 6, 4, tzinfo=timezone.utc).timestamp()
DISCOVERY_FRESH_MIN_LIQ = 40_000.0
HORIZONS = [3600, 7200, 14400, 21600]      # 1h, 2h, 4h, 6h
MIN_FORWARD_SECS = 21600                    # need >=6h elapsed to be measurable
MAX_LOOKBACK_DAYS = 5                       # DS candle reach limit (res=15)
MAX_FETCH_PER_COHORT = 140                  # cost/time bound on candle fetches
DEX_SLUGS = ["pumpswap", "raydium", "pumpfundex", "meteora", "raydiumcpmm"]


def _get_json(url, tries=4):
    for _ in range(tries):
        try:
            return cf.get(url, impersonate="chrome", timeout=25).json()
        except Exception:
            time.sleep(2)
    return None


def _detection_ts(rec):
    """Return detection epoch seconds, tolerating event_ts (s or ms) or ISO."""
    ev = rec.get("event_ts")
    if isinstance(ev, (int, float)) and ev > 0:
        return ev / 1000.0 if ev > 1e12 else float(ev)
    iso = rec.get("detected_at_iso")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def _fetch_bars(pair):
    """Forward candles for a pool, res=15min (covers ~5d at cb=500)."""
    for slug in DEX_SLUGS:
        try:
            resp = cf.get(
                f"https://io.dexscreener.com/dex/chart/amm/v3/{slug}"
                f"/bars/solana/{pair}?res=15&cb=500&q={SOL}",
                impersonate="chrome", timeout=15,
                headers={"Origin": "https://dexscreener.com",
                         "Referer": "https://dexscreener.com/"})
            if resp.status_code == 200:
                b = parse_chart_bars(resp.content)
                if b and len(b) > 8:
                    return sorted(
                        [{"ts": x["ts_ms"] / 1000, "h": x["high"],
                          "l": x["low"], "c": x["close"]} for x in b],
                        key=lambda x: x["ts"])
        except Exception:
            pass
    return None


def _outcome_from_detection(rec, now):
    """Anchor entry at detection_ts; return per-horizon peak/realized pct.

    Entry = close of the first candle at/after detection. Faithful to "buy when
    the scanner saw it." Returns None if candles don't reach the detection window.
    """
    det = _detection_ts(rec)
    if det is None or (now - det) < MIN_FORWARD_SECS:
        return None
    if (now - det) > MAX_LOOKBACK_DAYS * 86400:
        return None
    pair = rec.get("pair_address")
    if not pair:
        return None
    bars = _fetch_bars(pair)
    if not bars:
        return None
    # First bar at/after detection is the entry anchor; require candle coverage
    # to actually start at/before detection (else we'd be measuring a later window).
    if bars[0]["ts"] > det + 1800:        # coverage starts >30min after detection -> unusable
        return None
    entry_bars = [x for x in bars if x["ts"] >= det]
    if not entry_bars:
        return None
    ep = entry_bars[0]["c"]
    if ep <= 0:
        return None
    out = {}
    for h in HORIZONS:
        win = [x for x in entry_bars if x["ts"] <= det + h]
        if len(win) >= 2:
            out[f"pk{h}"] = 100.0 * (max(x["h"] for x in win) - ep) / ep
            out[f"ex{h}"] = 100.0 * (win[-1]["c"] - ep) / ep
    return out or None


def _summarize(label, outcomes):
    n = len(outcomes)
    print(f"\n{label}: n={n}")
    if n == 0:
        return
    for h in HORIZONS:
        ex = np.array([o[f"ex{h}"] for o in outcomes if f"ex{h}" in o])
        pk = np.array([o[f"pk{h}"] for o in outcomes if f"pk{h}" in o])
        if ex.size:
            print(f"  {h//3600}h: realized mean={ex.mean():+.1f}% "
                  f"median={np.median(ex):+.1f}% WR={np.mean(ex > 0):.2f} | "
                  f"peak>=50%={np.mean(pk >= 50):.2f} peak>=20%={np.mean(pk >= 20):.2f}")


def main():
    if len(sys.argv) > 1:
        recs = json.load(open(sys.argv[1], encoding="utf-8"))
        if isinstance(recs, dict):
            recs = recs.get("records") or recs.get("data") or []
        print(f"loaded {len(recs)} records from {sys.argv[1]}")
    else:
        resp = _get_json(RECORDER_URL)
        recs = (resp or {}).get("records") or (resp or {}).get("data") or \
            (resp if isinstance(resp, list) else [])
        print(f"pulled {len(recs)} records from recorder")

    now = time.time()
    deploy_iso = datetime.fromtimestamp(DEPLOY_TS, timezone.utc).isoformat()
    print(f"deploy anchor: {deploy_iso}  |  now: "
          f"{datetime.fromtimestamp(now, timezone.utc).isoformat()}")

    pre, post = [], []
    for r in recs:
        det = _detection_ts(r)
        if det is None:
            continue
        liq = r.get("liq_usd") or 0
        if det >= DEPLOY_TS and liq >= DISCOVERY_FRESH_MIN_LIQ:
            post.append(r)
        elif det < DEPLOY_TS and liq >= DISCOVERY_FRESH_MIN_LIQ:
            pre.append(r)

    # Measurable = >=6h forward and within candle-reach window.
    def measurable(rs):
        return [r for r in rs
                if (now - _detection_ts(r)) >= MIN_FORWARD_SECS
                and (now - _detection_ts(r)) <= MAX_LOOKBACK_DAYS * 86400]

    post_m, pre_m = measurable(post), measurable(pre)
    print(f"\npost-deploy fresh-mover (liq>=40k): {len(post)} total, "
          f"{len(post_m)} measurable (>=6h, <=5d)")
    print(f"pre-deploy baseline (liq>=40k):     {len(pre)} total, "
          f"{len(pre_m)} measurable")

    if len(post_m) < 10:
        print("\n>>> POST-DEPLOY COHORT TOO YOUNG to judge yet "
              f"(only {len(post_m)} measurable). The widened universe needs a few "
              "more days of >=6h-old detections. Re-run later. <<<")
        # Still characterize the baseline so the harness is proven working.

    fetched = 0
    cohorts = {
        "POST-DEPLOY fresh-mover": post_m[:MAX_FETCH_PER_COHORT],
        "PRE-DEPLOY baseline": pre_m[:MAX_FETCH_PER_COHORT],
    }
    results = {}
    for label, rs in cohorts.items():
        full = []
        for r in rs:
            out = _outcome_from_detection(r, now)
            time.sleep(0.1)
            if out:
                fetched += 1
                full.append(out)
        results[label] = full

    print(f"\ncandles fetched OK: {fetched}")
    for label, full in results.items():
        _summarize(f"{label} [entry@detection = dip-buy proxy]", full)

    print("\n(Reference -- survivorship-BIASED retroactive peek `.newcohort_mine`: "
          "6h WR 0.71, median +2.7%, mean +100%. This tracker is unbiased; expect "
          "lower/realistic numbers. OLD aged universe 6h realized mean was -8.6%.)")


if __name__ == "__main__":
    main()
