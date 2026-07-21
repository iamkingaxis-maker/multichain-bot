# GET-AHEAD DOCTRINE — 2026-07-21

(3 Fable lenses over 219k maker rows/1091 pools/5 tape days; adversarially verified: 1 moderate survivor, 2 nulls)

GET-AHEAD DOCTRINE — memo for AxiS (2026-07-21 11:24 UTC)

Basis: 3 lenses mined + adversarially verified over 219,320 maker rows / 1,091 pools / 5 tape days. Verdicts: 1 moderate survivor, 2 nulls (one of them a useful null). Tape recorder is confirmed DARK since 07-18 01:32 UTC (newest file 07-17 20:31 local; 3.4 days of nothing) — several answers below are gated on restarting it.

---

**1. WHAT SURVIVED**

Is the registry real?
- As ADDRESSES: NO. Verified null, parameter-robust (9-config sweep): flagged-wallet pools show corpse precision 33-44% vs 68% base cross-day, recurrence 11-13% vs 7% base. Burner-grade. Address blocklist is a do-not-build.
- As an OPERATOR: YES. Cross-era role persistence is proven: 6-wallet buy-only squad ($27-33 median buys, $55-95k each), 4 sell-only distributors (0xcaf681 / 0x65050a / 0x578980 / 0x243a17, $62k-$350k dumped on ~zero buys), and the kill shot — bridge wallet 0xf70da9 serially refilling all 4 era-2 squad wallets (~2.6 native every 10-20 min) with funding confirmed through 07-20. The operation is live right now. The persistent key is the FUNDING PARENT, not the hot wallet.

Are the scripts predictable? PARTIALLY (the moderate survivor).
- YES, decision-time: distributor first-sell detection walk-forwards from era-1 registry to era-2 catching 93% of distributor-sell pools at median 0s delay. That is a real, forward-valid, computable-from-the-firehose trigger.
- YES, safe-side: pool death median ~25 min after last squad buy (uncensored n=40; 70% of the original sample was recorder-censored, so treat as a floor).
- NO, as a clock you can ride: distributors sell BEFORE the squad's first buy in 37% of pools. The "7-min rider window" is a tendency, not a script. The razor 14s clock belonged to a retired wallet. Corpse inversion is ~2x, not 4.4x, after censoring controls.

Is the organic class tradeable? NO. The 2:1 staged-corpse separation was a tape-truncation artifact (4h sessions cannot compute a 6h death label; staged events are mechanically flagged late-session). On the only honestly labelable day (07-10, 13.9h) it INVERTS — staged dips 0/18 dead vs organic 12%. The proposed ≥40%-manufacturer-share BLOCK would have blocked the safer class. Organic dips alone bounce 40%, median net −$91. Salvage is descriptive only: within-session persistence 37-56%, next-day 2-3%, plus two real collector bugs.

---

**2. RANKED BUILD LIST** (shadow stamps first; gates only where verified; all bars pre-registered per safe-live framework: n≥30, drop-top-2 still positive, ≥5 days / ≥20 tokens, tape-benchmarked)

0. **INFRA — prerequisite to everything, not optional.** (a) Restart the tape recorder; sessions must be ≥12h to make a 6h forward death label computable (4h sessions label zero events — this single fact killed lens 3). Per the no-local-24/7 rule, the right home is the Railway box if it fits the <$25/mo cap — your call. (b) Fix the collector to record pools the FLEET actually enters: the 0/22 bot-buy↔tape join means no per-class fidelity-honest P&L is measurable at all today. (c) flow_flags on buy rows started accruing 07-21 — day one is today.

1. **DISTRIBUTOR-SELL SHADOW STAMP → GATE.** Trigger: any of the 4 distributor wallets prints its first sell in a pool → stamp every subsequent paper entry in that pool `dist_active=1`. Verified forward (93% @ 0s), decision-time, operator live. Ship as stamp + paper-gate A/B under standing paper-lever consent. Bar to promote to a real block: n≥30 stamped entries, corpse-rate and net-$ separation vs unstamped, drop-top-2 positive.

