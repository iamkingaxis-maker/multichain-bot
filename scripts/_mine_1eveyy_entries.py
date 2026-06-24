"""Focused entry-miner for ONE copyable winner wallet — parameterized.

Reuses _mine_8zkg_entries.py method verbatim (trade_map + GeckoTerminal minute-OHLC
entry-feature reconstruction), but derives the output filename from the wallet so it
can be run for 1eveYY (and any other wallet) without clobbering the 8zkg artifact.

Realized data only. Single process, ~3s GT pacing, 429-backoff (inside mwe). Analysis only.

Usage: python scripts/_mine_1eveyy_entries.py <WALLET> [sigs=150] [outtag]
"""
from __future__ import annotations
import json, os, sys, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import wallet_decode as wd
import mine_wallet_entries as mwe
from _mine_8zkg_entries import pc_h1, stats_block  # reuse exact feature/printers

GATE_DIP = -16.0
GATE_AGE_H = 24.0
GATE_MCAP_LO, GATE_MCAP_HI = 500_000, 10_000_000
GATE_LIQ = 25_000


def main():
    addr = sys.argv[1]
    sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 150
    tag = sys.argv[3] if len(sys.argv) > 3 else addr[:6].lower()
    outpath = os.path.join(os.path.dirname(HERE), f"_{tag}_entry_feats.json")

    print(f"trade_map {addr[:12]} sigs={sigs} ...", file=sys.stderr)
    tok = wd.trade_map(addr, sigs)
    print(f"  {len(tok)} tokens touched", file=sys.stderr)

    trips = []
    for m, r in tok.items():
        if not r["buys"] or not r["sells"] or not r["spent"]:
            continue
        b0 = min(b[0] for b in r["buys"])
        ret = (r["recv"] / r["spent"] - 1) * 100
        trips.append((m, b0, ret))
    print(f"  {len(trips)} closed trips", file=sys.stderr)

    feats = []
    for i, (m, ts, ret) in enumerate(trips):
        meta = mwe.token_pool_ohlc(m)
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
        feats.append(f)
        print(f"  [{i+1}/{len(trips)}] {m[:8]} ret={ret:+.1f} dip={f['dip_90m']:+.1f} "
              f"age={f['age_h'] if f['age_h'] is None else round(f['age_h'],1)} "
              f"liq={f['liq']:,.0f} mcap={f['fdv']:,.0f} pch1={f['pc_h1']}", file=sys.stderr)

    json.dump(feats, open(outpath, "w"))
    print(f"\nreconstructed {len(feats)}/{len(trips)} entries -> {outpath}")

    wins = [f for f in feats if f["ret"] > 0]
    losses = [f for f in feats if f["ret"] <= 0]
    print(f"\n=== {tag} ENTRY-STATE: WINNERS vs LOSERS  (realized) ===")
    print(f"reconstructed WR = {len(wins)}/{len(feats)} = "
          f"{len(wins)/max(1,len(feats))*100:.0f}%")
    sw = stats_block("WINNERS", wins)
    sl = stats_block("LOSERS", losses)

    print("\n=== SEPARATORS (winner median vs loser median) ===")
    for key, nm, fmt in [("dip_90m", "dip off 90m high", "{:+.1f}%"),
                         ("age_h", "age (h)", "{:.1f}h"),
                         ("liq", "liquidity $", "{:,.0f}"),
                         ("fdv", "mcap $", "{:,.0f}"),
                         ("pc_h1", "pc_h1 momentum", "{:+.1f}%")]:
        if key in sw and key in sl:
            wmed, lmed = sw[key][1], sl[key][1]
            print(f"  {nm:18s} WIN={fmt.format(wmed)}  LOSE={fmt.format(lmed)}")

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

    # WR ladder (same readout as 8zkg mission) — built dynamically here
    print("\n=== CANDIDATE-GATE WR LADDER ===")
    def ladder(name, pred):
        sub = [f for f in feats if pred(f)]
        if not sub:
            print(f"  {name:42s} n=0"); return
        w = sum(1 for f in sub if f["ret"] > 0)
        med = statistics.median([f["ret"] for f in sub])
        print(f"  {name:42s} n={len(sub):2d} WR={w/len(sub)*100:.0f}% medret={med:+.1f}%")
    ladder("ALL", lambda f: True)
    ladder("dip<=-15", lambda f: f["dip_90m"] <= -15)
    ladder("dip<=-15 & pc_h1<=40", lambda f: f["dip_90m"] <= -15 and f.get("pc_h1") is not None and f["pc_h1"] <= 40)
    ladder("dip<=-15 & pc_h1<=40 & age>=10h",
           lambda f: f["dip_90m"] <= -15 and f.get("pc_h1") is not None and f["pc_h1"] <= 40 and f["age_h"] is not None and f["age_h"] >= 10)
    ladder("dip<=-15 & pc_h1<=40 & age>=10 & liq>=8k",
           lambda f: f["dip_90m"] <= -15 and f.get("pc_h1") is not None and f["pc_h1"] <= 40 and f["age_h"] is not None and f["age_h"] >= 10 and f["liq"] >= 8000)
    ladder("pc_h1>40 (parabolic chase)", lambda f: f.get("pc_h1") is not None and f["pc_h1"] > 40)
    ladder("mcap<500k (below OUR band)", lambda f: f["fdv"] < 500_000)


if __name__ == "__main__":
    main()
