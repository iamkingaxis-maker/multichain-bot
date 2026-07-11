"""RH regime mining pass — one stream over sweep_logs.jsonl.gz (10.36M swaps).

Products (scratchpad/rh_regime/):
  windows.json  : per 30-min UTC window feed-wide composition —
                  {win_key: [n, buy_eth, sell_eth, buy_n, sell_n, distinct_pools,
                             new_pools, eth_usd_last]}
  trips.jsonl.gz: synthetic dip-style entries (our strategy proxy) + pop events,
                  each stamped with hour/day/pool-age/npph at entry and resolved
                  at +20m (ret), +60m (rug = trough <= 0.2x entry), MFE/MAE.

Synthetic dip trip (mirrors the paper lane trigger, maker-less):
  pool has >=10 swaps and >=0.3 ETH cum volume; price window (10 min) has >=5
  points spanning >=120s; latest px <= 0.88 * window max (dip >= 12%); last-30s
  buys >= 0.03 ETH (~$50) and > last-30s sells (demand turn). 600s per-pool
  cooldown. Pop event: latest px >= 1.35 * window min, same establishment bar,
  own 600s cooldown; outcome = follow-through vs pop px.

Resolution: first px at/after t0+1200 = ret20 (resolution="t20"); stream/pool
going silent -> last px if >=300s after entry (resolution="stale") else dropped.
rug=1 when trough within t0+3600 <= 0.2*px0.
"""
import bisect
import collections
import gzip
import json
import os
import time

HIST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_regime"
ETH_USD_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"

WIN_S = 1800
PRICE_WINDOW_S = 600.0
DIP_FRAC = 0.88          # <= 12% off the 10-min high
POP_FRAC = 1.35          # >= 35% off the 10-min low
FLOW_WINDOW_S = 30.0
MIN_BUYS_ETH_30S = 0.03  # ~$50
COOLDOWN_S = 600.0
RESOLVE_S = 1200.0
RUG_WINDOW_S = 3600.0
RUG_FRAC = 0.2
MIN_SWAPS = 10
MIN_CUM_ETH = 0.3

# ── block -> ts ──────────────────────────────────────────────────────────────
anchors = json.load(open(os.path.join(HIST, "anchors.json")))
A_B = [a[0] for a in anchors]
A_T = [a[1] for a in anchors]


def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0:
        return A_T[0]
    if i >= len(A_B) - 1:
        return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0, b1, t1 = A_B[i], A_T[i], A_B[i + 1], A_T[i + 1]
    return t0 + (t1 - t0) * (block - b0) / max(1, b1 - b0)


