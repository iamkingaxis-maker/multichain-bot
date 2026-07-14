# TP-Peel Exit Replay — winner capture — 2026-07-05

**Question:** does our TP2 +12 cap amputate the right tail that the Axiom in-pond wallets keep by peeling (sell ~half at first spike, trail a runner, no cap)?

**Answer:** the hypothesis is FALSE as stated, but a conditional variant of the peel is a real lever. Our TP2 is a **soft** cap — TP1/TP2 checks fill at loop-cadence observed price, not at the trigger, so wick-spike winners already exit the full position at +26/+33/+45/+64/+70 (TP1 and TP2 fire the same second at the same price). An unconditional peel gives half of those wick fills back (the wick collapses before any trail can act) and **loses** −59.6pp (8pp trail) / −17.1pp (5pp trail) vs realized over the window. The money is in the middle: winners whose TP1 fill is modest (+5..+12) currently hand the remainder to a 2pp trail/breakeven-lock that exits near flat, while bars show runners going +24..+35. **Peel only when the TP1 fill < +12, with a 5pp trail: +72.0pp over the 4.5-day window, positive in both halves, gains spread over 31 positions (top single gain +14.6pp), worst single loss −4.6pp.**

## Data & method

- One pull per bot of `/api/bots/{bot}/trades?limit=1000&meta_keys=_none_` (2026-07-05). Closed rounds reconstructed by joining sells to prior buys per token (round ends at `fully_closed`). Scrub rule applied: 24 sell legs with pnl>0 & hold<10s dropped.
- **Positions since 07-01: 238** (173 flush / 60 young_absorb / 5 adolescent_absorb). Winners = recorded peak_pnl_pct >= 3.
- Bars: GT minute OHLC `[entry, entry+6h]`, 3s pacing + 429 backoff, checkpointed. **Coverage 235/238** (missing: flush Indy x2, young Fatcoin — Fatcoin is TP1-fired realized +37.9, excluded equally from all policy columns). dexscreener io res=1 was tried and rejected: it ignores `to` (recent bars only).
- All 80 winners AND all 158 losers replayed (no sampling needed).
- **Conservatism:** replay skips the entry bar entirely (its pre-entry flush low falsely triggers stops, its high may predate entry); downside exits processed before upside within each bar; running peak updates end-of-bar (trail rises late = worse fills); trail/stop fills at min(trigger, bar open); TP fills at max(trigger, bar open). Runner-side numbers are a pessimistic bound.
- **Two replay modes.** PURE (everything simulated on bars — comparable across policies but strips loop-cadence fill advantage from all of them) and **HYBRID (headline)**: every actual exit up to and including TP1 kept at its actual fill; only the post-TP1 remainder differs (current: 0.75@TP1fill + 0.25@actual later legs; peel: 0.50@TP1fill + 0.50@trail-sim from actual TP1 timestamp). Positions where TP1 never fired are identical by construction — this isolates the exit-shape change from replay-fill noise.

## Winner inventory & current capture

| bot | positions | winners | losers | realized total | winner pp | loser pp |
|---|---|---|---|---|---|---|
| badday_flush | 173 | 55 | 118 | −196.5pp | +452.6 | −649.1 |
| badday_young_absorb | 60 | 21 | 39 | +77.8pp | +317.6 | −239.8 |
| badday_adolescent_absorb | 5 | 4 | 1 | +83.7pp | +90.1 | −6.4 |

Winner capture ratio (realized / recorded peak): med 0.64, p25 0.50, p75 0.83. Current post-TP1 remainder (0.25 leg) realizes med −0.5pp *relative to its own TP1 fill* (p75 +1.6) — the 2pp trail/belock gives the runner back almost nothing above TP1.

## Policy scoreboard — HYBRID (actual fills + runner sim)

Winners only (losers identical by construction — zero losers ever fired TP1):

| bot (winners) | realized | PEEL-8 | PEEL-5 |
|---|---|---|---|
| flush n=55 | **+452.6** (med +6.4, top5 +178.6) | +410.2 | +445.4 |
| young n=20* | **+279.7** (med +9.6, top5 +177.1) | +264.5 | +268.0 |
| adolescent n=4 | **+90.1** | +88.1 | +91.8 |

*1 TP1-fired winner (Fatcoin +37.9) excluded from all columns — no bars.

**Unconditional peel loses.** Across the 66 TP1-fired positions: PEEL-8 delta −59.6pp (win% 36), PEEL-5 delta −17.1pp (med +0.05, win% 52). Both halves negative. Cause: 14 positions had TP1 fill ≥ +12 (wick fills at +12..+70); current policy sells 100% into the wick; the peel's runner exits near flat because the next bar opens back at ~0 (genuine gap — even a live tick trail can't catch a one-tick wick).

**Conditional peel wins** — runner only when TP1 fill < +12 (else behave exactly as today):

