"""LOCAL ONLY: entry-timing recon = join tape buys x GT minute OHLC.

For every harvested io BUY ($30..$3000, maker present) on a token with an
ohlc_{mint8}.json file spanning the buy minute, emit one rip_recon.jsonl line.
Also processes rip_artifact_buys.json (bt epoch, sol size -> usd via sol_usd_minute).
"""
import glob, json, os
from bisect import bisect_right
from datetime import datetime, timezone

OUT = "scratchpad/ripday"

# load ohlc
OHLC = {}
for p in glob.glob(os.path.join(OUT, "ohlc_*.json")):
    try:
        d = json.load(open(p))
    except Exception:
        continue
    if d.get("bars"):
        OHLC[d["token"]] = d

SOL = None
try:
    SOL = json.load(open(os.path.join(OUT, "sol_usd_minute.json")))["bars"]
except Exception:
    pass

def sol_px(ep):
    if not SOL:
        return None
    idx = [b[0] for b in SOL]
    i = bisect_right(idx, ep) - 1
    return SOL[i][4] if i >= 0 else None

def recon_one(tok, ep, usd, wallet, sym, pair, src):
    d = OHLC.get(tok)
    if not d:
        return None
    bars = d["bars"]
    ts_list = [b[0] for b in bars]
    i = bisect_right(ts_list, ep) - 1
    if i < 0 or ep - ts_list[i] > 120:
        return None  # buy outside bar coverage
    entry_px = bars[i][4]
    if not entry_px:
        return None
    def pct(x):
        return round((x / entry_px - 1) * 100, 2)
    # forward windows
    def fwd(mins):
        j2 = bisect_right(ts_list, ep + mins * 60)
        seg = bars[i + 1:j2]
        if not seg:
            return None, None
        return pct(min(b[3] for b in seg)), pct(max(b[2] for b in seg))
    low15, _ = fwd(15)
    low30, _ = fwd(30)
    low90, hi90 = fwd(90)
    fwd_min6, fwd_max6 = fwd(360)
    # backward 90m context
    j0 = bisect_right(ts_list, ep - 90 * 60)
    back = bars[max(0, j0 - 1):i + 1]
    dip_from_high = None; posr = None; back_lo = None
    if back:
        hi = max(b[2] for b in back); lo = min(b[3] for b in back)
        dip_from_high = pct(hi)      # negative if entry below prior high... pct(hi) = hi vs entry
        back_lo = pct(lo)
        if hi > lo:
            posr = round((entry_px - lo) / (hi - lo), 3)
    ev = d.get("event_ts") or 0
    return {
        "wallet": wallet, "token": tok, "sym": sym, "pair": pair,
        "ts": datetime.fromtimestamp(ep, timezone.utc).isoformat()[:19],
        "usd": round(usd, 2), "src": src,
        "entry_px": entry_px,
        "fwd_low15_pct": low15, "fwd_low30_pct": low30, "fwd_low90_pct": low90,
        "fwd_hi90_pct": hi90, "fwd_max6h_pct": fwd_max6, "fwd_min6h_pct": fwd_min6,
        "prior90m_high_vs_entry_pct": dip_from_high,
        "prior90m_low_vs_entry_pct": back_lo,
        "pos_in_prior90m_range": posr,
        "mins_from_event": round((ep - ev) / 60.0, 1) if ev else None,
        "fwd_coverage_mins": round((bars[-1][0] - ep) / 60.0, 1),
    }

n_out = 0
with open(os.path.join(OUT, "rip_recon.jsonl"), "w") as f:
    # tape buys
    for tp in glob.glob(os.path.join(OUT, "tape_*.jsonl")):
        for line in open(tp, encoding="ascii", errors="replace"):
            try:
                t = json.loads(line)
            except Exception:
                continue
            if t["kind"] != "buy" or not t.get("maker"):
                continue
            usd = t.get("volume_usd") or 0
            if not (30 <= usd <= 3000):
                continue
            ep = int(datetime.fromisoformat(t["ts"]).timestamp())
            r = recon_one(t["token"], ep, usd, t["maker"], t.get("sym"), t["pair"], "io_tape")
            if r:
                f.write(json.dumps(r) + "\n"); n_out += 1
    # artifact buys
    try:
        awb = json.load(open(os.path.join(OUT, "rip_artifact_buys.json")))
        for wal, buys in awb.items():
            for b in buys:
                px = sol_px(b["bt"]) or 150.0
                r = recon_one(b["mint"], b["bt"], b["sol"] * px, wal, None, None, "artifact_0629")
                if r:
                    f.write(json.dumps(r) + "\n"); n_out += 1
    except FileNotFoundError:
        pass

print("rip_recon.jsonl lines:", n_out, "| ohlc tokens:", len(OHLC))