# ── registry: creation ts per pool + sorted creation array (npph) ────────────
reg_ts = {}
for line in open(os.path.join(HIST, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    reg_ts[d["pool"]] = d["ts"]
creation_ts = sorted(reg_ts.values())


def npph_at(ts):
    """Pools created in the trailing hour (matches lane new_pools_per_hour)."""
    return (bisect.bisect_left(creation_ts, ts)
            - bisect.bisect_left(creation_ts, ts - 3600.0))


# ── state ─────────────────────────────────────────────────────────────────────
class PoolState:
    __slots__ = ("buf", "flows", "n", "cum_eth", "cd_dip", "cd_pop", "last_ts")

    def __init__(self):
        self.buf = collections.deque()     # (ts, px)
        self.flows = collections.deque()   # (ts, kind, w)
        self.n = 0
        self.cum_eth = 0.0
        self.cd_dip = 0.0
        self.cd_pop = 0.0
        self.last_ts = 0.0


pools = {}
pending = collections.defaultdict(list)   # pool -> [trip dicts]
done = []
windows = {}                               # win -> [n,b,s,bn,sn]
win_pools = collections.defaultdict(set)   # win -> distinct pools
eth_px_by_win = {}                         # win -> last raw px of USDG pool
eth_scale = None                           # calibrated 10^k so eth_usd ~1500-1900

t_run = time.time()
n_rows = 0
n_trips = n_pops = 0


def resolve_rows(pool, ts, px):
    """Advance pending trips on this pool with a fresh (ts, px) sample."""
    global done
    lst = pending.get(pool)
    if not lst:
        return
    keep = []
    for tr in lst:
        dt = ts - tr["t0"]
        if dt <= RUG_WINDOW_S:
            if px < tr["trough"]:
                tr["trough"] = px
            if px > tr["peak"] and dt <= RESOLVE_S:
                tr["peak"] = px
        if tr["px20"] is None:
            if dt >= RESOLVE_S:
                tr["px20"] = px
                tr["res"] = "t20"
            else:
                tr["last_px"] = px
                tr["last_dt"] = dt
        if dt > RUG_WINDOW_S:
            done.append(tr)
        else:
            keep.append(tr)
    if keep:
        pending[pool] = keep
    else:
        del pending[pool]


def finalize(pool, lst):
    for tr in lst:
        if tr["px20"] is None:
            if tr.get("last_px") and tr.get("last_dt", 0) >= 300:
                tr["px20"] = tr["last_px"]
                tr["res"] = "stale"
            else:
                tr["res"] = "dropped"
        done.append(tr)


def open_trip(kind, pool, ts, px, st):
    global n_trips, n_pops
    age_h = (ts - reg_ts[pool]) / 3600.0 if pool in reg_ts else None
    tm = time.gmtime(ts)
    tr = {"kind": kind, "pool": pool, "t0": ts, "px0": px,
          "day": time.strftime("%Y-%m-%d", tm), "hour": tm.tm_hour,
          "win": int(ts // WIN_S), "age_h": (round(age_h, 3)
                                             if age_h is not None else None),
          "npph": npph_at(ts), "px20": None, "res": None,
          "peak": px, "trough": px, "last_px": None, "last_dt": 0.0}
    pending[pool].append(tr)
    if kind == "dip":
        n_trips += 1
    else:
        n_pops += 1


src = os.path.join(HIST, "sweep_logs.jsonl.gz")
with gzip.open(src, "rt", encoding="utf-8") as f:
    for ln in f:
        n_rows += 1
        try:
            d = json.loads(ln)
        except Exception:
            continue
        p = d["p"]
        px = d["px"] or 0.0
        w = d["w"] or 0.0
        ts = est_ts(d["b"])
        win = int(ts // WIN_S)
        wagg = windows.get(win)
        if wagg is None:
            wagg = windows[win] = [0, 0.0, 0.0, 0, 0]
        wagg[0] += 1
        if d["k"] == "buy":
            wagg[1] += w
            wagg[3] += 1
        else:
            wagg[2] += w
            wagg[4] += 1
        win_pools[win].add(p)
        if p == ETH_USD_POOL:
            if px > 0:
                eth_px_by_win[win] = px
            continue                      # not a memecoin pool
        if px <= 0:
            continue
        st = pools.get(p)
        if st is None:
            st = pools[p] = PoolState()
        st.n += 1
        st.cum_eth += w
        st.last_ts = ts
        resolve_rows(p, ts, px)
        buf = st.buf
        buf.append((ts, px))
        while buf and ts - buf[0][0] > PRICE_WINDOW_S:
            buf.popleft()
        fl = st.flows
        fl.append((ts, d["k"], w))
        while fl and ts - fl[0][0] > FLOW_WINDOW_S:
            fl.popleft()
        if st.n < MIN_SWAPS or st.cum_eth < MIN_CUM_ETH or len(buf) < 5:
            continue
        if buf[-1][0] - buf[0][0] < 120.0:
            continue
        hi = max(x for _, x in buf)
        lo = min(x for _, x in buf)
        if px <= hi * DIP_FRAC and ts - st.cd_dip > COOLDOWN_S:
            b30 = sum(x for _, k, x in fl if k == "buy")
            s30 = sum(x for _, k, x in fl if k == "sell")
            if b30 >= MIN_BUYS_ETH_30S and b30 > s30:
                st.cd_dip = ts
                open_trip("dip", p, ts, px, st)
        if px >= lo * POP_FRAC and ts - st.cd_pop > COOLDOWN_S:
            st.cd_pop = ts
            open_trip("pop", p, ts, px, st)
        if n_rows % 2_000_000 == 0:
            print(f"[mine] {n_rows} rows {time.time()-t_run:.0f}s "
                  f"trips={n_trips} pops={n_pops} pending={len(pending)}",
                  flush=True)

for pool, lst in list(pending.items()):
    finalize(pool, lst)
pending.clear()

# ── ETH/USD calibration (power of 10 putting the median in 1000..3000) ───────
med = None
if eth_px_by_win:
    v = sorted(eth_px_by_win.values())
    med = v[len(v) // 2]
    for k in range(-24, 25):
        cand = (10.0 ** k) / med
        if 1000 <= cand <= 3000:
            eth_scale = k
            break
print(f"[mine] eth raw median={med} scale=10^{eth_scale}", flush=True)

win_out = {}
for wk, agg in windows.items():
    raw = eth_px_by_win.get(wk)
    eth_usd = (round((10.0 ** eth_scale) / raw, 2)
               if (raw and eth_scale is not None) else None)
    win_out[wk] = agg + [len(win_pools[wk]), 0, eth_usd]
# new pools per window (registry creations)
for cts in creation_ts:
    wk = int(cts // WIN_S)
    if wk in win_out:
        win_out[wk][6] += 1

os.makedirs(OUT, exist_ok=True)
json.dump({"win_s": WIN_S,
           "cols": ["n", "buy_eth", "sell_eth", "buy_n", "sell_n",
                    "distinct_pools", "new_pools", "eth_usd"],
           "windows": {str(k): v for k, v in sorted(win_out.items())}},
          open(os.path.join(OUT, "windows.json"), "w"))

n_written = 0
with gzip.open(os.path.join(OUT, "trips.jsonl.gz"), "wt",
               encoding="utf-8") as g:
    for tr in done:
        if tr["res"] == "dropped":
            continue
        px0 = tr["px0"]
        rec = {"kind": tr["kind"], "pool": tr["pool"],
               "t0": round(tr["t0"], 1), "day": tr["day"],
               "hour": tr["hour"], "win": tr["win"], "age_h": tr["age_h"],
               "npph": tr["npph"], "res": tr["res"],
               "ret20": round((tr["px20"] / px0 - 1) * 100, 2),
               "mfe": round((tr["peak"] / px0 - 1) * 100, 2),
               "mae": round((tr["trough"] / px0 - 1) * 100, 2),
               "rug": int(tr["trough"] <= RUG_FRAC * px0)}
        g.write(json.dumps(rec, separators=(",", ":")) + "\n")
        n_written += 1

print(f"[mine] DONE rows={n_rows} in {time.time()-t_run:.0f}s | "
      f"dip trips={n_trips} pops={n_pops} written={n_written} "
      f"windows={len(win_out)}", flush=True)
