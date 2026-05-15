"""Mine historical signal_flip SHADOW data from closed trades.

The signal_flip detector runs in SHADOW during every hold (60s cadence).
On first bearish flip it records signal_flip_first_ts + signal_flip_first_pnl
on the Position object — these get persisted into entry_meta at close time.

Question: for trades where signal_flip fired, did the SHADOW exit-pnl beat
the actual exit-pnl? If yes, peak_detector / signal_flip-as-exit would have
saved $.

Reads .audit_trades.json (102 trades, 33 from today) and aggregates.
"""
import json
from collections import defaultdict
from datetime import datetime


def main():
    trades = json.load(open(".audit_trades.json"))
    # Pair buys with their sells by address
    by_addr = defaultdict(list)
    for t in trades:
        by_addr[t.get("address", "")].append(t)
    pairs = []
    for addr, tlist in by_addr.items():
        tlist.sort(key=lambda t: t.get("time", ""))
        buy = None
        sells = []
        for t in tlist:
            if t.get("type") == "buy":
                if buy is not None and sells:
                    pairs.append((buy, sells))
                buy = t
                sells = []
            elif t.get("type") == "sell" and buy is not None:
                sells.append(t)
        if buy is not None and sells:
            pairs.append((buy, sells))

    print(f"Total closed paired trades: {len(pairs)}")
    fired = 0
    not_fired = 0
    saved_total = 0.0
    samples = []
    for buy, sells in pairs:
        em = buy.get("entry_meta") or {}
        if isinstance(em, str):
            try:
                em = json.loads(em)
            except Exception:
                em = {}
        # Try entry_meta on the last sell as well — some flows
        # serialize state at close time.
        em_last = sells[-1].get("entry_meta") or {}
        if isinstance(em_last, str):
            try:
                em_last = json.loads(em_last)
            except Exception:
                em_last = {}
        # signal_flip_first_pnl can be on either buy.entry_meta or sell.entry_meta
        flip_pnl = (em_last.get("signal_flip_first_pnl")
                    if em_last.get("signal_flip_first_pnl") is not None
                    else em.get("signal_flip_first_pnl"))
        flip_ts = (em_last.get("signal_flip_first_ts")
                   if em_last.get("signal_flip_first_ts")
                   else em.get("signal_flip_first_ts"))
        flip_reasons = (em_last.get("signal_flip_reasons") or em.get("signal_flip_reasons") or [])
        # Actual exit
        amount = float(buy.get("amount_usd") or 0)
        total_pnl_usd = sum(float(s.get("pnl") or 0) for s in sells)
        actual_pnl_pct = (total_pnl_usd / amount * 100.0) if amount > 0 else None
        # Peak
        peak_pct = 0
        for s in sells:
            em_s = s.get("entry_meta") or {}
            if isinstance(em_s, str):
                try:
                    em_s = json.loads(em_s)
                except Exception:
                    em_s = {}
            p = s.get("peak_pnl_pct") or em_s.get("peak_pnl_pct") or 0
            try:
                p = float(p) if p else 0
                if p > peak_pct:
                    peak_pct = p
            except Exception:
                pass
        if flip_pnl is None:
            not_fired += 1
            continue
        fired += 1
        try:
            flip_pnl_f = float(flip_pnl)
        except Exception:
            continue
        # "Would-have-saved" assumes selling at flip_pnl instead of actual exit.
        saved_pct = flip_pnl_f - (actual_pnl_pct or 0)
        saved_usd = (saved_pct / 100.0) * amount if amount > 0 else 0
        saved_total += saved_usd
        samples.append({
            "token": buy.get("token"),
            "time": buy.get("time"),
            "trigger": em.get("trigger_source"),
            "flip_pnl": flip_pnl_f,
            "actual_pnl": actual_pnl_pct,
            "peak_pnl": peak_pct,
            "saved_usd": saved_usd,
            "saved_pct": saved_pct,
            "reasons": flip_reasons,
        })

    print(f"signal_flip fired on:   {fired}/{len(pairs)}")
    print(f"signal_flip never fired: {not_fired}/{len(pairs)}")
    print()
    print(f"Would-have-saved total (if we'd exited at first flip): ${saved_total:+.2f}")
    print()
    print("Per-trade detail (sorted by saved $):")
    samples.sort(key=lambda s: -s["saved_usd"])
    print(f"{'token':14s} {'trigger':24s} {'flip%':>7s} {'actual%':>7s} {'peak%':>7s} {'saved$':>8s} {'reasons'}")
    for s in samples[:30]:
        trig = (s["trigger"] or "-")[:24]
        reasons = ",".join(s["reasons"])[:60]
        print(f"{(s['token'] or '?')[:14]:14s} {trig:24s} {s['flip_pnl']:>+6.2f}% "
              f"{(s['actual_pnl'] or 0):>+6.2f}% {s['peak_pnl']:>+6.2f}% "
              f"{s['saved_usd']:>+7.2f} {reasons}")
    print()
    print(f"Average saved per flip-fire: ${saved_total/max(1,fired):+.2f}")
    print(f"Trades where flip would have IMPROVED exit (saved > 0): "
          f"{sum(1 for s in samples if s['saved_usd'] > 0)}/{len(samples)}")


if __name__ == "__main__":
    main()
