# Entry-Mine: 1eveYYxZ2mDiAnmCh3fnAbJwjgErzokRA1b6UrRybSM

Mission date: 2026-06-15. Realized data only, no dollar projections.
Method: `scripts/_mine_1eveyy_entries.py` (parameterized copy of `_mine_8zkg_entries.py`;
reuses `wallet_decode.trade_map` + `mine_wallet_entries.token_pool_ohlc`/`entry_features`
+ the same `pc_h1` 1h-momentum feature). curl_cffi impersonate=chrome / UA / ~5s GT pacing /
429-backoff (inherited from mwe). Analysis only — no config/deploy touched.
Artifacts: `_1eveyy_entry_feats.json` (21 reconstructed closed trips), `_1eveyy_mine_out.txt`,
`_1eveyy_decode_out.txt`.

## Wallet profile (decode)
38 tokens touched, 22 closed (decode) / 21 reconstructed, 8 open. SIZING: conviction sizer
(median 1.38 SOL/token, variable). HOLDS: median **7min** (p25 2m / p75 22m) — a fast
**time-box scalper** (70% of losers exit at ~6min — the Dw5 archetype). RETURNS: WR 55% /
win med +7.7% / loss med -11.8% / best +128%. Scanner overlap 34% (we saw 13/38; traded 19/38).

## Entry-state: WINNERS (n=12) vs LOSERS (n=9) — reconstructed WR 57%

| feature        | WINNER p25/med/p75       | LOSER p25/med/p75        | direction |
|----------------|--------------------------|--------------------------|-----------|
| dip off 90m hi | -21.1 / **-15.0** / -10.0| -23.6 / **-6.8** / -1.8  | **winners DEEPER dip** |
| age (h)        | 100 / **174** / 2620     | 203 / **256** / 410      | winners slightly younger (both very aged) |
| liquidity $    | 24.8k / **33.2k** / 44.7k| 25.1k / **27.6k** / 45.0k| winners modestly higher liq |
| mcap $         | 94.5k / **149k** / 186k  | 71k / **90k** / 264k     | winners higher floor |
| pc_h1 %        | -14.0 / **-8.7** / +8.6  | -18.9 / **-1.9** / +9.4  | BOTH cool — see below |

### The separator (what winners have that losers don't)
1. **Deeper dip off the 90m high** — winner median **-15.0%** vs loser **-6.8%**. This is the
   dominant, clean separator. The shallow-dip cohort (`dip > -10%`) is **3/9 WR** (the bleed);
   the deep cohort (`dip <= -15%`) is **6/9 WR, +4.5% med**. Same dip-buy edge our stack knows,
   re-confirmed on a SECOND independent copyable wallet.
2. **Liquidity floor lifts it further** — `dip<=-15 & liq>=20k` = **4/5 WR (80%), +9.6% med**.
   The deep-dip losers cluster at **low liq** ($8,982 / $14,308 — the rug pocket). 1eveYY's edge
   sits RIGHT AT our $25k anti-rug floor, not below it (winner liq median $33k).
3. Both winners and losers are heavily **AGED** (winner med 174h, loser med 256h) — age is NOT a
   separator for this wallet (all 21/21 already pass our age>=24h floor). The two huge tails
   (+128% / +56%) are deep dips on cool 1h, with one at a 109-day-old token.

## ⭐ THE KEY QUESTION — does 1eveYY confirm the 8zkg pc_h1 (cool-1h) finding?
**No — it neither confirms nor refutes it; the wallet cannot test it.** 8zkg's finding was that
on a dip entry, LOSERS chase HOT pc_h1 (median +27.9%, p75 +56%) while winners enter cool.
**1eveYY essentially never dip-buys into a hot 1h candle.** Within its entire `dip<=-15` cohort,
**every single entry has pc_h1 between -11.9% and -61.4% — all cool/negative, none above +10%.**
The whole wallet has exactly **1 trade with pc_h1>40** (and it's a +4.3% shallow-dip winner, not
a dip). So the `pc_h1<=40` anti-parabolic ceiling is a **trivial no-op on 1eveYY** — it passes
9/9 of the dip cohort and removes nothing.

Implication: 1eveYY does NOT strengthen the `pc_h1<=40` gate with new data, because it never
generates the parabolic-dip trap cases that gate is designed to block. It's a clean wallet that
only dip-buys when 1h momentum is already cooling. This is **consistent with** (not contradictory
to) the 8zkg thesis — 1eveYY behaves like a wallet that already self-applies the cool-1h rule —
but it provides **zero incremental separation evidence** for `pc_h1<=40`. Treat 8zkg as still
the sole empirical basis for that ceiling (n=11). 1eveYY's separable edge is **elsewhere**.

