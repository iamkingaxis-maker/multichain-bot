"""Focused entry-miner for ONE copyable winner wallet (8zkgFGVZ...).

Reuses trade_map() from wallet_decode.py (per-token buys/sells/returns) and the
GeckoTerminal minute-OHLC entry-feature reconstruction from mine_wallet_entries.py,
but splits the wallet's CLOSED trips into WINNERS vs LOSERS and computes the
entry-state distributions (dip off 90m high, age, liq, mcap, pc_h1 momentum) for
each group -> find the separators.

Realized data only. Single process, ~3s GT pacing, 429-backoff. Analysis only.

Usage: python scripts/_mine_8zkg_entries.py <WALLET> [sigs=150]
"""
from __future__ import annotations
import json, os, sys, time, statistics
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import wallet_decode as wd                     # trade_map, STABLE via swd
import mine_wallet_entries as mwe              # token_pool_ohlc, entry_features, pctl

# our current gates (comparison readout)
GATE_DIP = -16.0
GATE_AGE_H = 24.0
GATE_MCAP_LO, GATE_MCAP_HI = 500_000, 10_000_000
GATE_LIQ = 25_000


def pc_h1(entry_ts, ohlcv):
    """1h momentum: entry_close / close 60m before entry - 1 (%)."""
    if not ohlcv:
        return None
    rows = sorted(ohlcv, key=lambda r: r[0])
    entry_close = None
    for r in rows:
        if r[0] <= entry_ts:
            entry_close = r[4]
        else:
            break
    if not entry_close:
        return None
    # close nearest to (entry_ts - 3600), within the prior window
    prior = [r for r in rows if r[0] <= entry_ts]
    cand = [r for r in prior if entry_ts - r[0] >= 3600]
    if not cand:
        return None
    base = max(cand, key=lambda r: r[0])  # closest to -60m from below
    if base[4] <= 0:
        return None
    return (entry_close / base[4] - 1.0) * 100


def stats_block(label, feats):
    print(f"\n--- {label}  (n={len(feats)}) ---")
    if not feats:
        print("  (none)")
        return {}
    out = {}
    def line(nm, key, fmt="{:.1f}"):
        v = [f[key] for f in feats if f.get(key) is not None]
        if not v:
            print(f"  {nm:14s} (none)"); return
        p25, med, p75 = mwe.pctl(v, .25), statistics.median(v), mwe.pctl(v, .75)
        out[key] = (p25, med, p75)
        print(f"  {nm:14s} p25={fmt.format(p25)}  median={fmt.format(med)}  p75={fmt.format(p75)}")
    line("dip_90m %", "dip_90m")
    line("age_h", "age_h")
    line("liq $", "liq", "{:,.0f}")
    line("fdv/mcap $", "fdv", "{:,.0f}")
    line("pc_h1 %", "pc_h1")
    line("ret %", "ret")
    return out


