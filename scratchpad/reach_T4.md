# T4 — HONEST P&L ON REACHABLE FILLS

## HEADLINE VERDICT
**The core edge does NOT survive at reachable fills. Speed is NOT the fix.**
Our arm→fire latency is already **~1.2s median** (mean 1.7s, p90 3.1s), and the booked
fill price `entry_price` (B) IS the fresh Jupiter price at that moment — i.e. **B already
IS the price reachable at our real latency (C ≈ B)**. The +2.6% gap between the profitable
stale book (A) and the breakeven/negative real book (B) is the **stale-snapshot illusion**
(decision price `entry_mid_price` is a ~2-min-old DexScreener REST snapshot), NOT arm→fire
latency we can close by filling faster. At reachable fills the strategy is breakeven-to-slightly-negative.

## THREE BOOKS, SAME EXITS (token-level, distinct address, n=113 tokens; 2785 sell legs)
| book | what | mean% | median% | WR | total$ |
|------|------|------:|--------:|---:|-------:|
| **A_stale** (fake) | entry = `entry_mid_price` (decision snapshot) | +2.26% | +0.25% | 52.2% | +$6,314 |
| **B_current = C reachable@1.2s** | entry = `entry_price` (fresh fill we book now) | −0.82% | −2.48% | 36.3% | −$92 |

Raw sell-leg level (n=2785): A +5.73%/+0.59%/54.6%WR vs B +2.48%/−3.32%/44.7%WR.

- A→B drift (entry above stale snapshot): **median +2.62%, mean +3.14%** (95% of buys fill above the snapshot).
- 10.3% of legs have the edge ERASED by drift (A>0 but B<=0).

## WHY C ≈ B (no faster-fill lever exists)
- decision→fire latency from `signal_ts_ms`→buy `time`: **median 1.2s / mean 1.7s / p90 3.1s**.
- A +2.62% drift over 1.2s would be ~2.2%/sec — physically implausible as a smooth time-drift.
  It is a **discrete stale-vs-fresh jump**: `entry_mid_price` is the ~2-min-stale main-scan
  DexScreener snapshot, not the price 1.2s ago. So the flush-low in A had already recovered
  by the time we *decided*. You cannot fill faster than the decision.
- Therefore filling faster than ~1.2s recovers almost nothing; the recoverable A→C gap is ~0.
  Modeling C(N=2,5,10s) by interpolating A→B over latency gives values WORSE than B (more drift),
  confirming B sits near the achievable floor.

## FORWARD-DATA CHECK (GT bars — does the market revisit A after we fill?)
GT minute retention only reaches ~16h, so the 1m sample is thin (4/45 covered).
5m sample (≈3.5d retention) below.

### 1m (n=4, thin): A revisited within 10min after fill in 4/4; B sits ~5.4% above the best
reachable 10-min low.
### 5m (n=11 covered of 90; 79 no GT coverage):
- A reachable within ~15min after fill: **11/11 (100%)**
- A below the fill-bucket low (stale already below mkt): only 1/11 (9%)
- **B sits +8.97% (median) ABOVE the best reachable 15-min low**
- **A sits +8.45% (median) ABOVE the best reachable 15-min low**
- A→B drift in this sub-sample: median +2.02%.

INTERPRETATION: Within 15 min of our fill the price dips BELOW BOTH A and B (both sit ~+9%
above the forward 15-min low). So (a) the stale price A is *revisited* after we fill — it was
not pure fiction — and (b) we are filling INTO continued downside (the knife keeps falling
~9% after we buy, consistent with the negative B book). This means A (and lower) is reachable
WITH PATIENCE — a **resting/limit bid at/below the dip price** would fill minutes later — NOT
by filling faster at market. The lever is **order-type / selection (let price come to the
dip), not latency.** n=11 is thin — treat as a lead, not proof.

## RECOMMENDATION
1. **Stop treating speed as the P&L fix.** We already fill in ~1.2s; the A→B gap is the
   stale-snapshot illusion, not latency. (Confirms reference_fresh_fill_gap fork toward the
   *selection* horn, not the latency horn.) Keep PAPER_FIDELITY_MODE=enforce (B is honest).
2. **The honest reachable edge (B) is breakeven-to-negative** at the fleet level. The edge that
   made A look good lives in the ~2.6% we can't reach at market. Profit must come from
   (a) SELECTION that still has edge at the fresh price, or (b) **resting limit bids at the dip
   price** so the market comes back to us (forward data hints A is revisited within minutes).
3. **Next measurement (firm up the patience lever):** widen the GT-5m forward sample / use the
   io.dexscreener internal API for deeper retention; quantify what fraction of A is revisited
   within 1/2/5/10 min and the realized P&L of a resting-bid-at-A book. If a limit-at-dip book
   is clearly +EV, that is the real fix — not latency.

## ARTIFACTS / METHOD NOTES
- scripts: scratch_reach_t4.py (A vs B, authoritative per-sell pnl_pct, FIFO buy-join by address+entry_price),
  scratch_reach_fwd.py (1m forward), scratch_reach_fwd5.py (5m forward).
- A→B is a pure ENTRY transform: A_ret = (1+B_ret)*(entry_price/entry_mid_price) − 1; same exits.
- Guards: dropped pnl_pct>1000% or <−100% glitches; dropped buy-join mismatches >2% (44 legs).
- Fat-tail caveat: medians negative for B; means are tail-driven. Token-level used to kill
  fleet rebuy inflation (same token bought 12-22x).
