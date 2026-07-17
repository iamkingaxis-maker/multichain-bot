# RH Wallet Decode — 2026-07-17 overnight (two disjoint windows)

Regime context: HEALTHY overnight tape (W1 net flow +$12.5k, W2 +$47.8k inflow) —
opposite of 07-16's bloodbath. All findings regime-tagged per the day-to-day rule.

## Cross-window stability split (the deliverable)
STABLE (candidate durable structure):
- EXTRACTION dominates: ~$0.5M/window pulled by sell-only wallets (cost basis
  invisible = snipers/insiders/allocations). Matches 07-11. The big money on RH
  extracts; it does not trade.
- WINNER EXIT SHAPE: single-leg, all-out sells (sellLegs median 1.0 in both
  windows; 4th consecutive window incl 07-12 decode). Supports strength_trail.
- EXTRACTOR-BUYER PENALTY (replicated, disjoint windows): buyers entering
  <=10min after an extractor sell lose ~6pp win-rate (53/56% vs 60/62%) and most
  of their median edge (+$0.25/+$0.56 vs +$4.83/+$2.69). n=379+385 tainted vs
  254+225 baseline. CANDIDATE: lane-side entry veto + held-exit trigger —
  needs (a) maker-level extractor registry in the lane feed, (b) revalidation
  in a DIFFERENT regime (this was one night, one macro family).
UNSTABLE (regime noise — do NOT build on):
- winner hold-time contrast (flipped W1->W2: 5.3v3.4 then 3.2v3.8)
- winner buy-size contrast (flat then smaller)

## Method notes
- scratchpad/_rh_decode_0717.py (repeatable; router contracts excluded from
  maker stats; sell_only >= $500 = extractor; closed = sold >=70% of buys).
- Windows: w1 scratchpad/_rh_decode_0717_w1.json, w2 _w2.json.
- Caveat: same-night windows share the macro regime; cross-DAY revalidation
  still required before any gate ships.
