All checks done. Synthesis follows.

---

# RIP-DAY OFFENSE — SYNTHESIS & RECOMMENDATION (sol_pc_h6 > 1.5)

## 0. Synthesis cross-check (new work, resolves the Lens1-vs-Lens3 conflict)

Lens 1's FLUSH-ABSORB (the only green archetype) was scored on covered-net, which Lens 3 proved forgeable by tape truncation. I re-scored all 55 FLUSH-ABSORB positions with the skeptic's conservative method (sells credited only up to 2x cumulative in-tape buys; entry leg required visible, `first_kind=buy`), vs the 631-position rest-of-population:

```
cohort                          n    WR%    median$   mean$
FLUSH-ABSORB, cap 2.0x         55   56.4    +1.18     +7.68
FLUSH-ABSORB, cap 1.5x         55   56.4    +1.18     +3.32
FLUSH-ABSORB minus kill-list   43   48.8    -0.20     +6.57
REST of population, cap 2.0x  631   33.6    -1.23    -20.55
```

Verdict of the check: **the discrimination is real, the profit claim is soft.** Even under the harshest scoring, FLUSH-ABSORB beats base by +15-23pp WR and ~$25-28 of mean, across 25-26 tokens / 42 wallets. But stripping the skeptic's kill-list wallets drops the median to flat (-$0.20) and WR below 50%; the mean is carried by a fat tail (top position = $202 of the $422 total; mean excl. top1 = +$4.07). Lens 1's "+$23.4 mean / 58% WR" was inflated ~3x by artifact-suspect wallets. And it is all one day (07-01), with zero ARCH1 rows covered by the independent forward-OHLC check.

## 1. THE MECHANISM

Rip-day winners do not chase momentum. On a day when every token in the universe ran >=+25%, the only behavior that separates winners from the 33%-WR base is: **enter an already-ignited runner (<=6h from ignition, age <=24-48h) on its first sell-dominant flush (net signed flow <=-$100/5min) ONLY when a single whale-sized buy print (>= $75/60s) is absorbing the flush, at a price in the lower third of the prior-90m range, then ladder out over 10-60+ min on trend-break, never averaging down.** Crowd-chasing the same runners (buy-dominant flow, >=4 buys/5min, top of range) runs 20% WR / -$42 mean. This is the rip-day mirror of our badday finding (capitulation met by buyer size), which raises confidence beyond the raw n.

**Evidence quality, stated plainly:** 55 positions / 25-26 tokens / 42 wallets, ONE day. Strict-bar independent winner wallets after the skeptic's audit: **~5, with ~$115 combined clean profit** — statistically indistinguishable from luck at the wallet level. The archetype-level discrimination (my cross-check above) is the strongest surviving evidence; the wallet-level "winners" are mostly tape-truncation artifacts. Exactly one wallet (6FYgn2apNXSq) replicates across two independent days, and its style matches the archetype. The one forward-OHLC EV check available was red (-6%/trade) but on n=19 non-ARCH1 chase-biased rows — a caution flag, not a test of this trigger.

## 2. THE SPEC — `RIP_PULLBACK_ABSORB` (env `RIP_ABSORB_MODE=off|shadow|enforce`, default **shadow**)

- **Activation:** sol_pc_h6 > 1.5 only (the unsolved lane; do not dilute into all-green).
- **Universe/arm:** token +>=25% from its 60m low within last 6h of the green window; age <= 24h (hard block > 48h); standard liq floor + rug gates. Arm via existing rip_runners recorder pipeline.
- **Fire** (per io.dexscreener trade-log poll — ~100 recent trades covers 5m on these tokens):
  1. net signed flow last 300s <= -$100 (sell-dominant)
  2. max single non-self buy print last 60s >= $75
  3. price in lower 35% of prior-90m range OR >=30% below run high (GT minute / Jupiter buffer)
