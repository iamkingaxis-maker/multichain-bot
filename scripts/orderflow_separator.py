"""Order-flow separator analysis (idea #2, tractable version).

True realtime CVD (cumulative volume delta) can't be reconstructed for our PAST
entries — the DexScreener recent-trades endpoint is a rolling window that won't
reach back hours. So this tests the order-flow features we DID capture at entry
(bs_m5/h1/h6, net flow, buyer concentration, etc.) as winner-vs-loser separators
on our actual traded tokens. If aggregate flow separates → we have a gate. If it
DOESN'T → that's the evidence justifying a true realtime-CVD forward feature.

Winner/loser token labels come from the post-fix afternoon attribution.
"""

import time
import numpy as np

BASE = "https://gracious-inspiration-production.up.railway.app"
RESET = "2026-05-25T21:25:00"

WINNERS = {"neet", "MAGA", "AMERICA", "WORLDCUP", "BABYTROLL", "RICH", "POKE", "PARALOOM"}
LOSERS  = {"TROLL", "TripleT", "TOLYBOT", "TOESCOIN", "PENGUIN", "CARDS", "PAC", "ROUTER"}

# order-flow features present in entry_meta (signed so + = more buying pressure)
FLOW_FEATURES = [
    "bs_m5", "bs_h1", "bs_h6", "buy_sell_volume_imbalance", "buy_pressure_60s",
    "large_buyer_volume_pct", "top_buy_makers_n", "unique_buyer_ratio",
    "n_consecutive_buys_at_end", "smart_buys_5m_count", "n_large_buys_2000_30m",
    "buys_h1", "sells_h1",
]


def _get(url):
    from curl_cffi import requests as r
    for _ in range(6):
        try:
            return r.get(url, impersonate="chrome", timeout=45).json()
        except Exception:
            time.sleep(2)
    return None


def cohens_d(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    na, nb = len(a), len(b)
    sp = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    return (a.mean() - b.mean()) / sp if sp > 0 else 0.0


def main():
    bots = [b["bot_id"] for b in _get(f"{BASE}/api/bots")]
    win_rows, los_rows = [], []
    seen = 0
    for bid in bots:
        tr = _get(f"{BASE}/api/bots/{bid}/trades?limit=120")
        if not tr:
            continue
        for s in tr:
            if s.get("type") != "buy" or s.get("time", "") < RESET:
                continue
            tok = s.get("token")
            em = s.get("entry_meta") or {}
            if not em:
                continue
            # buys_h1/sells_h1 ratio as a derived flow feature
            rec = {f: em.get(f) for f in FLOW_FEATURES}
            bh, sh = em.get("buys_h1"), em.get("sells_h1")
            rec["buys_per_sell_h1"] = (bh / sh) if (bh and sh) else None
            if tok in WINNERS:
                win_rows.append(rec); seen += 1
            elif tok in LOSERS:
                los_rows.append(rec); seen += 1
    print(f"samples: winners={len(win_rows)} losers={len(los_rows)} (buy entries on labeled tokens)\n")

    feats = FLOW_FEATURES + ["buys_per_sell_h1"]
    out = []
    for f in feats:
        wv = [r[f] for r in win_rows if isinstance(r.get(f), (int, float))]
        lv = [r[f] for r in los_rows if isinstance(r.get(f), (int, float))]
        if len(wv) < 3 or len(lv) < 3:
            continue
        d = cohens_d(wv, lv)
        out.append((f, np.mean(wv), np.mean(lv), d, len(wv), len(lv)))
    out.sort(key=lambda x: -abs(x[3]) if x[3] == x[3] else 0)
    print(f"{'feature':<28}{'winner_mean':>13}{'loser_mean':>12}{'Cohen_d':>9}  separation")
    for f, wm, lm, d, nw, nl in out:
        strength = ("STRONG" if abs(d) >= 0.8 else "moderate" if abs(d) >= 0.5
                    else "weak" if abs(d) >= 0.2 else "none")
        print(f"{f:<28}{wm:>13.3f}{lm:>12.3f}{d:>9.2f}  {strength}")
    print("\nReading: |d|>=0.8 = a clean separator (gate candidate). All 'weak/none'")
    print("= aggregate flow at entry does NOT distinguish winners from losers, which")
    print("would justify building true realtime CVD (finer-grained signed-flow) instead.")
    print("NOTE: d-magnitudes are inflated by repeated-token correlation (TROLL bought")
    print("108x = 108 near-identical loser samples); trust the DIRECTIONS, not the sizes.")


if __name__ == "__main__":
    main()
