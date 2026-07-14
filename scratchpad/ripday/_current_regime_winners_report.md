# Current-Regime Winner Decode (2026-07-01 → 07-03)

Built 2026-07-03. Data: 210 taped pairs / 90,103 unique trades / 28,943 maker wallets, window
2026-07-01T00:00Z → 2026-07-02T19:22Z (live_tapes recorder span; root tapes cover 07-01 daytime).
Bars: 209/210 pairs have minute OHLC (38 fetched fresh from GT this session). Our side: 652 unique
fleet exits since 07-01 (1,044 raw sells deduped across jerseys) from the dashboard trade API.

Method (counting-trap compliant): union-of-entries per (wallet, token) — ALL buys and sells inside
the window; realized = covered sells − buys (sells before first in-window buy excluded; sells
exceeding bought qty scaled down = pre-window inventory cap); tokens still held at window end marked
at last bar close and labeled **unrealized** everywhere. Base rate always shown. Aggregators/routers
excluded by flag (≥25 pairs seen, ≥400 trades, or churn-spray; e.g. `gasTzr…` 967 trades/143 pairs,
`F353Ajdp…` 56 pairs — all excluded).

Scripts/caches in scratchpad/ripday/: build_ledger2.py → ledger2_wallets.json, score_winners.py →
winners_current.json, decode_behavior.py → behavior_buys/shapes.json, overlap_lens.py,
same_token_lens.py, delta_quant.py, bail_forward_check.py, fetch_missing_bars.py, fetch_pair_ages.py.

---

## Q1 — WHO WINS NOW: winnable, but only for a ~15% minority

- 8,776 wallets made a ≥$20 buy on ≥1 taped pair; **327** humans traded ≥3 distinct tokens.
- Base rates: **33.7%** of all 11,827 (wallet,token) episodes are net-positive; median episode
  **−$4.88** (−6.8% on buy USD). Of the 327 multi-token humans, **110 (33.6%) are net-positive**,
  median wallet **−$20.5**.
- **Winner bar (≥3 individually-positive tokens, overall net>0, non-bot): 49 wallets.**
  Strict (net ≥$50 and pos>neg tokens): **21**. Hard core with **realized** (not mark-to-last)
  profit >0: **14 wallets, 70 episodes** — 67% of their episodes positive, median +2%,
  mean +10% on buy USD.
- Verdict: **this regime IS winnable** — a real minority is grinding green, and the top realized
  winner made **+$1,057 realized** in ~40h on 7 tokens (of which +$1,117 on PEACE alone — a token
  we lost $135 on). It is a thin-edge, minority-winner tape: 2 of 3 multi-token traders lose.
- Caveat: 28 of 49 winners are >50% unrealized (open bags marked at last price) — fragile. All
  behavior conclusions below were cross-checked against the 14-wallet realized core.

## Q2 — HOW THEY WIN

**(a) Entry timing.** Winners buy the same dip depth as everyone (median −20.5% vs 60m high;
base −19.6%) — dip depth does NOT discriminate. What differs: they buy **while still falling**
(10m momentum median −4.0% vs base −0.2%) **into net-sell tape** (5m net flow median −$146 vs
base −$3) — i.e. genuine capitulation, not the first bounce. Their entries then see fwd_max60
+20.7% vs base +15.6% and shallower fwd_min (−15.1 vs −17.2). n=613 winner buys / 7,795 base.

**(b) Hold.** Shorter than base, not longer: median 12.5m to first sell / 38.8m to last
(realized core: 17m to last sell, p75 48m). They are NOT diamond-handing; they cycle.
But they **endure the wick**: drawdown between first buy and first sell median −7.2%, p25 −12.4%
(they sit through the zone where we velocity-bail at −4).

**(c) Exit shape.** Scale-out into strength: 53% of winner sells are above their first-entry price
(base 36%); median sell at +1.5% vs entry (base −2.9%). Median 2 sells/episode, conviction episodes
10–18 sells. **40–49% of episodes re-buy after a sell** — campaign trading, not one-shot slots.
Open bags: 19% of winner episodes vs 40% of base — winners exit; losers hold.