- **Anti-triggers (hard):** net300 > 0 with >=4 buys/5m; price in top quartile of 90m range; >6h since ignition; age > 48h.
- **Exit (rip-window profile):** tp1 +6% trim 25-30% (not 75%), remainder on a 10-minute-lookback trail with 8-12% giveback; hard floor -12 (A/B -15 in shadow — their MAE median was -15.8); **no timebox** while trail unbroken (35% of winner dollars needed >=60m holds). This is the already-shipped `badday_flush_wideexit_ab` shape — reuse it, don't build new exit code.
- **Sizing:** fixed $5 probe, max position <=1.5x median, absolute ban on averaging down below -15% (the single variable separating +$292 kEFiAX from -$2,225 AgmLJBMD on identical tokens). Pyramid-above-entry after a partial is permitted later, not in v1.
- **Companion ship (the skeptic's decisive fix, higher priority than the bot):** a live tape recorder that starts continuous io.dexscreener sweeps on runner pairs the moment sol_pc_h6 crosses 1.5, so the NEXT rip window's entry legs are captured and covered-net stops being forgeable.

## 3. EXPECTED ECONOMICS (honest)

Conservative-scored wallet data: ~49-56% WR, median ~breakeven, mean +$3.3-7.7 on ~$50-160 clips = roughly **+2-5% gross mean/trade, fat-tail-shaped (median ~0)**. After our haircuts (fresh-price fills cost ~3.5pp on stale-flush illusions — partially avoided here because the trigger fires on live tape, not a stale snapshot; ~1-1.5pp live slippage at $5): **plausible net mean +0.5-3%/trade at ~45-50% WR, with a realistic downside case of ~0 or slightly negative.** Volume: rip windows are a minority of days and the trigger is narrow — expect low single-digit fires per green window. This does not move the P&L needle at $5 sizing; it is an edge-validation instrument first.

Uncertainty is maximal: one day, one market mood, outcome variable conflates the wallets' exit skill, and the only independent forward check available was red. Treat every number above as a hypothesis, not a forecast (no dollar projections — n>=30 realized bar applies).

## 4. FALSIFICATION PLAN (realized-trade-join only; never the forward-candle scorer)

1. **Shadow bar before any enforce:** n >= 30 realized shadow fires, across >= 3 distinct sol_pc_h6>1.5 windows on different days, >= 15 distinct tokens, scored at fresh Jupiter fill prices with the wideexit profile. Enforce only if realized mean > +2%/trade AND WR >= 45%.
2. **Early kill:** realized mean < 0 at n = 20, or the crowd-chase anti-trigger cohort outperforms the fire cohort (would mean the flow-direction sign is wrong out-of-sample).
3. **Tape-recorder validation (parallel, decisive):** the next live-recorded rip window must produce >= 3 wallets meeting the strict bar (>=3 distinct profitable tokens, entry legs visible, net > 0 under 2x-cap scoring). If a fully-recorded window produces zero, the "someone makes money in these windows" premise is falsified for this wallet class and the lane reverts to defense.
4. **Forward follow-list (no capital):** kEFiAX3jo5Nm, 6FYgn2apNXSq, DkULcixfUQyg, J1sfMsbxGNXD, 8P1msjLVVaZd + the 06-29 greenday cohort. Kill-list stays killed (DJocqRPK, 7JCe3GHw, DF8tRgFk, AgmLJBMD, 2tgUbS9, the three bot farms) — do not let them resurface as winners in future mines.

## 5. VERDICT

**Build the cheap layer this week; do not build a capitalized bot.** Specifically: (a) ship the live rip-window tape recorder — it is the single highest-value item, costs ~a day, and makes the next harvest decisive instead of forgeable; (b) ship RIP_PULLBACK_ABSORB in SHADOW only — the signals are already in our feeds and the archetype survived conservative re-scoring as a real discriminator (+15-23pp WR over base across 25 tokens / 42 wallets); (c) keep the defensive stand-aside ENFORCED for live capital in rip windows until the Section-4 bar is met. The evidence does not support offense with real money this week — median-flat after cleaning, one-day sample, wallet-level winners consistent with luck — but it does support instrumenting the lane properly. This is not "doomed": the map says the flush+whale-absorption axis is the same edge that works on bad days, showing up on rip days with the right sign; what's missing is a clean multi-day sample, and both ships above are exactly the machinery to get it on the next green window.

**Artifacts:** cross-check code inline above (reproducible from `C:\Users\jcole\multichain-bot\scratchpad\ripday\entry_decode_pop.json` + tapes); lens artifacts at `C:\Users\jcole\multichain-bot\scratchpad\ripday\entry_decode3.py`, `entry_decode5.py`, `exit_lens_rows.json`, `skeptic_wallets.json`.