# Entry-Mine: 7BNaxx6KdUYrjACNQZ9He26NBFoFxujQMAfNLnArLGH5

Mission date: 2026-06-15. Realized data only, no dollar projections.
Method: `scripts/_mine_7bnaxx_entries.py` (parameterized copy of `_mine_8zkg_entries.py`;
reuses `wallet_decode.trade_map` + `mine_wallet_entries.token_pool_ohlc`/`entry_features` +
the `pc_h1` 1h-momentum feature; ADDED a hold-time + size readout to discriminate
scalper vs runner-rider). curl_cffi impersonate=chrome / UA / ~3-5s GT pacing / 429-backoff
inherited from mwe. Analysis only — no config/deploy/_mission_miner touched.
Artifacts: `_7bnaxx_entry_feats.json` (34 reconstructed closed trips), `_7bnaxx_mine_out.txt`,
`_7bnaxx_mine_err.txt`, `_7bnaxx_decode_out.txt`.

## Wallet profile (decode)
55 tokens touched, 33 closed (decode) / 34 reconstructed, 12 open. SIZING: conviction sizer
(median 1.71-1.97 SOL/token, high variance p75 ~6 SOL). RETURNS: WR 58% / win med +9.0% /
loss med -8.4% / best +215.7%. Scanner overlap 27% (we saw 15/55; traded only 8/55 — big
discovery gap, same as the other two wallets).

## ⭐ ARCHETYPE VERDICT: fast TIME-BOX DIP-SCALPER (Dw5 family) — NOT a runner-rider
HOLD-TIME is the discriminator and it is unambiguous:
```
HOLD (min)   p25     median   p75    max
ALL          4.0      7.2     24.6   61.0
WINNERS      6.3     11.9     27.8
LOSERS       4.0      4.8      7.9
```
Decode flagged it directly: **"TIME-BOX SIGNATURE: 79% of losers exit at ~5min (the Dw5
archetype)."** Median hold 6-7min; losers stopped out at ~5min, winners let run to 12-28min
(max 61min). This is the SAME family as 1eveYY (7min hold, 70% losers at 6min) and 8zkg
(17min). It is a **fast time-box scalper**, not a momentum-continuation runner-rider or a
longer-hold conviction holder. Hold-time itself is an outcome signal here: `hold<=5min` = 38%
WR (the time-box flush-outs) vs `hold>5min` = 71% WR. So **(a)** — same archetype family as
8zkg/1eveYY, NOT a new archetype.

## Entry-state: WINNERS (n=20) vs LOSERS (n=14) — reconstructed WR 59%

| feature        | WINNER p25/med/p75       | LOSER p25/med/p75        | direction |
|----------------|--------------------------|--------------------------|-----------|
| dip off 90m hi | -62.2 / **-42.7** / -21.7| -79.6 / **-23.2** / -15.0| **winners DEEPER dip** (dominant) |
| age (h)        | 5.3 / **18.3** / 26.7    | 1.2 / **25.7** / 72.1    | not a separator (mixed) |
| liquidity $    | 5.3k / **9.4k** / 27.8k  | 4.6k / **8.2k** / 43.9k  | winners slightly higher (weak) |
| mcap $         | 5.0k / **16.2k** / 108k  | 4.1k / **12.5k** / 217k  | winners higher floor (weak) |
| pc_h1 %        | +11.2 / **+39.9** / +77.0| +12.6 / **+26.7** / +54.0| winners run HOTTER 1h (see below) |

### The separator (what winners have that losers don't)
1. **DEEPER dip off the 90m high — the dominant, clean separator.** Winner median **-42.7%**
   vs loser **-23.2%**. This wallet dips MUCH deeper than 8zkg (-16%) or 1eveYY (-15%); its
   edge lives in violent flushes. Shallow dips bleed: `dip > -16%` = **2/7 = 29% WR, -4.9%
   med** (the bleed cohort, same as both prior wallets). Each step deeper raises WR:
   dip<=-16 67% -> dip<=-30 72% -> `dip<=-30 & liq>=10k` 88% (n=8). THIRD independent
   confirmation of the deep-dip-buy edge.
2. **A modest liquidity floor stacks on the deep dip** — `dip<=-30 & liq>=10k` = 88% WR /
   +6.3% med (n=8); `dip<=-20 & liq>=10k` = 75% (n=12). But note the floor that helps here is
   ~$10k, BELOW our $25k anti-rug floor — this wallet lives in the low-liq pocket (median liq
   $8.7k, median mcap $14k).
3. Age is NOT a separator (winner med 18h vs loser med 26h — losers are if anything OLDER).