**(d) Token selection.** THE cleanest axis = **token age at entry**:

| age band | winner eps | winner ret on buy | base eps | base ret |
|---|---|---|---|---|
| <2h | 13 (thin) | +6.2% | 98 | **−22.0%** |
| 2–6h | 70 | +4.9% | 281 | −13.0% |
| **6–24h** | **88** | **+15.1%** | 332 | **+2.7% (only positive base band)** |
| 24–72h | 55 | +7.9% | 294 | −10.2% |
| >72h | 53 | +2.2% | 217 | −14.1% |

And **time of day**: winner buys are 0.4% in UTC 04–13 and ~0.8% in 23–01; 78% in 14–22 UTC plus
16% in 02–03. Convergence: 58% of winner buys have ≥2 other winner wallets buying the same pair
within ±15m (42% have ≥3) — demand waves, though partly circular (winners share tokens).

**(e) What they skip.** They do NOT skip our bleed tokens — the opposite: 21.1% of winner buy USD
went into our 13 covered bleed tokens (base: 13.8%), and they made money there (PEACE +$1,999 across
13 episodes, RUSH +$415, BongoCat +$84 where we lost −53/−71/−30). True zero-touch: Scamcoin,
SHROOM, SUPERMAN, TBB (0 winner episodes) — the pure dead-flush/no-second-wave class. Even winner
losses on failures are contained: on popeyes (which died) their worst episodes cost −1.7%…−4.5% of
buy USD via scale-out, similar per-trade % to ours — the difference is they were PRESENT for the
winners; we repeatedly were not.

### Case study: PEACE (GSGMBFWNVHrG, our −$135 / winners +$2.0k)
Winners bought the 07-02 02:00 flush (2.2e-4) and the 14:47–15:21 wave (6 distinct winner wallets
within 34 min), scaled out 16:00–18:00 into the pump. Top wallet: 7 buys averaged over 15h, 14
sells, +87% on $1,279. WE first touched the token 07-02 22:09 — 7 hours after the winning wave,
at a −6.6% higher... (median matched-token delta: winners' first buy is **174 min BEFORE ours at a
−6.6% lower price**, n=19 shared tokens) — then velocity-bailed 6 times in seconds at −2…−6%.
RUSH: our 14 exits, 0 wins, median hold 5s, all velocity-bails.

## Q3 — TOP 3 DELTAS vs THE BADDAY FAMILY (ranked, implementable)

**DELTA 1 — The velocity-bail is inverted in this regime (83% of our bleed).**
Since 07-01: 311/652 unique exits (48%) are `in-flight velocity-bail`, mean −5.78%, sum −$1,590 =
**83% of our total negative P&L (−$1,926)**. Forward-check on bails with bar coverage (n=48, thin):
**77% of bailed tokens printed ≥+6% above the bail price within 60m**; 42% recovered ≥+6 without
ever dropping another −7 (bail unambiguously wrong) vs 21% unambiguously right; worst-case-ordering
counterfactual (−12 hard floor, +6 TP) ≈ **+56pp better** than bailing. Winners sit through median
−7.2% / p25 −12.4% wicks and exit 67% of episodes green. The June validation of velbail (−4, never-
green, fast) does not hold on this chop tape where −5% wicks are the entry feature, not the failure.
IMPLEMENT: A/B jersey with `in_flight_floor` velbail_pnl −4→−8 (or velocity leg off), keep −7/−12
MAE floor — i.e. extend the already-live `badday_flush_wideexit_ab` thesis to the in-flight layer;
keep BAIL_COOLDOWN_MINS≥10 (shipped 07-03) so the bail that does fire can't churn-loop.

