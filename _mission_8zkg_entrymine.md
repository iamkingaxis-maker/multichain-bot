# Entry-Mine: 8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ

Mission date: 2026-06-15. Realized data only, no dollar projections.
Method: `scripts/_mine_8zkg_entries.py` (reuses `wallet_decode.trade_map` for per-token
buys/sells/returns + `mine_wallet_entries.token_pool_ohlc`/`entry_features` for entry-time
reconstruction from GeckoTerminal minute OHLC; added `pc_h1` 1h-momentum feature).
Artifacts: `_8zkg_entry_feats.json` (41 reconstructed closed trips), `_8zkg_mine_out.txt`,
`_8zkg_decode_out.txt`.

## Wallet profile (decode)
74 tokens touched, 29 closed (decode) / 41 closed reconstructed (miner uses max-delta parse),
28 open. SIZING: conviction sizer (median 2.33 SOL/token, high variance). HOLDS: median 17min
(p25 5m / p75 33m) — a **fast scalper**. RETURNS: WR 55% / win med +36.5% / loss med -10.0% /
best +233%. Scanner overlap 30% (discovery gap — most of its pond invisible to our feeds).

## Entry-state: WINNERS (n=25) vs LOSERS (n=16) — reconstructed WR 61%

| feature        | WINNER p25/med/p75      | LOSER p25/med/p75       | direction |
|----------------|-------------------------|-------------------------|-----------|
| dip off 90m hi | -25.2 / **-16.0** / -9.1 | -32.8 / **-8.2** / -4.0 | winners DEEPER dip |
| age (h)        | 10.6 / **38.3** / 77.7  | 11.1 / **18.9** / 56.7  | winners OLDER |
| liquidity $    | 9.8k / **16.4k** / 29.8k| 6.9k / **11.6k** / 31.5k| winners slightly higher liq |
| mcap $         | 16.9k / **50.5k** / 126k| 8.7k / **25.1k** / 166k | winners higher floor, lower ceiling |
| pc_h1 %        | -9.3 / **+17.9** / 30.4 | -6.8 / **+27.9** / 56.0 | **LOSERS chase HOTTER momentum** |

### The separators (what winners have that losers don't)
1. **Deeper dip off the 90m high** — winner median -16% vs loser -8.2%. Same dip-buy edge our
   stack already knows, confirmed on an independent copyable wallet.
2. **NOT parabolic at entry (pc_h1 ceiling)** — this is the NEW one. Losers enter into HOTTER
   1h candles (median +27.9%, p75 +56%) than winners (+17.9%). The two worst trades in the
   whole set are textbook parabolic chases: -35.9% (pc_h1 +69%) and -33.1% (pc_h1 +143%, on a
   near-flat dip of only -5.6%). Buying a shallow dip on a token already up huge in the last
   hour = buying the local top.
3. **Aged, not fresh** (secondary) — winner median 38h vs loser 19h; corroborates our age floor
   but the wallet's floor is ~10h, not 24h.

## Candidate-gate WR ladder (computed on the 41 trips)
```
ALL                                         n=41 WR=61% medret= +5.2%
dip<=-15                                     n=20 WR=75% medret= +9.8%
dip<=-15 & pc_h1<=40                         n=18 WR=78% medret= +9.8%
dip<=-15 & pc_h1<=40 & age>=10h              n=11 WR=82% medret= +9.5%   <-- robust core
dip<=-15 & pc_h1<=40 & age>=10 & liq>=8k     n= 9 WR=100% medret=+10.1%  (thin)
pc_h1>40 (parabolic chase)                   n= 9 WR=56%                 (the trap)
mcap<500k (below OUR band)                    n=40 WR=62%
```
The stack is monotone: each filter raises WR (61 -> 75 -> 78 -> 82). The `pc_h1<=40`
anti-parabolic filter alone lifts the dip cohort 75 -> 78 and removes the two -33%/-36%
disasters; combined with a modest age floor it reaches 82% WR at n=11.

