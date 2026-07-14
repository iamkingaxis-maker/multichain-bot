# Absorber-Wallet Lead-Lag Study (2026-07-03)

**Question:** when an absorber-ledger wallet buys at a flush low, does the bounce follow with
actionable lead time — and is the wallet identity worth anything over the anonymous
composition signal (n_buyers>=3 AND max_print>=$50) we already have?

**Script:** `scratchpad/ripday/absorber_leadlag.py` (imports flush/bounce/died definitions
verbatim from `absorption_decode2.py`; no network; per-pair tape dedup via `load_tape`).

## Data / n

- 172 ohlc2 files, 152 harvest + 95 live tapes. In-band (-15..-60%, non-rug), labeled,
  tape-joined events: **n=45** — BOUNCED=31 (23 pairs), DIED=14 (14 pairs). Base bounce
  rate 68.9%. Small n; everything below is direction-of-effect, not a calibrated rate.
- Absorber candidates (>=3 distinct bounced pairs, >=85% assoc): 16 wallets; fresh rebuild
  from current rows == persisted ledger (16/16 overlap). Top: `JD6rVaer…` (b=8 d=1),
  `hMMCnmxh…` (b=6 d=1), `7iVCXQn4…` (b=5 d=0), `7rbxsXch…` (b=5 d=0).

**Circularity flags (explicit):**
1. Wallet candidacy was mined from these same tapes. Cross-pair circularity is handled with
   a **leave-one-pair-out (LOPO)** candidate set (wallet must qualify on OTHER pairs before
   its buy counts on this event's pair). Headline numbers below are LOPO.
2. Even LOPO shares the tape SET with the mining step — no out-of-sample days exist yet.
   The head-to-head vs anonymous is fair (both scored on identical events, chronological
   first-crossing for anon vs first candidate-wallet buy for absorber).

## (a) Lead-time distribution (LOPO absorber, bounced fires, n=16)

| metric | med | p25 | p75 | min | max |
|---|---|---|---|---|---|
| lead to +10% bounce-CONFIRM bar (min) | **11.9** | 10.2 | 16.6 | -1.9 | 42.0 |
| lead to +5% bounce-START bar (min) | 10.0 | 8.0 | 11.8 | -1.9 | 39.0 |
| signal ts − flush-low ts (min) | **-6.7** | -8.3 | -2.9 | -9.7 | +3.9 |

- Lead time is real and actionable on paper: ~10-12 min before the bounce bar; only 1/16
  fires at/after confirm.
- **BUT the signal fires a median ~7 min BEFORE the flush low bar.** Price at the signal-time
  bar close is a median **+10.5% ABOVE the eventual low** (p75 +14.2%, worst +24.6%). An
  entry at signal time eats ~10% median further drawdown before the bounce. The "lead time"
  is mostly leading the LOW, not leading a risk-free entry.

## (b) False-positive rate (fires on DIED flushes)

- LOPO absorber: **2/14 = 14.3%** (event-level; 2 distinct died pairs, 0 pair overlap with
  bounced fires). Circular/global set: identical 2/14.
- Precision when fired: 16/18 = **88.9%** vs base 68.9% (+20pp). Half-split holds direction
  both halves (A: 10/11=91%, B: 6/7=86%; coverage drops 59%→43% in half-B).

## (c) Head-to-head vs anonymous composition (n_buyers>=3 & max_print>=$50)

| | LOPO absorber | anonymous | verdict |
|---|---|---|---|
| coverage of bounces | 16/31 = **51.6%** | 24/31 = **77.4%** | anon +26pp |
| FP rate on died | 2/14 = 14.3% | 3/14 = 21.4% | absorber −7pp (1 event) |
| precision when fired | 16/18 = **88.9%** | 24/27 = **88.9%** | TIE |
| lead to confirm (med min) | 11.9 | 11.5 | TIE |
| entry price vs low (med) | +10.5% | +10.2% | TIE |

- On the 15 bounces where BOTH fire: median timing delta **0.0 min**; absorber strictly
  earlier only **2/15** (anon earlier or simultaneous in 13/15). The absorber wallet IS one
  of the 3 buyers that trip the anonymous gate — same prints, same clock.
- Fired-by breakdown (bounced): both=15, absorber-only=1, anon-only=9, neither=6. The
  wallet index adds ONE bounce the anonymous signal misses, and misses NINE it catches.
- Additivity check (bounce rate among anon-fired events): with LOPO absorber present
  93.8% (n=16) vs anon-only 81.8% (n=11) — a +12pp confirmation kicker, but n=11 in the
  "only" cell = ~1-2 events of separation; not shippable as a standalone claim.

## Verdict / recommendation

**Anonymous absorption dominates for TIMING. A wallet index is NOT worth maintaining for
this purpose.** The absorber wallets carry no timing information beyond their own prints,
which the anonymous composition gate already counts: identical precision (88.9%), identical
lead (~12 min to confirm), identical entry-vs-low cost (~+10%), at 26pp LESS coverage and
the standing costs of an index (staleness, holdout fragility per the wallet-identity trap,
per-wallet maker tracking).

- Keep the anonymous trough-window gate (n_buyers>=3 & max_print>=$50, chronological
  first-cross) as the deployable bounce-timing signal; it needs no wallet state.
- The one residual use of the ledger: a possible **confirmation kicker** (+12pp precision
  when a proven absorber is among the 3 buyers) — park it as a shadow-only annotation,
  re-test when the tape set has genuinely new days (current test is same-tape circular).
- Real open problem for deployment is not lead time (12 min is plenty) but the **+10%
  residual drawdown from signal to low** — the signal triggers on the way down. Next study:
  condition entry on signal + N minutes of no-lower-close, and re-measure net edge.

*Learned, not doomed: identity adds nothing on this tape set, but the composition signal it
was mined from replicated cleanly across both pair-halves — the absorption axis itself keeps
passing checks.*
