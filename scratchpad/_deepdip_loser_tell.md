# Deep-dip loser tell — overnight analysis (2026-07-14)

**Data:** local `rh_paper_trades.jsonl` (yesterday's), deep-dip family joined buy→sell.
**n = 17 realized trips** (10 win / 7 lose, 58.8% win-rate). Median win +25.4%, median loser −4.9%.
Small n + cross-bot mix (barbell/deepdemand/deep_only have different triggers) → **DIRECTIONAL, not deploy-grade.**

## What separates winners from losers (entry-time)
| signal | winner median | loser median | read |
|---|---|---|---|
| **liq** | $48.4k | **$51.5k** | NO separation — losers even a touch deeper |
| **dip_pct** | **−26.9%** | −22.1% | winners dipped DEEPER (steeper flush bounces more) |
| **age_h** | **20.1h** | 44.3h | winners YOUNGER; old dips = dying tokens |

## Key structural insight
**Liquidity depth is a FILL lever, not an entry-SELECTION lever.**
- It does NOT predict winner vs loser (table above).
- It DOES fix slippage — the OSMO live loss took a near-max-slippage sell on a thin book; a $30k floor (capped) would have avoided that fill.
- So `rh_deep_barbell_capped`'s value = cleaner execution, NOT better token-picking. Don't promote it expecting better *selection*.

## Robustness
Deep-dip net (sum pnl%) = +242; **drop-top-2 = +122** (top2 = 79%, 41%). Edge survives dropping the fat tail → real, not luck.

## Verdict
- The tell is **dip depth + pool age** (deeper + younger = bounce), consistent with [[reference_bounce_vs_knife]] and the young-lane work. Liq only helps fills.
- **n=17 < 30 and cross-bot contaminated → NOT enough to add a new live/paper bot tonight** (would be overfitting per no-bandaids / forecast-calibration). barbell's −25% trigger is already in the "deeper" zone; the losers cluster in the looser-trigger sibling bots.
- **Plan:** let the live probe + existing paper fleet accumulate deep-dip trips to n≥30 per-bot, then test a "younger-pool + deeper-dip" barbell variant as a paper A/B with a clean held-out check. Revisit tomorrow.
- **Live-probe implication:** if fills (slippage) become the pain (OSMO-type), the capped liq floor is the lever — but as a fill fix, sized against throughput, not as a selection upgrade.
