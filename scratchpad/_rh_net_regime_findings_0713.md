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

## Conclusion / levers toward the goal
The honest answer: RH net is **regime-beta** — worth-the-risk requires trading ONLY the paying windows, not always-on. Levers, in order:
1. **Tail-cap** (mechanical net lift; deployed in rh_stable_*).
2. **Regime/timing-sizing gate** — size down / sit out the losing days+hours. NEEDS a real-time regime signal (agent working it) + MORE TAPE to calibrate (3 days = can't validate a gate).
3. **Size** on the net-positive, friction-clearing slice — but only once regime-gated (else you size up the 2/3 losing days).
Scorecard now has a **REGIME-NET panel** (net-$/pos after friction, per day, REGIME-ROBUST flag) so this is measured forward, not hidden by median-%.
