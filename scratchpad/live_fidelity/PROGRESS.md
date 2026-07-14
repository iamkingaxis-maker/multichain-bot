# Live Fidelity Audit 07-12 — PROGRESS

- 16:45 UTC start. Auth pulled (jcole / env password). Domain gracious-inspiration-production.up.railway.app.
- [x] Pull /api/live-swaps?limit=500 -> live_swaps.json (375 recs; 67 since 07-09)
- [x] Pull /api/trades?full=1&limit=5000 -> trades_full.json (covers 07-08..07-12 16:30)
- [x] Pull wallet-truth -> wallet_truth.json (delta -0.001157 SOL vs baseline 2.115936; baseline_ts needs conversion — booked sums since resume look ~-0.08 SOL, so baseline likely REBASED later; verify)
- [x] Q1 done (audit2.py -> trips2.json). 26 trips joined via live_signature. Era D ($22.5): friction med 2.10pp / p90 3.54pp (entry 1.64 + exit 0.37 + fee 0.09). Era C ($100): med 8.7pp. PREWARM (10:00 07-12 split): entry med 4.68 -> 1.64pp, p90 10.45 -> 1.85. WORKED.
- NOTE: 1 orphan buy mogdog 07-10 16:53 (sell not in swap log or trades — 07-10 sell-canary incident window).
- [x] Q2 edge vs friction: era D on-chain +$2.38 / 7 trips (+2.1pp mean, -0.26pp med AFTER friction); paper twins (scrubbed) show live-paper gap +7pp med (no fidelity gap); $100 correct only at gross edge >= ~6pp med. Balance-delta per-trip truth reconciles EXACTLY with /api/wallet-truth (-0.001157 SOL C+D).
- [x] Q3 rate: 2/11/13 trips per day 07-10/11/12; era-D cadence ~2/hr prime -> 20 fills in 2-3 days; 4-day leg is binding. Wallet truth by era: A indeterminate (unexplained +0.3 SOL inflow + orphan mogdog, sell-canary window), B -$0.39, C -$2.47, D +$2.38.
- [x] Q4 selection: nothing separates at n=26/13 tokens; tentative: re-entry same-token-same-hour skews red (2G/9R med -3.9 vs first-touch 9G/6R med +8.4) -> shadow counter candidate. $22.5 era = ONE token (PumpfunLife) — distinct-token leg needs funnel.
- [x] Wrote scratchpad/_live_fidelity_audit_0712.md. NO code changes, NO commits. Done 17:15 UTC.
