"""RH CANDIDATE FACTORY mining pass — extends scratchpad/rh_regime/mine_regimes.py.

One stream over rh_history/sweep_logs.jsonl.gz (10.36M swaps, all WETH pools).
Differences vs the regime mine (which resolved +20m mark-to-market only):

1. LOOSE candidate trigger (a SUPERSET for offline threshold sweeps):
   dip >= 6% off the 10-min high (was 12%) and last-30s buys >= 0.015 ETH
   (~$25, was 0.03) — the sweep re-applies tighter cuts offline.
2. RICH entry stamps: dip depth, pool age, 30s/120s buy/sell ETH + counts,
   session cum volume (liq proxy — real liq is not in the sweep rows),
   launch-arc position (vs session first px / session high), pop recency
   (seconds since a +35% pop and its magnitude -> the pop-retrace family is
   a FILTER over this file, not a separate mine).
3. REALISTIC EXITS: every candidate is run through THREE exit-ladder
   simulators mirroring the paper lane's PerBotPositionManager semantics
   (TP1 partial, TP2 partial, post-TP1 trail, pre-TP1 peak-armed trail
   arm>=5/gap2, hard stop, optional time box), with conservative fill
   haircuts: entry px*1.01, every exit px*0.99 (>=2% round trip — the
   lane's MAX_RT_COST_PCT=6 gate would allow worse, we assume the minimum
   the task mandates), minus 0.2pp flat gas (4 tx * $0.01 on $25).
   Stops/TPs fill at the NEXT OBSERVED swap price (gap-through-stop = the
   catastrophic fill is kept, never the stop price).

Exit classes:
  scalp = tp1 +6 (75%) / tp2 +12 (25%) / stop -15 / trail 3pp   (young_v1)
  aged  = tp1 +6 (50%) / tp2 +16 (30%) / stop -15 / trail 10pp  (rh_aged_hold)
  tbox  = tp1 +5 (90%) / tp2 +10 (10%) / stop -8  / 20m time box (launch-scalp shape)

Resolution: ladders run to +4h; stream/pool silence -> close at last px if
>=300s after entry (res="stale") else dropped. ret20/mfe/mae (+20m) and the
60m rug flag are kept for comparability with the regime mine.

Output: scratchpad/rh_factory/candidates.jsonl.gz (one row per candidate).
"""
import bisect
import collections
import gzip
import json
import os
import time

HIST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_factory"
ETH_USD_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"

PRICE_WINDOW_S = 600.0
DIP_FRAC = 0.94           # <= 6% off the 10-min high (loose superset)
POP_FRAC = 1.35           # >= 35% off the 10-min low (same as regime mine)
FLOW_WINDOW_S = 120.0     # keep 120s of flows; 30s sums scanned at entry
MIN_BUYS_ETH_30S = 0.015  # ~$25 (loose superset)
COOLDOWN_S = 600.0
POP_COOLDOWN_S = 600.0
HORIZON_S = 14400.0       # ladders run to +4h
RESOLVE_S = 1200.0        # ret20/mfe/mae window (comparability)
RUG_WINDOW_S = 3600.0
RUG_FRAC = 0.2
MIN_SWAPS = 10
MIN_CUM_ETH = 0.3
ENTRY_HAIRCUT = 1.01      # pay 1% above observed px
EXIT_HAIRCUT = 0.99       # receive 1% below observed px
GAS_PP = 0.2              # flat pp per trip (4 tx * $0.01 on $25)

CLASSES = {
    "scalp": dict(tp1=6.0, f1=0.75, tp2=12.0, f2=0.25, stop=-15.0,
                  trail=3.0, prearm=5.0, pregap=2.0, tbox=None),
    "aged":  dict(tp1=6.0, f1=0.50, tp2=16.0, f2=0.30, stop=-15.0,
                  trail=10.0, prearm=5.0, pregap=2.0, tbox=None),
    "tbox":  dict(tp1=5.0, f1=0.90, tp2=10.0, f2=0.10, stop=-8.0,
                  trail=3.0, prearm=5.0, pregap=2.0, tbox=1200.0),
}

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


