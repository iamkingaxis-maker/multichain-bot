# Winner EXIT-Behavior Decode (07-01 → 07-02 tape set)

Built 2026-07-03. Companion to `_current_regime_winners_report.md` (entries/holds). Decodes the SELL
side of the 14 realized-core winner wallets, plus the full 49-winner set as corroboration.

**Data.** Same tapes + dedup as the entry study (union-of-entries, W_START 07-01T00:00Z). Winner
trades: 1,799 on 118 pairs (116 with bars); no token spans >1 pair among them, so (wallet,pair) ==
(wallet,token). Minute-bar coverage was EXTENDED this session (`fetch_extend_bars.py`, all 106
gap pairs refetched from GT to last-trade+4h — COMPLETE): core episodes have **0 price-missing
events** and 100% bar coverage (the entry study's ledger had e.g. 19/21 PEACE trades unpriced).
Core set: **14 wallets, 78 episodes (buy ≥$20), 73 with ≥1 covered sell, 157 covered sells** —
THIN, every claim n-stated. Scripts: `exit_decode.py` → `exit_decode_rows.json`,
`exit_grid_results.json`, full console dump `_exit_decode_final2.txt`.

**Accounting honesty (matters).** This study uses matched avg-cost accounting: sell qty =
USD/bar-close, sells matched to visible in-window buys, uncovered sells (no position) EXCLUDED
(46 prints), leftover marked-to-last = unrealized. Under this accounting the core's in-window
realized sum is **+$40, not the +$1,628** the entry study's cash-flow ledger showed. The gap is
uncovered sells — e.g. 2mDuf/PEACE "+$1,117" becomes **−$11**: they sold ~2× the tokens they
visibly bought in-window (pre-window inventory / cross-pool / tape-sample gaps), and cash-flow
accounting credited all of it as profit. The 14 wallets remain valid **behavior exemplars** (their
covered exits are green-skewed and disciplined), but "realized winner" P&L attribution is soft.
Behavior metrics below are unaffected; dollar totals are.

---

## Q1 — EXIT SHAPE: modal exit is ONE full clip; scale-out is the conviction mode

n=73 sold core episodes (dust sells <$2 excluded from shape stats):

- **Sells per episode: median 1** (p75 2, p90 4, mean 2.2). 37/73 single full-clip, 35/73
  scale-out (≥2 sells), 1 single-partial.
- First sell takes **median 100% of position** (p10 50%, mean 89%). When they do scale, consecutive
  sells are **median 11.6 min apart at median 0.0% price change** (p10 −30%, p90 +20%) — tranches
  fired around one price zone, not a laddered price grid.
- Campaigns of 10–17 sells exist but belong to 2–3 wallets on conviction tokens (DR TRUMP ×3
  wallets, PEACE). Per-wallet style is heterogeneous: 4 of the top-6 realized wallets scale out
  (50–100% of their episodes); the grinders below them are pure single-clip.

**Profile:** they are not ladder-exiters like our TP1/TP2 — they are one-shot-out (small episodes)
or campaign scale-out (conviction episodes), with re-buys between sells (see Q4).

## Q2 — WHERE THEY SELL: scratch machines, not top-tickers (the prior study's "sell into strength" needs revision)

n=157 covered sells:

- **vs their entry VWAP: median 0.0%** (p25 −1.8 / p75 +4.2 / p90 +17.0, mean +1.1). USD-weighted
  buckets: <−12: 6.4% | −12..0: 23.3% | **0..+6: 48.8%** | +6..+12: 10.1% | ≥+12: 11.3%.
  **78% of their exit dollars leave BELOW our TP1 (+6%).**
- **Not top-ticking:** only 8% of sells within 2% of the running post-entry peak; 8% within 3% of
  the local 30m high; median sell sits −11.5% below running peak / −14.0% below the 30m high.
- **Not selling into strength either:** 55% of sells print into a FALLING 5m tape (mom5 <−1%) vs
  32% rising. The modal winner sell is a **failed-bounce scratch at ~breakeven**, not a strength
  scale-out. (The entry study's "53% of sells above first-entry price" was true but framed off
  first entry; vs campaign VWAP the median sell is a scratch. DELTA-2's "scale out into strength"
  phrasing should be revised to "scratch the failure, keep only proven runners".)
- They habitually leave tail: median fwd_max60 after a sell +14.7% (mean +30%). But median
  fwd_min60 is −16.2% — half the exits also dodge a bigger drop. Their exits are defensive, and
  roughly EV-neutral vs holding 60m more.
- Closed-episode realized multiple (n=69, ≥80% of buy USD exited): **median 0.0%, mean +0.1%,
  p75 +1.5, p90 +9.5, p10 −9.2**. Realized WR on closed episodes = 39/69 = **57%** (the 67%
  headline came from cash-flow accounting). Their realized game is: many scratches, small losses,
  a few +9..+28% campaign winners.

## Q2c — THE COUNTERFACTUAL: our ladder is NOT the leak vs their actual exits — but a wider ladder beats both

Simulated OUR ladder (TP1 +6 sell 75% / TP2 +12 / trail 2pp / stop −12 / 240m timestop, worst-case
intrabar ordering: bar low checked before high) from THEIR first-buy price on the same episodes:

| n=69 closed core eps | THEIR actual | OUR ladder |
|---|---|---|
| mean | +0.1% | −0.5% |
| median | 0.0% | +5.7% |
| USD-weighted (n=73 sold) | **+0.25%** | **+1.37%** |
| head-to-head | — | ours better on 36/69 |

All-49-winner corroboration (n=229 sold eps, USD-weighted): theirs +2.36% vs ours +2.49%.

**Answer to the money question: our ladder neither over- nor under-harvests vs their actual selling
— it's a wash-to-slightly-better.** Their per-episode advantage is entirely in LOSS SHAPE: our −12
stop fires on 25/69 episodes (booking −12.0 each) where they cut the same failures at median
−3.0%; **24% of THEIR green episodes get stopped out at −12 under our ladder** (their median
outcome on our stop-out set: −0.0%, 8/25 green). Conversely we harvest their winners better than
they do (cf median +5.7 vs their 0.0). The exits aren't why they out-earn us; entries + campaign
sizing are (entry study). BUT the same sim shows our CURRENT ladder is far from optimal on this
entry cohort:

**Ladder grid** (tp1 ∈ {4,6,9,13} × frac {0.5,0.75} × tp2 {12,20,30} × trail {2,4,6} × stop
{−8,−12,−18}, 198 configs, worst-case intrabar, 240m timestop):

| config | CORE n=77 mean / med / win | ALL n=299 mean / med / win |
|---|---|---|
| **current** 6/75%/12/tr2/−12 | −0.46 / +5.7 / 62% (rank 160/198) | +0.56 / +5.8 / 68% (rank 152/198) |
| **grid best: 13/50%/30/tr2/−18** | **+1.54 / +12.3 / 60%** | **+3.04 / +12.4 / 64%** |
| 13/50%/30/tr2/−12 | +1.23 / +0.7 / 51% | +1.89 / +4.0 / 53% |
| 13/30%/30/tr2/−12 (≈wideexit_ab) | — | +2.28 / +4.0 / 53% |
| 9/50%/20/tr2/−12 | +0.37 / +8.2 / 56% | +1.57 / +8.3 / 61% |
| 9/50%/30/tr2/−18 | — | +2.64 / +8.6 / **71%** |
| + failed-bounce scratch (30m,<+1%) on 13/50%/30/−18 | **+2.89 / +12.0 / 60%** | +2.42 / +0.8 / 55% |
| 4/50%/12/tr2/−12 ("harvest early like them") | — | +0.75 / +3.9 / 75% |

Decomposition: TP1 6→13 is worth ~+1.3pp (ALL, at stop −12); stop −12→−18 another ~+1.2pp (their
entries wick p25 −12.4 then recover — the −12 stop sits exactly inside their tolerated wick zone).
Copying their EARLY harvest (tp1=4) changes ~nothing (+0.75 vs +0.56 current): their scratch style is
defense, not alpha — the alpha configs let winners run PAST +12 while widening the floor below the
wick zone. This independently re-derives the `badday_flush_wideexit_ab` thesis from the winners'
own episodes and says its next A/B arm is the floor (−12 → −18) and TP2 (+30).

## Q3 — THE +4..+9 GIVEBACK BAND: they enter it, then scratch out flat; they don't harvest it and don't ride it red

Episodes whose peak within 120m of first buy landed in [+4,+9): core **6/78 (8%)**, all-winner
38/306 (12%) — they do NOT avoid these tokens.

- What they do: first sell within minutes at ~+0.3..+1.5 or scratch/cut small later (−0.0, −4.3,
  −6.4). Closed band realized: core mean −2.3% (n=4, VERY THIN), all-winner median 0.0 / mean
  −1.1 (n=13). **They neither capture the +4..+9 peak nor round-trip it — they scratch.**
- Our current ladder on the same band episodes: mean −2.3 / med +0.7 (n=38) — peaks 6–9 hit TP1,
  peaks 4–6 ride to stop/timestop (the NEIL shape). We are ~comparable to winners in this band
  already; the band is a small leak on winner-grade entries, not the main one.
- The wide ladder pays its tax here: 13/50%/30/−12 in-band = −7.2 mean / 24% win (never TP1s).
  Band episodes are ~12% of the cohort; the aggregate still strongly favors wide.
- **Breakeven-arm at +4 (arm floor 0 once peak ≥+4) is REFUTED as the band fix** on this cohort:
  it repairs the band to −2.1 but destroys the aggregate (ALL: win 53%→29%, mean −0.6pp; CORE mean
  +1.23→−0.13). Winner-style dip entries routinely tag +4, dip below 0, THEN run — a BE floor
  scratches out of the exact runners that pay for everything. (Contrast with the 06-26 breakeven-arm
  lever, which was mined on OUR entries — do not port it to wide-TP configs.)

## Q4 — RE-ENTRY: their round 2 LOSES; don't copy it

n=27 re-buys after a profitable sell (in 15/73 sold episodes; the entry study's "40–49%" counted
re-buys after ANY sell):

- Timing/price: median 11.5 min after the exit, at **−2.0%** vs their exit price (p25 −12.3,
  p75 +1.3 — half re-buy at-or-above their exit).
- **Round-2 realized on later covered sells vs re-entry price: median −6.6%, mean −6.4%** (n=25;
  2 open). The re-bought leg saw median +11.5% fwd_max60 but they didn't capture it.
- Verdict (thin): re-entry is the WEAKEST part of the winner playbook — small repeated giveback
  after banked profit. For the family: keep post-exit cooldown; if a re-entry lane is ever built,
  demand a deep discount (their −2% median discount lost; only the p25 −12%+ discounts had room),
  i.e. **re-entry only ≥10% below prior exit px**, not the ≥3% floated in DELTA 2.

## Q5 — LOSS EXITS: they cut small and early on failure-time, not at a price floor — and the worst reds are unbooked bags

n=30 closed losing core episodes (30/69 = 43% of closed):

- Cut depth: **median realized −3.0%** (last sell −3.9), p25 −8.8, p10 −12.4. 22/30 losses are
  shallower than −8; only 3/30 deeper than −16. Median time to the loss-cut: 15.1 min.
- Combined with the entry study's endured-wick stat (median −7.2 / p25 −12.4 drawdown BEFORE the
  first sell), their loss rule is **time-conditioned**: sit through the wick, and if the bounce
  hasn't materialized in ~15–30 min, scratch near flat — NOT a −12-ish price stop. Nobody in the
  core cuts systematically in the −12 zone; they're out (scratched) long before, or much later at
  −20+ on the rare conviction failure.
