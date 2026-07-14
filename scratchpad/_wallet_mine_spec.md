All computations done. Here is the synthesis.

---

# BADDAY WALLET-SIGNAL SYNTHESIS — decision-ready

## 1. VERDICT

**Wallet-IDENTITY signals add nothing. Wallet-FLOW adds a modest, robust lift ON TOP of the queued stack — the honest answer is: "wallet signals add little beyond flow/size features already stamped," with ONE shippable exception.**

Identity is dead three ways: (a) coverage — top_buy_makers (top-5 only) gives ~zero cross-token recurrence: train-scored winner wallets hit 1/24 holdout tokens, first-entry variant 0/24; (b) the loser-wallet veto INVERTS on holdout (veto-present tokens WON 60% vs 43% absent — it was scoring the bad train tape, not wallets); (c) v2 index wallets on our entries run 8W/19L = 30% vs 31.6% base. whale_buy_present_2k fired on 0/98 tokens; smart_wallet_count_* stamps are ~all zero (stale index).

**ADDITIVE LIFT (computed fresh, since the lenses did not):** spike-scrubbed (77 spike positions dropped from 1,866), per-token-deduped (98 distinct tokens), joined to the queued stack (rsi_15m<=44 AND pc_h6<=0 AND liq>=30k AND unique_buyers 12-19; n=34 tokens):

| cohort | n | mean/tok | win | never-green | dropTop |
|---|---|---|---|---|---|
| full badday book | 98 | -1.61 | 31.6% | 24.5% | -1.81 |
| queued stack alone | 34 | -0.85 | 38.2% | 20.6% | -1.15 |
| **stack + BLOCK nf5m in [0,+300)** | **28** | **-0.14** | **46.4%** | **14.3%** | **-0.47** |
| stack + require nf5m<0 | 19 | -0.32 | 42.1% | 10.5% | -0.83 |

Best wallet-level signal = **blocking the net_flow_5m_usd [0,+300) "weak-bounce-already-started" zone**: **+0.71pp mean/token, +8.2pp win rate, -6.3pp never-green, at 18% volume cost (6/34 stack tokens)**. Time-split (cut 06-25T23:32, stack buyers>=12): block-toxic improves BOTH halves (early -2.41 -> -1.77, late +0.22 -> +0.26 with win 45.8% -> 52.6%) and survives drop-top-token. The stricter "require nf5m<0" variant FAILS the early half (-3.16 vs -2.41 base) — do not ship that version. Toxic zone standalone on the full book: n=27, mean -3.37, win 11%, robust in both halves (12%/9% win) and dropTop — the single most robust cell in the study.

## 2. SHIP LIST (all shadow-first, realized-outcome confirmation required per shadow-scorer-overstates lesson)

1. **SHIP SHADOW: `nf5m_toxic_zone` gate — BLOCK badday fires when net_flow_5m_usd in [0,+300). FAIL-OPEN if net_flow_5m_usd is None.** Evidence: full-book toxic n=27 mean -3.37/win 11%/NG 33%, holds both time halves + dropTop; additive on queued stack +0.71pp mean/+8.2pp win. Expected effect: removes ~28% of full-book fires (72% volume kept), ~64% of negative ret-points sit in the nf>=0 side. Enforce bar: shadow-blocked cohort realized mean <= -2 at n>=30 distinct tokens.
2. **DO NOT add an nf5m<0 requirement, and do not flip filter_negative_net_flow_5m yet.** The flip's mean edge is late-half-only (early half nf<0 was WORSE than stack base). Log the flip as shadow only; the existing filter is directionally suspect (blocks the better cohort pooled: nf<0 = -0.58/43%/NG 15% vs nf>=0 = -2.5/22%/NG 33%) but not time-stable enough to touch.
3. **SHIP SHADOW LOG (no gate): anonymous absorption score** = {buy_size_max_last60s, unique_buyers_n, buy-imbalance} at fire, scored against realized exits. Tape pilot (n=8, out-of-sample): max print >=$75 -> 6/6 bounced vs 0/2 died; >=5 buyers + imbalance>=0.5 -> 5/5. Pilot-grade only (died class = 2 rug-collapses outside the entry band); payoff horizon is ~60m so evaluate it inside the badday_flush_wideexit_ab arm.
4. **Data step that unlocks the rest:** one paced GT/io.dexscreener minute-bar refetch for the 171 taped pairs (85/88 tapes extend >60m past their bars, median gap 841 min) -> multiplies labeled flushes ~5-10x and finally produces in-band died flushes. Needs network approval; highest-yield next action.