## vs OUR CURRENT GATES — same disjoint-universe finding as 8zkg
**0 / 21 of 1eveYY's entries pass our full stack** (dip<=-16 & age>=24h & mcap 500k-10M & liq>=25k).
Individually: dip<=-16 **7/21**, age>=24h **21/21**, liq>=25k **15/21**, mcap-band **4/21**.
The binder is again **MCAP** — only 4/21 land in our 500k-10M band (mcap p25 $86k / med ~$120k /
mostly sub-$200k). 1eveYY lives in the SAME microcap habitat as 8zkg, but with HIGHER liquidity
(median $33k vs 8zkg's $15k) — its edge sits at/above our $25k anti-rug floor, not below it.

## CANDIDATE-GATE WR LADDER (computed on the 21 trips)
```
ALL                                         n=21 WR=57% medret= +1.3%
dip<=-15                                     n= 9 WR=67% medret= +4.5%
dip<=-15 & liq>=15k                          n= 5 WR=80% medret= +9.6%   <-- the edge
dip<=-15 & liq>=20k                          n= 5 WR=80% medret= +9.6%
dip<=-15 & liq>=25k                          n= 4 WR=75% medret= +7.0%
dip<=-15 & pc_h1<=40                          n= 9 WR=67% (== dip<=-15; ceiling is a no-op)
dip>-10 (shallow chase)                       n= 9 WR=33%                 (the bleed)
mcap<500k (below OUR band)                    n=17 WR=53%
```
The liquidity floor — not pc_h1 — is what stacks on top of the dip here: `dip<=-15 & liq>=15k`
lifts the dip cohort 67% -> 80% and +4.5% -> +9.6% med, by removing the sub-$15k rug-pocket
deep dips. Note `liq>=25k` is slightly WORSE than `liq>=15k/20k` (75% vs 80%) because it drops
one $24.7k winner (the +128% tail at $24,699) — the wallet's real edge band starts ~$15-20k.

## VERDICT
- **pc_h1 cool-1h ceiling: NO new evidence (no-op on this wallet).** 1eveYY does not generate
  parabolic-dip cases, so it can neither confirm nor refute `pool_a_dipgate_cool1h`. Not a
  strengthener. 8zkg remains the only data behind that gate.
- **The separable edge here is DEEP-DIP + LIQUIDITY FLOOR**, which is a RESTATEMENT/confirmation
  of gates we already run (pool_a_dipgate `dip<=-16` + anti-rug `liq>=25k` #432), not a brand-new
  edge. 1eveYY is a clean **second independent confirmation of the deep-dip-buy edge**, sharpened:
  shallow dips (>-10%) are 33% WR (the bleed), deep dips (<=-15%) with liq>=$15-20k are 80% WR.
- **One nuance worth flagging:** 1eveYY's deep-dip edge tops out cleanly at our liq>=25k floor —
  it does NOT need the sub-$15k rug pocket where 8zkg dabbled. This is a small piece of evidence
  that the anti-rug `liq>=25k` floor (#432) is NOT cutting into a real copyable edge for this
  wallet; its winners already live at/above it. Keep the floor.

## CANDIDATE ENTRY-TRIGGER SPEC
No new gate warranted. The mine **re-confirms** the existing `pool_a_dipgate` (dip<=-16) +
anti-rug liq floor (#432) as the right instrument, on an independent copyable wallet. If anything
were to ship, it would be a confirmation tag, not a new trigger:
```
re-confirms: pool_a_dipgate  (dip_90m <= -16%)        — winner med -15% vs loser -6.8%, n=21
re-confirms: anti-rug #432   (liq >= 25k)             — 1eveYY's edge sits AT/ABOVE 25k, not below
no-op:       pool_a_dipgate_cool1h (pc_h1 <= +40)     — 0 parabolic-dip cases in 1eveYY; untestable
realized basis: dip<=-15 & liq>=15k = 80% WR, +9.6% med, n=5 (thin)
```

## Caveats
- Thin: the 80%-WR core cell is n=5; the dip cohort is n=9. Single wallet, single ~24h entry
  cluster (entry_ts mostly 2026-06-15). 1/22 closed trips dropped (no GT pool/OHLC).
- This wallet's habitat is microcap (~$90-200k mcap), below our 500k-10M band — same disjoint
  universe as 8zkg; the shared logic is the dip-buy, not the operating range.
- pc_h1 conclusion is a NULL-by-absence: 1eveYY simply doesn't take the parabolic-dip bet, so the
  ceiling can't be evaluated here. Do not read this as evidence against the 8zkg ceiling.
