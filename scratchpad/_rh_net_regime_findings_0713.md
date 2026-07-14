# RH net-$/position + regime findings — 2026-07-13 (autonomous block)

**Goal (AxiS):** get RH net-$/position higher AND sustainable across regimes, so it's worth the risk.

## 1. The net is genuinely thin — and it's NOT a paper illusion
- Best bot `rh_demand_heavy`: **+$0.52/position** after friction (+$26 total / 50 positions). Rest ~$0 or negative.
- CRUCIAL: paper `pnl_usd` already nets the **1% pool fee + price impact + gas** (real eth_call quotes, rh_paper_lane.py:23; gas $0.01/side). So the thin net is REAL, not a fee-cheating paper artifact. The scorecard's extra $0.20 friction is the estimated LIVE-EXTRA (latency-slippage during the ~1.2s fill) → net-after ≈ a conservative live net.
- So the friction isn't HIDING profit. The profit is just thin at $25 size.

## 2. NOT regime-sustainable — RH loses 2 of 3 days
Fleet-wide per regime-day:
| day | net USD | WR |
|---|---|---|
| 07-10 | −$50 | 62% |
| 07-11 | −$212 | 53% |
| 07-12 | +$253 | 73% |
- **Days net-positive: 1/3.** RH bleeds on typical days; one good day covers it.
- The good day's profit is concentrated **09-11 UTC (+$212 of +$253)** — a 3-hour window.
- Per-bot: EVERY bot is net+ only on 07-12, net− on 07-11. Beta to regime, not a standalone edge. The median-% metric HID this (07-11 median was +3.3% while dollars were −$212).

## 3. Weak hour-of-day structure (too thin to gate on)
- Only robust prime hour = **hour 0 UTC** (+$0.48/sell, 74% WR, spans 2 days). Trading only hr0: 90 sells, +$43, 74% WR.
- Hour 1 UTC consistently BAD (−$1.55/sell, 41% WR, 2 days).
- The big 9-11 UTC money is single-day (07-12) — likely regime, not a fixed window.
- 3 days is too thin to establish a reliable timing gate.

## 4. The #1 net lever = TAIL-CAP (mechanical, not overfit)
- `rh_deep_only`: net −$3 → +$18 by capping its ONE −25% rug at −$3.75.
- `rh_demand_heavy`: +$36 → +$44 (caps 3 catastrophic sells worth −$19.6).
- Cutting the 1-3 catastrophic sells per bot is where net improves. The `rh_stable_*` racers (−15 cap) already do this.

## 5. What does NOT work (overfit, ruled out)
- `liq>=50k`: negative at n=95 (the earlier +8.8% was n=5 noise).
- `deep+liq30-50k`: +1.71/pos but 51% one token, flips −2.81 on odd days.
- `flow_confirm`: known single-token leak.
- Finer selection keeps overfitting. Only demand_heavy + deep_only survive per-token OOS, but regime-fragile in $.

## ⭐⭐ REGIME-SIZING GATE — BUILT (shadow), the regime-sustainability lever
Agent result (rigorous, verified): the winning regime signal is NOT first-N-WR (under-flags 07-11 — its early trips looked fine) NOR market buy_share (inverted/pre-stamp). It's the **fleet-wide rolling expectancy dial** = mean net-$/pos of last 20 closed positions across ALL racers (REUSES the existing `expectancy_dial`, no new tuned constant). Ranks days cleanly: frac-entries-while-dial-negative = 07-12 0.28 < 07-10 0.67 < 07-11 0.87. Causal (only closed-before-entry), self-referential (every day), ~92s refresh, catches 87% of bad-day entries.
Gate: `would_size = 0.3x if last-20-fleet-dial<0 else 1.0x`. Causal shadow sim: 3-day net **−$43 → +$31 (Δ +$75)**, saves $126 on bad day for $53 cost on good day. Every sweep combo improved (+$45..+$133); pause-on-defense = +$133. SHIPPED SHADOW: core/rh_regime.py `regime_size()`/`regime_size_mode()` (RH_REGIME_SIZE env, default shadow), stamps regime_score+would_size; rh_paper_lane fleet_realized series. +10 tests. CAVEAT: 3 days ≈ 2-3 regime samples, can't validate — shadow-only, pre-reg promotion bar = ≥2 more bad-regime days with would_size<1 entries materially worse at n≥40. Reflexivity: keep stamping full-size counterfactual when enforced.

