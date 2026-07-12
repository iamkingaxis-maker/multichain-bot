# RH deep-decode: what the GREEN racers do right (2026-07-12)

AxiS: "the deep racers are green — figure out what they're doing correctly."
Today's RH paper fleet (railway leaderboard = ground truth):

| grp | racer | day P&L |
|-----|-------|--------:|
| GREEN | rh_deep_only | +$4.65 |
| GREEN | rh_bites2 | +$4.56 |
| GREEN | rh_f_arc_scalp | +$3.08 |
| RED | rh_demand_heavy | **-$14.61 (WORST)** |
| RED | rh_wide_ladder | -$4.38 |
| RED | rh_moonbag | -$2.49 |

## Data honesty
- Local `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` is a PARTIAL earlier snapshot;
  the ledger is per-bot and NOT deduped in the file (unique ms-ts seq — dedup is only in
  the downstream dashboard ingest), but its absolute per-bot P&L differs from AxiS's later
  railway read (local demand_heavy even looks green — its selection edge hadn't bitten yet
  in the snapshot window). So **AxiS's P&L = ground truth; the local tape is used only for
  the STRUCTURAL/behavioral distributions** (entry depth, exit-kind mix), which are stable.
- `rh_f_arc_scalp` and all factory racers = **0 local fires** (require_session_anchor +
  <10-min age); its edge is the full-history backtest (`_rh_candidate_factory.md`) + railway.

## Green-vs-red decomposition

Config (deterministic) + observed local distributions:

| grp | racer | ENTRY | EXIT ladder | dipMed | nbuy | lossLeg% | TP2-reach% | moon-tail legs | tripStd |
|-----|-------|-------|-------------|-------:|-----:|---------:|-----------:|---------------:|--------:|
| GREEN | rh_deep_only | dip **-25** (capitulation) | **scalp** +6/.75 +12/.25 -15 3pp | -28.0 | 24 | 23% | 21% | 0 | 5.74 |
| GREEN | rh_bites2 | dip -12 + **2-bite cap** | **scalp** (same) | -15.2 | 27 | 38% | 21% | 0 | 2.73 |
| GREEN | rh_f_arc_scalp | dip -6..-25 + vol≥$4.8k + arc≤+300% | **scalp** (same) | — | 0* | — | — | 0 | — |
| RED | rh_demand_heavy | dip -12 + **demand $150** | scalp (same) | -17.9 | 50 | 15% | 22% | 0 | 2.92 |
| RED | rh_wide_ladder | dip -12 | **WIDE** +10/.75 **+20**/.25 -15 3pp | -17.9 | 65 | 34% | **13%** | 0 | 4.67 |
| RED | rh_moonbag | dip -12 | scalp + **10% moonbag** 0%-floor 20pp trail | -17.8 | 64 | 21% | 16% | **18** | 2.90 |

\* backtest: tokmed_ex2 +$1.97, cat 0.4%, 4/4 halves (`_rh_candidate_factory.md`).

## Q1 — Is "green = deeper entries" literally true? NO.
Depth does **not** sort green from red:
- Green `rh_bites2` enters at **-15.2** median dip — SHALLOWER than red `rh_demand_heavy`
  at **-17.9**. Green `rh_f_arc_scalp` is explicitly a MODERATE-band buyer (-6..-25).
- Only one green (`rh_deep_only`, -28) is actually deep. If depth were the lever, bites2
  (shallow) would be red and demand_heavy (deeper) would be green. They aren't.

**The lever is two things the three greens SHARE and each red BREAKS:**
1. **The tight scalp exit** — bank 75% at **+6**, TP2 25% at +12, full -15 stop, tight 3pp
   trail, **NO moonbag, NO time box**. All three greens run this verbatim (LaneBot defaults).
2. **Entry by PRICE STRUCTURE, never chasing demand strength** — depth/arc/pullback, at the
   DEFAULT $50 demand floor.

## Q2 — Why is demand_heavy the WORST?
Its only deviation is raising `demand_min_buy_usd` 50 → **$150** — i.e. it filters on
**demand-at-the-moment**. Two independent decodes say that is the wrong axis:
- **RH winner-delta** (`_rh_candidate_factory.md` §1): *"Demand-at-the-moment (net inflow,
  distinct buyers, buy counts) does NOT separate the cohorts — position in the arc and
  proven volume do."* Requiring a big $150 print just admits pools where the pop already
  happened (buying the top of the move = the LATE-arc loser profile).
- **SOL selection mine** (`_sol_selection_mine.md` §3): the `mean_buy ≥ $34` gate **INVERTS**
  — bigger buyers = marginally MORE red. The lane is *"least-red buying capitulation, worst
  chasing strength."*

So demand_heavy over-trades (most buys, 50) on a **non-separating / inverting** axis that
tilts it toward chasing strength. Its exit mechanics look fine locally (15% loss legs) —
the damage is pure SELECTION, which is exactly why its P&L collapsed later than the snapshot.

The other two reds break the EXIT instead:
- **wide_ladder**: waits for +10/+20. RH fades revert fast, so +10 is often never reached —
  observed **TP2-reach 13%** vs the greens' ~21%, and the highest loss-leg rate (34%). It
  converts would-be +6 winners into trail/stop exits.
- **moonbag**: keeps a 10% residual on a 0%-floor, loose 20pp trail. That tail rides the rug
  down (18 MOONBAG_FLOOR/TRAIL legs observed), giving back the banked TP1.

## Q3 — The consolidated "deep" recipe
Fuse the three green edges; keep the anti-chase discipline:

| lever | value | source |
|-------|-------|--------|
| entry trigger | dip **-25** (deep capitulation) | rh_deep_only + cross-chain thesis |
| proven-volume floor | `min_session_vol_usd = $4,800`, anchor OFF (reads OBSERVED lifetime vol, conservative lower bound like rh_f_reload24) — defends the thin-flush LOSER profile | rh_f_arc_scalp |
| re-entry cap | `max_bites_per_token = 2` | rh_bites2 |
| demand floor | **DEFAULT $50 — never raised** (the anti-chase lesson) | demand_heavy failure |
| exit ladder | **scalp defaults verbatim**: TP1 +6/0.75, TP2 +12/0.25, stop -15, trail 3pp, NO moonbag, NO time box | all three greens |
| universe | `max_pool_age_h = 24h`, `min_liq_usd = 30k` default | scalp universe pin |

Shipped as **`rh_deep_consolidated`**, `exclusion_group="deepsynth"`, in
`scripts/rh_paper_lane.py` ROSTER. No new gate logic — every knob is an already-tested
existing gate (dip_trigger, proven_vol_block, bite_gate, scalp exit). Roster spec pinned in
`tests/test_rh_factory_racers.py::TestDeepConsolidatedRacer` (RH suite: 515 passed, exit 0).

**Pre-registered confirm** (backtest earns a RACE seat, never a live seat): grade at **n≥30
CLOSED positions** vs the scalp fleet as control, per-token medians (ex-top-2), never sums.
CONFIRM = tokmed ex-top2 green AND cat ≤ 1/20 AND direction = deep-capitulation; else it
retires to the kills list, no re-tune on the same tape.

## Recommended CUT (do NOT delete — AxiS retires candidates)
**`rh_demand_heavy`** — its sole lever (demand $150) filters an axis that two independent
decodes (RH winner-delta + SOL selection mine) show is non-separating and inverting; it was
the worst racer (-$14.61) and structurally chases strength, the opposite of the proven edge.
Flag for retirement to the documented-kills list once AxiS confirms; leave it in the roster
until then.