- **Bag-hold caveat:** 8 core episodes are still >20% open; 5 are net-red marked-to-last, and the
  two biggest core drawdowns of the whole window are OPEN BAGS (GNOCCHI −$84, dog −$179
  unrealized). Their realized WR is flattered by unbooked reds — "someone takes losses somewhere"
  = partly "someone hasn't booked them yet."

---

## RECOMMENDED EXIT-LADDER DELTA (parameters, thin-n flagged)

Everything below is derived from n=77 core / n=299 all-winner episode sims on ONE 07-01→07-02 tape
window, simulated on winner-selected entries with worst-case intrabar minute bars. Ship as A/B /
shadow, not enforce.

1. **Wide-arm A/B for the badday family** (extends the already-live `badday_flush_wideexit_ab`
   thesis with two new arms): **TP1 +13 sell 50% / TP2 +30 / trail 2pp / hard floor −18 / timestop
   unchanged.** vs current ladder: +2.0pp (core) to +2.5pp (all) per trade, median +12 vs +6, win%
   −2..−4pp. The floor −12→−18 alone is ~+1.2pp because winner-grade dips wick p25 −12.4 before
   running. ⚠️ Floor −18 was validated ONLY on winner-selected entries; on our unselected book a
   deeper floor eats bigger rug tails — pair with the rug-gate stack + young-holder guard and A/B
   at $5 size first.
