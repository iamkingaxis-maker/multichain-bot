#!/usr/bin/env python
"""Do ENTRY features separate green-peakers that HELD from those that GAVE BACK?

Distinct from the never-green scorer (which targets peak<2% duds). This mines a
DIFFERENT failure mode: trades that went solidly GREEN (peak>=+3%) then reversed
to a loss (the 245-trade, -3.89% rescue cohort from giveback_analysis.py). If
entry_meta features separate HELD (final>+1%) from GAVE-BACK (final<=0) among
green-peakers, give-back risk is partly knowable at entry -> condition sizing /
a tighter ladder at entry for high-give-back-risk setups.

Join buy(entry_meta) -> sell(peak,pnl) by (bot,addr). Restrict to peak>=+3%.
Cohen's d on numeric entry_meta features, HELD vs GAVE-BACK. Token-deduped (FCM).
Read-only; hypothesis-generating (thin cohort — directional only).
"""
from __future__ import annotations
import json, urllib.request, time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"


def _get(path, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(BASE + path, timeout=120))
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(5)


def main():
    trades = _get("/api/trades?limit=5000&full=1")
    buys = [x for x in trades if x.get("type") == "buy" and x.get("entry_meta")]
    sells = [x for x in trades if x.get("type") == "sell"]
    sidx = {}
    for s in sells:
        sidx.setdefault((s.get("bot_id"), s.get("address")), []).append(s)

    rows = []
    for b in buys:
        scand = sidx.get((b.get("bot_id"), b.get("address")))
        if not scand:
            continue
        s = scand[0]
        peak, pnl = s.get("peak_pnl_pct"), s.get("pnl_pct")
        if peak is None or pnl is None or peak < 3.0:
            continue
        rows.append((b.get("token"), b["entry_meta"], float(pnl)))

    # token-dedup: one (median-pnl) entry per token
    bytok = {}
    for tok, em, pnl in rows:
        bytok.setdefault(tok, []).append((em, pnl))
    ded = []
    for tok, g in bytok.items():
        g.sort(key=lambda x: x[1])
        em, pnl = g[len(g)//2]
        ded.append((tok, em, pnl))

    held = [(em, pnl) for _, em, pnl in ded if pnl > 1.0]
    gave = [(em, pnl) for _, em, pnl in ded if pnl <= 0.0]
    print(f"green-peakers (peak>=+3%): {len(rows)} trades, {len(ded)} unique tokens")
    print(f"  HELD (final>+1%): {len(held)} tokens | GAVE-BACK (final<=0): {len(gave)} tokens")
    if len(held) < 5 or len(gave) < 5:
        print("too thin for a differential — re-run as cohort grows.")
        return

    # numeric feature universe (skip verdict/reason/bool-ish meta keys)
    feats = set()
    for em, _ in held + gave:
        for k, v in em.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                feats.add(k)

    def vals(pop, f):
        return np.array([em[f] for em, _ in pop if isinstance(em.get(f), (int, float))
                         and not isinstance(em.get(f), bool)], float)

    ranked = []
    for f in feats:
        a, b = vals(held, f), vals(gave, f)
        if len(a) < 5 or len(b) < 5:
            continue
        sp = np.sqrt(((len(a)-1)*a.std()**2 + (len(b)-1)*b.std()**2) / max(len(a)+len(b)-2, 1))
        if sp == 0 or np.isnan(sp):
            continue
        d = (a.mean()-b.mean())/sp
        if abs(d) >= 0.45:
            ranked.append((abs(d), d, f, a.mean(), b.mean()))
    ranked.sort(reverse=True)
    print(f"\n=== entry features separating HELD vs GAVE-BACK (|d|>=0.45) — {len(ranked)} ===")
    print(f"{'feature':40} {'d':>6} {'held_mean':>12} {'gave_mean':>12}")
    for ad, d, f, ma, mb in ranked[:25]:
        print(f"{f:40} {d:>+6.2f} {ma:>12.3f} {mb:>12.3f}")
    print("\nNOTE: thin cohort, hypothesis-only. Survivors -> shadow + forward-validate")
    print("before any entry-conditioning. Distinct failure mode from never-green scorer.")


if __name__ == "__main__":
    main()