## vs OUR CURRENT GATES — the big finding
**0 / 41 of this wallet's entries pass our full stack** (dip<=-16 & age>=24h & mcap 500k-10M
& liq>=25k). The binder is **MCAP**: only **1/41** entries fall in our 500k-10M band. This
wallet lives in MICROCAPS — mcap p25 $12.8k / median $36k / p75 $137k / max $1.09M. Our band
floor ($500k) sits at this wallet's ~p95. Individually: dip<=-16 passes 17/41, age>=24h 21/41,
liq>=25k 16/41, mcap-band 1/41.

So our gates and this wallet operate in **disjoint universes**. Our pool_a/goodpond stack is a
500k-10M-mcap, liq>=25k, aged-dip instrument; this wallet is a sub-$140k-mcap, liq~$15k,
fast-scalp dip instrument. The dip-buy *logic* is shared; the *operating range* is not.

## Is the candidate NEW or a restatement?
- **dip<=-15**: NOT new — restates pool_a_dipgate (-16%). Confirmation, not discovery.
- **age>=10h**: weaker version of goodpond's age>=24h. Not new.
- **mcap microcap range (~$10k-$200k)**: this is a DIFFERENT universe from our gates, but it's
  the high-rug sub-$60k pocket our anti-rug floors (#432 liq>=25k, mcap>=60k) deliberately
  avoid. NOT a recommendation to drop those — flagged as where this wallet's edge lives, with
  the rug caveat.
- **pc_h1<=40 anti-parabolic ceiling**: this is the **genuinely NEW separator**. None of
  pool_a / dipgate / goodpond / momentum_pump_tight gate on an UPPER 1h-momentum bound.
  momentum_pump_tight does the OPPOSITE (it BUYS pc_h1 20-60 continuation). The novel insight:
  on a DIP-buy entry, a high pc_h1 is a CONTRA signal (you're buying a shallow pullback inside
  a parabola = the top), whereas momentum_pump_tight's positive-pc_h1 edge is a CONTINUATION
  play with NO dip. The two are not contradictory — they gate different entry archetypes. This
  "dip + cool 1h" pairing is not expressed anywhere in our current stack.

## CANDIDATE ENTRY-TRIGGER SPEC (shadow only)
```
name:  dip_cool_momo_8zkg   (paper / SHADOW)
gate:  dip_90m  <= -15%          (off 90-minute high)
   AND pc_h1    <= +40%          (NEW: anti-parabolic ceiling — don't dip-buy into a hot 1h)
   AND age_h    >= 10h
   AND liq      >= 25k           (keep our anti-rug floor #432; the wallet's raw edge sat at
                                  ~$15k liq but that is the rug pocket — do NOT relax)
   leave mcap band OFF for the shadow (this wallet proves the dip+cool edge persists below
   500k; let the shadow measure whether it survives WITH our rug floors on).
realized basis: dip<=-15 & pc_h1<=40 & age>=10h = 82% WR, +9.5% med, n=11 (this wallet).
judge: forward-shadow to n>=30 distinct tokens before any enforce decision.
```
The ONE thing to actually test is the **pc_h1<=40 anti-parabolic ceiling layered on a dip-buy**
— that is the only element not already in our gate stack. Everything else (deep dip, age floor)
is confirmation of gates we already run.

## Caveats
- n=11 for the full core gate (thin). The 100% liq>=8k cell (n=9) is too thin to trust.
- Single wallet, single ~24h window of entries (entry_ts cluster ~2026-06-15). pc_h1 missing on
  3 brand-new tokens (<1h old, no 60m-prior bar).
- This wallet's native edge is in a microcap/low-liq range our anti-rug floors exclude; the
  candidate above keeps those floors, so it tests the SEPARATOR (pc_h1 ceiling), not the wallet's
  full habitat.