2. **SQUAD-SILENCE FORCE-EXIT (paper A/B).** Force-exit any held position after >6 min without a squad buy in its pool (death median ~25 min after last squad buy; rule is safe-side even under censoring). Bar: n≥30 forced exits vs zero-illusion parent, net-$ and drop-top-2.

3. **FLEET-SWARM SHADOW STAMP (annotation only).** ≥8 distinct both-sides-early wallets in a pool's first 10 min (24/267 pools on 07-17). Currently ZERO corpse lift (66.7% vs 68% base) — it earns nothing but a log line until n≥30 labeled flagged pools exist. No gate talk before that.

4. **FUNDING-PARENT REGISTRY (data job, not a gate).** Weekly Blockscout pass: fanout of 0xf70da9 + first-inbound funding for the 54-wallet fleet one hop up. This is the persistent-identifier hypothesis lens 1 and lens 2 both point at; it is UNPROVEN (first probe hit a likely relayer). Refresh by watching the funder, not a decay clock — the "~1-week rotation half-life" was refuted (core wallets persisted and scaled ~100x).

**DO-NOT-BUILDS** (verified dead): address blocklist gate; ≥40%-manufacturer-share entry block (blocks the safer class); 7-min shadow-rider bot as designed (37% ordering violation).

---

**3. MISSING DATA + TAPE-DAYS ARITHMETIC**

- Binding constraint is LABEL COVERAGE, not wallet mining. Corpse label was discriminative on 1 of 5 tape days; every lift test in all three lenses rests on n=222 labeled pools from that one day, with 6-9 flagged.
- Arithmetic: a 6h death label needs session length ≥ 6h + decision window → 12h sessions honestly label their first ~6h. At 07-17 rates (~24 fleet-swarm pools and ~30-40 distributor-touched pools per ~8h), 12h sessions yield roughly 12-20 honestly-labeled flagged pools/day → **n≥30 in 2-3 recording days** for items 1 and 3.
- Item 1's promotion bar additionally needs the bot-entry↔tape join fixed (item 0b); until then stamped-entry P&L is unmeasurable no matter how many days accrue.
- flow_flags: first n≥30 flagged live-shaped buys plausibly within ~2-4 days of paper flow at current entry rates.
- Operator continuity check: one Blockscout look at 0xf70da9 per day, paced politely — if funding stopped after 07-20 the registry may already be stale.

---

**4. HONEST CEILING + FALSIFIERS**

Per the calibration rule, no $ forecast from proxies — this is bound arithmetic, not a projection.
- What defense already claims: entry filters halve bleed; manufactured fresh-launch dips = 58% of phantom wins; 34/36 live entries carried wash/rt flags. Fleet-level paper bleed has run O($100/day) in sick windows (−$401 vs +$67 healthy-routed on the same data).
- What offense can ADD on top of defense: (i) 0s-delay detection via distributor first-sell instead of waiting for wash stamps to accrue; (ii) exit timing ahead of the ~25-min death clock; (iii) riding the pump — refuted as designed. So the incremental offense claim is confined to operator-touched pools the fleet actually enters — and that overlap is measured at exactly 0/22 today because of the collector bug. **Plausible incremental: $0 until item 0b lands; low-tens/day at current paper sizing if the overlap is real; the larger prize is fidelity (deleting the 58% manufactured phantom-win class from every gradebook), which changes what we promote, not what we earn directly.** Realized n≥30 with haircut before any number goes in a plan.
- Falsifiers for the whole doctrine (any one kills it):
  1. Distributor-stamp shadow shows no corpse/net-$ separation at n≥30.
  2. 0xf70da9 funding stops and no successor parent is found within ~1 week (operator gone, or funding-parent is the wrong key).
  3. Collector fixed and fleet entries still show ~zero overlap with operator pools (offense is irrelevant to our flow — defense-only world).
  4. Fleet-swarm count still shows no lift at n≥30 labeled pools.

Bottom line: one operator, provably live, with one forward-valid trigger (distributor first-sell) and one safe-side clock (25-min death). Everything else the offense thesis promised — persistent addresses, ridable timing, a tradeable organic class — is verified dead. Ship the stamps, restart the tape at 12h sessions, fix the entry-pool join, and the doctrine gets its first real n≥30 test within ~3 recording days.