reg_ts = {}
for line in open(os.path.join(HIST, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    reg_ts[d["pool"]] = d["ts"]
creation_ts = sorted(reg_ts.values())


def npph_at(ts):
    return (bisect.bisect_left(creation_ts, ts)
            - bisect.bisect_left(creation_ts, ts - 3600.0))


# ── exit-ladder state machine (mirrors PerBotPositionManager order) ─────────
def ladder_new():
    return {"rem": 1.0, "real": 0.0, "peak": -100.0, "tp1": False,
            "tp2": False, "closed": False, "dt": 0.0, "legs": 0}


LEG_CAP = 300.0     # no single fill books more than +300% (phantom-print
                    # guard: V2 px=|wnet|/|tnet| glitch rows print 1e6x)
TP_SLIP = 15.0      # TP fills book at most threshold+15pp: a TP decision is
                    # made AT the threshold; crediting a whole phantom spike
                    # to a TP leg is fantasy fill quality


def ladder_step(c, s, pnl, dt):
    """One observed price sample -> ladder actions. pnl is NET (haircuts in)."""
    if s["closed"]:
        return
    pnl = min(pnl, LEG_CAP)
    if pnl > s["peak"]:
        s["peak"] = pnl

    def close_all():
        s["real"] += s["rem"] * pnl
        s["rem"] = 0.0
        s["closed"] = True
        s["dt"] = dt
        s["legs"] += 1

    if pnl <= c["stop"]:
        close_all(); return
    if c["tbox"] is not None and dt >= c["tbox"]:
        close_all(); return
    if not s["tp1"]:
        # pre-TP1 peak-armed trail (PM: arm at peak>=5, fire at peak-2)
        if s["peak"] >= c["prearm"] and pnl <= s["peak"] - c["pregap"]:
            close_all(); return
        if pnl >= c["tp1"]:
            f = min(c["f1"], s["rem"])
            s["real"] += f * min(pnl, c["tp1"] + TP_SLIP)
            s["rem"] -= f
            s["tp1"] = True
            s["legs"] += 1
            if s["rem"] <= 1e-9:
                s["closed"] = True; s["dt"] = dt
            return  # PM books one action per tick
    if s["tp1"] and not s["closed"]:
        if not s["tp2"] and pnl >= c["tp2"]:
            f = min(c["f2"], s["rem"])
            s["real"] += f * min(pnl, c["tp2"] + TP_SLIP)
            s["rem"] -= f
            s["tp2"] = True
            s["legs"] += 1
            if s["rem"] <= 1e-9:
                s["closed"] = True; s["dt"] = dt
            return
        if pnl <= s["peak"] - c["trail"]:
            close_all(); return


DEAD_PNL = -90.0    # a pool that never trades again = the bag is worthless;
                    # -90 (not -100) allows for scraping the last drops of a
                    # drained book. THE dead-pool-masking fix: v1 booked these
                    # at the last observed px as if someone would buy it.


def ladder_final(s, last_pnl, last_dt):
    """Force-close remainder at the last observed sample (stale/timeout)."""
    if s["closed"]:
        return
    s["real"] += s["rem"] * min(last_pnl, LEG_CAP)
    s["rem"] = 0.0
    s["closed"] = True
    s["dt"] = last_dt
    s["legs"] += 1


# ── pool state ───────────────────────────────────────────────────────────────
class PoolState:
    __slots__ = ("buf", "flows", "n", "cum_eth", "cd", "cd_pop",
                 "first_px", "max_px", "pop_ts", "pop_mag")

    def __init__(self):
        self.buf = collections.deque()     # (ts, px)
        self.flows = collections.deque()   # (ts, kind, w)
        self.n = 0
        self.cum_eth = 0.0
        self.cd = 0.0
        self.cd_pop = 0.0
        self.first_px = None
        self.max_px = 0.0
        self.pop_ts = None
        self.pop_mag = None


pools = {}
pending = {}          # pool -> [trip dicts]
n_rows = 0
last_ts_seen = 0.0
n_cands = n_pops = n_written = 0
t_run = time.time()
os.makedirs(OUT, exist_ok=True)
gz_out = gzip.open(os.path.join(OUT, "candidates.jsonl.gz"), "wt",
                   encoding="utf-8")


def write_trip(tr):
    global n_written
    px0 = tr["px0"]
    rec = {k: tr[k] for k in ("pool", "day", "hour", "win", "age_h", "npph",
                              "dip", "b30", "s30", "nb30", "ns30", "b120",
                              "s120", "nb120", "cum_eth", "n_sw", "arc",
                              "athdd", "pop_ago", "pop_mag", "res")}
    rec["t0"] = round(tr["t0"], 1)
    rec["ret20"] = (round((tr["px20"] / px0 - 1) * 100, 2)
                    if tr["px20"] else None)
    rec["mfe"] = round((tr["peak_raw"] / px0 - 1) * 100, 2)
    rec["mae"] = round((tr["trough"] / px0 - 1) * 100, 2)
    rec["rug"] = int(tr["trough"] <= RUG_FRAC * px0)
    for name in CLASSES:
        s = tr["lad"][name]
        rec[name] = {"ret": round(s["real"] - GAS_PP, 2),
                     "hold": round(s["dt"], 1), "legs": s["legs"]}
    gz_out.write(json.dumps(rec, separators=(",", ":")) + "\n")
    n_written += 1


def resolve_rows(pool, ts, px):
    lst = pending.get(pool)
    if not lst:
        return
    keep = []
    for tr in lst:
        dt = ts - tr["t0"]
        pnl = (px * EXIT_HAIRCUT / tr["px_eff"] - 1) * 100.0
        # mfe/mae/ret20/rug tracking (raw px, comparability with regime mine)
        if dt <= RUG_WINDOW_S and px < tr["trough"]:
            tr["trough"] = px
        if dt <= RESOLVE_S and px > tr["peak_raw"]:
            tr["peak_raw"] = px
        if tr["px20"] is None and dt >= RESOLVE_S:
            tr["px20"] = px
        tr["last_pnl"] = pnl
        tr["last_dt"] = dt
        tr["last_px"] = px
        if dt > HORIZON_S:
            for s in tr["lad"].values():
                ladder_final(s, pnl, dt)
            tr["res"] = "t4h" if tr["res"] is None else tr["res"]
            write_trip(tr)
            continue
        all_closed = True
        for name, cfg in CLASSES.items():
            s = tr["lad"][name]
            ladder_step(cfg, s, pnl, dt)
            if not s["closed"]:
                all_closed = False
        if all_closed and tr["px20"] is not None:
            tr["res"] = "closed"
            # still need rug window for the rug flag; keep until 60m
            if dt > RUG_WINDOW_S:
                write_trip(tr)
                continue
        keep.append(tr)
    if keep:
        pending[pool] = keep
    else:
        pending.pop(pool, None)


def finalize_all(stream_end_ts):
    """End-of-stream booking. TWO kinds of unresolved trip (the v1 bug
    conflated them and booked both at the last observed px):
      DEAD: the POOL went silent long before the stream ended (no sample for
            > horizon+10m after entry). Nobody ever traded it again — the
            remaining bag is booked at DEAD_PNL (res="dead"). This is the
            rug/abandonment reality the +20m mark and v1 both masked.
      STALE_END: the STREAM ended inside the trip's horizon — outcome truly
            unknown; book remainder at the last observed px (res="stale_end")
            if the trip had >=300s of tape, else drop (regime-mine rule)."""
    for pool, lst in list(pending.items()):
        for tr in lst:
            died = (stream_end_ts - tr["t0"]) > HORIZON_S + 600.0
            if died:
                for s in tr["lad"].values():
                    ladder_final(s, DEAD_PNL, tr["last_dt"])
                if tr["px20"] is None:
                    tr["px20"] = tr.get("last_px")
                tr["res"] = "dead" if tr["res"] is None else tr["res"]
                write_trip(tr)
            elif tr["last_dt"] >= 300.0:
                for s in tr["lad"].values():
                    ladder_final(s, tr["last_pnl"], tr["last_dt"])
                if tr["px20"] is None:
                    tr["px20"] = tr.get("last_px")
                tr["res"] = "stale_end" if tr["res"] is None else tr["res"]
                write_trip(tr)
            # else dropped (stream ended <5 min into the trip)
    pending.clear()


def flows_sums(fl, ts):
    b30 = s30 = b120 = s120 = 0.0
    nb30 = ns30 = nb120 = 0
    for t, k, w in fl:
        dt = ts - t
        if dt > FLOW_WINDOW_S:
            continue
        if k == "buy":
            b120 += w
            nb120 += 1
            if dt <= 30.0:
                b30 += w
                nb30 += 1
        else:
            s120 += w
            if dt <= 30.0:
                s30 += w
                ns30 += 1
    return b30, s30, nb30, ns30, b120, s120, nb120


src = os.path.join(HIST, "sweep_logs.jsonl.gz")
with gzip.open(src, "rt", encoding="utf-8") as f:
    for ln in f:
        n_rows += 1
        if n_rows % 2_000_000 == 0:
            print(f"[factory] {n_rows} rows {time.time()-t_run:.0f}s "
                  f"cands={n_cands} pops={n_pops} written={n_written} "
                  f"pending={len(pending)}", flush=True)
        try:
            d = json.loads(ln)
        except Exception:
            continue
        p = d["p"]
        if p == ETH_USD_POOL:
            continue
        px = d["px"] or 0.0
        if px <= 0:
            continue
        w = d["w"] or 0.0
        ts = est_ts(d["b"])
        if ts > last_ts_seen:
            last_ts_seen = ts
        st = pools.get(p)
        if st is None:
            st = pools[p] = PoolState()
            st.first_px = px
        st.n += 1
        st.cum_eth += w
        if px > st.max_px:
            st.max_px = px
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
        # pop event tracking (own cooldown; feeds pop_ago/pop_mag stamps)
        if px >= lo * POP_FRAC and ts - st.cd_pop > POP_COOLDOWN_S:
            st.cd_pop = ts
            st.pop_ts = ts
            st.pop_mag = round((px / lo - 1) * 100, 1)
            n_pops += 1
        # loose dip candidate
        if px <= hi * DIP_FRAC and ts - st.cd > COOLDOWN_S:
            b30, s30, nb30, ns30, b120, s120, nb120 = flows_sums(fl, ts)
            if b30 >= MIN_BUYS_ETH_30S:
                st.cd = ts
                n_cands += 1
                age_h = ((ts - reg_ts[p]) / 3600.0 if p in reg_ts else None)
                tm = time.gmtime(ts)
                tr = {"pool": p, "t0": ts, "px0": px,
                      "px_eff": px * ENTRY_HAIRCUT,
                      "day": time.strftime("%Y-%m-%d", tm),
                      "hour": tm.tm_hour, "win": int(ts // 1800),
                      "age_h": (round(age_h, 3) if age_h is not None
                                else None),
                      "npph": npph_at(ts),
                      "dip": round((px / hi - 1) * 100, 2),
                      "b30": round(b30, 4), "s30": round(s30, 4),
                      "nb30": nb30, "ns30": ns30,
                      "b120": round(b120, 4), "s120": round(s120, 4),
                      "nb120": nb120,
                      "cum_eth": round(st.cum_eth, 2), "n_sw": st.n,
                      "arc": (round((px / st.first_px - 1) * 100, 1)
                              if st.first_px else None),
                      "athdd": (round((px / st.max_px - 1) * 100, 1)
                                if st.max_px else None),
                      "pop_ago": (round(ts - st.pop_ts, 1)
                                  if st.pop_ts else None),
                      "pop_mag": st.pop_mag,
                      "px20": None, "res": None,
                      "peak_raw": px, "trough": px,
                      "last_pnl": 0.0, "last_dt": 0.0,
                      "lad": {name: ladder_new() for name in CLASSES}}
                pending.setdefault(p, []).append(tr)

finalize_all(last_ts_seen)
gz_out.close()
print(f"[factory] DONE rows={n_rows} in {time.time()-t_run:.0f}s | "
      f"cands={n_cands} pops={n_pops} written={n_written}", flush=True)