def main():
    addr = sys.argv[1]
    sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    print(f"trade_map {addr[:12]} sigs={sigs} ...", file=sys.stderr)
    tok = wd.trade_map(addr, sigs)
    print(f"  {len(tok)} tokens touched", file=sys.stderr)

    # build closed trips: per token, first buy ts + realized ret% + hold-time
    trips = []   # (mint, entry_ts, ret%, hold_min, spent_sol)
    for m, r in tok.items():
        if not r["buys"] or not r["sells"] or not r["spent"]:
            continue
        b0 = min(b[0] for b in r["buys"])
        s_last = max(s[0] for s in r["sells"])
        hold_min = (s_last - b0) / 60.0
        ret = (r["recv"] / r["spent"] - 1) * 100
        trips.append((m, b0, ret, hold_min, r["spent"]))
    print(f"  {len(trips)} closed trips", file=sys.stderr)

    feats = []
    for i, (m, ts, ret, hold_min, spent) in enumerate(trips):
        meta = mwe.token_pool_ohlc(m)   # paced ~5.2s/token internally
        if not meta:
            print(f"  [{i+1}/{len(trips)}] {m[:8]} no pool/ohlc", file=sys.stderr)
            continue
        created, liq, fdv, ohlcv = meta
        f = mwe.entry_features(ts, created, liq, fdv, ohlcv)
        if not f:
            print(f"  [{i+1}/{len(trips)}] {m[:8]} no entry features", file=sys.stderr)
            continue
        f["pc_h1"] = pc_h1(ts, ohlcv)
        f["ret"] = ret
        f["mint"] = m
        f["entry_ts"] = ts
        f["hold_min"] = hold_min
        f["spent_sol"] = spent
        feats.append(f)
        print(f"  [{i+1}/{len(trips)}] {m[:8]} ret={ret:+.1f} dip={f['dip_90m']:+.1f} "
              f"age={f['age_h'] if f['age_h'] is None else round(f['age_h'],1)} "
              f"liq={f['liq']:,.0f} mcap={f['fdv']:,.0f} pch1={f['pc_h1']}", file=sys.stderr)

    json.dump(feats, open(os.path.join(os.path.dirname(HERE), "_7bnaxx_entry_feats.json"), "w"))
    print(f"\nreconstructed {len(feats)}/{len(trips)} entries -> _7bnaxx_entry_feats.json")

    # hold-time distribution (scalper vs runner-rider discriminator)
    hold_all = sorted(f["hold_min"] for f in feats)
    if hold_all:
        print(f"\n=== HOLD-TIME (min) — archetype discriminator ===")
        print(f"  ALL    p25={mwe.pctl(hold_all,.25):.1f}  median={statistics.median(hold_all):.1f}  "
              f"p75={mwe.pctl(hold_all,.75):.1f}  max={max(hold_all):.1f}")
        hw = sorted(f["hold_min"] for f in feats if f["ret"] > 0)
        hl = sorted(f["hold_min"] for f in feats if f["ret"] <= 0)
        if hw:
            print(f"  WINNERS p25={mwe.pctl(hw,.25):.1f} median={statistics.median(hw):.1f} p75={mwe.pctl(hw,.75):.1f}")
        if hl:
            print(f"  LOSERS  p25={mwe.pctl(hl,.25):.1f} median={statistics.median(hl):.1f} p75={mwe.pctl(hl,.75):.1f}")
    sp = sorted(f["spent_sol"] for f in feats)
    if sp:
        print(f"  SIZE SOL/tok p25={mwe.pctl(sp,.25):.2f} median={statistics.median(sp):.2f} p75={mwe.pctl(sp,.75):.2f}")

    wins = [f for f in feats if f["ret"] > 0]
    losses = [f for f in feats if f["ret"] <= 0]
    print(f"\n=== 7BNaxx ENTRY-STATE: WINNERS vs LOSERS  (realized) ===")
    print(f"reconstructed WR = {len(wins)}/{len(feats)} = "
          f"{len(wins)/max(1,len(feats))*100:.0f}%")
    sw = stats_block("WINNERS", wins)
    sl = stats_block("LOSERS", losses)

    # separator readout
    print("\n=== SEPARATORS (winner median vs loser median) ===")
    for key, nm, fmt in [("dip_90m", "dip off 90m high", "{:+.1f}%"),
                         ("age_h", "age (h)", "{:.1f}h"),
                         ("liq", "liquidity $", "{:,.0f}"),
                         ("fdv", "mcap $", "{:,.0f}"),
                         ("pc_h1", "pc_h1 momentum", "{:+.1f}%")]:
        if key in sw and key in sl:
            wmed, lmed = sw[key][1], sl[key][1]
            print(f"  {nm:18s} WIN={fmt.format(wmed)}  LOSE={fmt.format(lmed)}")

    # how many entries pass OUR gates, and their WR
    print("\n=== vs OUR CURRENT GATES (dip<=-16, age>=24h, mcap 500k-10M, liq>=25k) ===")
    def passes(f):
        if f["dip_90m"] > GATE_DIP:
            return False
        if f["age_h"] is None or f["age_h"] < GATE_AGE_H:
            return False
        if not (GATE_MCAP_LO <= f["fdv"] <= GATE_MCAP_HI):
            return False
        if f["liq"] < GATE_LIQ:
            return False
        return True
    passed = [f for f in feats if passes(f)]
    pw = sum(1 for f in passed if f["ret"] > 0)
    print(f"  {len(passed)}/{len(feats)} of its entries pass ALL our gates "
          f"(WR among passed = {pw}/{len(passed) if passed else 0})")
    for key, gate, nm in [("dip_90m", GATE_DIP, "dip<=-16"),
                          ("age_h", GATE_AGE_H, "age>=24h"),
                          ("liq", GATE_LIQ, "liq>=25k")]:
        if key == "dip_90m":
            c = sum(1 for f in feats if f["dip_90m"] <= gate)
        elif key == "age_h":
            c = sum(1 for f in feats if f["age_h"] is not None and f["age_h"] >= gate)
        else:
            c = sum(1 for f in feats if f["liq"] >= gate)
        print(f"    {nm:12s}: {c}/{len(feats)} pass individually")
    inband = sum(1 for f in feats if GATE_MCAP_LO <= f["fdv"] <= GATE_MCAP_HI)
    print(f"    mcap-band  : {inband}/{len(feats)} pass individually")


if __name__ == "__main__":
    main()
