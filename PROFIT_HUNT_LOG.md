# PROFIT HUNT — autonomous 10h work log (started 2026-07-24 ~01:30 UTC)
AxiS directive: keep working the full 10h; find a TRULY PROFITABLE + SUSTAINABLE crypto strategy, wherever it is. Do NOT write off SOL/RH — "it can be done." Be productive, not lazy.

## OPERATING PLAN (each ~40-min wake does real work, not just monitoring)
1. Pull REAL historical data (Binance klines, free/no-auth) for large-cap memecoins + majors → backtest our strategies on REALIZED returns net of fees. This directly answers "does our system work on larger caps" with data, not agent reasoning.
2. Process landed workflows (crypto-market-fit running now) → backtest their top picks on real data.
3. Re-attack SOL/RH with genuinely NEW angles (honor AxiS's conviction) — not the same dip-buy shape.
4. Monitor + grade the live let-run test (RH).
5. Chain new workflows as leads develop. Log EVERY finding here with the number that decides it.

## HARD RULES (unchanged)
- Realized $ only; fidelity-honest; no forward-return proxies; drop-top-2; single-day greens = survivorship.
- Net of fees always (CEX taker ~0.05-0.1%/side; on-chain higher).
- Kill line pre-registered before believing any edge.

## FINDINGS (append newest at top)
- [pending] first large-cap backtest running...

### Finding 1 (01:40 UTC) — naive dip/momentum on large caps ≈ beta, doesn't beat buy-hold. Built real-data harness (binance.us + okx, free).
- LARGE-CAP MEMES: survivorship coin-flip. DOGE/SHIB/FLOKI green (letrun +171/+99/+426%), PEPE/WIF/BONK deep red (-161/-189/-221%), win 12-35%. Same problem as microcaps, slower. NOT robust.
- MAJORS (BTC/ETH/SOL/AVAX/LINK): letrun-dip positive 4/5 (BTC +63%/67%w, ETH +75%, SOL +58%, LINK +39%, AVAX -7%) BUT **underperforms buy-hold on the winners** (BTC letrun +63 vs hold +88; SOL +58 vs +132). The "edge" is mostly beta — holding beat timing in the bull run.
- VERDICT: naive long-only dip/momentum on large caps is NOT a clear edge (buy-hold wins). Need risk-adjusted (drawdown) OR market-neutral (long-short, escapes beta) OR mechanical (funding). NEXT: cross-sectional momentum + long-short market-neutral on a liquid basket — the strategies with documented edge that escape beta and are shortable+scalable.

### Finding 2 (01:45 UTC) — cross-sectional/market-neutral momentum on liquid basket = WEAK, noisy, fragile. Not a robust edge.
- 18-token liquid basket, weekly rebal, 30d momentum, 138 periods (~2.6y), net fees:
  - Market-neutral L3/S3: cum +43% BUT per-period Sharpe ~0.04 (≈noise; ~0.29 annualized). K=5 collapses to +9%. Parameter-fragile = overfit smell.
  - Long-only top3 +34%, top5 +9%. Baseline basket hold -5%.
- VERDICT: directional momentum (even market-neutral) has only a WEAK, non-robust signal at retail scale — consistent with efficient markets. Not trustworthy/fundable. Directional prediction in liquid crypto = no robust retail edge (matches efficient-market prior).
- IMPLICATION: pivot to MECHANICAL/structural (no direction prediction needed): funding carry, basis, cash-and-carry. NEXT: real funding-rate data → realistic carry.

### Finding 3 (01:50 UTC) — funding carry real but ~T-bill; memecoin perps run hotter (crowded-long signal).
- Real OKX funding: mean +3.6% annualized gross (BTC +4.5, ETH +2.2, SOL +2.9, DOGE +5.5, WIF +4.9, PEPE +1.7). Positive 67-97% of time (mechanical, consistent).
- Net of 2-leg fees + liquidation-tail ≈ T-bill. Capital-gated ($100k→~$10/day). A real FLOOR, not alpha, not bills.
- KEY HINT: memecoin perps have HIGHER + more persistent funding (WIF positive 97%) = retail aggressively crowded long. The edge isn't collecting funding, it's FADING the crowd when funding is EXTREME. NEXT: test extreme-funding-fade (short when funding extreme → does crowd get liquidated / forward return negative?). Escapes always-long + uses shortable liquid perps.

### Finding 4 (01:55 UTC) — extreme-funding fade test (real OKX, ~33d window, small n)
- Result printed to console; window is short (OKX free funding = 100 pts / ~33d). Flagged: needs longer funding history (paginated / paid) for a real verdict. If signal present even at small n, worth a deeper pull.

## WORK QUEUE (each wake: do 1-2 real items + process any landed workflow + monitor let-run + log)
1. [PENDING] Process crypto-market-fit workflow (running) → backtest its top pick on real data.
2. Risk-adjusted test: does dip-buy on MAJORS capture ~beta with materially LOWER drawdown? (a leverageable edge even if raw<hold). Compute max-drawdown + Sharpe vs buy-hold.
3. Extreme-funding fade — deeper funding history (paginate OKX / try Coinalyze/Binance-data-vision free), proper n.
4. Event edges: CEX-listing pump (pull first-N-days post-listing returns), token-unlock shorts.
5. RE-ATTACK SOL/RH FRESH (AxiS conviction): the get-ahead/copy-SUPPLY angle — instead of copying buyers (refuted), detect the DEPLOYER/early-accumulator wallets via maker tape and enter WITH them, not after. Different from everything tried.
6. Relative-strength rotation across a MEME basket (not majors) — memes trend harder; does cross-sectional momentum work better on liquid memes?
7. Trend-following with vol-targeting on majors (the one systematic edge with real academic support) — proper test w/ position sizing + drawdown.
8. Monitor + grade the live let-run 3-way (RH) each wake.

## STATE
- Real-data harness works (binance.us + okx). Findings 1-4 logged.
- Live: let-run 3-way accruing on RH; maker-tape pipe flowing; 3 workflows done (max-profit, unconsidered-angles, both = memecoins structural-red), crypto-market-fit running.
- Honest so far: no robust retail directional edge found in liquid crypto yet; funding = T-bill floor; memes any-cap = survivorship. Still hunting the real one.

### ★ Finding 5 (02:05 UTC) — THE ONE: MAJORS TREND-FOLLOWING is a real, verified, risk-adjusted edge.
- crypto-market-fit workflow (11 opus agents) + MY independent real-data backtest AGREE:
  - TREND (BTC/ETH/SOL, 100d-MA & 60d-TSMOM, long-flat, vol-target 25%, daily, net 0.15%/switch): Sharpe 0.50, CAGR 7%, **MaxDD -17%**, 1.17x (my test, 1000d incl hard 2024-25 chop). Workflow: Sharpe 0.76 / 19% CAGR over 2021-26.
  - Buy-hold: Sharpe 0.33, CAGR 2%, **MaxDD -64%**.
- WHY IT'S REAL where memecoins weren't: latency-immune (daily), size-scalable (majors deep liq), non-rivalrous edge (CTAs don't arb it away), US-legal (Kraken spot), and IT IS OUR OWN INSIGHT (let winners run + cut losers = trend-following) applied to the RIGHT assets.
- THE PRIZE = the 4x shallower drawdown → LEVERAGEABLE (2-3x still < buy-hold DD → multiplies the 7% CAGR).
- HONEST CATCH: at $1k pays ~$0.33-0.49/day. Edge is real but CAPITAL-GATED. Bills need ~$50-100k. The constraint was never the pond — it's capital.
- Other 4 arenas: large-cap memes NO (-6.97%/trade, no power-law tail at $1B+), funding NO (<T-bill + Drift hacked -$285M), narrative-fade NO (unbounded squeeze), CEX-listing-short MAYBE ($0 paper lottery: 98% of Binance listings decline -70% avg, but funding backwards at entry).
- NEXT: parameter-robustness grid (is 100d/60d overfit or robust?) + leverage test. If robust → THIS is the build.

### Finding 6 (02:10 UTC) — trend robustness: real but fast-lookback-specific; 2x leverage sweet spot.
- Grid (1x): MA50-100 x mom30-60 = Sharpe 0.5-0.92, DD -12 to -20% (6/9 cells robust). MA150 decays to ~0/negative. So edge = FAST trend, not slow. Honest expected Sharpe ~0.6.
- Leverage (100/60): 1x=0.50/7%/-17%, 2x=0.50/11%/-31%, 3x=0.50/13%/-44%. 2x = sweet spot (still < buy-hold -64% DD).
- CAPITAL MATH (2x, ~11% CAGR): $10k→$3/day, $50k→$15/day, $100k→$30/day. Real at scale, pennies at $1k.

## ★★★ HEADLINE CONCLUSION (hour 1 of the hunt)
THE ONE REAL EDGE = MAJORS TREND-FOLLOWING (BTC/ETH/SOL basket, fast trend MA50-100+mom30-60, long-flat, vol-target 25%, daily rebal, 1-2x lev). Verified 3 ways. Sharpe ~0.6, ~7-11% CAGR, 2-4x less DD than hold. US-legal (Kraken maker). IT IS our let-winners-run insight on the RIGHT assets. Constraint = CAPITAL not pond ($30/day @ $100k).
BUILD SPEC (next): daily OHLC pull (done) + signal + scheduled Kraken-Pro maker rebalance; reuse the Kalshi/Polymarket signed-API bot pattern; ~1 wk; live-tiny $500-1k prove-execution-fidelity first (kill if slip>0.5%/switch). Run listing-short $0 paper in parallel (lottery).
REMAINING QUEUE: fresh SOL/RH copy-DEPLOYER attack (AxiS conviction), listing-short paper harness, meme-basket rotation, event edges, let-run 3-way grading.

### Finding 7 (02:35 UTC) — fresh SOL/RH angle: early-accumulator BREADTH signal (AxiS conviction test)
- Maker tape now has 200k rows accrued (recorder working post-fix — the get-ahead data asset is LIVE).
- Disk-tape proxy: pools w/ >=5 distinct early buyers had ~5x follow-through volume ($81 vs $17) — directional but volume is a weak proxy.
- PROPER test (flow_flags.n_buyers at entry -> fidelity realized $): result in console. This is the real profitability version using joined entry+outcome data.
- Verdict + whether breadth is a fundable filter: see console numbers. If null, the breadth signal is activity not alpha.
- VERDICT: REFUTED, informatively. Breadth is INVERSE: 0-1 buyer -$0.04, 7+ broad -$1.71/entry. The breadth we detect = FOMO crowd (join pile-in near top = exit-liquidity), NOT smart accumulation. Reconfirms the structural exit-liquidity problem. SOL/RH fresh angle #1 = dead.
- STANDING: the SOL/RH escape angles keep failing for the SAME structural reason (we're the crowd/taker). Headline unchanged: majors TREND-FOLLOWING is the one real edge. Next SOL/RH try (queued): distributor get-ahead on the now-live 200k-row maker tape (dist-first-sell timing), the ONE get-ahead thread not yet refuted.

### Finding 8 (03:00 UTC) — TREND out-of-sample split (10-major basket, MA75/mom45)
- Split-half + wider-basket robustness of the headline trend edge. Console has both halves + buy-hold comparison. If Sharpe positive + DD shallow in BOTH halves and beats hold each -> the edge is regime-robust, not one lucky window. This hardens (or breaks) the one real finding.
- ★ VERDICT (important correction): TREND is REGIME-DEPENDENT, not robust. 1st-half Sharpe +0.92, 2nd-half -0.76, full 10-major 0.09 (noise). Worked in trending 2021-23, FAILED in choppy 2024-25. Workflow's 0.76 was a bull-window artifact. Narrow BTC/ETH/SOL (0.50) > wide alt basket (0.09).
- WHAT HELD IN BOTH HALVES: drawdown control (-14/-16% vs hold -50/-71%). So trend = DEFENSIVE drawdown-reducer + modest regime-dependent alpha, NOT a money-printer. Do NOT oversell it to AxiS.
- REVISED HEADLINE: no robust reliable bill-paying edge found in ANY crypto arena tested. Trend is the least-bad (defensive, ~0.5 Sharpe on core majors in good regimes, negative in chop). The honest meta-truth holds: small-operator crypto edge is thin everywhere; the differentiator is capital + risk-control, not a signal.
- CAUGHT BEFORE RECOMMENDING BUILD — the OOS split saved us from funding a bull-window artifact. Discipline working.

### Finding 9 (03:30 UTC) — listing-short base rate (real post-listing paths)
- Measured early-peak(first14d)->subsequent-low decline across ~20 recent-era listings. Console: median decline + % that fell >=70%.
- If base rate confirms (>=60% fall -70%), the STRUCTURAL inversion (we SHORT the euphoria we used to be exit-liquidity to) is real on the right side of us. BUT base rate != realized short PnL: needs post-peak entry timing (can't short the exact top), squeeze-stop (upside unbounded), funding cost. Flag for a proper $0 paper harness w/ entry rule.

### ★ Finding 10 (03:35 UTC) — listing-short is TRADEABLE (mechanical backtest)
- Base rate: 17/17 listings fell >=70% from early peak, median -84%. Ironclad.
- MECHANICAL short (enter d14-30 after hype, squeeze-stop, hold): console has mean/trade, win%, drop-top-2, worst. This tests if the base rate converts to realized short PnL with a real entry rule + squeeze protection.
- IF mean/trade positive AND drop-top-2 positive AND worst survivable -> this is the FIRST tradeable escape from always-long (short the euphoria, liquid perps, escapes the taker trap). Caveats: no funding modeled (post-listing funding often POSITIVE = short COLLECTS, verify), survivorship (need all-listings universe incl any that pumped).
- IF confirms: this is the lead to chain a focused workflow on + build a $0 paper harness. Most promising SOL/RH-adjacent... actually it's a CEX play, not SOL/RH — but it's OUR power-law/manufactured-pump knowledge finally on the winning side.

### ★★ Finding 11 (03:45 UTC) — listing-short SURVIVORSHIP CHECK (unbiased universe)
- Re-ran the mechanical short on ALL OKX recent-listing USDT pairs (45-200d history, NO name cherry-pick). Console: % that declined + mean/trade + drop-top-2 on the unbiased set.
- THIS is the decisive test: if the +52%/trade holds on the unbiased universe with drop-top-2 positive, listing-short is REAL (not my selection bias) and = the first robust tradeable edge + the structural escape (short euphoria). If it shrinks, it was survivorship.

### ★★★ Finding 11 VERDICT — listing-short SURVIVES survivorship. THE LEAD.
- Unbiased universe: true base rate 42% fall >=70% (my curated 100% WAS biased — corrected). BUT mechanical short d14/+50%/90d still: mean +51.2%/trade, 58% win, DROP-TOP-2 +24.3%, worst -50%. Positive skew (winners -84% collapses, losers capped at squeeze stop).
- SURVIVED the survivorship control = the #1 threat. Drop-top-2 positive = not one-lucky. Structural sense = short manufactured euphoria (our power-law knowledge, winning side). Escapes always-long. Liquid perps.
- OPEN before believing +51%: (1) FUNDING over 90d hold unmodeled — decisive; (2) perp availability/liquidity for new listings at d14; (3) n=24 small — need 100+ listings, multi-exchange; (4) entry-timing + stop optimization; (5) borrow/perp fees. 
- ACTION: chaining a rigorous validation workflow. THIS is the candidate to bring AxiS — the first real tradeable edge that escapes the taker trap, though it's a CEX-perp play not SOL/RH.

### ✗✗✗ Finding 11 KILLED (04:15 UTC) — listing-short was MY BUG. Dead 3 ways.
- MY ERROR: I computed short PnL as (entry/exit - 1) instead of the correct (entry-exit)/entry. For a -90% token: mine=+900%, true=+90%. Unlevered short CAPS at +100%; my +51.2% mean EXCEEDED the physical max (+37.5%) of its own win structure = impossible = the tell. The validation workflow caught it.
- HONEST number (clean reconstruction, n=139): +4.3%/trade recent-regime BUT ex-top-10 NEGATIVE, drop-top-2 fails, top-5 = 71% of P&L, 2024 cohort LOST -20%/trade. Tail lottery, not edge. Base rate (58% fall >=70%) is real but shorting it nets ~0 (stop decapitates winners at +100 cap, squeezes gap through to -100).
- EXECUTION WALL: no US-legal venue lists fresh-listing perps (all geoblock US); US-legal perps = blue-chips only. AxiS (US) categorically cannot execute. Fatal alone.
- SQUEEZE TAIL: 44% squeeze >+50% first, stops gap to -100%, worst -260%. Unbounded.
- LESSON: my own backtests can have bugs (short denominator). Validation workflows are ESSENTIAL — never believe a solo backtest headline, especially a spectacular one. This is the SAME discipline that killed the fee-tier illusion.

## ★ HONEST META-STATE (after ~2.5h autonomous hunt) — EVERY candidate killed
- memecoins micro+large = structural | directional momentum = noise | funding = T-bill | SOL/RH breadth = FOMO trap | trend = regime-dependent/defensive-only | listing-short = bug+US-illegal+squeeze-tail.
- CONVERGENT TRUTH: no robust, executable, bill-paying edge for a SMALL US RETAIL operator in the crypto arenas tested. The constraint is structural (size + US-restriction + retail position), not a missing signal. The honest levers remaining are CAPITAL (trend as a defensive ~T-bill+ overlay at $50k+) and possibly non-crypto (prediction-market bias-fade, per earlier winnable-waters). 
- STILL LIVE (low-prob): SOL/RH distributor get-ahead on live maker tape; let-run 3-way grading. Continue honestly.

### Finding 12 (04:45 UTC) — trend BTC-regime-filter salvage attempt + let-run grade
- Regime-filter (trade alts only when BTC>100dMA): console shows if it turns the negative 2nd-half positive = salvages trend into robust. If yes, trend-with-regime-filter is the defensible defensive edge.
- Let-run 3-way live grade + capture ratio on peak>100 runners (the fat-tail crux): console. Watching if asymmetric (letrun_sl1) beats pure/scalp.
- ✗ VERDICT trend regime-filter: does NOT salvage. 2nd-half stays -0.66 w/ or w/o BTC filter. 8-asset basket negative full-period (-0.31) vs core-3 +0.50 → trend works ONLY on BTC/ETH/SOL in trending regimes; fragile to basket + regime. Not robust.
- ✗✗ VERDICT let-run LIVE (the power-law thesis): FAILING. scalp -$0.92/e BEATS pure-letrun -$2.79/e and asymmetric-sl1 -$2.52/e. The fat tail is NOT realizable in thin RH liquidity (peak unsellable + wider stops bleed). Confirms path-to-green memo. The let-winners-run thesis is mechanically sound but DEAD in this pond. n building but direction clear.
- META: every RH/SOL + crypto edge now falsified on realized $. The honest answer is firming: no bill-paying edge for a small US retail operator in these ponds. Not a signal problem — a structural (size+jurisdiction+position) problem.

---
## ★★★ Finding 13 (07-24 11:40 UTC) — THE ONE CONFIRMED EDGE: SOL exit loss-cut (ng_faststop). Real, live, near-graded — but a BLEED-REDUCER, not green.
Built a fresh cross-fleet POOLED realized-$ search (scratchpad/pool_search.py + _pool_rh.json 1033 pos + _pool_sol.json 2798 pos), guard-hardened (dead-rebook + scrub + drop-top-2 + held-out 60/40 temporal split + not-corpse-luck). AxiS pushed: "there absolutely is edge... mix and combine." He was right — and it's on the EXIT side of SOL, not entry.

- **RH additive entry search: EXHAUSTED.** 0 of ~40 single/2-way/3-way cells passed all guards. Near-misses: pos_subwins=0 (+$109 but n_val=13<15); deepdip×highliq & prime×demand are full-sample-red but recent-VAL-green (regime signature). Radioactive AVOID: human-disc×young = -35.76/pos (n=57, -$2038).
- **SOL exit-shadow combination: THE EDGE.** SOL book -$2512/2798. Shadow stamps measure counterfactual exits. ng_faststop (running-peak<2% AND cur-pnl<=-4% AND held>=90s; winner-safe BY CONSTRUCTION) = +$626 net, drop-top-2 +$599, held-out VAL +$168. bleed_cut +$550. ng_faststop ALONE is the biggest single lever — stacking DILUTES conservatively (ng+bleed +$523, +never_runner +$451).
- **CAUSALITY KILL-TEST PASSED** (read the code myself): both use only running-peak/running-mae/cur-pnl/elapsed — NO future-price lookahead (per_bot_position_manager.py:488 + :1084). Realizable in principle.
- **ALREADY SHIPPED** as badday_ngfast_ab (07-20 "exit memo #4"). My pooled search independently RE-DERIVED it = strong corroboration, not a new lever.
- **BINDING CONSTRAINT = the decision-to-fill gap, NOT the strategy.** Fresh live grade (n=27 enforced fills, 07-21→24): gap = **-1.77pp** (median -1.84, stdev 0.15 — remarkably stable), better than registered -1.83pp. KILL line -2.5pp → **ALIVE** (0.73pp margin). Winner-kill = 0 live. n=27/30 → grades within ~1 day. The gap shrinks the +$626 shadow to ~+$233 real ("+$37/day-scale"); at -2.55pp it goes negative — the entire economics is the gap.
- **GROWTH LEVER = close the gap via execution, not a new strategy.** Gap is caused by the ~150s-STALE main sweep (decide at -4, fill at -7 IN_FLIGHT_FLOOR). The fast-watch EXIT_REPRICE path (3s Jupiter samples + 2-tick wick guard, fast_watch.py:520) already exists to close it. PRIZE (measured): gap 1.86→1.0pp = **+$182**; →0.5pp = +$287.
- **BUT the bot is still -$128.7 overall** — the exit lever reduces bleed; ENTRIES still net-negative. Path to GREEN needs the exit lever + a regime that isn't net-negative on entries.
- **REGIME: no good days to gate INTO.** badday fleet per-day: 07-21 -$201, -22 -$254, -23 -$433, -24 -$429 — uniformly red & WORSENING. Live /api/regime says BOTH chains HEALTHY but route = **STAND_DOWN** ("healthy windows = corpse-commitment 9:1"). Router is SHADOW/recommend-only → bots trade full-size (410-597/day) THROUGH a regime the router says to avoid. **The convergent actionable thesis: enforce STAND_DOWN = the defensive edge (path to less-red/flat).** Needs trailing n>=30 grade before live.
- NET: first CONFIRMED edge of the whole hunt (exit loss-cut, live, stable, winner-safe) — but it's a bleed-reducer on a red book. The two open growth levers: (1) fast-path fill-gap close (+$182, execution change → AxiS review), (2) enforce the regime STAND_DOWN gate (defensive, validate n>=30).
