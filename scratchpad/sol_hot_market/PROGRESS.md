# SOL Hot-Market Analysis — PROGRESS

Goal: quantify hot-market opportunity we leave on Solana. Is heat in OUR universe? Are TP targets/holds too conservative for a hot bounce? Regime-conditional lever.

Data: scratchpad/sol_selection/_trips.json (955 trips, 2026-07-02..07-12, 10 bots).
Honest metric: ex-top-2 token-median; 4-half OOS where claiming a rule.

## Steps
- [x] Load + inspect trips (955, peak/mae/ret/hold/liq/entry_meta present)
- [x] Q1 recent vs prior: HOTTER in tail (reach30 21.2% vs 8.6%; reach50 7.7% vs 0). liq/mcap flat-lower.
- [x] Q2 TP-gap: given +12, 55-62% reach +20; TP2-reacher peak p50 21-28 vs +12 cap (~+5-12pp left)
- [x] Q3 hot-regime signal = trailing universe-heat (rolling reach20, K=25); reach20 spread holds 4/4
- [x] Q4 #1 lever: regime-gated TP2 lift +12->+18/20 (keep TP1); Δ +0.08/.37/.21/.20 4/4. Raising TP1 LOSES.
- [x] Wrote scratchpad/_sol_hot_market.md + SHADOW stamp scratchpad/sol_hot_market/hot_regime_shadow.jsonl (utf-8)

DONE. Deliverable: scratchpad/_sol_hot_market.md. Shadow: scratchpad/sol_hot_market/hot_regime_shadow.jsonl