## (superseded) adaptive "is-today-working?" gate — first-N-WR under-flags the bad day
Sample-then-commit: trade a small probe batch each day; if the first-10 positions' WR < 60%, SIT OUT the rest of the day.
| day | first-10 WR | gate | full-day net | result |
|---|---|---|---|---|
| 07-10 | 30% | SIT OUT | −$50 | ✓ avoided |
| 07-11 | 50% | SIT OUT | −$212 | ✓ avoided the big loss |
| 07-12 | 100% | TRADE | +$253 | ✓ caught winner |
Would turn fleet −$9 (all-in) → ~+$216. Real-time computable, mechanistically sound (sample the regime before committing capital). CAVEAT: n=3 days (3/3 could be luck), 60% threshold fit to 3 days, probe trades still lose a bit on bad days. NEEDS forward validation — building as a SHADOW gate (regime-signal agent). This is the strongest lever found for "sustainable across regimes + worth the risk."

## whats-missing workflow (11 agents, adversarial) — cross-confirms + adds
- **4 of 5 "what are we missing" angles FAILED adversarial verification.** The apparent "money printing" is largely unrealizable MFE (peak) + paper-wick illusions, not realized $.
- **The ONE survivor: RH `demand_heavy` entry composition** (+5.37 ex2 vs −4.3 pooled; buy demand-heavy FLOW not dips). But n=12-13, OOS-fragile (H1 −2.3 / H2 +7.6), green concentrated in ONE token family (CASHCAT). GO/NO-GO gate: grade to n≥30 distinct tokens, four-half OOS, cat≤1/20, AND survive dropping the top token family. This is the first real edge the program has produced — fragile, needs the gate.
- **RH net is REAL (not wick-inflated):** RH ledger has only 5 legit >+30% sells; unlike SOL's fabricated $7,884 SPCX (43s +3913% paper-wick glitch). Fleet net −$9 over 3 days, carried by a few tail wins. SOL paper P&L IS inflated by wick fills → SOL needs a wick-fill sanity guard (realized%>>TP or hold<120s+extreme → flag). RH is clean.
- **RH MOMENTUM IS UNTESTED (real gap):** 100% of RH entries are dip-buys, into a tape that is 76-90% BUY-dominated with 47-55% of volume in the first pool-hour. The one strength racer (rh_launch_scalp) is mis-windowed (0.5-20min slot) and has fired 0 times. We insist on buying WEAKNESS in a market structurally BUYING. The specific momentum prescription failed verification, but the DIAGNOSIS is bulletproof — fix launch_scalp's window to actually fire + shadow-test buying RH strength.
- **Refuted (don't chase):** SOL freshness/<2h pond (survivorship — fresh pond also dies more), SOL mcap pond (OOS-fails, realized cap ~0), RH moonbag-holding (un-catchable; holding for tail = −38% ex2), SOL momentum (strictly worse), SOL entry selection (no signature, 3 ways).

## RH MOMENTUM — untested, diagnosed, ready decision (NOT shipped — AxiS call)
- `rh_launch_scalp` (entry_mode=launch_strength, buys STRENGTH) has fired **0 times**: its window is 30s-20min (`min_pool_age_h=0.5/60`, `max_pool_age_h=20/60`) but the FEED can't surface pools that fresh — by the time a hood.fun pool has 30k liq + enough tape to be watchable, it's already >20min old. Age gate is per-bot (rh_paper_lane.py:1459), NOT a global block — so it's a WINDOW mismatch, not a floor.
- So 100% of RH entries are dips; we have NEVER tested buying strength, in a tape that's 76-90% buy-dominated with 47-55% of volume in the first pool-hour.
- CONCRETE FIX (one-word greenlight): re-window a strength racer to the REACHABLE **15-60min band** (min_pool_age_h=0.25, max_pool_age_h=1.0) + launch_strength entry + rug guards + tight scalp exit (tp1 +5/0.9, stop -8, 10min timebox). Paper/shadow to GENERATE the momentum-test data. CAVEAT: the workflow's momentum PRESCRIPTION failed adversarial verification (survives=false) — so this is a DATA-GENERATING test, not an expected win. Held for AxiS because it's a new strategy DIRECTION + involves fresher (rug-riskier) pools.

## Conclusion / levers toward the goal
The honest answer: RH net is **regime-beta** — worth-the-risk requires trading ONLY the paying windows, not always-on. Levers, in order:
1. **Tail-cap** (mechanical net lift; deployed in rh_stable_*).
2. **Regime/timing-sizing gate** — size down / sit out the losing days+hours. NEEDS a real-time regime signal (agent working it) + MORE TAPE to calibrate (3 days = can't validate a gate).
3. **Size** on the net-positive, friction-clearing slice — but only once regime-gated (else you size up the 2/3 losing days).
Scorecard now has a **REGIME-NET panel** (net-$/pos after friction, per day, REGIME-ROBUST flag) so this is measured forward, not hidden by median-%.
