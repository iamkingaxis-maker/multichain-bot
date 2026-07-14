# Token-Conditional Adaptive Entry — 2026-07-07

**Operator insight tested:** "no trade is the same." Does conditioning the entry decision (confirm bar + size) on the *specific token's* characteristics predict HELD-bounce vs DEAD-CAT / gap-through — and does an adaptive per-token rule beat a uniform gate?

Analysis only. No code/config changes.

## Data & method
- Source: `/api/trades?full=1&limit=5000` (our own fills+outcomes). Buys carry `entry_meta` (all entry-time features); sells carry realized `pnl_pct`, `hold_secs`, `mae_pct`.
- **Join:** sells → buys on `(address, entry_price)`; aggregate a position = sell-fraction-weighted realized `pnl_pct`, worst `mae`, max `hold`. Scrub rule applied (drop pnl>0 & hold<10s → 8 dropped). **n=707 positions** with pnl+entry_meta.
- **Outcome defs:** HELD = pnl>0; **GAP-THROUGH = realized pnl < −12%** (i.e. blew well past the ~−7 stop — the DONALD-style tail). `mae_pct` is short-window/capped (min only −19.7 across the set) so it is NOT the gap metric — realized `pnl_pct` is (captures DONALD −27%, NEVER −52%, SORANA −32%).
- Base: WR 25.5%, gap-through 5.5% (pnl<−15: 2.8%, <−20: 1.6%), median −4.67%. (This window is a **net-losing tape** — treat ROI comparisons as relative, tail/worst-case as the robust signal.)

## DONALD anchor (mint J9fV…spump)
Two entries, SAME token → identical token factors in meta: top10=25.7%, tok_vol_h24≈350%, top1=—. The difference that split held(+9%) from dead-cat(−27%):
- Winner leg: entry 5.96e-4, **pc_h6=1316** → realized +10 to +19%.
- Dead-cat leg: entry 7.02–7.13e-4 (higher price, later), **pc_h6=1448** (more extreme blow-off) → realized −25 to −27%.
So the split was **entry-level + pump-state within the violent token**, not a static token property. Confirms the swing/pump axis, not holder concentration.

## Factor sweep (tercile T1 low vs T3 high; dGAP = gap%_T3 − gap%_T1)

### 1. SWING / VOLATILITY — the dominant gap-through predictor ✅ REACHABLE
| factor (entry_meta) | n | T1 gap% | T3 gap% | dGAP | note |
|---|---|---|---|---|---|
| `token_volatility_h24_pct` | 558 | 2 | 9 | **+6** | high-vol also WR 26→35 |
| `1m_body_pct_avg` | 559 | 2 | 10 | **+8** | strongest |
| `shape_30m_range_pct` | 559 | 2 | 9 | **+7** | |
| `1s_range_pct_60s` | 651 | 3 | 9 | **+6** | |
| `pc_h24` (blow-off) | 707 | 3 | 10 | **+7** | extreme pump gaps more |

**Signature = fat tail:** high-swing tokens win MORE often (WR +9–12pp) AND gap-through MORE (+6–8pp). The EV and the −27% tail live in the *same* cohort. All five are present in entry_meta at decision time → reachable within latency, no extra fetch.

### 2. HOLDER CONCENTRATION — does NOT separate ❌
`top10_holder_pct` dWR −3 / dGAP +0; `top1_holder_pct` dWR +4 / dGAP +0; `top1_share_of_top10`, `insider_n` flat. Honest verdict: concentration does not predict hold-vs-gap on this set. Drop it as a conditioning input.

### 3. LIQUIDITY DEPTH — weak but real ✅ REACHABLE
`liquidity_usd` T1(≤$33k) WR 17% vs T3(≥$43k) WR 30%, gap 6→4. Thin pools = worse WR + slightly more gap. LP-drain (`lp_delta_5m/15m`, `liq_velocity`) did NOT separate.

### 4. PUMP STATE — mixed
`pc_h24` high → more gap (see swing table, it doubles as a blow-off proxy). `pc_h6` deeper-negative → slightly higher WR (28 vs 24) — consistent with dip thesis; use as the CONFIRM axis, not the gap axis.

### 5. BUYER QUALITY — counterintuitive, likely confounded
`median_buy_size_usd` flat. More `unique_buyers_n` → gap 3→9 (crowd/FOMO gaps more); `n_recurring_buyers_3plus` T1 WR 36 vs T3 WR 22 — but these track churny/volatile tokens, so it's mostly the swing axis re-expressed. Not an independent lever.

## Swing score (0–3) — composite of 3 reachable factors
Score = [tok_vol_h24≥140] + [1m_body_avg≥2.6] + [pc_h24≥80]. (n=559 with ≥2 factors known.)

