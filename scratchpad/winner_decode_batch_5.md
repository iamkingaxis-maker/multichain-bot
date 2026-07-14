# Winner Decode — BATCH 5 (lowest-net winners: break-even grinders vs real edge?)

Decoded 2026-06-29 via scripts/wallet_decode.py (sigs=150). Entry-state via GT minute OHLC sample.

## VERDICT UP FRONT
These are NOT a homogeneous "break-even grinder" bucket. The low *net* SOL hides three
DIFFERENT engines:
- **6dzn = runner-rider / launch-sniper** (huge fat-tail, small net = small size + big losers, unfollowable pond)
- **72QU = real-edge precision grinder** (tight asymmetric control, 89% in our pond) — the COPYABLE template
- **4mdpm = fast-scalper with a hard ~17min time-box** (symmetric +/-9%, 51% WR, MM-bot-like cadence)

---

## W1 — 6dzn1gFuWrNeV9oByVeVmAZhMaVWSheA6HjpNU9xputR
DECODE: 22 tokens, 13 closed, 9 open. SIZING median 0.36 SOL/token (variable/conviction).
HOLDS median 180min | p25 46m | p75 720m. Loser exits dispersed (discretion/price-stopped, NOT time-box).
RETURNS: **WR 62% | win med +625.6% | loss med -62.8% | best +64466.1%**.
OVERLAP: scanner saw 0/22 (0%) — **DISCOVERY GAP, pond invisible to us**; we traded 1/22.
- This is a MOONSHOT runner-rider / launch-sniper. The "+1.2 SOL / 40% WR" pull label is misleading:
  gross returns are astronomical (multiple +100x..+644x), net is small only because position size
  is tiny (0.36 SOL) and it eats full -94%/-99%/-62% losers on the misses.
- Fat-tail = UNREPLICABLE variance for a fleet; pond is fresh launches we never see. NOT followable as
  a copy template (0% pond overlap). Keep as evidence that the green-day money is in fresh-launch upside,
  not as a seat.

## W2 — 72QUWNCwsmYUMwkCGqvagCKAmq7Ng5mfG7yCeUYSLzEc
DECODE: 35 tokens, 32 closed, 1 open. SIZING median 0.86 SOL/token (variable).
HOLDS median 90min | p25 23m | p75 369m. Loser exits dispersed (discretion/price-stopped).
RETURNS: **WR 59% | win med +8.5% | loss med -2.7% | best +56.2%**.
OVERLAP: **scanner saw 31/35 (89%)** — HIGHLY followable pond; we traded 10/35.
- REAL EDGE, not break-even: asymmetric control (cuts losers at ~-2.7%, lets winners run to +8.5% median,
  occasional +30-56%). 59% WR on n=32 is robust. This is precisely the green-day grinder profile we want.
- 89% pond overlap = we ALREADY SEE its tokens but exit/select worse on them (traded only 10/35).
  ** BEST COPYABLE TEMPLATE of the batch.**

## W3 — 4mdpm59apd6PktGhSwu1385Kr4X9KTvM1rxxSrUBHA6C
DECODE: 38 tokens, 35 closed, 1 open. SIZING median 0.82 SOL/token (variable).
HOLDS **median 14min | p25 7m | p75 86m** — fast-scalper.
** TIME-BOX SIGNATURE: 71% of losers exit at ~17min (the Dw5 archetype).**
RETURNS: **WR 51% | win med +9.2% | loss med -9.2% | best +425.3%**.
OVERLAP: scanner saw 20/38 (53%); we traded 4/38.
- Fast in/out scalper with a HARD ~17min time-stop on losers. Symmetric win/loss magnitude (+9.2/-9.2),
  coin-flip WR — edge is the discipline (cut at 17min, occasional +425% tail carries net positive).
  Behaves MM-bot/scalper-like (high cadence, tight time-box). Followable-ish (53% pond) but the edge is
  EXECUTION/timing (17min cut), hard to copy without matching fill speed.

