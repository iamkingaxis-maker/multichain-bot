# Serial-swinger discriminator study (2026-07-03)
# Replicates the swing-latch sim (entry close<=rolling_peak*0.65, TP+25/stop-12/90m timestop on closes,
# peak reset from exit, per-token sequential) and tests first-entry-observable features that
# separate serial swingers (>=3 winning swings) from one-and-done tokens.
import json, glob, math, statistics as st
from datetime import datetime, timezone

BASE = r"C:\Users\jcole\multichain-bot\scratchpad\ripday"
import os
os.chdir(BASE)

# ---------- load bars, merged per pair ----------
bars_by_pair = {}
for f in glob.glob("ohlc2_*.json") + glob.glob("ohlc_*.json"):
    d = json.load(open(f))
    p = d["pair"]
    m = bars_by_pair.setdefault(p, {})
    for b in d["bars"]:
        ts = int(b[0])
        if ts not in m:
            m[ts] = b
pairs = {}
for p, m in bars_by_pair.items():
    bars = [m[t] for t in sorted(m)]
    if len(bars) >= 30:
        pairs[p] = bars

# ---------- meta ----------
meta = json.load(open("token_meta.json"))

# ---------- tapes ----------
def load_tape(pair):
    fn = f"tape_{pair[:8]}.jsonl"
    if not os.path.exists(fn):
        return None
    rows = []
    for line in open(fn, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        ts = datetime.fromisoformat(r["ts"]).timestamp()
        rows.append((ts, r.get("kind"), float(r.get("volume_usd") or 0), r.get("maker")))
    rows.sort()
    return rows

# ---------- swing sim ----------
TP, STOP, TIMESTOP = 0.25, -0.12, 90 * 60

def sim_swings(bars):
    swings = []
    peak = None
    in_pos = False
    entry = entry_ts = None
    trough = None; trough_ts = None
    bounce10_ts = None
    for b in bars:
        ts, o, h, l, c, v = int(b[0]), b[1], b[2], b[3], b[4], b[5]
        if c is None or c <= 0:
            continue
        if not in_pos:
            if peak is not None and c <= peak * 0.65:
                in_pos = True
                entry = c; entry_ts = ts
                trough = c; trough_ts = ts; bounce10_ts = None
                continue
            peak = c if peak is None else max(peak, c)
        else:
            if c < trough:
                trough = c; trough_ts = ts; bounce10_ts = None
            if bounce10_ts is None and c >= trough * 1.10:
                bounce10_ts = ts
            pnl = c / entry - 1
            done = None
            if pnl >= TP: done = "tp"
            elif pnl <= STOP: done = "stop"
            elif ts - entry_ts >= TIMESTOP: done = "time"
            if done:
                swings.append(dict(entry_ts=entry_ts, exit_ts=ts, entry=entry, exit=c,
                                   pnl_pct=pnl * 100, how=done,
                                   trough=trough, trough_ts=trough_ts,
                                   bounce10_min=(bounce10_ts - trough_ts) / 60 if bounce10_ts else None))
                in_pos = False
                peak = c  # reset peak from exit
    return swings

# ---------- features at first-entry time ----------
def feat_bars(bars, t0):
    pre = [b for b in bars if b[0] < t0]
    w60 = [b for b in pre if b[0] >= t0 - 3600]
    out = {}
    out["bars_rate_60m"] = len(w60) / 60.0
    if len(pre) >= 2:
        span_min = (pre[-1][0] - pre[0][0]) / 60.0
        out["bars_rate_full"] = len(pre) / max(span_min, 1)
        out["pre_history_min"] = span_min
    else:
        out["bars_rate_full"] = None; out["pre_history_min"] = None
    if len(w60) >= 5:
        vols = [b[5] for b in w60]
        mv = st.mean(vols)
        out["vol_cv_60m"] = (st.pstdev(vols) / mv) if mv > 0 else None
        out["vol_usd_60m"] = sum(vols)
        rng = [(b[2] - b[3]) / b[4] * 100 for b in w60 if b[4] > 0]
        out["range_mean_60m"] = st.mean(rng) if rng else None
        rets = []
        for i in range(1, len(w60)):
            if w60[i-1][4] > 0:
                rets.append((w60[i][4] / w60[i-1][4] - 1) * 100)
        out["ret_std_60m"] = st.pstdev(rets) if len(rets) >= 3 else None
    else:
        out["vol_cv_60m"] = out["vol_usd_60m"] = out["range_mean_60m"] = out["ret_std_60m"] = None
    # oscillation structure over full pre-history: mean |1m ret| and count of >=10% up-moves within 30m windows
    if len(pre) >= 20:
        rng_all = [(b[2] - b[3]) / b[4] * 100 for b in pre if b[4] > 0]
        out["range_mean_pre"] = st.mean(rng_all)
    else:
        out["range_mean_pre"] = None
    return out

def feat_tape(tape, t0):
    out = {}
    if tape is None:
        return dict(tape_n=None, tape_buyers=None, tape_maxprint=None, tape_buyfrac=None, tape_medbuy=None)
    w = [r for r in tape if t0 - 1800 <= r[0] <= t0 + 60]
    out["tape_n"] = len(w)
    buys = [r for r in w if r[1] == "buy"]
    out["tape_buyers"] = len({r[3] for r in buys})
    out["tape_maxprint"] = max((r[2] for r in w), default=0)
    bu = sum(r[2] for r in buys); se = sum(r[2] for r in w if r[1] == "sell")
    out["tape_buyfrac"] = bu / (bu + se) if (bu + se) > 0 else None
    out["tape_medbuy"] = st.median([r[2] for r in buys]) if buys else None
    return out

# ---------- run ----------
rows = []
for p, bars in pairs.items():
    sw = sim_swings(bars)
    if not sw:
        continue
    wins = [s for s in sw if s["pnl_pct"] > 0]
    t0 = sw[0]["entry_ts"]
    r = dict(pair=p, n_swings=len(sw), n_wins=len(wins),
             serial=len(wins) >= 3,
             first_win=sw[0]["pnl_pct"] > 0,
             first_pnl=sw[0]["pnl_pct"],
             first_bounce10=sw[0]["bounce10_min"],
             first_how=sw[0]["how"],
             t0=t0,
             swings=[(s["pnl_pct"], s["how"]) for s in sw])
    # latch economics: swings until first loss inclusive
    latch = []
    for s in sw:
        latch.append(s["pnl_pct"])
        if s["pnl_pct"] <= 0:
            break
    r["latch_gross"] = sum(latch)
    r["latch_n"] = len(latch)
    r.update(feat_bars(bars, t0))
    m = meta.get(p)
    if m:
        try:
            created = datetime.fromisoformat(m["pool_created_at"].replace("Z", "+00:00")).timestamp()
            r["age_h"] = (t0 - created) / 3600
        except Exception:
            r["age_h"] = None
        r["liq"] = float(m["reserve_usd"]) if m.get("reserve_usd") else None
        r["mcap"] = float(m.get("fdv_usd") or 0) or None
    else:
        r["age_h"] = r["liq"] = r["mcap"] = None
    r.update(feat_tape(load_tape(p), t0))
    rows.append(r)

json.dump(rows, open("_serial_rows.json", "w"), indent=1)

# ---------- summary ----------
n = len(rows)
serial = [r for r in rows if r["serial"]]
print(f"tokens with >=1 swing: {n} (of {len(pairs)} pairs with bars)")
print(f"serial swingers (>=3 winning swings): {len(serial)} = {len(serial)/n*100:.1f}%")
print(f"n_swings dist: ", sorted((r['n_swings'] for r in rows), reverse=True)[:15])
print(f"n_wins dist:   ", sorted((r['n_wins'] for r in rows), reverse=True)[:15])
allsw = [s for r in rows for s in r["swings"]]
print(f"total swings {len(allsw)}, mean gross {st.mean(s[0] for s in allsw):+.2f}")
afterwin = []
for r in rows:
    won = False
    for pnl, how in r["swings"]:
        if won:
            afterwin.append(pnl)
        won = pnl > 0
print(f"after-WIN swings n={len(afterwin)} mean {st.mean(afterwin):+.2f} (motivating sim said +4.09)")
lg = [r["latch_gross"] for r in rows]
print(f"latch per-token gross: mean {st.mean(lg):+.2f} median {st.median(lg):+.2f}")
net = [r["latch_gross"] - 2.6 * r["latch_n"] for r in rows]
print(f"latch per-token net(2.6pp/swing): mean {st.mean(net):+.2f}")
print(f"first-swing win rate: {sum(1 for r in rows if r['first_win'])/n*100:.1f}%")
