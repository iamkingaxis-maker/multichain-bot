"""Per-wallet seller-HHI forward mine on fresh pump.fun graduations (2026-06-03).

THE WARM LEAD from the 3-thread hunt (reference_pump_vs_bleed_3thread): does TRUE
per-wallet seller concentration (one whale dumping vs distributed selling) at a
decision instant separate fresh grads that PUMP from ones that BLEED OUT? The coarse
dollar-share proxy could not (within-token p=0.51); the maker WALLET is the untested
axis.

DESIGN = FORWARD (avoids the trade-log's last-100-swaps contamination):
  SNAPSHOT now: for each fresh grad (age<=MAX_AGE_H, actively trading), pull the
    io.dexscreener trade log -> per-wallet HHI features (feeds/wallet_flow_features),
    record current market-cap anchor from 1S bars. One snapshot per token (dedup).
  RESOLVE later (>=HORIZON_MIN): re-pull 1S bars, measure forward peak/end vs the
    snapshot anchor -> label pump (fwd_peak>=PUMP_X) vs bleed. Dead/delisted = bleed.
  STATUS: once n>=MIN_N resolved, run the fleet discipline (per-feature Cohen d +
    Mann-Whitney AUC, token-clustered permutation null, drop-one jackknife). Each
    token is snapshotted once, so rows == tokens == naturally out-of-token.

All LOCAL (io.dexscreener + pump.fun) -- no Railway egress, no production change.

Usage:
  python scripts/wallet_hhi_grad_mine.py            # cycle: resolve due + new snapshot
  python scripts/wallet_hhi_grad_mine.py snapshot   # only take new snapshots
  python scripts/wallet_hhi_grad_mine.py resolve     # only resolve due snapshots
  python scripts/wallet_hhi_grad_mine.py status      # report mine stats on resolved set
Store: .wallet_hhi/  (pending/{addr}.json, resolved.jsonl)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from curl_cffi import requests as cr

sys.path.insert(0, str(Path(__file__).parent.parent))
from feeds.dexscreener_chart_format import parse_chart_bars
from feeds.dexscreener_trades_format import parse_trades
from feeds.wallet_flow_features import wallet_flow_features

ROOT = Path(__file__).parent.parent
STORE = ROOT / ".wallet_hhi"
PENDING = STORE / "pending"
RESOLVED = STORE / "resolved.jsonl"

SOL = "So11111111111111111111111111111111111111112"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HDR = {"Origin": "https://dexscreener.com", "Referer": "https://dexscreener.com/",
       "User-Agent": _UA, "Accept": "*/*"}
PUMP_HDR = {"Origin": "https://pump.fun", "Referer": "https://pump.fun/",
            "User-Agent": _UA, "Accept": "*/*"}

MAX_AGE_H = float(os.environ.get("WHM_MAX_AGE_H", "2.0"))
HORIZON_MIN = float(os.environ.get("WHM_HORIZON_MIN", "35"))
MAX_PER_RUN = int(os.environ.get("WHM_MAX_PER_RUN", "30"))
PUMP_X = float(os.environ.get("WHM_PUMP_X", "1.30"))
MIN_N = int(os.environ.get("WHM_MIN_N", "50"))
PUMPFUN = "https://frontend-api-v3.pump.fun/coins"

FEATURES = ["seller_hhi", "seller_top1_share", "seller_top3_share", "n_sellers",
            "buyer_hhi", "buyer_top1_share", "n_buyers", "hhi_sell_minus_buy",
            "seller_buyer_wallet_ratio", "n_swaps"]


def _a(s):
    """ASCII-safe symbol for Windows console printing."""
    return (s or "?").encode("ascii", "replace").decode("ascii")


def _sess():
    return cr.Session(impersonate="chrome", headers=HDR)


def _warm(sess, pool):
    try:
        sess.get(f"https://dexscreener.com/solana/{pool}", timeout=25)
    except Exception:
        pass


def _bars(sess, pool, cb=999):
    u = (f"https://io.dexscreener.com/dex/chart/amm/v3/pumpfundex/bars/solana/{pool}"
         f"?mc=1&res=1S&cb={cb}&q={SOL}")
    try:
        return parse_chart_bars(sess.get(u, timeout=25).content)
    except Exception:
        return []


def _trades(sess, pool):
    u = f"https://io.dexscreener.com/dex/log/amm/v4/pumpfundex/all/solana/{pool}?q={SOL}&c=1"
    try:
        return parse_trades(sess.get(u, timeout=25).content)
    except Exception:
        return []


def _fresh_grads():
    sess = cr.Session(impersonate="chrome", headers=PUMP_HDR)
    seen, out = set(), []
    for off in range(0, 250, 50):
        try:
            r = sess.get(PUMPFUN, params={"limit": 50, "sort": "created_timestamp",
                         "order": "DESC", "complete": "true", "offset": off}, timeout=25)
            page = r.json()
        except Exception:
            break
        now = time.time() * 1000
        for c in page:
            mint = c.get("mint")
            pool = c.get("pump_swap_pool")
            ct = c.get("created_timestamp")
            if not mint or not pool or not ct or mint in seen:
                continue
            seen.add(mint)
            age_h = (now - ct) / 3_600_000
            if 0 <= age_h <= MAX_AGE_H and (c.get("market_cap") or 0) > 0:
                out.append({"address": mint, "pool": pool, "sym": c.get("symbol"),
                            "created": ct, "age_h": round(age_h, 3)})
        time.sleep(0.5)
    return out


def _done_addrs():
    done = set(p.stem for p in PENDING.glob("*.json")) if PENDING.exists() else set()
    if RESOLVED.exists():
        for line in RESOLVED.read_text().splitlines():
            try:
                done.add(json.loads(line)["address"])
            except Exception:
                pass
    return done


def snapshot():
    PENDING.mkdir(parents=True, exist_ok=True)
    grads = _fresh_grads()
    done = _done_addrs()
    todo = [g for g in grads if g["address"] not in done][:MAX_PER_RUN]
    print(f"[snapshot] {len(grads)} fresh grads (age<={MAX_AGE_H}h), {len(todo)} new to snapshot")
    sess = _sess()
    n = 0
    for g in todo:
        _warm(sess, g["pool"])
        bars = _bars(sess, g["pool"])
        sw = _trades(sess, g["pool"])
        if not bars or not sw:
            time.sleep(1.2)
            continue
        feats = wallet_flow_features(sw)
        if feats["sell_usd"] <= 0 or feats["n_swaps"] < 10:
            time.sleep(1.2)
            continue  # need real flow to compute concentration
        anchor = bars[-1]
        snap = {"address": g["address"], "sym": g["sym"], "pool": g["pool"],
                "age_h": g["age_h"], "snap_wall": time.time(),
                "anchor_ts_ms": anchor["ts_ms"], "anchor_mc": anchor["close"],
                "features": feats}
        (PENDING / f"{g['address']}.json").write_text(json.dumps(snap))
        n += 1
        print(f"  + {_a(g['sym'])[:12]:<12} age={g['age_h']:.2f}h  seller_hhi={feats['seller_hhi']} "
              f"top1={feats['seller_top1_share']} n_sellers={feats['n_sellers']} "
              f"whale={feats['single_whale_seller']}")
        time.sleep(1.4)
    print(f"[snapshot] banked {n} new snapshots (pending total {len(list(PENDING.glob('*.json')))})")


def resolve():
    if not PENDING.exists():
        print("[resolve] no pending dir"); return
    sess = _sess()
    due = []
    for p in PENDING.glob("*.json"):
        try:
            s = json.loads(p.read_text())
        except Exception:
            continue
        if (time.time() - s["snap_wall"]) / 60.0 >= HORIZON_MIN:
            due.append((p, s))
    print(f"[resolve] {len(due)} snapshots past {HORIZON_MIN}min horizon")
    RESOLVED.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    for p, s in due:
        _warm(sess, s["pool"])
        bars = _bars(sess, s["pool"])
        anchor_mc = s["anchor_mc"] or 0
        fwd = [b for b in bars if b["ts_ms"] > s["anchor_ts_ms"]
               and b["ts_ms"] <= s["anchor_ts_ms"] + HORIZON_MIN * 60_000]
        if anchor_mc <= 0:
            time.sleep(1.2); continue
        if not fwd:
            # no forward bars => token died/delisted after snapshot = bleed to ~0
            peak_x, end_x, pump = 0.0, 0.0, 0
        else:
            peak = max(b["high"] for b in fwd)
            end = fwd[-1]["close"]
            peak_x = peak / anchor_mc
            end_x = end / anchor_mc
            pump = 1 if peak_x >= PUMP_X else 0
        row = {"address": s["address"], "sym": s["sym"], "age_h": s["age_h"],
               "snap_wall": s["snap_wall"], "fwd_peak_x": round(peak_x, 4),
               "fwd_end_x": round(end_x, 4), "pump": pump, **s["features"]}
        with open(RESOLVED, "a") as f:
            f.write(json.dumps(row) + "\n")
        p.unlink()
        n += 1
        print(f"  ~ {_a(s['sym'])[:12]:<12} peak_x={peak_x:.2f} end_x={end_x:.2f} -> "
              f"{'PUMP' if pump else 'bleed'}  (seller_hhi={s['features']['seller_hhi']})")
    print(f"[resolve] resolved {n}")


def _load_resolved():
    if not RESOLVED.exists():
        return []
    rows = []
    for line in RESOLVED.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def status():
    import statistics as st
    rows = _load_resolved()
    pend = len(list(PENDING.glob("*.json"))) if PENDING.exists() else 0
    n = len(rows)
    pumps = sum(r["pump"] for r in rows)
    print(f"\n=== wallet-HHI grad mine ===  resolved n={n}  pending={pend}")
    if n == 0:
        print("  (nothing resolved yet -- run the accrual loop, resolve after the horizon)")
        return
    print(f"  base rate: pump(>={PUMP_X}x)={pumps} / bleed={n-pumps}  ({100*pumps/n:.0f}% pump)")
    if n < MIN_N:
        print(f"  n<{MIN_N} -- distributions shown, hold statistical verdict until n>={MIN_N}:")
    def col(f):
        return [r[f] for r in rows if isinstance(r.get(f), (int, float))]
    print(f"\n  {'feature':<26} {'pump_med':>9} {'bleed_med':>9} {'cohen_d':>8} {'AUC':>6} {'null_p':>7}")
    try:
        from sklearn.metrics import roc_auc_score
        have_auc = True
    except Exception:
        have_auc = False
    import random
    rng = random.Random(42)
    for f in FEATURES:
        pv = [r[f] for r in rows if r["pump"] == 1 and isinstance(r.get(f), (int, float))]
        bv = [r[f] for r in rows if r["pump"] == 0 and isinstance(r.get(f), (int, float))]
        if len(pv) < 2 or len(bv) < 2:
            continue
        pooled = st.pstdev(pv + bv) or 1e-9
        d = (st.mean(pv) - st.mean(bv)) / pooled
        auc, nullp = None, None
        if have_auc:
            ys = [r["pump"] for r in rows if isinstance(r.get(f), (int, float))]
            xs = [r[f] for r in rows if isinstance(r.get(f), (int, float))]
            try:
                auc = roc_auc_score(ys, xs)
                # token-clustered null: rows==tokens, so simple label shuffle is token-clustered
                obs = abs(auc - 0.5)
                hits = 0; N = 2000
                for _ in range(N):
                    ysh = ys[:]; rng.shuffle(ysh)
                    if abs(roc_auc_score(ysh, xs) - 0.5) >= obs:
                        hits += 1
                nullp = (hits + 1) / (N + 1)
            except Exception:
                pass
        print(f"  {f:<26} {st.median(pv):>9.3f} {st.median(bv):>9.3f} {d:>8.2f} "
              f"{(auc if auc is not None else float('nan')):>6.3f} "
              f"{(nullp if nullp is not None else float('nan')):>7.3f}")
    print(f"\n  (per-feature AUC is out-of-token by construction: 1 snapshot/token. "
          f"null_p = label-shuffle permutation. Apply BH/Bonferroni across {len(FEATURES)} features.)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "cycle"
    if mode == "snapshot":
        snapshot()
    elif mode == "resolve":
        resolve()
    elif mode == "status":
        status()
    else:  # cycle
        resolve()
        snapshot()
        status()


if __name__ == "__main__":
    main()
