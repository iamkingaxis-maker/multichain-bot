# T1 — REACHABILITY WINDOW (the crux)

## Question
For our SELECTED dips, was the decision/snapshot price actually reachable in real-time
long enough to fill, or an instant-recovery wick? If reachable → edge is real, fix =
speed. If not → artifact, selection must change.

## Data
- `_full_trades.json`, 5000 records: 2142 buys, 2858 sells.
- Distinct buy tokens = **138**. Tokens with both buy+sell = **124**.
- Token-level = first (earliest) buy per address. All numbers below are token-level.

## 1+2. Detection-timing distribution (captured 1s features = the REAL real-time path the bot saw)
The `entry_meta` 1s_* fields are the bot's own live 1s capture of the prior 60s at the
decision instant — this IS the ground-truth real-time reachability evidence (better than any
re-pull, which can't reach sub-minute history anyway).

`1s_close_pos_60s` (0 = decided AT the 60s low, 1 = decided at the high), n=126:
- median **0.653**, mean 0.606
- **AT/near the low (close_pos<=0.2): 18% (23/126)**
- **on the BOUNCE (close_pos>=0.5): 68% (86/126)**
- **at the very TOP of the 60s range (close_pos>=0.9): 32% (40/126)**

`1s_bars_since_low_60s` (seconds elapsed since the 60s low at decision), n=81:
- median **6.0 bars**, mean 8.5, p25=2, p75=11, p90=20
- bars_since_low<=2 (low still fresh): 21/81

**Both at-low AND fresh (close_pos<=0.2 AND bars_since_low<=2): 8% (10/126).**

`1s_range_pct_60s` median 2.7%; `1m_max_drop` median -3.9%; `1s_bottom_score` median 20.

### Interpretation
By the time we DECIDE, the 60s flush-low is already a median ~6 seconds stale and price has
recovered to ~65% of the 60s range. 68% of entries fire on the bounce, a third literally at
the top of the range. We are structurally NOT catching the low — we detect the recovery.

## Booked fill vs decision price (the fidelity gap, token-level n=109)
`entry_price` (booked) vs `entry_mid_price` (decision/snapshot):
- median **+3.04%** above decision, mean +2.12%, p90 +5.42%
- **92.7% of fills booked ABOVE the decision price.**
- entry_slip_pct median 0.10% (this is small — the +3% gap is decision→fire drift, NOT swap slippage).

So the chain is: actual 60s flush low → (+~1.8%, ~6s later) decision/snapshot price →
(+3.0%) booked fill. We pay the recovery twice.

## 4. Outcome split (winners vs losers): is the edge in reachable dips?
124 tokens with buy+sell, outcome = mean pnl_pct over the token's sells.
47 winners (meanpnl>0), 77 losers.

| feature | WIN median | LOSE median |
|---|---|---|
| 1s_close_pos_60s | 0.597 | 0.678 |
| 1s_bars_since_low | 5.5 | 6.0 |
| 1s_range_pct_60s | 2.61 | 3.01 |
| 1m_max_drop | -4.00 | -4.37 |
| 1s_bottom_score | 20.0 | 20.0 |

**Detection-timing is statistically identical between winners and losers.** Winners decided
a hair closer to the low (0.60 vs 0.68) but both groups fire firmly on the bounce. There is
NO "reachable-dip edge": profitable selections are NOT the ones where we caught a deeper /
fresher / more-reachable dip. The profit difference lives in WHICH token, not in HOW
reachable its dip price was.

## 3. Forward fill-window (seconds price <= decision price AFTER the dip): NOT reconstructable from free data
- io.dexscreener chart endpoint is **minute-resolution only** (res 1/5/15/60) — can't resolve a seconds window.
- Trade-log endpoint returns only the ~100 most-recent swaps — can't reach historical entries.
- Minute bars (cb=1000) cover only the most-recent ~1000-2900 min; for all but the freshest
  tokens that window POST-dates the entry (returns the ~98%-decayed price, not the entry window).
- Of 16 recent sampled tokens, only ~4 had minute coverage reaching the entry. Among those the
  one clean signal split both ways: Ansem's decision price was never revisited in the next 10 min
  (ran away — unreachable); BABYANSEM's entry-minute low printed +1.2% ABOVE the decision price
  (stale snapshot) but chopped back to it within minutes. n far too small to conclude.

**Honest limit:** the exact ">=2s / >=5s reachable" figure cannot be measured from free tools
for historical entries. The best available reachability proxy is the captured-1s evidence above:
only **8% of entries** were both detected at the low AND with the low still fresh (<=2s old).

## HEADLINE VERDICT
The snapshot/decision price is **NOT a reachable flush-low** — by the time we decide, the 60s low is
already a median ~6 seconds stale and price has recovered to ~65% of the range (68% of entries fire
on the bounce, 32% at the top). We then book a further **+3.0%** above even that decision price.
Critically, reachability does **not** separate winners from losers — there is no reachable-dip edge.

This contradicts the pure "fix latency → profitable" thesis: we are not arriving late to a price
that sat there waiting; we are deciding on the recovery **by construction**. Faster filling still
recovers real money (the +3% decision→fire gap), but it will NOT recover the flush-low, because the
low was gone ~6s before we even decided. The lever that matters is **earlier/finer DETECTION**
(fire AT the 1s low instead of ~6 bars after it) — a detection-resolution problem, not a fill-speed
problem. The 8% of entries we already catch at a fresh low are the proof the reachable dip exists;
we just admit it only 8% of the time.

## Recommendation
1. Keep speeding the fill (closes the measured +3% decision→fire gap = direct money), but stop
   expecting it to restore the old paper edge — that edge was the flush-low, which is a DETECTION
   miss, not a fill miss.
2. Move the trigger to the 1s low: arm on the dip, but FIRE on a fresh-low confirmation
   (close_pos<=0.2 AND bars_since_low<=2) rather than after the bounce is underway. Paper-safe,
   flag-gated, winner-safe A/B (the 8% fresh-low cohort is the seed).
3. Since reachability is flat across outcomes, do NOT expect a reachability filter to lift P&L —
   selection (which token) remains the dominant lever.
