# RH Rug Gate v2 — Concentration Signal (2026-07-13)

SHADOW-only tightening of the RH-chain (4663) rug defense, driven by the paper
ledger. Answers: (1) how often does the RH racer fleet buy rugs, and (2) does a
pre-buy holder-distribution / LP signal catch them without killing winners
(<=5% winner-kill bar, same as the Solana gate). Working tree only — no enforce,
no deploy, no push.

Provenance scripts: `scratchpad/_rh_rug_analyze.py` (trip reconstruction),
`_rh_rug_join.py` (stamp↔outcome join), `_rh_rug_gate_sweep.py` (threshold sweep).
Prior work this builds on: `_rh_rug_port.md` (retro RQ1-4), `_rh_blockscout.md`.

---

## 1. Rug / violent-loss rate (ledger)

Ledger `scratchpad/robinhood_tapes/rh_paper_trades.jsonl`: 1314 real rows
(47 synthetic 1970-epoch test rows dropped), 462 buys / 740 sells / 112
`rug_signals` stamps, 2026-07-10 → 07-12, **45 distinct tokens**. Trips
reconstructed per `(bot_id, pool)` walk; realized trip return = Σ slice
`pnl_usd` / entry_usd. **456 closed trips.**

Closed-trip realized-return distribution:
`min -100.1 · p10 -16.3 · p25 -6.1 · p50 +3.2 · p75 +9.2 · p90 +13.1 · max +33.4`
(mean **-0.12%** — the fleet is ~breakeven; the tail is the problem).

| violent-loss cut | trips | % of closed | distinct tokens |
|---|---|---|---|
| ret ≤ -20% | 22 | 4.8% | 11 |
| ret ≤ -30% | 7 | 1.5% | 4 |
| ret ≤ -40% | 3 | 0.7% | 3 |
| ret ≤ -50% | 3 | 0.7% | 3 |

