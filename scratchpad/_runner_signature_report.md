# Runner Signature Mine — monster vs regular winner, at the +5..+15% decision moment
2026-07-10 (read-only analysis; no code/config changed)

## Question
While HOLDING a token that is up +5..15% off the dip-buy level, what on-chain tape
features say "forming MONSTER (mogdog-class)" vs "regular +10% pop that fades"?
Output feeds a real-time `runner_score` for the moonbag-hold decision.

## Data actually available (honesty first)
- **mogdog / SMOLE / Balloon / Bullscan decision-window maker tapes DO NOT EXIST.**
  io.dexscreener trade-log holds only the last ~100 trades (mogdog fetch at 20:32 UTC
  reached back 2 minutes). GT trades endpoint likewise. No recorder was running during
  those holds. Retroactive maker-level scoring of today's named monsters is impossible.
- What DOES exist, same schema `{kind, volume_usd, ts, maker}`:
  - **Solana rip tapes** `scratchpad/ripday/live_tapes/tape_*.jsonl` (Jul 2-6, 334 pairs,
    swept every ~10 min while active). 18 pairs used (top-volume + the bot-traded
    labeled regulars RTM/Bullmerica/ACM/ELIZABETH/CLIVE/Goofreck/Fro).
  - **RH-chain tapes** `scratchpad/robinhood_tapes/tape_0x*.jsonl` (Jul 9-10) incl.
    CASHCOW/KITTY/RANGER/BILLY/THROBBIN. 18 pools used.
- **Labels**: GT 1-minute bars fetched per pair (paced, cached in
  `scratchpad/_runner_bars/`). Price segmented into runs (local low -> peak; run ends on
  60% retrace). Run label: **monster = gain >= +40%**, **regular = gain +8..20%**,
  20-40% gray zone excluded. This yields BOTH classes from the same pairs/venues —
  same-token runs land in both classes, so the score is forced to read run character,
  not token identity.
- **Decision window D** = 10 min from the bar where price first crosses low*1.05 (t5) —
  no peeking at the outcome. Reference window R = 10 min before t5.
- **Coverage gate**: tape volume in D vs bar volume in D (`tape_coverage`); headline
  stats use cov >= 0.5 and >= 20 tape trades in D. (Sweeps cap ~100-300 trades/10 min,
  so hot minutes undersample; the gate drops those windows.)

### n (after gates, cov >= 0.5)
| class   | runs | distinct tokens | venues |
|---------|------|-----------------|--------|
| monster | 44   | 16              | 34 sol / 6 rh (runs) |
| regular | 221  | 19              | 215 sol / 5 rh |

Bot-position labels from `_tr.json` (Jul 4-10, `peak_pnl_pct`): 11 monster positions
(SMOLE +118, LADY +111, TESTPACK +87, MIKE +56, MAYO +50, AVAJAK x2, $SBTV, CLOPY,
Balloon, febu) and 115 regular positions corroborate the class definition, but almost
none overlap the tape-recorder days, hence the run-level labeling above.

## Feature separation (token-deduped medians; AUC = P(monster token > regular token))
Stable across cov cuts 0.25 / 0.5:

| feature | monster med | regular med | AUC (cov.5 / cov.25) | verdict |
|---|---|---|---|---|
| **net_ratio_D** = (buy-sell)/(buy+sell) USD, 10 min | **+0.15** | **+0.02** | **0.82 / 0.84** | best single signal |
| **bpm_accel** = buys/min 2nd half / 1st half of D | **0.92-1.08** | **0.69-0.70** | **0.72 / 0.78** | monsters build, regulars fade |
| net_ratio_early (first 5 min) | +0.20 | +0.07 | 0.75 / 0.76 | redundant w/ net_ratio_D |
| **bpm_late** (buys/min, 2nd half) | 5.1 | 3.2 | 0.73 / 0.68 | same dimension as accel |
| **med_buy_rel** = median buy $ in D / in R | **1.67** | **1.16** | 0.67 / 0.67 | buyers upsize into strength |
| med_buy (abs $) | $32 | $16 | 0.69 / 0.66 | venue-dependent; use rel |
| **new_maker_frac** (buyers unseen before t5) | **0.51-0.56** | **0.42-0.43** | 0.63 / 0.69 | fresh wallets arriving |
| pos_windows_10 (60s wins net>0, of 10) | 5 | 4 | 0.71 / 0.63 | weaker, redundant |
| buy_vol_D ($) | 4.0k | 2.3k | 0.71 / 0.64 | size-confounded |
| makers_per_1k buy vol | 7.4 | 14.2 | 0.31 / 0.36 (inverted) | = med_buy mirror: monsters have FEWER, BIGGER buyers per $1k |
| seller_top3_share | 0.45 | 0.56 | 0.33 / 0.43 (inverted) | weak; if anything regulars show MORE seller concentration |
| bpm_early, bpm_vs_ref, n_buyers_D | — | — | 0.43-0.56 | no signal |

Surprises worth stating:
- **Wallet-diversity-per-dollar is ANTI-predictive.** Monsters are driven by fewer,
  larger buyers (median buy ~2x reference), not by a swarm of small wallets. The swarm
  shows up in regulars that fade.
