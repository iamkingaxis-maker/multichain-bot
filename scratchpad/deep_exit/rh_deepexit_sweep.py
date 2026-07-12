"""DEEP-COHORT exit-ladder sweep for RH (2026-07-12).

Re-streams rh_history/sweep_logs.jsonl.gz (10.36M swaps) exactly like
scratchpad/rh_factory/factory_mine.py, but:
  1. Keeps ONLY deep-dip entries (dip <= DEEP_DIP, the deep-capitulation cohort
     that is the edge on both chains).
  2. Runs a GRID of exit ladders per candidate (not just scalp/aged/tbox) to
     find the exit that maximizes realized capture of the deep-flush bounce
     while minimizing giveback.
  3. Emits dip depth + day/win for depth-band and 4-half OOS aggregation, plus
     mfe/mae and per-variant ret/hold.

Fill model + ladder semantics are byte-identical to factory_mine.py
(entry px*1.01, exit px*0.99, 0.2pp gas, next-observed-swap fills, LEG_CAP 300,
TP_SLIP 15, DEAD_PNL -90 for pools that never trade again). Honest caveat: the
tape gives real continuation (unlike the SOL summary-stat replay), but is
maker-less and WETH-substrate only.
"""
import bisect, collections, gzip, json, os, time

HIST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
OUT = r"C:\Users\jcole\multichain-bot\scratchpad\deep_exit"
ETH_USD_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"

PRICE_WINDOW_S = 600.0
DEEP_DIP = -20.0          # deep-capitulation entry cut (%, off 10-min high)
FLOW_WINDOW_S = 120.0
MIN_BUYS_ETH_30S = 0.015  # ~$25 demand floor (same as factory)
COOLDOWN_S = 600.0
POP_FRAC = 1.35
POP_COOLDOWN_S = 600.0
HORIZON_S = 14400.0
RESOLVE_S = 1200.0
RUG_WINDOW_S = 3600.0
RUG_FRAC = 0.2
MIN_SWAPS = 10
MIN_CUM_ETH = 0.3
ENTRY_HAIRCUT = 1.01
EXIT_HAIRCUT = 0.99
GAS_PP = 0.2

# ── exit-ladder GRID (prearm=5/pregap=2 pre-TP1 trail on all; PM order) ──────
def C(tp1, f1, tp2, f2, stop, trail, tbox=None):
    return dict(tp1=tp1, f1=f1, tp2=tp2, f2=f2, stop=stop, trail=trail,
                prearm=5.0, pregap=2.0, tbox=tbox)

CLASSES = {
    # references (live/factory shapes)
    "scalp":       C(6, .75, 12, .25, -15, 3),
    "aged":        C(6, .50, 16, .30, -15, 10),
    # fast harvest (sell all / near-all at TP1)
    "fast5_all":   C(5, 1.0, 99, .0, -15, 3),
    "fast6_all":   C(6, 1.0, 99, .0, -15, 3),
    "fast8_all":   C(8, 1.0, 99, .0, -15, 3),
    "fast5_90":    C(5, .90, 10, .10, -15, 3),
    "fast6_90":    C(6, .90, 12, .10, -15, 3),
    # time-boxed fast (pops die fast)
    "tbox5_10m":   C(5, .90, 10, .10, -12, 3, tbox=600.0),
    "tbox5_5m":    C(5, .90, 10, .10, -12, 3, tbox=300.0),
    "tbox6_10m":   C(6, .75, 12, .25, -15, 3, tbox=600.0),
    # trail-width sweep (fixed tp1 6/.75 tp2 12/.25 stop -15)
    "trail2":      C(6, .75, 12, .25, -15, 2),
    "trail5":      C(6, .75, 12, .25, -15, 5),
    "trail8":      C(6, .75, 12, .25, -15, 8),
    "trail12":     C(6, .75, 12, .25, -15, 12),
    # patient / let-run
    "patient":     C(8, .34, 25, .33, -15, 10),
    "patient_wd":  C(6, .34, 30, .33, -18, 15),
    "runner40":    C(6, .50, 40, .20, -15, 12),
    # barbell (fast harvest bulk + small wide-trailed runner)
    "barbell8020": C(5, .80, 50, .20, -15, 15),
    "barbell7030": C(5, .70, 40, .30, -15, 12),
    "barbell9010": C(5, .90, 60, .10, -12, 20),
    # stop sweep
    "stop12":      C(6, .75, 12, .25, -12, 3),
    "stop20":      C(6, .75, 12, .25, -20, 3),
}

anchors = json.load(open(os.path.join(HIST, "anchors.json")))
A_B = [a[0] for a in anchors]; A_T = [a[1] for a in anchors]

