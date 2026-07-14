# Rug-Forensics Mine — Actor-Behavior Entry Gate (RESUME GATE evidence)

Date: 2026-07-11. Author: rug-forensics agent. Mandate: produce the evidence base for an
ACTOR-BEHAVIOR entry gate that would have blocked HOODLANA-class rugs at decision time with
winner-kill <= 5%. Report + reusable artifacts only — NO live-path code shipped, no prod/config/
Railway state touched. All artifacts under `scratchpad/rug_forensics/` (per-token JSON, scripts,
PROGRESS.md) + this report.

## VERDICT (read this first)
**No actor-behavior rule in the mined axes meets "catch HOODLANA-class AND winner-kill <= 5%."**
Three independent reasons, each evidenced below:
1. **HOODLANA was an LP-PULL rug, not an actor-history rug.** Its largest holder is the PumpSwap
   pool vault, which is exactly why our `dev_pct_remaining` creator-dump gates passed. The tell is
   pool/LP custody + reserve behavior, not deployer identity.
2. **The actor tells HOODLANA *did* exhibit are non-discriminative on Solana pump-tokens.** Fresh
   throwaway deployer and dev-sniped-own-launch occur at the SAME rate in tokens that ultimately
   died and tokens that survived (17% vs 13%; 39% vs 40%). Gating on them kills 13–40% of winners
   (>> 5% bar) while barely touching the rug base rate.
3. **After containment, rug vs non-rug are EV-identical**, so a rug-blocking gate's only value is
   preventing the rare cap-hitting TAIL — and we have exactly **n=1** confirmed catastrophic Solana
   rug (HOODLANA). You cannot validate a catch-rate or a <=5% winner-kill from n=1.

What WOULD decide it is listed at the end ("What data would decide this").

---

## 1. HOODLANA anatomy  (artifact: `rug_forensics/hoodlana_anatomy.json`)
Mint `C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump` (pump-suffix; PumpSwap-quoted). Rugged -98%
2026-07-11 despite passing holder-rug / security / liquidity guards. Pulled deployer, funder, early
buyers, and LP custody live via `core.rpc_pool` (Alchemy + publics; NEVER Helius).