2. **Failed-bounce scratch leg on the wide arm** (their actual loss mechanic): if TP1 unhit AND
   price < +1% at t=30m → exit at market. Adds +1.35pp on core (+2.89 total, best core config),
   costs −0.62pp on the all-set — genuinely ambiguous, ship measure-only shadow alongside arm 1.
3. **Do NOT ship a +4-peak breakeven-arm fleet-wide** — refuted here (win% collapses to 23–29% on
   winner-style entries). The +4..+9 giveback is the rent the runner tail pays; the wide arm's
   aggregate already nets it out. If NEIL-type giveback must be addressed, it has to be gated on
   OUR entry quality (e.g. only on tokens failing the demand/absorption gates), not by peak level.
4. **Re-entry rule correction:** post-profitable-exit re-entry discount should be **≥10%** below
   prior exit (winners' median −2% discount re-entries realized −6.6%); keep BAIL_COOLDOWN.
5. **Keep TP-shape work OFF the "copy their harvest" path:** their 78%-of-dollars-below-+6 selling
   is defensive scratching, and simulating it (tp1=4) returns exactly our current EV. The exit
   alpha on this tape is: wider floor below the wick zone + runner leg past +12 — not earlier
   harvesting.

## Honesty ledger
- Core: 14 wallets / 78 episodes / 157 covered sells / 69 closed / 30 losers / 6 band / 27
  re-entries. All THIN; all-winner set (306 eps, incl. 35 mark-dependent wallets) used as
  corroboration everywhere and agrees directionally on every headline except scratch-leg value.
- Matched avg-cost accounting; 46 uncovered sell prints excluded (pre-window inventory/tape gaps);
  qty = USD/bar-close (minute-bar approximation); dust sells <$2 excluded from shape stats only.
- Counterfactual books TP fills AT the level, stop fills AT the level, low-before-high intrabar
  (conservative), 240m timestop, entry at their first-buy bar close, single entry (their realized
  benefits from DCA adds; ours doesn't — comparison favors them, and we still tie).
- One regime, one window (07-01→07-02, recorder died 07-02T19:22). Bars extended to last-trade+4h
  for ALL 106 gap pairs this session (fetch complete; n=299/306 sims — 7 episodes on 2 pairs GT
  returns no minute bars for).
- Prior-study numbers this report REVISES: "realized +$1,628" (cash-flow, includes uncovered
  sells), "67% episode WR" (57% on closed matched episodes), "scale out into strength" (median
  sell = breakeven scratch into falling tape; only conviction campaigns scale into strength).