def est_ts(block):
    i = bisect.bisect_right(A_B, block) - 1
    if i < 0: return A_T[0]
    if i >= len(A_B) - 1: return A_T[-1] + (block - A_B[-1]) * 0.1
    b0, t0, b1, t1 = A_B[i], A_T[i], A_B[i+1], A_T[i+1]
    return t0 + (t1 - t0) * (block - b0) / max(1, b1 - b0)

reg_ts = {}
for line in open(os.path.join(HIST, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line); reg_ts[d["pool"]] = d["ts"]

LEG_CAP = 300.0
TP_SLIP = 15.0
DEAD_PNL = -90.0

def ladder_new():
    return {"rem": 1.0, "real": 0.0, "peak": -100.0, "tp1": False,
            "tp2": False, "closed": False, "dt": 0.0, "legs": 0}

def ladder_step(c, s, pnl, dt):
    if s["closed"]: return
    pnl = min(pnl, LEG_CAP)
    if pnl > s["peak"]: s["peak"] = pnl
    def close_all():
        s["real"] += s["rem"] * pnl; s["rem"] = 0.0
        s["closed"] = True; s["dt"] = dt; s["legs"] += 1
    if pnl <= c["stop"]: close_all(); return
    if c["tbox"] is not None and dt >= c["tbox"]: close_all(); return
    if not s["tp1"]:
        if s["peak"] >= c["prearm"] and pnl <= s["peak"] - c["pregap"]:
            close_all(); return
        if pnl >= c["tp1"]:
            f = min(c["f1"], s["rem"])
            s["real"] += f * min(pnl, c["tp1"] + TP_SLIP)
            s["rem"] -= f; s["tp1"] = True; s["legs"] += 1
            if s["rem"] <= 1e-9: s["closed"] = True; s["dt"] = dt
            return
    if s["tp1"] and not s["closed"]:
        if not s["tp2"] and pnl >= c["tp2"]:
            f = min(c["f2"], s["rem"])
            s["real"] += f * min(pnl, c["tp2"] + TP_SLIP)
            s["rem"] -= f; s["tp2"] = True; s["legs"] += 1
            if s["rem"] <= 1e-9: s["closed"] = True; s["dt"] = dt
            return
        if pnl <= s["peak"] - c["trail"]:
            close_all(); return

def ladder_final(s, last_pnl, last_dt):
    if s["closed"]: return
    s["real"] += s["rem"] * min(last_pnl, LEG_CAP)
    s["rem"] = 0.0; s["closed"] = True; s["dt"] = last_dt; s["legs"] += 1

class PoolState:
    __slots__ = ("buf", "flows", "n", "cum_eth", "cd", "cd_pop",
                 "first_px", "max_px", "pop_ts", "pop_mag")
    def __init__(self):
        self.buf = collections.deque(); self.flows = collections.deque()
        self.n = 0; self.cum_eth = 0.0; self.cd = 0.0; self.cd_pop = 0.0
        self.first_px = None; self.max_px = 0.0; self.pop_ts = None; self.pop_mag = None

pools = {}; pending = {}
n_rows = 0; last_ts_seen = 0.0; n_cands = n_written = 0
t_run = time.time(); os.makedirs(OUT, exist_ok=True)
gz_out = gzip.open(os.path.join(OUT, "rh_deep_cands.jsonl.gz"), "wt", encoding="utf-8")

def write_trip(tr):
    global n_written
    px0 = tr["px0"]
    rec = {k: tr[k] for k in ("pool", "day", "hour", "win", "age_h",
                              "dip", "cum_eth", "n_sw", "arc", "pop_ago",
                              "pop_mag", "res")}
    rec["mfe"] = round((tr["peak_raw"] / px0 - 1) * 100, 2)
    rec["mae"] = round((tr["trough"] / px0 - 1) * 100, 2)
    rec["rug"] = int(tr["trough"] <= RUG_FRAC * px0)
    ex = {}
    for name in CLASSES:
        s = tr["lad"][name]
        ex[name] = round(s["real"] - GAS_PP, 2)
    rec["ex"] = ex
    gz_out.write(json.dumps(rec, separators=(",", ":")) + "\n")
    n_written += 1

def resolve_rows(pool, ts, px):
    lst = pending.get(pool)
    if not lst: return
    keep = []
    for tr in lst:
        dt = ts - tr["t0"]
        pnl = (px * EXIT_HAIRCUT / tr["px_eff"] - 1) * 100.0
        if dt <= RUG_WINDOW_S and px < tr["trough"]: tr["trough"] = px
        if dt <= RESOLVE_S and px > tr["peak_raw"]: tr["peak_raw"] = px
        tr["last_pnl"] = pnl; tr["last_dt"] = dt; tr["last_px"] = px
        if dt > HORIZON_S:
            for s in tr["lad"].values(): ladder_final(s, pnl, dt)
            tr["res"] = "t4h" if tr["res"] is None else tr["res"]
            write_trip(tr); continue
        all_closed = True
        for name, cfg in CLASSES.items():
            s = tr["lad"][name]
            ladder_step(cfg, s, pnl, dt)
            if not s["closed"]: all_closed = False
        if all_closed and dt > RUG_WINDOW_S:
            tr["res"] = "closed"; write_trip(tr); continue
        keep.append(tr)
    if keep: pending[pool] = keep
    else: pending.pop(pool, None)

def finalize_all(stream_end_ts):
    for pool, lst in list(pending.items()):
        for tr in lst:
            died = (stream_end_ts - tr["t0"]) > HORIZON_S + 600.0
            if died:
                for s in tr["lad"].values(): ladder_final(s, DEAD_PNL, tr["last_dt"])
                tr["res"] = "dead" if tr["res"] is None else tr["res"]
                write_trip(tr)
            elif tr["last_dt"] >= 300.0:
                for s in tr["lad"].values(): ladder_final(s, tr["last_pnl"], tr["last_dt"])
                tr["res"] = "stale_end" if tr["res"] is None else tr["res"]
                write_trip(tr)
    pending.clear()

def flows_sums(fl, ts):
    b30 = 0.0; nb30 = 0
    for t, k, w in fl:
        dt = ts - t
        if dt > 30.0: continue
        if k == "buy": b30 += w; nb30 += 1
    return b30, nb30

src = os.path.join(HIST, "sweep_logs.jsonl.gz")
with gzip.open(src, "rt", encoding="utf-8") as f:
    for ln in f:
        n_rows += 1
        if n_rows % 2_000_000 == 0:
            print(f"[deepexit] {n_rows} rows {time.time()-t_run:.0f}s "
                  f"cands={n_cands} written={n_written} pending={len(pending)}",
                  flush=True)
        try: d = json.loads(ln)
        except Exception: continue
        p = d["p"]
        if p == ETH_USD_POOL: continue
        px = d["px"] or 0.0
        if px <= 0: continue
        w = d["w"] or 0.0
        ts = est_ts(d["b"])
        if ts > last_ts_seen: last_ts_seen = ts
        st = pools.get(p)
        if st is None:
            st = pools[p] = PoolState(); st.first_px = px
        st.n += 1; st.cum_eth += w
        if px > st.max_px: st.max_px = px
        resolve_rows(p, ts, px)
        buf = st.buf; buf.append((ts, px))
        while buf and ts - buf[0][0] > PRICE_WINDOW_S: buf.popleft()
        fl = st.flows; fl.append((ts, d["k"], w))
        while fl and ts - fl[0][0] > FLOW_WINDOW_S: fl.popleft()
        if st.n < MIN_SWAPS or st.cum_eth < MIN_CUM_ETH or len(buf) < 5: continue
        if buf[-1][0] - buf[0][0] < 120.0: continue
        hi = max(x for _, x in buf); lo = min(x for _, x in buf)
        if px >= lo * POP_FRAC and ts - st.cd_pop > POP_COOLDOWN_S:
            st.cd_pop = ts; st.pop_ts = ts; st.pop_mag = round((px/lo - 1)*100, 1)
        dip = (px / hi - 1) * 100.0
        # DEEP candidate only
        if dip <= DEEP_DIP and ts - st.cd > COOLDOWN_S:
            b30, nb30 = flows_sums(fl, ts)
            if b30 >= MIN_BUYS_ETH_30S:
                st.cd = ts; n_cands += 1
                age_h = ((ts - reg_ts[p]) / 3600.0 if p in reg_ts else None)
                tm = time.gmtime(ts)
                tr = {"pool": p, "t0": ts, "px0": px, "px_eff": px*ENTRY_HAIRCUT,
                      "day": time.strftime("%Y-%m-%d", tm), "hour": tm.tm_hour,
                      "win": int(ts // 1800),
                      "age_h": (round(age_h, 3) if age_h is not None else None),
                      "dip": round(dip, 2), "cum_eth": round(st.cum_eth, 2),
                      "n_sw": st.n,
                      "arc": (round((px/st.first_px - 1)*100, 1) if st.first_px else None),
                      "pop_ago": (round(ts - st.pop_ts, 1) if st.pop_ts else None),
                      "pop_mag": st.pop_mag, "res": None,
                      "peak_raw": px, "trough": px, "last_pnl": 0.0, "last_dt": 0.0,
                      "lad": {name: ladder_new() for name in CLASSES}}
                pending.setdefault(p, []).append(tr)

finalize_all(last_ts_seen)
gz_out.close()
print(f"[deepexit] DONE rows={n_rows} in {time.time()-t_run:.0f}s | "
      f"cands={n_cands} written={n_written}", flush=True)
