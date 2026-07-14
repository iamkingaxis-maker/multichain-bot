# Session Discipline Decode — 2026-07-06

**Question:** do net-positive wallets show loss-streak discipline (stop/pause after consecutive losing round trips) while losers keep grinding?
**Window:** 2026-07-05T12:00Z → 2026-07-06T13:44Z (tape end). 316 tapes, 182,690 deduped legs (key: ts+maker+kind+usd+pair), 120 pairs.
**Method:** per established behavior-decode method (`_behavior_decode_2026_07_06.md`): anonymous union-counted per-wallet USD deltas. Round trip = within wallet-pair, buy-accumulation closed by sell(s) until next buy; requires sell ≥50% of buy (3,865 partials/holders dropped); scrub rule applied (360 dropped: ret>0 & hold<10s). 23,892 round trips.
**Cohorts:** 641 qualified wallets (≥5 RTs in window): **510 winners** (med total +$51.8) vs **131 losers** (med −$9.2). RTs 10,714 W / 1,156 L across 113 pairs.
**Scripts:** temp scratchpad `_sd_0706.py`, `_sd_0706b.py`.

## (a) Behavior after 2 consecutive losing round trips

| cohort | events n | med min to next buy | P(next buy ≤15m) | P(pause >60m) | P(pause >4h) |
|---|---|---|---|---|---|
| WIN (all) | 1,480 | **2.1** | 0.82 | **0.08** | 0.04 |
| LOS (all) | 364 | 9.9 | 0.66 | **0.26** | 0.17 |
| WIN (human-scale*) | 271 | 14.1 | — | 0.23 | 0.15 |
| LOS (human-scale*) | 228 | 8.0 | — | 0.21 | 0.12 |

*human-scale = 5–30 RTs and median buy ≥$10 (256 W / 83 L wallets) — strips MM/arb-bot grinders.

- **The hypothesis is INVERTED at the pooled level:** winners re-engage after 2 losses in a median 2.1 min and pause >60min only 8% of the time; losers pause 3x more often (26%). Winners do not stop — they grind harder.
- The pooled result is partly driven by hyperactive grinders: wallets with >30 RTs are **59/61 net-positive** (med +$617). On this tape, grinding correlates with WINNING (these are bot/MM-like).
- Human-scale strips that and the two cohorts look the same after 2 losses (14.1 vs 8.0 min; P(pause>60m) 0.23 vs 0.21). The only discipline-flavored signal anywhere: **at 3 consecutive losses, losers ACCELERATE (8.0 → 4.7 min) while winners hold pace (14.1 → 14.7 min)** — tilt shows as speeding up, not failing to stop. n=90 W / 123 L events.
- Quality of the post-streak trade: winners' next RT after 2 losses stays good (human-scale med **+7.48%, WR 61%**, n=244 — at or above their after-win baseline +6.04%/62%). Losers' next RT is bad (−1.96%, WR 36%, n=197) — but their after-WIN baseline is also bad (−3.51%, WR 38%). **Losers are bad after wins too: the axis is selection skill, not stopping behavior.**

## (b) Same-token persistence after a loss

| cohort | P(re-buy same token after LOSS) | after WIN |
|---|---|---|
| WIN | 0.50 (n=4,009 events / 475 wallets) | 0.58 (n=6,529) |
| LOS | 0.50 (n=694 / 131) | 0.67 (n=445) |

No winner/loser separation after losses — both re-buy the losing token half the time. Both cohorts prefer re-buying tokens they just won on; losers chase their own winners hardest (0.67). Same-token revenge is NOT the loser marker either.

## (c) Daily round-trip counts
- WIN: RTs/wallet-day p25/med/p75/p90 = 3/5/9/20, max 954 (n=898 wallet-days).
- LOS: 3/5/7/10, max 70 (n=203).
- Medians identical; winners own the heavy-activity tail. Trade count does not separate.

## (d) Revenge (≤15m after a loss) vs cooloff (>60m), within-wallet skill control
- Pooled: revenge med **−0.25% WR 48%** (n=1,406) vs cooloff med **+2.13% WR 56%** (n=396) — looks like a 2.4pp revenge tax.
- **Within-wallet** (wallets with both trade types, n=142): median diff = **+0.86pp IN FAVOR of revenge** (human-scale: +1.58pp, n=72).
- The pooled "tax" is composition (which wallets take cooloff trades), **not causal**. For a given wallet, waiting an hour does not improve the next trade.