- **Mechanism = LP-pull ("invisible" because our metrics can't see it).** Current largest holder
  `F6KmxYyuMDUUN2YBTGxFCirwaTXCS8TQopRvi2GCQps1` holds **98.78% of supply and is owned by
  `pAMMBay6…` (PumpSwap AMM)** — i.e. it is the **pool vault**, not a wallet. `feeds/dev_wallet.py`
  derives `dev_pct_remaining` from the largest **non-program** holder, so it structurally skips the
  pool and reads a ~0.3–14% retail holder as the "dev." That number was >= our block thresholds at
  buy time, so `dev_not_dumped` / `filter_dev_dumping` / the hard creator-rug gates all PASSED. The
  loaded gun (the LP) was never in view.
- **Deployer = `Hk4HUiTo7DGC1VzcjypNRehQDTjfo4XnLk8XmZbpx9TR` — a fresh single-purpose throwaway.**
  ~7–13 lifetime signatures total; first tx ~**1–25 s before launch**; **zero prior tokens**.
- **Deployer sniped its own launch** — it appears as an early-second buyer of HOODLANA.
- **Funder = `JEK8ciMXxvuNpbyqS9pW62QDFdaohDgYXqKQY4ayxvZt`** — an ultra-high-frequency wallet
  (1000 sigs in a 160 s window); an infra/bot funder, not obviously a per-rug funder. The funder-
  lineage tell is weak/ambiguous here (this hop looks like a generic funding relay, not a farm).

So at our buy time an actor check *could* have seen: (a) a deployer with ~no history that (b) sniped
its own token, and (c) that the LP is the dominant holder. (a)+(b) are the "actor" tells; (c) is an
LP-security tell. Sections 3–5 test whether (a)/(b) actually separate rugs from winners. They do not.

---

## 2. Cohorts  (artifacts: `rug_forensics/cohorts.json`, `rug_labels.json`, `death_split.json`)
Source of touched tokens: `scratchpad/_full_trades.json` (our paper fleet, 5000-row cap, to 2026-07-08),
keyed on the real mint (`address`; note the `token` field is only the symbol). 224 distinct mints.

- **Realized-PnL cohorts are unusable for rug labeling.** Containment caps our loss: across all 224
  mints the worst realized token-mean is ~-66%, no -80%+, and any single slice yields only ~5
  clear winners and ~7 deep losers — far too few to grade a 5% kill rate. **This is itself a finding:
  our realized history contains no clean labeled rug cohort — the severity is masked by our exits.**
- **On-chain death labels (dexscreener, NO RPC credits — `label_rugs.py`).** Of 224 mints, 152 still
  listed / 72 gone. **Ultimate-death label** = (not-found) OR (current liq < $5k) OR (price now
  <= -80% vs our median entry) → **105 DEAD (47%) / 119 ALIVE.** This is the closest thing to a real
  Solana rug cohort we can build, but "ultimately dead" ≠ "catastrophic cap-hitting rug like
  HOODLANA" — most of these bled out, they didn't -98% in minutes.

### The pivotal control result
Our **realized token-mean PnL is statistically identical for DEAD vs ALIVE tokens: −3.04% vs −3.61%**
(n=105 / 118). Whether a token ultimately rugged or survived, containment brought us out at ~-3%.
**Implication for the gate's value:** blocking the entire dead cohort would have changed our realized
PnL by ~nothing, while any winner-kill is pure cost. A rug gate is only worth its winner-kill if it
specifically prevents the rare **cap-hitting tail** (HOODLANA), not the ordinary dead cohort.

---

## 3–5. Actor-feature table + candidate rules  (artifact: `rug_forensics/actor_features_v2.json`)
Balanced RPC crawl (1121 calls, 1 error): 18 DEAD + 15 ALIVE (highest current-liq survivors as the
winner control) + HOODLANA. Per mint: true deployer = fee-payer of the genesis tx (paged to real
genesis, cap 30 pages, archive-aware `getTransaction`); deployer lifetime-sig count; deployer
age-at-launch; deployer-sniped-own-launch.

Measurement caveats (documented so the next agent trusts the numbers): (a) many tokens — even dead
ones that pumped before dying — exceed 30k txs, so genesis is not always reached on the free stack;
(b) `dep_age_at_launch` is only meaningful when the deployer's own lifetime sig count < 1000, because
`getSignaturesForAddress` caps at 1000 and an active deployer's "first" sig is then just its
1000th-most-recent (yielding spurious negative ages). We therefore use **lifetime-sig-count < 50** as
the robust "fresh throwaway" proxy and report self-snipe directly.

| Candidate actor rule (BLOCK if…) | Catch on DEAD | Winner-kill on ALIVE | Verdict |
|---|---|---|---|
| Deployer is a fresh throwaway (lifetime sigs < 50) | 17% (3/18) | **13% (2/15)** | FAIL — kill > 5% AND no separation (17 vs 13) |
| Deployer sniped its own launch | 39% (7/18) | **40% (6/15)** | FAIL — kill 40%; literally zero separation |
| `dev_pct_remaining` proxy < 20 (ALREADY LIVE) | — (dead 11% vs alive 14% median dev_pct) | — | Already enforced; blind to LP-pull (§1), missed HOODLANA |

HOODLANA itself: lifetime sigs 13, age ~1 s, sniped = True → it has both actor tells. But **13–17% of
SURVIVORS carry the same tells**, so the tells are properties of the pump.fun launch style, not of
rugging. There is no threshold on these axes that isolates HOODLANA-class tokens from winners.

Local-only cross-check (free, no RPC): the `dev_wallet_addr` proxy (largest non-program holder) shows
**no serial-rugger structure** — 178/182 wallets touched exactly one token; the 6- and 22-token
wallets are whales/market-makers, not repeat deployers. This mirrors the RH-chain decode
(`scratchpad/_rh_history_decode.md`: "0 repeat pre-collapse net-positive sellers; blacklist empty").
Funder-lineage / insider-cluster axes: HOODLANA's funder is a generic high-frequency relay (§1), and
with only n=1 catastrophic case there is no cross-rug funder overlap to mine on Solana yet.

---

## Existing related work — folded in (not duplicated)
- `feeds/dev_wallet.py` — the `dev_pct_remaining` proxy = largest non-program holder. Root cause of
  §1: it cannot see LP-pull because it excludes the AMM pool by design.
- `tests/test_dev_not_dumped_gate.py` + `core.bot_evaluator.dev_not_dumped_blocks` — blocks only a
  CONFIRMED dev-dump (dev_pct < 20), fail-open on missing. HOODLANA's proxy dev_pct was above
  threshold at buy → passed. Plus 3 more creator-rug gates in `dip_scanner.py` (<50, <2.0, <1.0),
  all keyed on the same blind proxy.
- `scratchpad/_verify_dev.py`, `scratchpad/_pending_gate_analysis.py` — prior dev-gate grading
  harness (FIFO buy→sell join, scrub trivial round-trips, token-mean). Reused the join/scrub method.
- `scratchpad/_rh_rug_actors.json` / `_rh_history_decode.md` — EVM (Robinhood chain) seeds; a
  DIFFERENT HOODLANA (`0x02bf449e…`). RH rug-actor blacklist is empty-not-disproven; same "identity
  dead / LP-pull invisible in swap tape" conclusion as here.

---

## What data WOULD decide this (the resume-gate ask)
The question is not yet answerable because the positive class (catastrophic cap-hitting Solana rugs)
is n=1 and the mechanism is LP-custody, not actor history. To decide it, in priority order:

1. **Build a labeled catastrophic-rug cohort (target n>=30).** At every paper/live entry, persist a
   durable actor+LP snapshot (see #3), then follow each token 24–48 h and label catastrophic
   collapse (-90% with liquidity drained). Only this yields a real catch-rate. (Our realized PnL
   can't label it — containment masks severity; §2.)
2. **Grade the mechanism-aligned gate, which is LP-custody, not actor-history.** Since HOODLANA was
   LP-pull: (a) LP mint authority / `markets[].mintLP` null check (rugcheck lpLockedPct is
   DEX-inconsistent for burns — check mintLP null, per memory); (b) a live pool-reserve drain
   monitor (SOL side of the PumpSwap pool falling fast) as a decision-time block. These need a
   reserve time-series we don't currently record.
3. **An archival/indexer path for at-buy-time deployer + funder features on high-volume tokens.** The
   free public RPC cannot reach genesis for the >30k-tx tokens winners live in, and caps wallet
   history at 1000 sigs — so deployer age / prior-token-outcomes / funder-graph are not extractable
   at scale for the winner side. The RH backfill path (1,129 collapse-block pools) is the EVM analog.

**Bottom line for AxiS's resume decision:** the "invisible rug" was invisible because it was an
LP-pull and our dev-proxy watches the wrong wallet — not because we lacked an actor blacklist. An
actor-behavior entry gate on the tells HOODLANA showed would kill 13–40% of winners for ~no rug-EV
gain (rugs already cost us the same ~-3% as survivors after containment). If the resume bar is
"block HOODLANA-class with winner-kill <= 5%," the evidence says pursue an **LP-custody / reserve
monitor** and build the labeled catastrophic-rug cohort first; the actor-identity axes tested here do
not clear the bar.

### Artifacts (survive death)
`scratchpad/rug_forensics/`: `PROGRESS.md`, `hoodlana_anatomy.json`, `cohorts.json`, `rug_labels.json`,
`death_split.json`, `actor_features.json` (v1, superseded), `actor_features_v2.json` (graded),
and scripts `rpc_lib.py`, `hoodlana_anatomy.py`, `actor_crawl.py`, `actor_crawl_v2.py`, `label_rugs.py`.
