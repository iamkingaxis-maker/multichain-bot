# Rug-Forensics Mine — PROGRESS

Mission: evidence base for an ACTOR-BEHAVIOR entry gate that would block HOODLANA-class
rugs at decision time with winner-kill <= 5%. Report only. RESUME GATE.

HOODLANA mint: C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump (Solana), rugged -98% 2026-07-11.

## Checkpoint log (append after every step)
- [step0] Created scratchpad/rug_forensics/. Read rpc_pool.py (Alchemy+publics), test_dev_not_dumped_gate.py.
  Existing dev-not-dumped gate: blocks ONLY confirmed dev-dump (dev_pct_remaining < min_pct=20), fail-open on missing.

## Findings from existing work (step0 done)
- Bot ALREADY has 4 creator-rug gates keyed on `dev_pct_remaining`: dev_not_dumped(<20 block),
  filter_dev_dumping(<50), hard creator-rug(<2.0), creator-dumped-99% (<1.0). HOODLANA passed ALL =>
  at OUR buy time the top non-program holder still held its bag. dev_pct is NOT the tell.
- feeds/dev_wallet.py: `dev_pct_remaining` is a PROXY = largest NON-PROGRAM holder via
  getTokenLargestAccounts (top-10, resolve owners, skip known programs). NOT the true mint creator.
  So existing infra never looks at: deployer identity/history, funder lineage, insider clusters. GAP.
- RH actor seeds (_rh_rug_actors.json) are EVM (Robinhood chain) — different HOODLANA (0x02bf449e...).
  RH decode: 0 repeat pre-collapse net-positive sellers found; LP-pull rugs never appear in swap tape.
  RH rug-actor blacklist EMPTY (not disproven, just uncaptured). Solana is the live gate -> prioritize.
- Local paper history: scratchpad/_full_trades.json (76MB, entry_meta present). follow_exits.jsonl
  (461 rows, wallet-follow tape). _trades_cache.json 538MB. bot_state/ empty (live paused). No
  live_swaps.jsonl locally.

## Cohort plan
- Rug cohort (Solana): tokens in _full_trades that cratered to ~zero after our touch. Confirmed rug =
  large negative pnl_pct AND (later price ~0 / no recovery). Also token addresses ending 'pump'.
- Winner cohort: tokens we net-won on (token-mean pnl positive), for winner-kill grading.
- Actor features per token via RPC: true deployer (creation tx feepayer), deployer funder hop,
  deployer prior-token count, early-buyer funding overlap, LP mintLP null.

## STEP1 DONE — HOODLANA anatomy (hoodlana_anatomy.json). DECISIVE.
- Mechanism = LP-PULL invisible rug. Top holder F6Kmx (98.78%) is owned by pAMMBay = PumpSwap POOL
  VAULT, not a dev. Our dev_pct proxy (largest NON-program holder) is structurally blind to LP-pull.
- Deployer Hk4HUiTo7DGC1VzcjypNRehQDTjfo4XnLk8XmZbpx9TR: only 7 lifetime sigs, first tx ~25s before
  launch (age-at-launch ~25s), ZERO prior tokens -> fresh single-purpose throwaway deployer.
- Deployer SNIPED its own launch (appears as 4th early-buyer tx, ~same second).
- Funder JEK8ciMXxvuNpbyqS9pW62QDFdaohDgYXqKQY4ayxvZt = ultra-high-freq wallet (1000 sigs in 160s);
  infra/bot funder, not obviously a per-rug funder. Funder-lineage tell is weak/ambiguous here.
- CANDIDATE actor tells visible at buy time: (a) deployer age-at-launch tiny, (b) deployer sniped own
  launch, (c) LP custody (pool = largest holder). Local dev-proxy identity shows NO serial structure
  (178/182 wallets = 1 token; the 22/6-token wallets are whales/MMs, not ruggers) -> mirrors RH.

## STEP2/3 plan — grade candidates. Winner-kill is the HARD bar (<=5%).
- Extracting cohorts by real mint (`address` field; `token` was just the symbol).
- Actor crawler: per mint -> true deployer (feepayer of genesis tx), deployer lifetime-sig-count,
  deployer age-at-launch, deployer-sniped-own-launch. Run on winners (kill) + deep-losers (catch).

## STEP2/3 RESULTS (checkpointed)
- Sig-paging deployer-ID FAILS on winners: they are high-volume Token-2022 mints (>5000 txs, genesis
  unreached in 5 pages) and getTransaction on old versioned genesis txs returns None. So a fair
  winner-vs-rug deployer-freshness comparison is NOT extractable from the free RPC stack at scale.
  (actor_features.json: all 5 winners lifesig=0/age=None; only shallow dead tokens resolved.)
- Dexscreener labeler (rug_labels.json, NO rpc credits): of 224 touched mints, 152 found / 72 gone.
  ULTIMATE-DEATH label (not-found OR liq<$5k OR dd_from_entry<=-80%) = 105 DEAD / 119 ALIVE (47% die).
- ⭐ KEY: OUR realized token-mean pnl is ~identical DEAD -3.04% vs ALIVE -3.61% (n=105/118).
  Containment already neutralizes rug EV — a rug-blocking gate gains ~0 on average while any
  winner-kill is pure cost. Rug gate only matters for the rare CAP-HITTING TAIL (HOODLANA class).
- dev_pct@entry (proxy) barely separates: DEAD 11.0 vs ALIVE 14.3 -> not a clean gate.
- death_split.json holds dead/alive mint lists.

## STEP5 RESULTS — actor-freshness grading (actor_features_v2.json, 1121 rpc, balanced 18 dead/15 alive)
- Reliable deployer-ID caveats: (a) many tokens (even dead ones that pumped first) exceed 30k txs so
  genesis unreached; (b) dep_age only meaningful when dep_lifetime_sigs<1000 (active wallets' own
  sig history is capped at 1000 -> garbage negative ages). Used lifetime-sig-count as freshness proxy.
- fresh-throwaway deployer (lifesig<50): DEAD 17% (3/18) vs ALIVE 13% (2/15) -> NO separation.
- sniped-own-launch:                    DEAD 39% (7/18) vs ALIVE 40% (6/15) -> ZERO separation.
- HOODLANA has BOTH (lifesig 13, sniped True) but so do 13-17% of SURVIVORS.
- => actor-freshness/self-snipe gates FAIL: winner-kill 13-40% (>>5% bar) AND don't even separate
  rugs from survivors. Mechanism (LP-pull) is not an actor-history signal.

## DONE. Report written to scratchpad/_rug_forensics.md. Verdict: NO rule meets bar (see report).
- [ ] Step1: HOODLANA anatomy via RPC -> hoodlana_anatomy.json
- [ ] Step2: rug cohort from local caches
- [ ] Step3: winner control cohort
- [ ] Step5: feature axes mine
- [ ] Report: _rug_forensics.md
