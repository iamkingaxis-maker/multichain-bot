# RH regime-robust entry-lever hunt — 2026-07-13

**Goal (AxiS):** find ANY entry/config lever that is BOTH net-$-positive AND regime-robust
(net-positive on EACH of 07-10, 07-11, 07-12) — or honestly report null.

**Verdict: NULL.** Zero of the ~55 tested entry-feature cells passed the full gauntlet.
RH net is **regime-beta**, not an entry-selection problem. The killer is day **07-11**:
no entry cell is net-positive on it without being single-token-driven. The real levers are
the **tail-cap** and a **regime-sizing gate** (+ more tape) — exactly the fallback the task
anticipated. No racer was added to `rh_paper_lane.py` (nothing earned a seat).

---

## Method

- **Data:** `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (local, not re-pulled).
- **Join:** position key = `(bot_id, pool)`. The lane runs ~13+ racers that each buy the
  SAME shared per-pool facts, so `pool` alone is NOT unique — multiple bots hold the same pool
  concurrently. Walk each key chronologically: a buy opens a position (carrying ITS entry
  features), sells accrue, `fully==True` closes it. `net_usd = Σ(pnl_usd over the position's sells) − $0.20` friction (~round-trip gas/slippage on a $25 entry; same constant the scorecard uses).
- **Reconciliation:** 457 `fully`-sells in the tape (== scorecard's split-at-fully trip count)
  vs my **456** buy-joined positions; the 1 gap is a fully-sell whose opening buy predates the tape. Join is complete.
- **1970-epoch test rows** (sym "T", fake pool `0xp…`, 49 buys) excluded.
- **Day attribution is unambiguous:** entry_day == close_day for all 456 positions (scalps close same day).
- **Gauntlet (ALL must hold):** `mean net$>0` · `drop-top-2 mean>0` · net-positive on
  EACH of 07-10/11/12 (each day needs n≥5 to count as *proven*) · single-token share <40% (by position count).

## The premise, quantified — the whole lane is regime-beta

| entry_day | n | net$/pos | total$ |
|---|---:|---:|---:|
| 2026-07-10 | 70 | **−0.92** | −64.2 |
| 2026-07-11 | 162 | **−1.53** | −247.4 |
| 2026-07-12 | 224 | **+0.92** | +207.0 |
| ALL | 456 | −0.23 | −104.6 |

Two of three days are deep red at the lane level. For a cell to clear the bar it must overcome
a **−0.9 to −1.5/pos** drag on the two bad days. That is the high bar, and nothing cleared it.

---

## Systematic gauntlet table (every cell × 3-day net + verdict)

Format: `mean` and `dt2`(drop-top-2) are net$/pos; each day shows `net$/pos(n)`; `1tok`=top-token position share.

```
CELL                                 n   mean    dt2 |    07-10(n)    07-11(n)    07-12(n) | 1tok verdict reasons
-----------------------------------------------------------------------------------------------------------------
ALL                                456  -0.23  -0.26 |  -0.92( 70)  -1.53(162)  +0.92(224) |  17% FAIL  mean<=0;drop_top2<=0;10_neg;11_neg
dip<=-30                            44  +0.40  +0.19 |  +0.26(  9)  -2.42( 10)  +1.58( 25) |  30% FAIL  07-11_neg(-2.42)
dip -30..-25                        59  -0.10  -0.33 |  +1.32(  5)  -1.02( 28)  +0.63( 26) |  24% FAIL  mean<=0;dt2<=0;11_neg
dip -25..-20                        68  +0.31  +0.16 |  -0.87( 19)  -1.74( 13)  +1.68( 36) |  31% FAIL  10_neg;11_neg
dip -20..-15                       130  -0.40  -0.50 |  -1.72( 18)  -1.57( 54)  +1.10( 58) |  18% FAIL  mean<=0;dt2<=0;10_neg;11_neg
dip -15..-10                       155  -0.55  -0.62 |  -1.36( 19)  -1.53( 57)  +0.35( 79) |  17% FAIL  mean<=0;dt2<=0;10_neg;11_neg
dip<=-25 (deep)                    103  +0.12  -0.01 |  +0.64( 14)  -1.39( 38)  +1.10( 51) |  21% FAIL  dt2<=0;11_neg
dip<=-20                           171  +0.19  +0.11 |  -0.23( 33)  -1.48( 51)  +1.34( 87) |  18% FAIL  10_neg;11_neg
dip -25..-15 (mod)                 198  -0.16  -0.22 |  -1.28( 37)  -1.60( 67)  +1.32( 94) |  15% FAIL  mean<=0;dt2<=0;10_neg;11_neg
liq<15k                              2  +0.39   n/a  |  +0.39(  2)   n/a (  0)   n/a (  0) | 100% FAIL  thin;1tok=100%
liq 15-30k                           3  -9.72 -22.71 |  -9.72(  3)   n/a (  0)   n/a (  0) |  33% FAIL  mean<=0;thin
liq 30-50k                         257  +0.12  +0.07 |  -0.45( 36)  -1.32( 86)  +1.20(135) |  30% FAIL  10_neg;11_neg
liq 50-100k                        114  -0.42  -0.50 |  -0.68( 29)  -1.55( 28)  +0.27( 57) |  39% FAIL  mean<=0;dt2<=0;10_neg;11_neg
liq>=100k                           80  -0.76  -0.87 |   n/a (  0)  -1.87( 48)  +0.92( 32) |  60% FAIL  mean<=0;dt2<=0;10_thin;11_neg;1tok=60%
liq>=50k  [prior FAIL ref]         194  -0.56  -0.61 |  -0.68( 29)  -1.76( 76)  +0.50( 89) |  25% FAIL  mean<=0;dt2<=0;10_neg;11_neg
liq>=40k                           238  -0.43  -0.48 |  -0.34( 44)  -1.77( 87)  +0.63(107) |  20% FAIL  mean<=0;dt2<=0;10_neg;11_neg
liq>=30k                           451  -0.17  -0.20 |  -0.55( 65)  -1.53(162)  +0.92(224) |  17% FAIL  mean<=0;dt2<=0;10_neg;11_neg
fee_tier=100                         6  +1.06  +0.48 |  -3.97(  1)   n/a (  0)  +2.06(  5) |  83% FAIL  thin;1tok=83%
fee_tier=3000                       77  +2.18  +2.04 |   n/a (  0)   n/a (  0)  +2.18( 77) | 100% FAIL  10_thin;11_thin;1tok=100% (07-12 ONLY)
fee_tier=10000                     373  -0.75  -0.78 |  -0.87( 69)  -1.53(162)  +0.21(142) |  13% FAIL  mean<=0;dt2<=0;10_neg;11_neg
hour h00-02                        106  -0.35  -0.44 |   n/a (  0)  -1.03( 51)  +0.27( 55) |  41% FAIL  mean<=0;dt2<=0;10_thin;11_neg;1tok=41%
hour h03-07                        103  -1.59  -1.68 |   n/a (  0)  -1.59(103)   n/a (  0) |  24% FAIL  mean<=0;dt2<=0;10_thin;12_thin (07-11 ONLY)
hour h08-12                        140  +1.20  +1.11 |   n/a (  0)   n/a (  0)  +1.20(140) |  55% FAIL  10_thin;11_thin;1tok=55% (07-12 ONLY)
hour h13-16                         35  -0.23  -0.47 |  -5.46(  6)   n/a (  0)  +0.85( 29) |  17% FAIL  mean<=0;dt2<=0;10_neg;11_thin
hour h17-21                         40  -0.21  -0.40 |  -0.21( 40)   n/a (  0)   n/a (  0) |  30% FAIL  mean<=0;dt2<=0;11_thin;12_thin (07-10 ONLY)
hour h22-23                         32  -1.71  -2.02 |  -0.97( 24)  -3.95(  8)   n/a (  0) |  38% FAIL  mean<=0;dt2<=0;10_neg;11_neg;12_thin
hour prime 13-21                    75  -0.22  -0.34 |  -0.89( 46)   n/a (  0)  +0.85( 29) |  16% FAIL  mean<=0;dt2<=0;10_neg;11_thin
flow_confirm=True [prior LEAK ref] 104  +1.00  +0.87 |  -3.94(  7)  +0.19( 41)  +2.20( 56) |  53% FAIL  10_neg;1tok=53%
flow_confirm=False                 352  -0.59  -0.63 |  -0.58( 63)  -2.11(121)  +0.50(168) |  12% FAIL  mean<=0;dt2<=0;10_neg;11_neg
avoid_block=True                     0    —      —   |  DEAD FIELD: avoid_block is False on all 456 positions (zero signal)
buy_share>=0.95                     77  +0.16  +0.05 |   n/a (  0)  -3.95(  8)  +0.64( 69) |  44% FAIL  10_thin;11_neg;1tok=44%  (regime field ~07-12 only)
buy_share 0.90-0.95                 37  +2.74  +2.48 |   n/a (  0)   n/a (  0)  +2.74( 37) |  49% FAIL  10_thin;11_thin;1tok=49% (07-12 ONLY)
buy_share<0.90                     118  +0.52  +0.43 |   n/a (  0)   n/a (  0)  +0.52(118) |  21% FAIL  10_thin;11_thin (07-12 ONLY)
netflow>=30k                       205  +0.60  +0.54 |   n/a (  0)  -3.95(  8)  +0.79(197) |  28% FAIL  10_thin;11_neg (07-12 dominated)
n_swaps>=700                       134  +0.27  +0.17 |   n/a (  0)  -3.95(  8)  +0.53(126) |  28% FAIL  10_thin;11_neg (07-12 dominated)
distinct_pools>=30                 214  +0.78  +0.72 |   n/a (  0)   n/a (  0)  +0.78(214) |  31% FAIL  10_thin;11_thin (07-12 ONLY)
band young                          94  +1.76  +1.64 |   n/a (  0)  -3.84(  7)  +2.21( 87) |  82% FAIL  10_thin;11_neg;1tok=82% (07-12 ONLY)
band mid                            97  +0.14  +0.07 |   n/a (  0)  -4.78(  1)  +0.19( 96) |  35% FAIL  10_thin;11_thin (07-12 ONLY)
band aged                           41  -0.08  -0.31 |   n/a (  0)   n/a (  0)  -0.08( 41) |  24% FAIL  mean<=0;12_neg (07-12 ONLY)
disc human                         203  +0.82  +0.75 |   n/a (  0)  -3.95(  8)  +1.01(195) |  35% FAIL  10_thin;11_neg (07-12 dominated)
deep + liq30-50k [prior FAIL ref]   73  +0.58  +0.41 |  +0.87(  8)  -1.01( 17)  +1.10( 48) |  30% FAIL  07-11_neg(-1.01)
deep + liq>=30k                    102  +0.12  -0.01 |  +0.73( 13)  -1.39( 38)  +1.10( 51) |  22% FAIL  dt2<=0;11_neg
deep + liq>=50k                     29  -1.03  -1.28 |  +0.52(  5)  -1.69( 21)  +1.08(  3) |  52% FAIL  mean<=0;dt2<=0;11_neg;12_thin;1tok=52%
deep + fee3000                      22  +1.28  +0.74 |   n/a (  0)   n/a (  0)  +1.28( 22) | 100% FAIL  10_thin;11_thin;1tok=100% (07-12 ONLY)
deep + fee10000                     81  -0.20  -0.31 |  +0.64( 14)  -1.39( 38)  +0.96( 29) |  25% FAIL  mean<=0;dt2<=0;11_neg
deep + prime13-21                   15  +0.91  +0.43 |  +1.90(  8)   n/a (  0)  -0.23(  7) |  40% FAIL  11_thin;12_neg;1tok=40%
deep + hour17-21                     7  +2.26  +1.80 |  +2.26(  7)   n/a (  0)   n/a (  0) |  43% FAIL  11_thin;12_thin;1tok=43% (07-10 ONLY)
mod + prime13-21                    36  -0.38  -0.59 |  -1.34( 24)   n/a (  0)  +1.53( 12) |  22% FAIL  mean<=0;dt2<=0;10_neg;11_thin
liq>=40k + prime13-21               46  +0.69  +0.57 |  -0.04( 27)   n/a (  0)  +1.72( 19) |  22% FAIL  10_neg;11_thin
liq>=50k + fee3000                   0    —      —   |  EMPTY (fee3000 only exists 07-12; no 07-12 liq>=50k+fee3000)
deep + fee3000 + liq>=30k           22  +1.28  +0.74 |   n/a (  0)   n/a (  0)  +1.28( 22) | 100% FAIL  1tok=100% (07-12 ONLY)
buy_share>=0.95 + deep              15  -1.30  -1.98 |   n/a (  0)  -4.70(  7)  +1.67(  8) |  47% FAIL  mean<=0;dt2<=0;11_neg;1tok=47%
netflow>=30k + deep                 52  +0.20  -0.06 |   n/a (  0)  -4.70(  7)  +0.96( 45) |  38% FAIL  dt2<=0;10_thin;11_neg
deep + liq30-50k + fee3000          22  +1.28  +0.74 |   n/a (  0)   n/a (  0)  +1.28( 22) | 100% FAIL  1tok=100% (07-12 ONLY)

PASSED FULL GAUNTLET: NONE
```

---

## Two structural confounds that make most "winners" fake

**1. Demand/regime micro-signals & fee_tier=3000 exist almost ONLY on the winning day.**
The regime dict (`buy_share_30m`, `netflow_30m_usd`, `distinct_pools_30m`, `n_swaps_30m`, `band`, `disc`)
was added late — non-null coverage by entry-day:

| field | 07-10 | 07-11 | 07-12 |
|---|---:|---:|---:|
| buy_share_30m / netflow / distinct_pools / n_swaps / band | 0 | 8 | 224 |
| disc | 0 | 8 | 218 |
| fee_tier=3000 (count) | 0 | 0 | 77 |

So every cell built on these (`fee3000 +2.18`, `band young +1.76`, `buy_share 0.90-0.95 +2.74`,
`distinct_pools>=30 +0.78`) is **~100% sampled from 07-12, the winning day.** They look like edges
but are just "was I trading on the good day" — un-testable for regime robustness. `avoid_block` is
a **dead field** (False on all 456).

**2. Hour-of-day is fully entangled with day** — the per-session tapes cover DISJOINT UTC hours:

| day | UTC hours present |
|---|---|
| 07-10 | 16–23 (evening) |
| 07-11 | 0–5, 23 (overnight) |
| 07-12 | 0–1, 9–14 (morning) |

`h08-12` = 100% on 07-12 (good), `h03-07` = 100% on 07-11 (bad). You cannot separate an "hour effect"
from the "day effect" in this tape, so hour-of-day selection is un-provable for regime-robustness.

**Net:** after removing day-confounded features, the only entry features actually sampled across all
three regimes are **dip depth, liq, fee_tier=10000, and flow_confirm.** None of those survive.

---

## The crux: 07-11 is un-tradeable by entry selection

07-11 (n=162, −1.53/pos) kills every candidate. Scanning ALL entry-testable cells on 07-11 alone (n≥8):

```
  dip<=-30       n= 10 mean=-2.42  |  dip -30..-25 n=28 mean=-1.02  |  dip -25..-20 n=13 mean=-1.74
  dip -20..-15   n= 54 mean=-1.57  |  dip -15..-10 n=57 mean=-1.53  |  dip<=-25     n=38 mean=-1.39
  liq 30-50k     n= 86 mean=-1.32  |  liq>=50k     n=76 mean=-1.76  |  liq>=40k     n=87 mean=-1.77
  deep+liq30-50k n= 17 mean=-1.01  |  flow_confirm=F n=121 mean=-2.11
  flow_confirm=T n= 41 mean=+0.19  dt2=-0.01  1tok=51%   <-- ONLY positive cell
```

The **only** cell net-positive on 07-11 is `flow_confirm=True` at +0.19/pos — and it **fails on
its own terms**: drop-top-2 = **−0.01**, single-token share = **51%**. Its token breakdown:

```
  QUANT +19.4 (8 pos)  RSHIB +15.4 (6 pos)  |  CASHCATGAME -18.5 (21 pos)  NOOT -8.5 (6 pos)
```

The "+0.19" is two winner tokens (QUANT, RSHIB) barely offsetting a 21-position CASHCATGAME loser.
Remove the two best and it's negative. This is the **known single-token leak**, now confirmed at the
per-token level. **No entry feature is net-positive on 07-11 without being single-token-driven.**
Since regime-robust REQUIRES all 3 days, the answer is null.

## Near-misses and exactly why each dies

| cell | why it fails the gauntlet |
|---|---|
| `dip<=-30` (+0.40, dt2 +0.19, 1tok 30%) | Best-looking deep-dip cell; green 07-10 (+0.26) & 07-12 (+1.58) but **07-11 = −2.42**. The single closest miss — killed only by the bad regime day. |
| `dip<=-25 (deep)` (+0.12) | 07-11 = −1.39; drop-top-2 = −0.01. Deep-dip is real edge on GOOD days, none on bad. |
| `deep + liq30-50k` (+0.58) | Prior finding reconfirmed: 07-11 = −1.01; also 30% one-token. |
| `flow_confirm=True` (+1.00) | Prior LEAK reconfirmed: −3.94 on 07-10, **53% one token**; 07-11 "win" is QUANT/RSHIB only. |
| `fee3000`, `band young`, `buy_share`, `distinct_pools`, `netflow` | 07-12-only sampling — cannot be tested on the bad days at all (see confound #1). |
| `liq>=50k` / `liq>=40k` | Prior finding reconfirmed: negative at n=194/238; −1.7 on 07-11. |
| `deep + hour17-21` (+2.26) / `deep + prime13-21` (+0.91) | 07-10-only / no 07-11 coverage — hour entanglement (confound #2). |

---

## Honest conclusion

**RH net-$/position is regime-beta, not an entry-selection problem.** Across ~55 entry-feature cells —
dip-depth bins, liq bins, fee tiers, hour-of-day, demand micro-signals (flow_confirm/buy_share/netflow/
n_swaps/distinct_pools), band, and their combinations — **none is net-positive on all three days with
drop-top-2 robustness and <40% single-token share.** The lane loses on 07-10 (−0.92) and 07-11 (−1.53)
and wins on 07-12 (+0.92), and finer entry selection cannot rescue the two bad days: on 07-11 literally
every entry cell is red except a single-token leak. This is the same overfitting AxiS already found —
now proven exhaustively at the cell × 3-day level. **No config was shipped.**

## Where the real levers are (per the task's own fallback — with numbers)

1. **Tail-cap (biggest, and it IS cross-regime).** The loss is entirely tail-driven: the worst **2%**
   of positions (9 of 456) lose **−$118.7**, more than the lane's whole −$104.6. Applying a per-position
   loss cap compresses the regime spread on *every* day:
   - cap@−$2 (~−8% on $25): mean **+0.41**/pos; per-day **07-10 +0.02 / 07-11 −0.36 / 07-12 +1.09**
   - cap@−$3: mean +0.21; per-day −0.26 / −0.75 / +1.04
   The two bad days go from −0.92/−1.53 to +0.02/−0.36. It still doesn't make 07-11 green (so it's not a
   gauntlet "pass"), but it's a real regime-DAMPENER — the opposite of entry selection, which does nothing.
   Caveat: −$2 is an aggressive hard stop; it needs paper-validation of exit-fill realism on RH-chain rugs
   before trusting it, and the −$2/−$3 range must stay broad (not knife-edge optimized) to avoid overfit.
   This is an EXIT/config lever, not entry — consistent with "exits are the lever" (07-13 memory).

2. **Regime-sizing gate (the ceiling).** Standing aside on 07-10/07-11-type regimes and trading only
   07-12-type yields **+0.92/pos over 224 positions (+$207)**. That's the prize. The blocker: the regime
   dict that could *power* such a gate is only populated on 07-12, so the gate cannot yet be built or
   backtested across regimes. **Need more tape with `regime` stamped on ALL days** before a regime-detect
   gate is buildable — this is the concrete next data ask, not another entry cell.

3. **More tape.** 07-10 (n=70) and especially the demand-signal coverage are thin/one-sided; the demand
   micro-signals may or may not be real edges but are currently untestable because they were only recorded
   on the winning day.

### Files
- Writeup: `C:\Users\jcole\multichain-bot\scratchpad\_rh_regime_robust_lever_0713.md`
- Join + gauntlet harness (scratchpad, reusable): `rh_join.py`, `rh_gauntlet.py`
- No change to `scripts/rh_paper_lane.py` — nothing passed, so no racer added.
