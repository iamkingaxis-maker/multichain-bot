# Portfolio Structure: 10 Winner Wallets vs Our Dip-Buy Fleet (2026-06-26)

Structural companion to the hold-time/stop-out study. Focus: concurrency, breadth,
capital rotation, survivorship. Quantitative + skeptical.

## DATA CAVEAT (read first)
The decode files expose only the **last 12 closed trips** per wallet plus header
counts (`N tokens, M closed, K open`). Full trip history is NOT in these files, so
every concurrency number I compute from trip windows is a **lower bound** and ignores
the open bags entirely. The header **open-count is the better live-concurrency proxy**
(positions held simultaneously at decode time). Our side (`_full_trades.json`) is
complete: 2,197 buys / 2,210 fully-closed positions over a 6.7-day window.

---

## 1. CONCURRENCY

**Winners (open-count = live concurrent holdings at snapshot):**

| wallet | tokens | closed | OPEN (concurrent now) | maxconc in last-12 trips |
|---|---|---|---|---|
| 2tYcX  | 116 | 44 | **32** | 3 |
| 7d54Pt | 46  | 8  | **32** | 1 |
| B1zhrW | 42  | 28 | 9 | 4 |
| C3zP   | 35  | 22 | 7 | 6 |
| jStURX | 21  | 15 | 6 | 5 |
| DU25Xy | 18  | 14 | 4 | 3 |
| DaxfeJ | 25  | 18 | 4 | 6 |
| DznHq  | 8   | 6  | 2 | 3 |
| ArWird | 10  | 8  | 2 | 3 |
| Zsp75  | 28  | 25 | 1 | 7 |

Concurrency spans an order of magnitude: the wide rotators (2tYcX, 7d54) sit on **32
concurrent open positions**; the concentrated holders (DznHq, ArWird) hold **2**. The
last-12-trip maxconc (3–7) understates true concurrency because it excludes the open
book — the real simultaneous-holding number is the open-count.

**Us:**
- **Per-bot max concurrent ≈ 3** (badday family all =3; timebox/chameleon variants 5–6,
  a soft cap). This is the binding constraint.
- **Fleet-wide max concurrent = 62, time-weighted avg = 4.3** — but this is mostly
  redundancy: ~15 bot variants independently buying the SAME entries, not 62 distinct bets.

**Verdict:** The high-WR winners we most want to emulate (2tYcX, 7d54) run **WIDE
portfolios of ~32 concurrent small bets**. Our per-strategy structure is **concentrated
to 3 slots** that we recycle hundreds of times. Even the winners' concentrated archetype
(2–7 concurrent) matches or modestly exceeds our 3-slot cap.

---

## 2. BREADTH vs DEPTH (spread-once vs re-buy-few)

**Winners — breadth, ~1 position per token:** distinct tokens 2tYcX 116, 7d54 46,
B1zhrW 42, C3zP 35, Zsp75 28, DaxfeJ 25, jStURX 21, DU25 18, ArWird 10, DznHq 8. The
trip lists are essentially one trip per distinct token — they **spread across many
tokens, each bet once**.

**Us — depth, heavy re-entry:**
- Fleet: 157 distinct tokens, **14.0 buys/token**, 14.1 closed positions/token.
- Per-bot (apples-to-apples, single strategy over the window): **~73 distinct tokens,
  re-entry ~2.7x** each (badday family 2.5–2.7x; timebox 2.9x).
- The QUEST 109x / RUST 79x figures in `_us_profile` are **fleet aggregates** (15 bots ×
  re-entry), not one bot hammering a token.

**Verdict:** Confirmed asymmetry. Winners = **breadth without re-entry** (one shot per
token across a wide pond). We = **moderate breadth + systematic re-entry** (each bot
re-buys its tokens ~2.7x; the fleet stacks the same names 14x). A meaningful slice of
the winners' breadth is also a **discovery gap, not just a slot gap**: our scanner saw
only 4% of 7d54's tokens and 33% of 2tYcX's — their pond is largely invisible to our feeds.

---

## 3. CAPITAL ROTATION / VELOCITY — two archetypes

| | **A — WIDE FAST ROTATOR** | **B — CONCENTRATED SLOW HOLDER** |
|---|---|---|
| wallets | 7d54 (7m), DaxfeJ (9m), 2tYcX (91m, p25 12m) | DU25 (6.7d), DznHq (11h), ArWird (6.6h), jStURX (p75 14.5h) |
| tokens | many (46–116) | few (8–21) |
| concurrent open | high (32) | low (2–4) |
| sizing | small (0.0–0.77 SOL) | large (1.5–24 SOL) |
| capital | spread thin across many slots, rotated fast | tied up in few slots for days |
| tail mechanism | spray many tail-shots, high WR on closed | hold one bag through to the moonshot (+178000%, +9.8M%, +162261%) |

**Which archetype supports "many concurrent tail-shots"? Archetype A.** Spraying many
small bets and rotating fast keeps slots free to keep adding shots; the concurrent open
count (32) is exactly the spray. Archetype B is the opposite — it locks capital in 2–4
large bags for days to ride a single fat tail.