**DELTA 2 — Campaign the token; stop the slot churn-loop.**
We take median 5 (mean 9.2) independent fixed-size entries per token, each bailing in seconds;
winners run ONE campaign: enter the capitulation, add on confirmation (mean 2.7 buys), scale out
into strength (53% of sells above entry), re-buy after a profitable sell in 40–49% of episodes.
Same tokens, opposite mechanics. IMPLEMENT (anonymous, no wallet index): per-token campaign state —
(i) after a NEGATIVE exit, re-entry blocked 30–60m (harden BAIL_COOLDOWN to enforce, fleet-wide);
(ii) after a PROFITABLE exit, re-entry allowed only ≥3% below prior exit px (buy the retrace, not
the top); (iii) sell TP1 into strength but keep the runner leg (patient-sleeve A/B already built —
this is its corroboration).

**DELTA 3 — Fish the 6–24h maturity band in winner hours; treat <2h and >72h as young-lane-only.**
The 6–24h band is +15.1% for winners and the ONLY band where even average multi-token traders are
green (+2.7%); <2h is the most adversarial band on the tape (base −22%). Winners place 0.4% of buys
in UTC 04–13 and ~0.8% in 23–01; we placed ~14% of entries in 04–14 and 25.6% in 23–01 (01:00 alone
−$84, 02:00 −$100 at 11% win). Our green hours (15–18, 20) coincide with winner hours. IMPLEMENT:
(i) token-age gate for non-young dip bots: prefer pool_age 6–24h, de-size or block <4h (young lane
keeps its own <2h scope) and >72h; (ii) measure-then-extend the overnight block: 03–08 → candidate
04–14 UTC, plus a 23–01 shadow (hour 00 was +$24 for us, so shadow first, per rulebook cadence).

## Q4 — YOUNG LANE (badday_young_absorb): corroborated on mechanics, challenged on band

- **Corroborated — gates, not band, make young green.** <2h is the WORST base band (−22% ret);
  only selective entries survive it. Young absorb's demand/absorption gates are exactly the
  selectivity that separates the winner <2h result (+6.2%, n=13 thin) from the crowd. Its
  current-regime numbers (n=49 sells: +6.70 mean, 44.9% win, +$88.9 vs rest-family −0.94/31.2%/
  −$2,091) are the only-green-bot proof.
- **Corroborated — exit cadence.** Winners' realized core holds 17m median to last sell with
  scale-outs and fast recycling — same shape as young lane TP1/TP2 + fast bail. Do not slow it.
- **Challenged — the band is bigger than <2h.** The deepest profit pond now is **6–24h-old tokens
  that already survived their launch arc**, bought on capitulation wicks in 14–22 UTC with
  multi-buyer demand waves. That is young-absorb mechanics aged forward. Highest-corroboration new
  jersey: **"adolescent absorb"** — same absorption/demand gates + TP1/TP2 + rug guard, applied to
  6–24h dips, prime-hours only, with Delta-1 wick tolerance (−8 in-flight, −12 floor).

## Honesty ledger
- Union-of-entries everywhere; base rate stated next to every winner claim.
- Unrealized always split out (`realized` vs `unreal` columns in winners_current.json); 28/49
  winners are mark-dependent; the 14-wallet realized core drives conclusions.
- Thin-n flags: bail forward-check n=48 (of 311; rest lack bar coverage), winner <2h band n=13,
  shared-token head-to-head n=19 tokens, young-lane sells n=49.
- Tape truncation: root tapes are sweep samples (~100 trades/call); live_tapes are continuous
  within 07-01T22 → 07-02T19:22. Wallet P&L is complete only within tape spans — pre-window
  inventory capped (flagged per episode), sells-before-buy excluded.
- Convergence stat (58% ≥2 co-buyers) is partly circular (winners defined on shared tokens);
  treat as suggestive, validate as a live demand gate before enforcing.
- No 07-03 tape exists (recorder stopped 07-02 19:22) — our 07-03 trades (e.g. RUSH bails at
  02:53) were compared against GT bars, not tape. Restart the live recorder.