**Token-level rug rate ≈ 4/43 ≈ 9%** (matches the RH history decode's ~8%).
The 4 catastrophic tokens (worst realized trip):

| token | worst trip | class |
|---|---|---|
| CASHCATWIF | **-100.1%** | dump — extreme concentration |
| CASHCATGAME | -97.8% | dump — whale overhang |
| Halp | -90.0% | LP-pull / low-concentration |
| QUANT | -30.6% | (no at-entry features captured) |

AxiS's read is right: the fleet's violent value swings ARE rug buys — a thin but
real ~1.5% of trips wipe the position, and they concentrate in ~4 tokens.

**Data caveat that shapes everything below:** the accrued `rug_signals` stamps
cover only **20 pools**, and of the 4 rugs only **CASHCATWIF is stamped**
(stamping was wired after most rug entries; the running lane wasn't restarted, so
the stamps have NO `bs_` Blockscout fields yet either). To grade a gate at all I
combined the ledger stamps with the retro at-entry reconstructions from
`_rh_rug_port.md` RQ3 (CASHCATGAME, Halp, MONSIEUR, KUNA, TREAT + 4 survivors).
Combined labeled set: **3 catastrophic RUG / 4 LOSS(-20..-30) / 22 WIN**.

---

## 2. Which pre-buy signal catches them (catch vs winner-kill sweep)

All features are available pre-buy from the rug stamp (eth_getLogs recon) or its
Blockscout equivalent (`bs_top1_pct`/`bs_top10_pct`). Sweep over the combined set:

| predicate | catch (rug n=3) | winner-kill (n=22) | loss-hit (n=4) |
|---|---|---|---|
| **top1 ≥ 9** | **2/3** (CASHCATWIF, CASHCATGAME) | **0/22** | 0/4 |
| top10 ≥ 30 | 1/3 (CASHCATWIF) | 0/22 | 0/4 |
| **top1 ≥ 9 OR top10 ≥ 30** | **2/3** | **0/22** | 0/4 |
| nhold < 250 | 2/3 (CASHCATWIF, Halp) | **2/22** (hehe, BILLY) | 0/4 |
| shoulder/top10 ≥ 0.6 | 1/3 (Halp) | **11/22** | 4/4 |
| float ≥ 60 | 2/3 | **20/22** | 4/4 |
| pool < 25 | 3/3 | **20/22** | 4/4 |

**Winner: `top1_pct ≥ 9 OR top10_pct ≥ 30`.** Catches the two CATASTROPHIC
dump-class rugs (CASHCATWIF -100%, CASHCATGAME -98%) at **0/22 winner-kill and
0/4 loss-hit** — inside the Solana gate's ≤5% bar. This is the dump-class tell:
one whale positioned to sell an oversized stake into the pool. Max winner top1 on
the stamped set is 7.77 (BROKEBEAR); no winner exceeds top10 23. The threshold has
real margin (top1 9 vs winner-max 7.77; rugs at 10.6/11.9).

**What it cannot catch (honest):** Halp (-90%, top1 1.6 / top10 12.1) is
indistinguishable from winners on holder distribution — it is the
low-concentration LP-pull class. EVERY predicate that caught Halp
(nhold<250, fat shoulder, float≥60, pool<25) killed 2-20 of 22 winners. So Halp
is deliberately left to the **LP-custody stamp** (`lp_any_eoa_owner`), which fires
0 on today's launchpad-custodied hood.fun pools but is the mechanism-defense for
the non-hood.fun EOA-LP class (RQ2, `_rh_rug_port.md`). QUANT has no captured
features. Realistic ceiling for a holder-distribution gate: the 2 extreme dumps.

Applied to the 20 accrued stamps as a live forward-grade sanity check: **1/20
flagged (CASHCATWIF), 0 winners flagged.**

---

## 3. The gate — design + latency

Ported the Solana two-signal shape to RH, tuned to RH data availability:

1. **Concentration (leading tell, promotable-track):** `top1 ≥ 9 OR top10 ≥ 30`.
   Prefers Blockscout `bs_top1/bs_top10`, falls back to eth_getLogs recon.
2. **LP custody (mechanism-defense, always 0 today):** `lp_any_eoa_owner` — the
   Halp/LP-pull class guard; kept stamping for when non-hood.fun pools trade.

**Latency (RH detect→fill ≤ 2s budget).** The eth_getLogs reconstruction is up to
90s — unusable inline. Blockscout is 2 calls / ~1-6s cold, **0 on cache hit**,
10-min TTL. So enforcement, if it ever promotes, must be an **arm-time PREWARM**
(mirror Solana `rug_gate_prewarm`): fire `blockscout_stamp(token)` when a pool
first arms, cache the verdict, and read it at fill from cache (0 added latency).
The concentration verdict reads exactly the `bs_*` fields the prewarm produces.

---

## 4. What shipped (code, SHADOW, fail-open)

`core/rh_rug_signals.py`:
- `rug_gate_verdict(stamp, top1_thr=, top10_thr=)` — **PURE**. Reads
  `bs_top1/bs_top10` (preferred) or recon `top1/top10`, returns
  `{rug_gate_block, rug_gate_reason, rug_gate_source, rug_gate_top1,
  rug_gate_top10, rug_gate_thr, rug_gate_mode}`. **FAIL-OPEN**: neither source
  present → `block=False, source="none"` (never vetoes on absent data).
- Thresholds env-tunable: `RH_RUG_GATE_TOP1` (9), `RH_RUG_GATE_TOP10` (30).
- Mode env: `RH_RUG_GATE` = `shadow` (default — stamps the verdict, never
  blocks) · `block` (verdict authoritative, for a future prewarm gate) · `off`
  (no `rug_gate_*` keys at all).
- `compute_entry_stamp` merges the verdict onto every `rug_signals` row after the
  Blockscout merge → **it forward-grades** on all future stamped entries. Wrapped
  in try/except (never raises into the stamper).

`tests/test_rh_rug_signals.py`: +7 tests (`TestRugGateVerdict`) — block on
top1/top10, winner-shape passes, Halp-class passes (documents the miss),
bs preferred over recon, fail-open, custom thresholds. **Suite: 24 passed;
`tests/test_rh_paper_lane.py` 37 passed.** Smoke-verified: shadow stamps the 7
keys, `off` suppresses them.

**Not done (correctly):** no enforcement (default shadow), no lane wiring beyond
the stamp, no deploy/push. The stamp runs post-fill on the daemon thread already
in place, so it structurally cannot block a fill today.

---

## 5. Honest framing & next

- **n = 3 labeled rugs** (1 ledger-stamped + 2 retro). Nothing here is promotable
  yet. The concentration gate is a clean, zero-winner-kill SHADOW signal, not an
  enforced rule.
- **Promotion bar (unchanged):** n ≥ 30 rugged stamped entries, catch the
  catastrophic class, winner-kill ≤ 5%, then AxiS approval — and enforcement lands
  as a Blockscout PREWARM, not inline.
- **To accelerate accrual:** restart the lane so stamps carry `bs_*` (the
  Blockscout source the gate prefers and a future prewarm needs) and cover the
  aged-pool racers' longer holds. At ~9% token-level rug rate over
  ~10-30 tokens/session, n≥30 rugs is ~2-4 weeks of normal sessions.
- **Gap the gate can't close:** the low-concentration LP-pull class (Halp). That
  needs the LP-custody signal to become live — i.e. the fleet trading a
  non-hood.fun pool with EOA-held liquidity, which hasn't happened yet.
