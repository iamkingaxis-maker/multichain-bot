"""RH deep-exit sweep #2: the EXACT runtime moonbag (house-money) barbell shapes.

Sweep #1 approximated the barbell runner with a -15 stop; the runtime moonbag
closes the runner at a BREAKEVEN FLOOR after TP2 (strictly better downside). This
run models that floor precisely so the shipped shape is graded on real tape.
dip<=-25 cohort (the shipped trigger). Same fill model / anchors as sweep #1."""
import bisect, collections, gzip, json, os, time

HIST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
OUT = r"C:\Users\jcole\multichain-bot\scratchpad\deep_exit"
ETH_USD_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"
PRICE_WINDOW_S = 600.0; DEEP_DIP = -25.0; FLOW_WINDOW_S = 120.0
MIN_BUYS_ETH_30S = 0.015; COOLDOWN_S = 600.0; POP_FRAC = 1.35; POP_COOLDOWN_S = 600.0
HORIZON_S = 14400.0; RESOLVE_S = 1200.0; RUG_WINDOW_S = 3600.0; RUG_FRAC = 0.2
MIN_SWAPS = 10; MIN_CUM_ETH = 0.3; ENTRY_HAIRCUT = 1.01; EXIT_HAIRCUT = 0.99; GAS_PP = 0.2
LEG_CAP = 300.0; TP_SLIP = 15.0; DEAD_PNL = -90.0

# moonbag ladder config: tp1/f1 fast harvest, tp2 sells down to mb_frac, then
# the moonbag rides with a breakeven floor (mb_floor) + wide trail (mb_trail).
def MB(tp1, f1, tp2, mb_frac, mb_trail, stop=-15.0, mb_floor=0.0, trail=3.0):
    return dict(tp1=tp1, f1=f1, tp2=tp2, mb_frac=mb_frac, mb_trail=mb_trail,
                mb_floor=mb_floor, stop=stop, trail=trail, prearm=5.0, pregap=2.0,
                kind="mb")
# plain reference ladders (no moonbag) reuse the sweep-1 semantics
def C(tp1, f1, tp2, f2, stop, trail, tbox=None):
    return dict(tp1=tp1, f1=f1, tp2=tp2, f2=f2, stop=stop, trail=trail,
                prearm=5.0, pregap=2.0, tbox=tbox, kind="plain")