- **Buys-per-minute level doesn't matter; its direction does.** Regular pops decay
  (accel ~0.7 = second 5 min has 30% fewer buys); monsters hold or accelerate (~1.0+).
- Seller concentration (distribution signature) did NOT separate — dropped.

## Proposed pure function: `runner_score`
```python
def runner_score(trades_window, ref_stats):
    """
    trades_window: list of {kind, volume_usd, ts, maker} for the LAST 10 MIN,
                   accumulated by polling the maker-level trade-log (~30-60s poll,
                   dedup key (ts, maker, volume_usd, kind)) while holding.
    ref_stats:     {"median_buy_usd_ref": float,   # median buy size 10 min pre-run (at/before entry)
                    "makers_seen_before": set()}   # makers seen from entry-10min up to window start
    returns (score: float 0..1, reasons: list[str])
    """
    buys  = [t for t in trades_window if t["kind"] == "buy"]
    sells = [t for t in trades_window if t["kind"] == "sell"]
    bvol, svol = sum(t.volume for t in buys), sum(t.volume for t in sells)
    if len(buys) + len(sells) < 20 or bvol <= 0:
        return None, ["thin_tape"]          # fail-open upstream: None, not 0

    half = window_midpoint_ts
    net_ratio   = (bvol - svol) / (bvol + svol)
    bpm_accel   = n_buys_2nd_half / max(n_buys_1st_half, 1)
    med_buy_rel = median(buy sizes) / max(ref_stats["median_buy_usd_ref"], 1)
    new_frac    = frac of window buy-makers not in ref_stats["makers_seen_before"]

    s_flow  = clip01(net_ratio / 0.2)               # 0 at 0.00, 1 at +0.20
    s_accel = clip01((bpm_accel - 0.6) / 0.6)       # 0 at 0.6,  1 at 1.2
    s_size  = clip01((med_buy_rel - 1.0) / 1.0)     # 0 at 1.0,  1 at 2.0
    s_fresh = clip01((new_frac - 0.35) / 0.3)       # 0 at 0.35, 1 at 0.65
    score = (s_flow + s_accel + s_size + s_fresh) / 4
    reasons = [name for name, s in zip(("flow","accel","size","fresh"),
               (s_flow, s_accel, s_size, s_fresh)) if s >= 0.5]
    return score, reasons
```
4 features, round thresholds, no ML. Missing maker data (GT fallback strips maker)
must degrade to None on s_fresh, never to 0 (read-as-zero bug class).

## Validation on labeled runs (cov >= 0.5; the mined set — NOT held-out)
- Median score: monster **0.56** vs regular **0.38**; run-level AUC **0.71**,
  token-level AUC **0.84**.

| threshold | monster runs firing | regular runs firing |
|---|---|---|
| >= 0.4 | 77% | 46% |
| >= 0.5 | 64% | 35% |
| **>= 0.6** | **41%** | **14%** |

Per-token medians (monster class): COBRA 0.86, 0x 0.83, BullWorld 0.76, ELIZABETH 0.66,
ANSUM 0.60, ACM 0.56, CASHCOW-family RH runners 0.52-0.56 ... vs regular class mostly
0.18-0.50 (one outlier: Bullmerica250 0.76, n=1 run).
Named 2026-07-10 cases: **not scorable retroactively** (no maker tape in their windows) —
this is a coverage gap, not a model result.

## How to use it (SHADOW FIRST — no live behavior keyed on this yet)
1. While holding, accumulate the maker-level trade-log (io.dexscreener, maker parsed)
   into a per-position ring buffer; poll ~30-60s (hot tokens burn >100 trades in
   minutes — single fetches are not a window).
2. At TP1 fire (and every exit), stamp `runner_score`, `runner_reasons`,
   `runner_tape_n`, `runner_tape_cov` into the sell row (same pattern as existing
   shadow stamps).
3. After >= 30 stamped exits with realized `peak_pnl_pct`, check: median realized peak
   for score >= 0.6 vs < 0.6. Gate any moonbag-hold decision on THAT realized table
   (score >= 0.6 keeps ~86% of regular pops out while catching ~41% of monster
   windows — asymmetric in the right direction for a small moonbag).
4. Only after shadow validation: moonbag rule candidate = "at TP1, if runner_score >= 0.6
   and tape_n >= 20, hold 25% with wide trail; else exit full."

## Exact tape coverage needed to close the gap
- Run a hold-tape recorder DURING bot sessions: on every fill, start polling that
  pair's trade-log every 30-60s until 30 min after flat (append to
  `scratchpad/ripday/live_tapes/` schema). Per project rules this is per-session, not 24/7.
- That yields scored-at-TP1 stamps on every future mogdog/SMOLE-class event and the
  realized-peak validation table this analysis cannot produce retroactively.

## Files
- Labels: `scratchpad/_runner_labels.py` -> `scratchpad/_runner_labels.json`
- Bars fetch (GT, paced, cached): `scratchpad/_runner_bars.py` -> `scratchpad/_runner_bars/`
- Features: `scratchpad/_runner_features.py` -> `scratchpad/_runner_features.json`
- Separation stats: `scratchpad/_runner_stats.py` (arg = min coverage)
- Score + validation: `scratchpad/_runner_score_validate.py`
