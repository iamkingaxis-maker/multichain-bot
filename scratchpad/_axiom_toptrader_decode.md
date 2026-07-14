# Axiom Top-Trader Decode — 2026-07-05

## Source (and why it's not Axiom directly)

**Axiom is hard auth-walled.** Verified directly: `api6.axiom.trade/*` and `api8.axiom.trade/*` return 502 `{"error":"No auth cookies present"}` / `"Session invalid, please login again"` on every leaderboard-shaped path; `axiom.trade/leaderboard` serves a Cloudflare challenge. No public API exists (web search confirms only auth'd SDKs).

**Fallback used: gmgn.ai public leaderboard** (`gmgn.ai/defi/quotation/v1/rank/sol/wallets/7d`, 200 OK via curl_cffi chrome impersonation) — **filtered to wallets gmgn itself tags as `axiom` platform users**, so the cohort IS Axiom traders, ranked by 7d realized PnL. This is the closest honest reconstruction of "Axiom's top traders" available without login.

- 60 wallets selected (axiom-tagged, top of pnl7d + smart_degen ranks)
- 8,094 trades harvested over a 5-day window (140-trade cap/wallet — for the hyperactive wallets that's only ~1-3 days of history)
- 1,271 unique tokens; 996 resolved on DexScreener (275 dead/delisted = rugs/never-graduated)
- **mcap at trade time** computed honestly as `price_usd x total_supply` from each trade record (not current mcap)
- Leaderboard PnL figures ($6k-$729k/7d) are gmgn-computed and unverifiable from our side — treated as marketing numbers. Win rates we quote below are our own matched computations.

## Task 2 — Do they trade our mcap ranges?

**Classification** (IN-POND = >=25% of buys with mcap-at-trade $100k-5M AND token age <7d):

| Class | n | Notes |
|---|---|---|
| IN-POND | 15/60 | 14 with n>=10 buys; AvHAW2Aj ≡ YupUTKEj (token-set jaccard 1.00, same operator) → **13 unique operators** |
| ADJACENT | 1 | GvmgmxkE — $5M+ caps |
| OUT/MIXED | 44 | dominated by sub-$100k fresh pump.fun sniping — BELOW our mcap floor |

Key structural finding: **the biggest earners are NOT in our pond.** Cupsey ($729k/7d, 2fg5QD1e) is 2% in-pond; the $50k-300k/7d tier lives almost entirely in sub-$100k fresh-launch sniping at 300-1000 tx/day (several carry gmgn `wash_trader` flags — excluded from all behavioral claims). The in-pond cohort earns $6.6k-$45k/7d realized (per gmgn), token-level winrate_7d 0.36-0.61 (med 0.50).

Top in-pond wallets by pond share: AvHAW2Aj 79%, AoZ74Czd 74%, B9kJYdzb 56%, BtMBMPko 53%, Hv2xzYAQ 47%, 45tKsjwZ 44%, 2p2mgFLm 40%. Full table in `scratchpad/_toptrader_class.json`.

**Direct tape intersection (strongest evidence):** 15 of the 60 wallets appear as makers in OUR OWN recorded tapes (387 token tapes, 355k trades, last ~4 days) — including 5 IN-POND wallets: 9Q5Jd6W2 (12 trades), AoZ74Czd (11 trades/$746), 2p2mgFLm ($905), Hv2xzYAQ ($1,557), 6S8Gezkx. They are literally in our exact pools on our exact tokens.

## Task 3 — Behavioral profile (13 operators, union-counted)

Coverage: 829 buys (13% zero-cost dust excluded), 819 matched sells, 238 wallet-token episodes; entry-style decode on 180 pond buys with bar coverage (res=5 history caps at ~3.5d; 12 tokens had no bars).

| Axis | Them (in-pond top traders) | Us |
|---|---|---|
| Buy size | med $132, p75 $242, p90 $717 | $25-100+ clips |
| Sizing shape | **scale-in heavy**: 30-88% of buys are adds before first sell (most wallets 40-60%) | one-shot clip |
| Entry style | **bimodal & per-operator**: 38% deep dips (<=-15% off 60m high), 45% at/above prior-hour high. Dedicated dippers exist: AvHAW2Aj 62%, 45tKsjwZ 71%, B9kJYdzb 67%, Hv2xzYAQ 55% dip-share | dip-only (correct for us; do NOT chase the breakout half — falsified repeatedly) |
| Dip timing | buy ~49 min after the 60m local low (p25 33m) — **after stabilization, not the first flush** | pump-retrace gate + consolidation racers point the same way |
| Token age at buy | med 13.8h (p25 6.4h, p75 55h) | young lane <24h; **independently confirms the 6-24h adolescent_absorb pond** |
| mcap at buy | med $832k (p25 $502k, p75 $2.6M) | $100k-1M young / to $5M family |
| Exit speed | **episode first-buy→last-sell med 1.1 min, p75 7 min, p90 36 min** — sell into the first spike, in pieces | TP +6/+12, velocity bail; we hold much longer |
| Exit shape | many partial sells per episode: per-sell med +52% vs episode med +7% ⇒ they peel winners in tranches and let a runner ride | mostly full-position tiered TP |
| Realized (matched, sold portion only) | episode win 64%, med +7%, p25 -6.5%, p75 +33% — **fat right tail carries the P&L, median is modest**; per-sell 79%/+52% is inflated by partial-sell counting + 3/29 unsold bags; gmgn token-level winrate med 0.50 | consistent with our fat-tail selector finding (median stays thin) |
| Loss cutting | episode p25 -6.5% — they cut at roughly -5 to -10% | matches our -7 MAE floor |
| Re-entry | only 13% of episodes re-enter after a sell — swing/latch is minor for them | swing_latch shadow — keep expectations modest |
| Hours (UTC) | buys peak 14-16 & 19-21 (= our 13-22 prime); secondary 05-07 cluster | 03-08 block; 13-22 prime confirmed |

## Actionable learnings (ranked by evidence strength)

1. **Exit shape: peel partials into the first spike, keep a runner** (n=819 matched sells, 238 episodes — strong). Their median episode is only +7% but they bank it in <7 minutes in tranches and keep exposure for the +33%/+470% tail. Our fixed +6/+12 full exits either leave the tail or hold the median too long. Concrete delta: on first green spike post-entry, sell ~half at +5-8% (covers the 3.5pp fee round-trip), trail the rest — this is a TP-shape A/B on existing exits, not new machinery. Complements GIVEBACK_TRAIL_SHADOW.
2. **Sizing shape: half-clip entry + add-on-confirmation** (40-60% of their buys are scale-in adds — strong on prevalence, unproven on causality). Instead of one $50 shot, $25 at the dip signal + $25 only if demand confirms (buyers still present N minutes later). Two-sided ruin math already argues for this; it also cuts never-green bleed since the second half never deploys without confirmation.
3. **Stabilization confirmation on dips** (n=180 scored entries, med 49 min from local low — medium). The successful dippers among them buy well after the flush low, not into it. Our pump-retrace gate + consolidation racers are directionally right; consider a minimum time-since-local-low (>=30m at 5m granularity) as a racer variant.
4. **Pond validation, not a change** (strong): their age-at-buy med 14h and mcap med $832k lands exactly on our adolescent_absorb winners' pond. The 6-24h window thesis now has third-party confirmation from currently-profitable wallets.

Anecdotal only (n too small, do not act): dip-entry vs breakout-entry episode outcomes (n=26 episodes with sells: DIP 60% win / BREAKOUT 71%); the 05-07 UTC activity cluster (mixed tokens, modest n) — do NOT reopen the 03-08 block on this alone.

## Verify tomorrow before shipping anything

1. Re-pull the same 13 operators' activity (1 day fresh) — confirm exit-shape and scale-in stats replicate out-of-window before building the TP-peel A/B.
2. Track the 3/29 unsold episodes: if those bags went to ~0, episode win rate drops ~5-8pp — reprice learning #1's median.
3. For the 5 wallets seen in our tapes, replay our tape around their fills: did they enter before/after our bots on shared tokens, and at better prices? (Direct head-to-head on identical tokens.)
4. Half-clip + confirm-add: spec as SHADOW stamp first (log what the second half would have done) — zero live risk.

## Intermediates
`_toptrader_wallets.json` (60 wallets + gmgn stats), `_toptrader_activity.jsonl` (8,094 trades), `_toptrader_tokens.json` (996 resolved), `_toptrader_class.json` (classification), `_entry_scored.json` (180 scored entries), scripts `_axiom_probe.py`, `_gmgn_harvest.py`, `_ds_resolve.py`, `_classify.py`, `_behavior.py`, `_entry_decode.py`, `_entry_outcomes.py` — all in scratchpad/.