## (4) Aggregate: trade after a loss vs after a win
| cohort | after-LOSS med ret / WR | after-WIN med ret / WR | tax |
|---|---|---|---|
| WIN | +3.42% / 59% (n=3,878) | +6.15% / 63% (n=6,326) | −2.72pp / −4.0pp WR |
| LOS | −2.17% / 35% (n=611) | −1.02% / 40% (n=414) | −1.15pp / −5.1pp WR |
| ALL | +2.04% / 55% (n=4,489) | +5.20% / 61% (n=6,740) | **−3.16pp / −5.8pp WR** |

The after-loss trade IS worse — for both cohorts, similar magnitude. Combined with (d), this is **loss clustering (regime autocorrelation)**, not human tilt: losses happen in bad tape stretches, and the next trade in that stretch is worse regardless of who takes it.

## VERDICT
**"Winners stop, losers grind" is FALSIFIED at the wallet level.** Winners re-engage faster after losses and their post-loss trades remain good; the pooled revenge-tax is a composition artifact (within-wallet it vanishes, +0.86pp for revenge). The only real behavioral tell is losers speeding UP as streaks deepen. Skill (what they buy next), not discipline (whether they pause), separates the cohorts — winning humans rotate: P(same-token re-buy after loss) is only 0.50.

**BUT the streak is still a deployable regime signal for OUR fleet** — because our bots are the opposite of rotating humans: they keep firing the same signal into the same degraded stretch. The aggregate after-loss tax (−3.2pp med, −5.8pp WR) is exactly the autocorrelation a mechanical pause harvests.

## Fleet join (_fro.json, 2,163 positions / 24 bots, 06-27 → 07-06)
Positions rebuilt from sells grouped by (bot, address, entry_time = time − hold_secs, 30s bucket); position pnl = sell_fraction-weighted pnl_pct. Sequential per-bot simulation (blocked trades don't update streak state; pause runs from last losing close).

| rule (per-bot) | FULL 9d: blocked / net pp saved / winners killed | 07-05T12Z window: blocked / net / killed |
|---|---|---|
| A: 2 consec any-loss → 60m pause | 1,031/2,163 / **+1,230pp** / 231 (22%) | 105/243 / +274pp / 18 (17%) |
| B: 2 consec floored → 60m | 904 / +1,138pp / 194 (21%) | 89 / +243pp / 14 (16%) |
| **C: 3 consec any-loss → 60m** | **832 / +1,626pp / 164 (20%)** | **75 / +294pp / 7 (9%)** |
| D: 2 consec floored → 120m | 1,186 / +674pp / 287 (24%) | 103 / +246pp / 18 (17%) |
| E: same-token 4h loss-lockout (SEMAN-style) | 517 / +685pp / 117 | 46 / +144pp / 8 |

- Blocked trades' median pnl is −4.6 to −5.3% everywhere — our post-streak entries are loser-cohort-shaped (wallet losers' post-streak RT: −2.05%, WR 34%).
- **Rule C robustness: 16/17 bots net-positive; 7/10 days positive** (worst day 07-04: −176pp giveback; best days +404 to +424pp on 07-02/03/05). Only bot hurt: `badday_young_absorb` −11.2pp (n=14) — the green young lane; consider exempting it.
- SEMAN: 46 fleet positions across 9 bots, serial losses across the day — rule E alone would have blocked 46 window trades for +144pp; rule C subsumes most of it.
- Winner-kill: 9% of blocked on the window (7 trades, 56pp — already netted), 20% on full period. Above the ≤5% winner-kill audit bar — flagged, but net savings dwarf it at every cut.

## Deployable predicate (mechanical cooldown — sanctioned rule class)
**Per-bot: after 3 consecutive losing position closes (position-level weighted pnl < 0), pause NEW entries for 60 min from the last losing close. Streak resets on any winning close. Optional: exempt `badday_young_absorb`.**
Expected on recent tape: ~+294pp saved per ~25h window across ~9 active bots (~+1.2pp per fleet position), 9% winner-kill; +1,626pp over the full 9-day file, positive for 16/17 bots and 7/10 days.

## Caveats
- Single ~25.7h tape window; loser cohort is small (131 wallets / 1,156 RTs) — after-2-loss loser cells n=194–364 events.
- Moonbag bias: partial-exit holders can misclassify as losers (biases against the "losers grind" finding, not for it).
- Fleet sim is counterfactual: assumes blocked entries don't change later fills; pnl weighting by sell_fraction is approximate for peeled positions.
- The wallet decode says the pause is NOT what winning humans do — the fleet gain comes from loss-clustering in OUR signal, so re-validate after any major entry-gate change.
