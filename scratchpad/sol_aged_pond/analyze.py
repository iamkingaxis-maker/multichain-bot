import json, statistics as st
from collections import defaultdict, Counter

AGED = json.load(open('scratchpad/sol_aged_pond/_trips.json'))
AGED = [t for t in AGED if t.get('ret') is not None]
# broader young-lane dataset for age-band control
try:
    YOUNG = json.load(open('scratchpad/sol_selection/_trips.json'))
    YOUNG = [t for t in YOUNG if t.get('ret') is not None]
except Exception:
    YOUNG = []


def per_token_meds(trips):
    by = defaultdict(list)
    for t in trips:
        by[t['address']].append(t['ret'])
    return {a: st.median(v) for a, v in by.items()}


def ex_top2(trips):
    """Return (ex_top2_tokmed, n_tokens, pct_green_tokens). Drop 2 best tokens by median."""
    meds = per_token_meds(trips)
    if not meds:
        return None, 0, None
    vals = sorted(meds.values(), reverse=True)
    n = len(vals)
    rest = vals[2:] if n > 2 else []
    tm = st.median(rest) if rest else None
    pct_green = 100.0 * sum(1 for v in meds.values() if v > 0) / n
    return tm, n, pct_green


def plain_tokmed(trips):
    meds = per_token_meds(trips)
    if not meds:
        return None, 0
    return st.median(list(meds.values())), len(meds)


def winrate(trips):
    return None if not trips else 100.0 * sum(1 for t in trips if t['ret'] > 0) / len(trips)


def halves_chrono(trips):
    s = sorted(trips, key=lambda t: t.get('sell_time') or t.get('time') or '')
    m = len(s) // 2
    return s[:m], s[m:]


def halves_oddeven(trips):
    odd, even = [], []
    for t in trips:
        d = (t.get('time') or '')[:10]
        try:
            day = int(d[-2:])
        except Exception:
            continue
        (odd if day % 2 else even).append(t)
    return odd, even


def report(label, trips):
    tm, n, pg = ex_top2(trips)
    pm, _ = plain_tokmed(trips)
    wr = winrate(trips)
    legs = len(trips)
    mret = st.mean(t['ret'] for t in trips) if trips else None
    verdict = "PROFITABLE" if (tm is not None and tm > 0 and pg is not None and pg >= 50 and n >= 15) else \
              ("underpowered" if n < 15 else "FAIL")
    def f(x, p=1):
        return f"{x:+.{p}f}" if isinstance(x, (int, float)) else "  -"
    print(f"{label:<42} legs={legs:4d} nTok={n:3d} ex2Med={f(tm):>7} plainMed={f(pm):>7} "
          f"tokGrn%={f(pg,0):>5} wr={f(wr,0):>5} mret={f(mret):>7}  [{verdict}]")
    return tm, n, pg


def oos(label, trips):
    print(f"\n--- OOS four halves: {label} ---")
    c1, c2 = halves_chrono(trips)
    o, e = halves_oddeven(trips)
    for hl, tr in [('chrono-early', c1), ('chrono-late', c2), ('odd-day', o), ('even-day', e)]:
        report(f"  {hl}", tr)