---

## ENTRY-STATE (momentum vs dip) — sample reconstruction (GT minute OHLC, dip90m = % off prior-90m high)

**W1 6dzn** (n=6): dip90m median **-2.3%** (p25 -28.4 / p75 +0.0) | MOM=4 DIP=2 | age median 42.8h | fdv median $0.01M (sub-$1M microcaps).
  Mixed, leans MOMENTUM/breakeven entry — buys at-or-near local highs (4 of 6 within -3%), occasional deep dip.
  Forward 6h shows the moonshot pattern (+218%, +62% on two, flat on others). = launch-sniper buying strength on tiny fresh microcaps.

**W2 72QU** (n=6): dip90m median **-24.6%** (p25 -27.2 / p75 -22.8) | MOM=1 DIP=5 | age median 202.9h | fdv median $0.56M.
  Clear DIP-BUYER on ESTABLISHED tokens (median ~8 days old, up to ~84d) after 20-47% pullbacks off 90m high; they recover (fwd +12 to +76%).
  Same mean-reversion thesis as our fleet, executed better with tighter loss-cut. ** Most aligned + copyable.**

**W3 4mdpm** (n=6): dip90m median **-31.0%** (p25 -34.0 / p75 -22.4) | MOM=0 DIP=6 | age median 556.8h (~23d) | fdv median $0.03M.
  PURE DIP-BUYER on OLD/established microcaps. Buys deep 20-79% pullbacks; fwd 6h recovers (+53%, +109% tails).
  Same dip thesis as W2 but executed as a FAST 14min scalp with a hard ~17min loser time-box (not a 90min hold).

---

## BATCH SUMMARY — common green-day winning pattern

**These were mislabeled as "break-even grinders." Two of three have genuine, identifiable edge; none is an MM-bot.**

The dominant green-day pattern across this batch (W2 + W3, the two followable ones) is **DIP-BUY mean-reversion on
AGED / established microcaps** — NOT momentum chasing:
- Both buy 20-31% median pullbacks off the prior-90m high (DIP entries, not strength), on tokens **days-to-weeks old**
  (W2 median ~8d, W3 median ~23d) — the opposite of fresh-launch sniping.
- Edge = **asymmetric loss control + letting recovery run**: W2 cuts at ~-2.7% / wins +8.5%; W3 enforces a hard ~17min
  time-box on losers and rides the occasional +100-425% recovery tail. WR is only 51-59% — money is discipline + tail, not hit-rate.
- Even on a green/pump day the WINNERS were buying DIPS within strength — vindicates our dip thesis; the gap is EXIT/SIZE
  execution, not entry style. (W1 is the exception: a fat-tail launch-sniper on $0.01M fresh microcaps, unfollowable pond.)

**Most copyable templates:**
1. **W2 (72QU)** — THE template. Dip-buy on established tokens, 89% of its pond already in our scanner (we see the tokens,
   we just trade only 10/35 and exit worse). Tight -2.7% loss cut + ~90min hold + asymmetric +8.5% win. Directly replicable
   on feeds we already have. This is our strategy done right.
2. **W3 (4mdpm)** — same dip thesis, faster (14min scalp + hard 17min loser time-box, the Dw5 archetype reincarnated).
   Copyable-ish (53% pond) but edge leans on fill-speed/timing discipline.

**Fat-tail honesty:** W1's +net rides on +644x / +188x moonshots offset by -94%/-99% losers — UNREPLICABLE variance on a
pond (0% overlap) we never see. Do not seat it. W2/W3 returns are modest and repeatable (n=32/35 closed) = real edge.

**Followability:** W2 = Y (89% pond, clean map). W3 = Y-ish (53% pond). W1 = N (0% pond / fresh-launch sniper).
All three parsed cleanly as direct on-chain traders (no aggregator-proxy / unfollowable-custody signal in the decode).