## 3. smart_money_index v3

**v2 stands for additions — zero wallets met the inclusion bar** (best candidates Fgej4SPuW282 3W/2L, gasTzr94 4W/6L — too thin/mixed). Hygiene actions:

- **REMOVE: `AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51`** — kill-list bot-farm prefix inside the v2 index; present on 42/98 of our tokens (pure volume noise); its 5w/1l stats are tape artifacts.
- **DEMOTE/FLAG: `kEFiAX3jo5Nmem...`** — 1W/8L (11%) distinct-token record on our entries.
- Weak-negative flags (n=4 each, not removal-grade): `BGzLYcFcUZkW5GPZZAYK4Jxyf1W7aigyHQbvmKsQeeuq` (1W/3L), `AgTM7bcPQo2kkru8o2igeiwbAjJY2bubEHHZUQPRhyqG` (1W/3L).
- WATCHLIST (not index): toxic co-buyers `FYX5JQ2kP7TD...` (0W/7L, mean -4.65), `iK7BmyUoFm2G...` (1W/6L), `8n3m7Mj5zmaa...` (1W/4L, -6.07), `2Jy2VdYU6YmS...` (1W/6L); whale-print unknowns `9cWPpT2Y...` ($505 SHIH window), `Gj8DM8DL...`, `HE8UF2RC...` — need profitability confirmation before any use. Never add `2tgUbS9UMoQD...` (kill-list, net -$182 despite 3 bounce associations).

## 4. DO NOT RE-MINE (falsified this mission)

- **Winner-wallet presence gate from top_buy_makers** — top-5 window too sparse; 0-1/24 holdout coverage at every threshold. Retry only with full live-tape maker streams.
- **Loser-wallet veto** — inverts on holdout at every threshold (l>=3/l>=5, wr<=20/25%); high-frequency makers are regime-neutral tape furniture.
- **Whale print SIZE at entry** — monotone WORSE ( <20: -1.35 -> 200+: -1.86); whale_buy_present_2k never fires (tokens too thin); buy-size z>=3 fails time-split (early -1.72/14% vs late +1.45/67%).
- **Buy-size distribution features** — n_large_buys_500 zero on 90/98 tokens; top5_buyer_volume_pct non-monotone; max/median ratio flat.
- **Index-wallet co-presence as entry signal** — present -1.70/29% vs absent -1.49/35% (slightly worse). Kill-list presence = liquidity proxy (present -1.18 vs absent -2.03), not alpha.
- **First-entry-only wallet counting** — fabricates winners (1eve: 67% first-entry -> 25% union, below base); always use union-of-entries + period base rate.
- **"Any recurring wallet present" signal** — fails dropTop on mean; density>=2 inverts; at best re-derives unique_buyers_n.

**Key caveats:** honest n = 98 distinct tokens (34 in-stack; toxic-zone-in-stack cells n=6-10); train/test base rates differ 2x (25.7% vs 50.0%), which is exactly what fabricated the in-sample wallet signals; nf5m is family-specific and decay-prone (nf_60s/nf_15s already refuted fleet-wide) — shadow-first is mandatory, not optional. Analysis script: `C:\Users\jcole\AppData\Local\Temp\claude\C--Users-jcole-multichain-bot\ecbaef77-2f98-4dc5-9231-4bd9a529e92c\scratchpad\additive_lift.py` (dataset: `tok_dataset.json` alongside). No code/config modified; no network calls.