## ⭐ KEY QUESTION — does 7BNaxx confirm the 8zkg pc_h1 (cool-1h) ceiling?
**NO — it WEAKLY CONTRADICTS it.** 8zkg's thesis was: on a dip entry, LOSERS chase HOT pc_h1
(winners enter cool). On 7BNaxx the sign is **reversed**: winner pc_h1 median **+39.9%** is
HIGHER than loser **+26.7%**, and the gate ladder confirms the ceiling does not help:
```
dip<=-16                  n=27 WR=67% medret=+6.2
dip<=-16 & pc_h1<=40      n=19 WR=58% medret=+4.9   <-- the ceiling makes it WORSE
pc_h1>40 (parabolic)      n=12 WR=67% medret=+6.4   <-- not a trap; winners live here
pc_h1<=0 (cooling)        n= 6 WR=67% medret=+5.5
```
Applying 8zkg's `pc_h1<=40` ceiling to this wallet DROPS WR from 67% to 58% — it cuts
winners. This wallet happily dip-buys deep flushes that are still hot on the 1h candle (a deep
90m flush can coexist with a positive 60m-bounce). So across the 3 wallets the cool-1h ceiling
is: 8zkg = the only support (n=11), 1eveYY = no-op (never takes the bet), 7BNaxx = mild
contradiction (winners run hotter). Net: **the pc_h1<=40 ceiling does NOT generalize; treat it
as an 8zkg-only, low-confidence finding — do NOT enforce it fleet-wide.** (Caveat: 7/34 entries
had no pc_h1 — sub-1h tokens with no -60m bar — so the pc_h1 cells are thinner than the dip cells.)

## vs OUR CURRENT GATES — same disjoint-universe finding as 8zkg / 1eveYY
**0 / 34 of 7BNaxx's entries pass our full stack** (dip<=-16 & age>=24h & mcap 500k-10M &
liq>=25k). Individually: dip<=-16 **27/34**, age>=24h **13/34**, liq>=25k **11/34**, mcap-band
**1/34**. The binder is again **MCAP** — only 1/34 lands in our 500k-10M band (and that one is
SPCXwBHV at $3.4M mcap, which returned **-48.9%** — a loser; this is the same SPCX ticker that
caused the symbol-collision phantom bug, worth noting it appears in this wallet's tape as a
real loss). This wallet's habitat is the DEEPEST microcap of the three: median mcap $14k,
median liq $8.7k — below even 8zkg ($15k liq) and well below 1eveYY ($33k liq). Its raw edge
sits in the sub-$60k-mcap / sub-$25k-liq pocket our anti-rug floors (#432) deliberately exclude.

## CANDIDATE-GATE WR LADDER (computed on the 34 trips)
```
ALL                          n=34 WR=59% medret=+4.9
dip<=-16                     n=27 WR=67% medret=+6.2
dip<=-20                     n=24 WR=67% medret=+5.6
dip<=-30                     n=18 WR=72% medret=+5.6
dip<=-30 & liq>=10k          n= 8 WR=88% medret=+6.3   <-- the edge (deep flush + thin floor)
dip<=-20 & liq>=10k          n=12 WR=75% medret=+6.3
shallow dip>-16              n= 7 WR=29% medret=-4.9   (the bleed)
hold<=5min                   n=13 WR=38% medret=-0.9   (time-box stop-outs)
hold>5min                    n=21 WR=71% medret=+6.5
```

## CANDIDATE ENTRY-TRIGGER SPEC
No NEW gate warranted. The mine **re-confirms** existing gates and actively WEAKENS the one
candidate (cool-1h) it could have strengthened. If anything ships, it's a confirmation tag:
```
re-confirms: pool_a_dipgate (dip_90m <= -16%)  — winner -42.7% vs loser -23.2%; shallow=29% WR
             (this wallet wants DEEPER: dip<=-30 = 72% WR, the strongest single cut)
re-confirms: deep-dip + thin-liq edge lives in the sub-$25k-liq microcap pocket OUR floors
             (#432 liq>=25k, mcap>=60k) deliberately exclude — NOT a recommendation to drop
             them (rug pocket); flagged as where this wallet's raw edge sits.
WEAKENS:     pool_a_dipgate_cool1h (pc_h1<=40) — applying it here drops WR 67%->58%; winners
             run HOTTER pc_h1. Ceiling does NOT generalize beyond 8zkg. Do not enforce.
realized basis: dip<=-30 & liq>=10k = 88% WR, +6.3% med, n=8 (thin); dip<=-16 = 67% WR, n=27.
```

## ⭐ EXPLICIT VERDICT
- **Archetype: fast TIME-BOX DIP-SCALPER (Dw5 family)** — same family as 8zkg/1eveYY, NOT a
  new archetype, NOT a runner-rider/momentum-continuation. Hold median 7min, losers flush at
  ~5min, winners run to 12-28min.
- **CONFIRMS the deep-dip edge — THIRD independent copyable source.** This strengthens the
  shipped dip-buy lever (pool_a_dipgate / the badday sweep): three independent copyable winners
  all show deep dip = win, shallow dip = bleed (29% WR here). 7BNaxx pushes the depth even
  further (-30%/-40% flushes), suggesting the dip threshold could go DEEPER for the deepest-flush
  cohort, not shallower.
- **Does NOT introduce a new edge, and does NOT confirm cool-1h.** It mildly contradicts the
  8zkg pc_h1<=40 ceiling (winners here run hotter pc_h1). So: the cool-1h ceiling stays an
  8zkg-only n=11 curiosity — do not promote it. The deep-dip core is the durable, thrice-confirmed
  finding.

## Caveats
- Thin tails: the 88%-WR core cell is n=8. Single wallet, single ~24h entry cluster
  (entry_ts ~2026-06-15). 7/34 trips have no pc_h1 (sub-1h tokens) — pc_h1 conclusions rest on
  the remaining 27.
- Habitat is the deepest microcap of the three wallets (median mcap $14k, liq $8.7k) — far
  below our 500k-10M band; the shared, transferable logic is the deep-dip-buy, not the operating
  range. Keep anti-rug floors #432 (the raw edge sits in the rug pocket).