CLASSES = {
    # SHIPPED shape + neighborhood (deep barbell, house-money moonbag)
    "mb_60_30_t12": MB(5, .60, 12, .30, 12),   # <- rh_deep_barbell verbatim
    "mb_60_30_t15": MB(5, .60, 12, .30, 15),
    "mb_60_30_t20": MB(5, .60, 12, .30, 20),
    "mb_70_20_t12": MB(5, .70, 12, .20, 12),
    "mb_50_35_t15": MB(5, .50, 12, .35, 15),   # vdeep-scaled (bigger runner)
    "mb_60_30_t12_s12": MB(5, .60, 12, .30, 12, stop=-12.0),
    # references (sweep-1 semantics)
    "fast5_all": C(5, 1.0, 99, .0, -15, 3),
    "scalp":     C(6, .75, 12, .25, -15, 3),
    "patient":   C(8, .34, 25, .33, -15, 10),
    "aged":      C(6, .50, 16, .30, -15, 10),
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
        if c["kind"] == "mb":
            if not s["tp2"] and pnl >= c["tp2"]:
                f = max(0.0, s["rem"] - c["mb_frac"])   # sell down to moonbag
                if f > 0:
                    s["real"] += f * min(pnl, c["tp2"] + TP_SLIP); s["rem"] -= f
                    s["legs"] += 1
                s["tp2"] = True
                if s["rem"] <= 1e-9: s["closed"] = True; s["dt"] = dt
                return
            if s["tp2"]:   # moonbag phase: breakeven floor + wide trail
                if pnl <= c["mb_floor"]: close_all(); return
                if pnl <= s["peak"] - c["mb_trail"]: close_all(); return
                return
            if pnl <= s["peak"] - c["trail"]: close_all(); return   # pre-tp2 trail
        else:
            if not s["tp2"] and pnl >= c["tp2"]:
                f = min(c["f2"], s["rem"])
                s["real"] += f * min(pnl, c["tp2"] + TP_SLIP)
                s["rem"] -= f; s["tp2"] = True; s["legs"] += 1
                if s["rem"] <= 1e-9: s["closed"] = True; s["dt"] = dt
                return
            if pnl <= s["peak"] - c["trail"]: close_all(); return

def ladder_final(s, last_pnl, last_dt):
    if s["closed"]: return
    s["real"] += s["rem"] * min(last_pnl, LEG_CAP)
    s["rem"] = 0.0; s["closed"] = True; s["dt"] = last_dt; s["legs"] += 1

class PoolState:
    __slots__ = ("buf", "flows", "n", "cum_eth", "cd", "cd_pop", "first_px", "max_px")
    def __init__(self):
        self.buf = collections.deque(); self.flows = collections.deque()
        self.n = 0; self.cum_eth = 0.0; self.cd = 0.0; self.cd_pop = 0.0
        self.first_px = None; self.max_px = 0.0

pools = {}; pending = {}
n_rows = 0; last_ts_seen = 0.0; n_cands = n_written = 0
t_run = time.time(); os.makedirs(OUT, exist_ok=True)
gz_out = gzip.open(os.path.join(OUT, "rh_moonbag_cands.jsonl.gz"), "wt", encoding="utf-8")

def write_trip(tr):
    global n_written
    px0 = tr["px0"]
    rec = {"pool": tr["pool"], "day": tr["day"], "dip": tr["dip"], "res": tr["res"],
           "mfe": round((tr["peak_raw"]/px0 - 1)*100, 2)}
    rec["ex"] = {name: round(tr["lad"][name]["real"] - GAS_PP, 2) for name in CLASSES}
    gz_out.write(json.dumps(rec, separators=(",", ":")) + "\n"); n_written += 1

def resolve_rows(pool, ts, px):
    lst = pending.get(pool)
    if not lst: return
    keep = []
    for tr in lst:
        dt = ts - tr["t0"]
        pnl = (px * EXIT_HAIRCUT / tr["px_eff"] - 1) * 100.0
        if dt <= RESOLVE_S and px > tr["peak_raw"]: tr["peak_raw"] = px
        tr["last_pnl"] = pnl; tr["last_dt"] = dt
        if dt > HORIZON_S:
            for s in tr["lad"].values(): ladder_final(s, pnl, dt)
            tr["res"] = tr["res"] or "t4h"; write_trip(tr); continue
        allc = True
        for name, cfg in CLASSES.items():
            ladder_step(cfg, tr["lad"][name], pnl, dt)
            if not tr["lad"][name]["closed"]: allc = False
        if allc and dt > RUG_WINDOW_S:
            tr["res"] = "closed"; write_trip(tr); continue
        keep.append(tr)
    if keep: pending[pool] = keep
    else: pending.pop(pool, None)

def finalize_all(end_ts):
    for pool, lst in list(pending.items()):
        for tr in lst:
            if (end_ts - tr["t0"]) > HORIZON_S + 600.0:
                for s in tr["lad"].values(): ladder_final(s, DEAD_PNL, tr["last_dt"])
                tr["res"] = tr["res"] or "dead"; write_trip(tr)
            elif tr["last_dt"] >= 300.0:
                for s in tr["lad"].values(): ladder_final(s, tr["last_pnl"], tr["last_dt"])
                tr["res"] = tr["res"] or "stale_end"; write_trip(tr)
    pending.clear()

src = os.path.join(HIST, "sweep_logs.jsonl.gz")
with gzip.open(src, "rt", encoding="utf-8") as f:
    for ln in f:
        n_rows += 1
        if n_rows % 2_000_000 == 0:
            print(f"[mb] {n_rows} rows {time.time()-t_run:.0f}s cands={n_cands} "
                  f"written={n_written} pending={len(pending)}", flush=True)
        try: d = json.loads(ln)
        except Exception: continue
        p = d["p"]
        if p == ETH_USD_POOL: continue
        px = d["px"] or 0.0
        if px <= 0: continue
        w = d["w"] or 0.0; ts = est_ts(d["b"])
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
        hi = max(x for _, x in buf)
        dip = (px / hi - 1) * 100.0
        if dip <= DEEP_DIP and ts - st.cd > COOLDOWN_S:
            b30 = sum(ww for tt, kk, ww in fl if kk == "buy" and ts - tt <= 30.0)
            if b30 >= MIN_BUYS_ETH_30S:
                st.cd = ts; n_cands += 1
                tm = time.gmtime(ts)
                tr = {"pool": p, "t0": ts, "px0": px, "px_eff": px*ENTRY_HAIRCUT,
                      "day": time.strftime("%Y-%m-%d", tm), "dip": round(dip, 2),
                      "res": None, "peak_raw": px, "last_pnl": 0.0, "last_dt": 0.0,
                      "lad": {name: ladder_new() for name in CLASSES}}
                pending.setdefault(p, []).append(tr)

finalize_all(last_ts_seen)
gz_out.close()
print(f"[mb] DONE rows={n_rows} in {time.time()-t_run:.0f}s cands={n_cands} written={n_written}", flush=True)
