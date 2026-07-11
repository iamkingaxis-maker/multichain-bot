"""Labeled catastrophic-rug cohort builder (2026-07-11 rug forensics follow-up).

The forensics verdict (scratchpad/_rug_forensics.md): our realized PnL cannot
label rugs — containment masks severity (DEAD -3.04% vs ALIVE -3.61% token-mean).
The HOODLANA-class gate (hidden-supply dump) can only be graded on a labeled
cohort built FORWARD: snapshot holder-structure features at entry (shipped
405e73e: shoulder_11_20_pct / pool_topholder_pct / topholder_insider_pct /
total_holders stamped on every buy), then follow each token 24-48h and label
the outcome on-chain.

This script is the follower. Run it at the session ritual (per-session only —
no scheduled tasks / no 24-7 local machines):

    python scripts/rug_cohort_label.py                 # label + summary
    python scripts/rug_cohort_label.py --cache FILE    # offline trades cache

Labels (per distinct mint, first-buy anchored):
  catastrophic — price <= -90% vs our entry AND (liq < $5k OR pair gone) — the
                 HOODLANA class (cap-hitting tail)
  dead         — price <= -80% vs entry OR liq < $5k OR pair gone
  alive        — everything else
State: scratchpad/rug_cohort_labels.jsonl (append-only; a mint is labeled once
per first-buy day; re-runs skip already-labeled). Once catastrophic n >= 5 the
summary prints feature separation (median shoulder/pool/insider per label) so
the gate threshold comes from data.

Egress: DexScreener batch endpoint (30 mints/request), one pass per run.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "scratchpad", "rug_cohort_labels.jsonl")
API = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit=5000"
DS_BATCH = "https://api.dexscreener.com/latest/dex/tokens/{}"
LABEL_AFTER_H = float(os.environ.get("RUG_LABEL_AFTER_H", "24"))
FEATURES = ("shoulder_11_20_pct", "pool_topholder_pct", "topholder_insider_pct",
            "total_holders", "top10_holder_pct", "top1_holder_pct",
            "lp_locked_pct", "rugcheck_score")


def _load_labeled() -> set:
    done = set()
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["mint"])
                except Exception:
                    continue
    return done


def _fetch_trades(cache: str | None) -> list:
    if cache:
        with open(cache, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else d.get("trades", [])
    user = os.environ.get("DASHBOARD_USER", "")
    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    req = urllib.request.Request(API)
    if user:
        import base64
        req.add_header("Authorization", "Basic " +
                       base64.b64encode(f"{user}:{pw}".encode()).decode())
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read())
    return d if isinstance(d, list) else d.get("trades", [])


def _ds_batch(mints: list) -> dict:
    """mint -> best pair {price_usd, liq_usd} (None if pair gone).

    curl_cffi chrome impersonation — plain urllib gets blocked by DexScreener
    (first run mislabeled 198/198 catastrophic on silent empty responses).
    A batch FETCH FAILURE must not label anything: raise instead of
    defaulting to pair-gone.
    """
    from curl_cffi import requests as _cf
    out: dict = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        r = _cf.get(DS_BATCH.format(",".join(chunk)),
                    impersonate="chrome", timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"dexscreener batch HTTP {r.status_code} — aborting, no labels written")
        pairs = (r.json() or {}).get("pairs") or []
        best: dict = {}
        for p in pairs:
            m = ((p.get("baseToken") or {}).get("address") or "")
            liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
            if m and liq >= best.get(m, (None, -1))[1]:
                try:
                    price = float(p.get("priceUsd") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                best[m] = (price, liq)
        for m in chunk:
            out[m] = best.get(m)  # None = pair gone
        time.sleep(1.5)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=None, help="local trades JSON instead of API")
    args = ap.parse_args()

    trades = _fetch_trades(args.cache)
    labeled = _load_labeled()
    now = time.time()

    # First buy per mint, with its entry features.
    first: dict = {}
    for t in trades:
        em = t.get("entry_meta") or {}
        mint = t.get("address")
        ep = t.get("entry_price")
        ts = t.get("time") or t.get("timestamp")
        if not (mint and em and ep):
            continue
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        if mint not in first or (ts_f or 0) < (first[mint]["entry_ts"] or 1e18):
            first[mint] = {
                "mint": mint, "token": t.get("token"), "entry_price": float(ep),
                "entry_ts": ts_f,
                "features": {k: em.get(k) for k in FEATURES if em.get(k) is not None},
            }

    due = [v for m, v in first.items()
           if m not in labeled
           and (v["entry_ts"] is None or now - v["entry_ts"] >= LABEL_AFTER_H * 3600)]
    if not due:
        print(f"nothing due (labeled={len(labeled)}, tracked={len(first)})")
    else:
        states = _ds_batch([v["mint"] for v in due])
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "a", encoding="utf-8") as f:
            for v in due:
                st = states.get(v["mint"])
                if st is None:
                    label, price_now, liq_now, ret = "catastrophic", None, None, None
                else:
                    price_now, liq_now = st
                    ret = ((price_now / v["entry_price"]) - 1) * 100 if v["entry_price"] else None
                    if ret is not None and ret <= -90 and (liq_now or 0) < 5000:
                        label = "catastrophic"
                    elif (ret is not None and ret <= -80) or (liq_now or 0) < 5000:
                        label = "dead"
                    else:
                        label = "alive"
                f.write(json.dumps({
                    "mint": v["mint"], "token": v["token"], "label": label,
                    "entry_price": v["entry_price"], "entry_ts": v["entry_ts"],
                    "price_now": price_now, "liq_now": liq_now,
                    "ret_pct": round(ret, 2) if ret is not None else None,
                    "labeled_ts": now, "features": v["features"],
                }) + "\n")
        print(f"labeled {len(due)} mints")

    # Summary + feature separation once the tail cohort exists.
    rows = []
    with open(STATE, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    from collections import Counter
    import statistics as stx
    counts = Counter(r["label"] for r in rows)
    print(f"cohort: {dict(counts)} (n={len(rows)})")
    cat = [r for r in rows if r["label"] == "catastrophic" and r["features"]]
    alive = [r for r in rows if r["label"] == "alive" and r["features"]]
    if len(cat) >= 5 and alive:
        print("--- feature separation (median cat vs alive) ---")
        for k in FEATURES:
            cv = [r["features"][k] for r in cat if k in r["features"]]
            av = [r["features"][k] for r in alive if k in r["features"]]
            if cv and av:
                print(f"  {k}: cat={stx.median(cv):.2f} (n={len(cv)})  "
                      f"alive={stx.median(av):.2f} (n={len(av)})")
    elif cat:
        print(f"catastrophic n={len(cat)} (<5; separation stats unlock at n>=5)")


if __name__ == "__main__":
    sys.exit(main())