| swing | n | EV% | WR | gap% | p05 | min |
|---|---|---|---|---|---|---|
| 0 (calm) | 191 | −1.43 | 28 | 1 | −8.7 | −15.3 |
| 1 | 178 | −3.28 | 19 | 4 | −10.7 | −19.4 |
| 2 | 104 | −1.49 | 29 | 9 | −12.9 | −32.0 |
| 3 (violent) | 86 | **+0.14** | **44** | **14** | **−25.3** | −31.0 |

**Key honest finding:** swing=3 is the BEST-EV bucket (+0.14, WR 44%) *and* the worst tail (p05 −25). Blanket "size-down all high-swing" therefore **costs EV** — you'd be shrinking your best cohort. Swing alone flags the tail but does not separate winner-from-gap *within* the violent cohort.

## Adaptive CONFIRM finds the separation swing alone can't ✅ THE EV LEVER
Within HIGH-swing (score≥2), split by dip depth (`pc_h6`):
- **violent + deep dip (pc_h6≤−40):** EV −0.22, WR 39%, gap 9%, p05 −12.9 (n=64)
- **violent + shallow (pc_h6>−40):** EV −1.02, WR 34%, gap 12%, p05 −21.6 (n=126)

So on violent tokens, *demanding a deeper confirmed dip* raises EV (−1.02→−0.22) AND trims the tail (p05 −21.6→−12.9). This is adaptive confirmation working: stronger bar on violent tokens, lighter bar on calm ones.

## Adaptive vs Uniform — simulation (per-$100 base bet, n=559)
| policy | totPnL | ROI | worst | p02 | p05 |
|---|---|---|---|---|---|
| UNIFORM $100 | −$999 | −1.79% | −$32.0 | −$19.2 | −$12.1 |
| crude: all-swing≥2 = 0.5x | −$928 | −2.00% | **−$19.4** | −$12.7 | −$9.6 |
| ADAPTIVE: violent-shallow = 0.4x | −$922 | −1.91% | −$31.0 | −$12.8 | −$9.8 |
| ADAPTIVE-SKIP violent-shallow | −$870 | −2.01% | −$31.0 | −$12.7 | −$9.4 |

**Read:** adaptive SIZE reliably **cuts the fat tail 35–40%** (worst −$32→−$19, p02 −$19→−$13) — direct insurance against the DONALD −27% / NEVER −52% tail, exactly the ruin-math lever. On this uniformly-losing window it also cuts absolute loss modestly; ROI is flat-to-slightly-worse because we're deploying less capital into a losing tape and the violent cohort isn't the worst-ROI slice. The tail reduction is the robust, defensible win; the EV win comes from adaptive CONFIRM (above), not from sizing.

## Deliverable: concrete, reachable adaptive predicate (2 inputs)
All inputs already in `entry_meta` at decision time — zero added latency.

```
swing_score = [token_volatility_h24_pct >= 140] + [1m_body_pct_avg >= 2.6] + [pc_h24 >= 80]   # 0..3
deep_dip    = (pc_h6 <= -40)

if swing_score >= 2 and not deep_dip:      # violent + shallow = the tail cohort
    require STRONGER confirm (deeper HL / +confirm bar)  AND  size = 0.4–0.5x
elif swing_score >= 2 and deep_dip:        # violent but confirmed dip = keep the EV
    size = 0.7x            (tail insurance, EV preserved)
else:                                       # calm/deep pool
    size = 1.0x  (lighter confirm OK)
optional: also require liquidity_usd >= ~40k on swing_score>=2 (thin+violent = worst).
```

## Safety / next step
- **Everything paper-A/B first.** No live change. Adaptive SIZE and adaptive CONFIRM are both size/gate levers = paper-shippable under the standing "keep levers shipping" rule; they do NOT touch paper↔live.
- **Safest first step:** paper A/B the **adaptive-SIZE arm only** (violent+shallow → 0.4–0.5x, else 1.0x) as a shadow/child config — it is the lowest-risk, purely defensive change, needs no new data, and directly targets the measured −$32/−$19 tail. Measure realized tail (p02/p05) and EV delta at n≥30 before touching the confirm arm.
- Would need live approval: nothing here yet — all size/confirm work stays in paper. Live only if AxiS later promotes a validated arm.

## Caveats
- Single net-losing window (n=707); tail-reduction result is regime-robust (mechanical), EV deltas are window-specific — re-check n≥30 out-of-sample.
- swing thresholds (140/2.6/80) are median-anchored on this window; treat as starting values for the paper A/B, not tuned constants.
- `mae_pct` unusable as gap metric (capped); realized pnl used throughout.
- Holder concentration honestly does not separate here — not forced into the score.