| variant | total delta vs realized | eligible | notes |
|---|---|---|---|
| cond<12, 5pp trail | **+72.0pp** | 52/66 | best |
| cond<10, 5pp trail | +72.0pp | 45/66 | same |
| cond<12, 8pp trail | +26.5pp | 52/66 | |
| uncond, 5pp | −17.1pp | 66/66 | |
| uncond, 8pp | −59.6pp | 66/66 | |

Per-bot / per-half, cond<12 + 5pp trail:

| bot | 07-01/02 | 07-03+ | total |
|---|---|---|---|
| flush | +18.0 (n=15) | +45.7 (n=29) | **+63.7pp** (win% 52) |
| young_absorb | −0.7 (n=3) | +9.9 (n=15) | **+9.3pp** (win% 44) |
| adolescent_absorb | — (n=0) | −1.0 (n=4) | −1.0pp |

Gain structure: 31 gainers +89.9pp vs 21 losers −18.0pp; top gain +14.6 (flush ELIZABETH), top-3 +38.3; worst loss −4.6 (flush JELLYCAT). Gains spread across distinct tokens and days — not one-token luck. Zero runner timeouts at the 6h horizon.

## Loser-side no-harm check

- HYBRID: **exactly zero change** — no loser (peak<3) ever fired TP1, and pre-TP1 exits (stop/velocity-bail/MAE-floor/belock) are untouched by the peel.
- PURE replay cross-check (both policies fully simulated, same conservatism): losers flush CUR −835.5 vs PEEL-8 −802.9 / PEEL-5 −793.6; young −168.5 vs −168.2 / −149.5; adolescent identical. Peel never worsens losers; trail-vs-belock interaction is safe because the trail only exists post-TP1 (peak≥6 ⇒ trail floor ≥ −2 with 8pp, ≥ +1 with 5pp).
- PURE also confirms why replays must not be read as book forecasts: PURE-CURRENT (−650pp book) is far below realized (−73pp on the same 235) because real exits use loop-cadence fills and pre-TP1 bails the proxy lacks. Only HYBRID deltas are decision-grade.

## Task 1 — out-of-window wallet replication

Deep-paged the 13 in-pond operators' gmgn activity past the decode window (decode used ~06-30..07-05). **8 unique operators reached out-of-window** (06-23..06-30, 747 buys / 1,049 matched sells / 285 episodes); 6 wallets retention-blocked (40-page cap only reaches ~07-01 for the hyperactive ones — stated plainly, not inferred).

| metric | decode (in-window) | out-of-window |
|---|---|---|
| episode win | 64% | **49%** |
| episode median | +7% | **−0.4%** |
| episode p25 | −6.5% | −19.7% |
| episode p75 | +33% | +30.2% (p90 +77.8) |
| multi-sell (peel) episodes | high | 42% |

Verdict: the exit **shape** replicates (peeling prevalent, fat right tail p75/p90 intact) but the **headline win rate and median do not** — these wallets topped a 7d-PnL leaderboard, so the decode window was their hot streak (selection bias). Per-wallet OOW spread is wide (win 25%–75%, med −23.8 to +14.3). This downgrades "copy their stats" but does not touch our own replay, which is on our own fills.

## Ship recommendation

**Convert the losing wideexit_ab slot into a `peel_cond` A/B on the flush lane** (biggest, both-halves-positive effect):

- At TP1 trigger (+6), read the observed fill pnl:
  - if **fill < +12**: sell **0.50** (instead of 0.75); remainder trails **5pp giveback from running peak**, floor −12, **no TP2 cap** (trail replaces the current 2pp post-TP1 trail / belock / TP2 on the remainder);
  - if **fill ≥ +12** (wick): behave exactly as today (0.75 + immediate remainder exit at fill).
- Pre-TP1 behavior untouched (stop −12, velocity-bail, MAE-floor, belock) — loser book provably unchanged.
- Measured expectation (pessimistic bar fills): **+63.7pp/window ≈ +14pp/day on flush**; sell count unchanged so no added fee legs.
- young_absorb: same config as **SHADOW stamp only** (+9.3pp, but H1 slightly negative and n thin). adolescent_absorb: leave alone (n=4, slightly negative).
- Complements GIVEBACK_TRAIL_SHADOW (pre-TP1 peak trail) — this lever is strictly post-TP1.

Caveats: 4.5 days of data, one market regime; the runner sim is bar-granularity (pessimistic fills stated above, but bars also can't see sub-minute trail exits that would have fired *earlier* at *better* levels — direction of that bias favors the trail); the +12 conditioning threshold was chosen from the same window (cond<10 gives the identical +72.0, so it is not knife-edge, but treat the exact value as tunable, not truth).

## Intermediates

`scratchpad/_tp_trades_{bot}.json`, `_tp_positions.py/.json`, `_tp_bars_harvest.py`, `_tp_bars.jsonl` (238 lines), `_tp_replay2.py`, `_tp_replay2_rows.json`, `_tp_oow_harvest.py`, `_tp_oow_activity.jsonl`. (v1 `_tp_replay.py` kept for the record — it had an entry-bar/false-stop bug and trigger-level TP1 fills; superseded by v2.)