**The tension — and the slot-count implication.** "Hold longer **and** take many
tail-shots" are in direct conflict under a fixed slot budget. By Little's Law
(concurrent slots L = entry-rate λ × hold W), using our measured per-bot entry rates:

| bot | buys | λ/min | L @5.6m (now) | L @91m (2tYcX) | L @6.7d (DU25) |
|---|---|---|---|---|---|
| badday_flush_convex | 197 | 0.0315 | 0.18 | 2.9 | 304 |
| badday_flush_conviction | 177 | 0.0283 | 0.16 | 2.6 | 273 |
| timebox_probe_mcap | 130 | 0.0755 | 0.42 | 6.9 | 729 |

At our current 5.6-min hold the average slot demand is <0.5 (bursts hit the 3-cap). To
hold like 2tYcX (~91 min, **16x** longer) average demand rises to ~3–7 and **bursts
would blow far past the 3-slot cap**. To hold like DU25 (6.7 days) demand is **250–700
concurrent** — categorically impossible with 3 slots. **Our 3-slot/bot cap is the
mechanism that forces the 5.6-min exits.** Holding for the fat tail requires either a
~16–50x larger concurrent-slot budget or a proportionally slower entry cadence — you
cannot bolt long holds onto the current slot budget without dropping most entries.

---

## 4. SURVIVORSHIP CAVEAT (critical) — magnitude

Realized WR/returns book **only closed trips**; open positions are excluded. Open share
of total positions:

| wallet | open / total | % unbooked |
|---|---|---|
| 7d54Pt | 32/40 | **80%** |
| 2tYcX  | 32/76 | **42%** |
| jStURX | 6/21  | 29% |
| DznHq  | 2/8   | 25% |
| B1zhrW | 9/37  | 24% |
| C3zP   | 7/29  | 24% |
| DU25Xy | 4/18  | 22% |
| ArWird | 2/10  | 20% |
| DaxfeJ | 4/22  | 18% |
| Zsp75  | 1/26  | 4%  |

**Direction of bias is indeterminate from this data, but two regimes:**
- These wallets are **price/discretion-stopped, not time-boxed** ("loser exits
  dispersed"). A disciplined discretionary trader books losers at a stop and lets
  winners run → closed trips over-represent losers, open = winners-in-progress → that
  would **deflate** realized WR.
- The classic memecoin failure is the opposite (disposition effect): hold underwater
  bags hoping for recovery, never book the loss; a dead/rugged bag costs nothing to
  leave open → open positions = hidden losers → that would **inflate** realized WR. For
  memecoins this regime is common, so I lean toward open bags containing a non-trivial
  share of dead weight, i.e. headline WR is **optimistic** for the wide sprayers.

**Magnitude:**
- **7d54's 88% WR is computed on 8 of 40 positions (80% unbooked) — not credible**, a
  single decode snapshot, treat as noise.
- **2tYcX's 68% WR carries a 42% open overhang** — materially uncertain.
- The **concentrated holders (~18–25% open)** are more trustworthy but still carry a
  ~1-in-4 unbooked overhang on the headline WR/median.
- **What is NOT survivorship-biased: the monster best-returns are all CLOSED trips**
  (+178097%, +9,834,860%, +162261% are realized). So the **fat-tail upside edge is
  real and booked**; the uncertainty is concentrated in WR and the loss-median, not in
  whether the moonshots happened.

**Comparison fairness flag:** Our 29% WR is on 2,210 **fully-closed** positions with
near-zero open overhang (we time-box/floor everything out). Winners' WR is on partial,
optimistically-pruned books. The headline "29% vs 52–88%" comparison is **apples to
oranges and biased against us** — a fair comparison would mark their open bags to market.

---

## RECOMMENDATIONS (shadow-first; no per-token caps, no downsizing)

The structural lever is **slot budget × hold-time**, not selection or size. We already
established (overlap 34/35 C3zP, 21/28 Zsp75, 9/10 ArWird) that the same tokens made
them money — so the gap is how long we hold and how many slots we keep open.

1. **Shadow "patient sleeve."** Stand up a shadow cohort fed by the SAME entries as the
   badday/timebox bots, but with (a) a wider concurrent-slot budget and (b) a long
   max-hold + runner-trail instead of the 5.6-min time-box. Measure realized fat-tail
   capture on the SAME tokens we already trade. This is a pure exit/slot A/B, no new
   selection, no sizing change. Shadow-only until n≥30 closed.
2. **Quantify the slot bill before any live change.** Little's Law says a 91-min hold
   needs ~16x the slot-occupancy of today; a multi-day hold needs 250–700 concurrent.
   Any "hold longer" proposal must come with its slot budget attached or it silently
   drops entries.
3. **Breadth is partly a discovery gap, not only a slot gap.** 7d54 1/46, 2tYcX 19/116
   invisible to our feeds — widening feed coverage is an orthogonal lever to slot count
   and should be tracked separately.
