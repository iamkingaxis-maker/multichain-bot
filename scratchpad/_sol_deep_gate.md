# SOL young-lane DEEP+X combo gate (2026-07-12)

Turn the deep-capitulation separator (pc_h1<=-45, ex2 token-median **-3.0** — "less-red
not green", underpowered) into a GREEN, stable, high-volume selection gate by adding ONE
second axis. Same 955-trip young-lane dataset (151 tokens, 07-02..12, post-scrub, ex-top2
per-token median; `scratchpad/sol_selection/_trips.json`). OOS bar: PASS side green in
>=3/4 halves (chrono1/2 + odd/even) AND keeps >=60% of the deep-cohort volume AND
winner-preserving (p90 not clipped vs deep-alone). Pure selection — no sizing change.

Baseline: N_ALL=955 fills. N_DEEP (pc_h1<=-45)=269 = **28.2% of all young fills**.
All-trips ex2 tokmed **-5.8**; deep-alone **-3.0** (0/4 halves green).

## The combo grid (DEEP=pc_h1<=-45 AND second axis; PASS-side ex2 token-median)

| DEEP + second axis | ALL tm | CH1 | CH2 | ODD | EVEN | green/4 | vol%deep | vol%all | p90 (deep=+28.3) |
|---|---|---|---|---|---|---|---|---|---|
| **liq >= 30k** | **+4.6** | +3.9 | +10.1 | -2.2 | +8.0 | **3/4** | **68%** | **19.1%** | +32.5 (concentrates) |
| liq >= 32k | +5.4 | +3.9 | +7.8 | +0.8 | +7.4 | 4/4 | 64% | 17.9% | + |
| liq >= 35k | +6.0 | +3.9 | +24.4 | +0.7 | +10.7 | 4/4 | 48% | 13.4% | +33.4 |
| entry_vol_h24 >= 1.5M | +5.3 | +1.7 | +10.1 | +5.5 | +13.3 | 4/4 | 36% | 10.1% | +38.6 |
| entry_vol_h24 >= 1M | +1.7 | -0.1 | +10.1 | -0.2 | +6.6 | 2/4 | 57% | 16.0% | +34.0 |
| entry_vol_h24 >= 700k | -1.9 | -2.2 | +4.7 | -4.2 | +2.5 | 2/4 | 75% | 21.0% | +33.4 |
| rt_buys_usd >= 2500 | +0.8 | -0.7 | +10.4 | -6.3 | +10.4 | 2/4 | 50% | 14.1% | +34.0 |
| unique_buyers >= 40 | -0.7 | -2.2 | +5.4 | -5.7 | +8.5 | 2/4 | 56% | 15.8% | +26.4 |
| net_flow_15s >= 100 | -2.2 | -2.2 | +1.9 | -6.3 | +10.4 | 2/4 | 57% | 16.0% | +26.4 |
| net_flow_60s >= 0 | -5.0 | -5.1 | +7.8 | -6.5 | +6.9 | 2/4 | 62% | 17.5% | +24.2 |
| bs_h1 >= 1.3 (buy-skew) | -4.2 | -4.2 | -3.5 | -5.0 | -2.2 | 0/4 | 65% | 18.3% | +24.2 |
| buy_pressure_60s >= 0.55 | -6.4 | -6.3 | -6.4 | -8.8 | +5.4 | 1/4 | 55% | 15.6% | +23.7 |
| top10_holder <= 40% (distributed) | -5.4 | -5.0 | -6.4 | -6.0 | -2.8 | 0/4 | 48% | 13.4% | +26.2 |
| top10_holder <= 30% | -6.2 | -5.5 | -6.6 | -6.7 | -4.2 | 0/4 | 40% | 11.3% | +26.4 |
| hour 03-08 UTC (young window) | +10.4 | - | +10.4 | - | +10.4 | 2/4 | 1% (n=2) | 0.2% | dead in tape |
| hour 13-22 UTC (prime) | -5.2 | -5.1 | -6.4 | -5.2 | -1.9 | 0/4 | 48% | 13.6% | +33.4 |
| liq <= 35k (THIN, control) | -6.3 | -6.3 | -6.4 | -5.2 | -6.4 | 0/4 | 52% | 14.8% | +15.9 (clips!) |

(RH "proven pre-entry volume" axis, tested on Solana as instructed: entry_vol_h24 & rt_buys
DO lift the deep cohort — 1.5M reaches +5.3 4/4 — but only by gutting volume to 36% deep /
10% all; at volume-preserving thresholds (700k-1M, 57-75% deep) it stays red 2/4. It is a
weaker, higher-cost version of the liquidity axis. Hour axes: 03-08 has ~no deep fills in
this tape (young window just re-opened); 13-22 prime is flat-red. Demand-composition and
rug/holder axes do not separate on top of deep.)

## Best combo: DEEP + liquidity floor (pc_h1<=-45 AND liquidity_usd>=30k)

**GOES GREEN. ex2 token-median +4.6** (vs deep-alone -3.0, baseline -5.8), winrate 56, 3/4
halves green (only ODD -2.2, shallow), and it is winner-CONCENTRATING (p90 +32.5 > deep
+28.3 > all +25.2 — favoring this cohort raises the right tail, does not clip it).

**It is a GENUINE INTERACTION, not liquidity doing the work alone:**

| group | n | ex2 tm | wr | p90 |
|---|---|---|---|---|
| all trips | 955 | -5.8 | 45.9 | +25.2 |
| liq>=30k ALONE (no deep gate) | 688 | **-5.0** | 49.1 | +26.4 |
| DEEP ALONE | 269 | **-3.0** | 50.9 | +28.3 |
| **DEEP + liq>=30k** | 182 | **+4.6** | 56.0 | +32.5 |
| SHALLOW(pc_h1>-45) + liq>=30k | 506 | **-6.3** | 46.6 | +18.7 |

Neither ingredient crosses green alone; the product does (+4.6). The liquidity floor only
pays on the deep-flush side — SHALLOW+liq is the WORST cell (-6.3). Reading: a hard 1h
flush **on a pool with real liquidity** bounces; the same flush on a thin pool bleeds, and a
liquid pool that has NOT flushed (still near its high) is a trap. Consistent with the
`falling_knife` / `steep-flush-bounces-more` memory and the "buy the flush not the breakout"
direction of the whole young lane.

**Robustness (overfit checks):**
- Liq-floor neighborhood within deep is monotone, not a spike: 28k +0.5(2/4), **30k +4.6
  (3/4)**, 32k +5.4(4/4), 35k +6.0(4/4), 40k +0.2(2/4, n thins to 85). The green plateau
  is the whole 30-35k band.
- Deep-depth sweep at fixed liq>=30k also green across the band: -40 +3.4(3/4), **-45
  +4.6(3/4)**, -50 +6.8(3/4). Robust on both axes simultaneously.

**Threshold choice — volume vs 4/4:** 30k is the max-volume pick (68% deep / 19.1% all,
3/4 green, the single red half ODD is -2.2 ≈ 0). 32k flips ODD green (4/4, +5.4) at a small
volume cost (64% deep / 17.9% all). **Shipped default = 30k** (AxiS wants volume; the ODD
half is a near-zero coin-flip and the 30-35k plateau is uniformly green). 32-35k documented
as the "tighten to 4/4" variant.

## VOLUME COST (the honest headline)

- **DEEP + liq>=30k keeps 182 / 955 = ~19% of current young-lane fills.** It keeps 68% of
  the deep cohort, but the deep cohort is only 28% of fills, so a HARD gate would cut lane
  throughput ~5x. The green combo does clear the OOS bar cleanly — but hard-enforcing it
  guts volume.
- Therefore this is NOT recommended as a blanket hard block on the whole young lane.
  Recommended shapes (all preserve the profitable direction without starving the fleet):
  1. **Soft preference / routing tier** — FAVOR-stamped candidates get priority / a
     dedicated slot; SKIP candidates still fill for the rest of the fleet (size-neutral).
  2. **Dedicated sleeve/bot** — one young bot enforces DEEP+liq (the +4.6 sleeve), the
     others keep the deep-alone soft tilt. Isolates the volume cost to one lane.
  3. Deep-alone (-3.0, 28% of fills) stays the size-neutral soft preference for the
     un-sleeved lane, exactly as the prior mine recommended.

## Wired (SHADOW ONLY — no enforce, no sizing, no commit)

`feeds/dip_scanner.py`, alongside `deep_capitulation_shadow`:
- `deep_combo_shadow` = **"FAVOR"** when entry `pc_h1<=-45` AND `liquidity_usd>=30000`, else
  **"SKIP"**. Raw `deep_combo_liq` stamped for the join. Fail-open (isinstance guard,
  read-as-zero => not favored; missing axis => no stamp). `deep_combo_shadow_favor` counter +
  would-favor log line. Measure-only: grades forward on fresh tape. `py_compile` OK.
- Contract test `tests/test_deep_combo_shadow.py` (4 cases: favored / thin-skip /
  shallow-skip / fail-open) — locks the thresholds and the interaction guard. PASS.

## ENFORCE SPEC (written, env-gated, DEFAULT-OFF — do NOT enable; AxiS approves live enforce)

Pattern mirrors `FLEET_TOKEN_CAP_MODE` / `RISK_FLOOR_MODE` (`off|shadow|enforce`, default
shadow) in `feeds/dip_scanner.py`.

- Env: `DEEP_COMBO_MODE` ∈ `{off, shadow, enforce}`, **default `off`** (stamp already grades
  forward unconditionally, independent of this gate). `DEEP_COMBO_LIQ_FLOOR` default `30000`,
  `DEEP_COMBO_PC_H1_MAX` default `-45`.
- Scope: young lane only (`bot_id` startswith `badday_young` OR `young_token_probe`), and
  intended for a DEDICATED SLEEVE bot, not the whole fleet (volume cost above).
- Logic (in the buy-decision path, near the `hl_confirm_entry` skip at ~line 3235): when
  `DEEP_COMBO_MODE=="enforce"` and bot in scope and `entry_meta.deep_combo_shadow != "FAVOR"`
  → `return` (skip buy) with an info log. FAIL-OPEN: any missing axis / exception → allow
  (never block on unknown), same as the existing gates.
- **Enable prerequisites (unchanged discipline):** n>=20 distinct deep+liq tokens/side/half
  on FRESH forward tape AND pass side stays green — the two thin halves here are CH2 (9 tok)
  and EVEN (12 tok), still < 20. Plus explicit AxiS go for live enforce.

## Verdict (one line)

**Deep+X GOES GREEN:** DEEP (pc_h1<=-45) AND liquidity_usd>=30k = ex2 token-median **+4.6**
(3/4 halves green, winner-concentrating, genuine interaction, robust neighborhood) — but as
a hard gate it keeps only ~19% of young-lane fills, so ship it as the `deep_combo_shadow`
FAVOR stamp + a soft-preference / dedicated-sleeve enforce (spec written, `DEEP_COMBO_MODE`
default off), with deep-alone remaining the size-neutral soft tilt for the volume lane.
Files: `scratchpad/sol_selection/{combo_hunt.py,combo_verify.py,_trips.json}`,
`feeds/dip_scanner.py` (stamp), `tests/test_deep_combo_shadow.py`.
