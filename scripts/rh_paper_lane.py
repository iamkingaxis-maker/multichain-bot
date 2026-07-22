# scripts/rh_paper_lane.py
"""Robinhood Chain PAPER lane — the young-dip strategy on RH rails.

FLEET v1 (AxiS 2026-07-11): like the Solana fleet, this lane is a SELECTION
INSTRUMENT — N configs (ROSTER: 10 scalp racers + 3 aged-pool racers) run
CONCURRENTLY over the SAME
firehose feed and quote budget in one process. Per-POOL facts (tape, quote
prices, liq history, honeypot verdicts, dip/demand/micro/age/drain/rt-cost)
are computed ONCE and shared; each LaneBot config then applies its OWN
thresholds and trades independently (its own PerBotPositionManager, daily
P&L, cooldowns, block histogram). Ledger rows carry bot_id so the analysis
splits per racer; the dashboard's /api/rh-paper aggregates all rows as-is.

Wires the three shipped RH components into one per-session paper trader:
  detection  = sequencer firehose (scripts/rh_firehose_feed.py, ~0.9s lag)
  gates      = young-dip essence: quote-price dip trigger + demand turn +
               retrace-micro sell-distribution block (core/retrace_microstructure,
               same rip_tape schema) + honeypot (core/rh_honeypot, FAIL-CLOSED,
               mandatory before ANY entry) + liq floor
  exits      = core/per_bot_position_manager (the SAME exit engine as the
               Solana probe: tp1 6%/0.75, tp2 12%, trail, hard stop, bail)
  fills      = QuoterV2 quotes via core/rh_execution.RhExecutor (keyless
               eth_call: real pool state, fee + impact included) — the honest
               paper fill, and the exact call the live path would make.

PAPER BY DEFAULT: RhExecutor without RH_PRIVATE_KEY cannot sign
(RhPaperModeError); the shared quote machinery is quotes only. The ONE
exception is the LIVE FILL PROBE (2026-07-12): racers listed in
RH_LIVE_PROBE_BOTS route their fills through
core.rh_live_execution.RhLiveExecutor.live_buy/live_sell — and only while
the triple gate (RH_LIVE_CONFIRMED + RH_PAPER_MODE=false + RH_PRIVATE_KEY)
is open. Four conditions or pure paper; see the LIVE FILL PROBE block.

Latency parity mandate (AxiS 2026-07-10): every paper fill records the full
chain detect->fill: trigger lag_secs (firehose) + decision + quote round-trip.

Ledger: scratchpad/robinhood_tapes/rh_paper_trades.jsonl (one JSON per event).
Usage: python scripts/rh_paper_lane.py [max_minutes]
"""
import asyncio
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from rh_firehose_feed import (  # noqa: E402
    Firehose, WS_URL, RH_CHAIN_ID, RPC_DEFAULT, LOOKBACK_H, MAINT_SECS,
)
from rh_chain_feed import Feed, Rpc, _append, iso_utc, pctl  # noqa: E402
from core.retrace_microstructure import retrace_micro_eval  # noqa: E402
from core.rh_regime import (  # noqa: E402
    CompositionTracker, aged_hour_gate_ok, expectancy_dial, regime_stamp,
)
from core.rh_rug_signals import (  # noqa: E402
    compute_entry_stamp, fast_liq_bail_verdict, _fast_liq_bail_mode,
    rug_gate_verdict, rug_gate_enforcing, _rug_gate_mode,
)
from core.runner_signal import score_at_exit  # noqa: E402
from core.bot_config import BotConfig  # noqa: E402
from core.per_bot_position_manager import PerBotPositionManager  # noqa: E402
# sell-path canary (never-buys-while-sells-broken, 2026-07-10 incident rule).
# PAPER DEFAULT = canary mode OFF -> both hooks below are no-ops and the lane
# is byte-identical; RH_SELL_CANARY=auto turns it ON when RH_PAPER_MODE=false.
import core.rh_live_execution as rh_live  # noqa: E402

OUT_DIR = os.path.join("scratchpad", "robinhood_tapes")
LEDGER = os.path.join(OUT_DIR, "rh_paper_trades.jsonl")
STATE = os.path.join(OUT_DIR, "rh_lane_state.json")   # open positions survive restarts
# post-exit tail instrumentation (2026-07-10 trail-width analysis ask):
# every full close queues a +6h price check; results let the abandoned-tail
# question rerun from LOCAL data (no GT dependence). Pending file is durable —
# checks due after a session ends complete in the NEXT session.
POSTEXIT_PENDING = os.path.join(OUT_DIR, "rh_postexit_pending.jsonl")
POSTEXIT_RESULTS = os.path.join(OUT_DIR, "rh_postexit.jsonl")
POSTEXIT_DELAY_S = 6 * 3600.0
POSTEXIT_SWEEP_S = 600.0
DUST_SWEEP_S = 300.0        # sweep orphaned on-chain bags every 5 min (live only)
# ── FLEET-FIDELITY auto-refresh (2026-07-15). The dashboard's "real P&L" column
# was fed by a MANUAL run of scripts/rh_fleet_fidelity.py — it froze for 20h and
# read as "we haven't traded in 16 hours" while the lane was trading fine. A
# dashboard that silently serves stale numbers is worse than one that shows none,
# so the lane now refreshes it itself. Heavy-ish (quotes every unique token, paced
# 0.4s) -> 30 min cadence, OFF the event loop. FIDELITY_REFRESH_S=0 disables.
FIDELITY_REFRESH_S = float(os.environ.get("FIDELITY_REFRESH_S", "1800") or 0)
# ── LEDGER auto-upload (2026-07-15). THE root cause of "we havent traded in 16
# hours": scripts/rh_paper_upload.py says "Run per-session" — it was HAND-RUN, and
# nothing in the codebase calls it (main.py:47 is a string, not an invocation). So
# when a session ended the ledger froze while the lane kept trading for real (07-15:
# 50 on-chain txs / 24 live buys / $603 deployed vs ZERO ledger rows). WORSE: the
# lane's local ledger is EPHEMERAL (Railway) and resets on every redeploy, so rows
# not yet uploaded are LOST FOREVER — today's live fills were wiped that way. Upload
# is stdlib-only, append-mode and dedups on (ts,ev,pool) = idempotent + safe to run
# often, so run it OFTEN to shrink the loss window. LEDGER_UPLOAD_S=0 disables.
LEDGER_UPLOAD_S = float(os.environ.get("LEDGER_UPLOAD_S", "120") or 0)
# ── WALLET-TRUTH refresh (2026-07-13, AxiS: the RH hot wallet should ALWAYS
# show its on-chain balance on the dashboard, exactly like the SOL wallet) ────
# core.rh_live_execution.rh_wallet_truth() reads native ETH + WETH KEYLESS via
# env RH_WALLET_ADDRESS (no private key) and writes rh_wallet_truth.json to the
# rh_state_dir (= OUT_DIR by default). The lane refreshes it on the status
# cadence REGARDLESS of paper/live mode so the balance shows NOW, pre-live; the
# dashboard uploader (scripts/rh_paper_upload.py) ships the file to
# /api/rh-wallet-truth/ingest. ENV-DRIVEN + FAIL-OPEN: a clean no-op when
# RH_WALLET_ADDRESS is unset or a read errors — never blocks the hot path
# (called off the event loop from orchestrate's maintenance cadence).
WALLET_TRUTH_REFRESH_S = 300.0

# ── lane config (mirrors the Solana young probe where meaningful) ───────────
ENTRY_USD = 25.0            # probe sizing
MAX_CONCURRENT = 2          # probe cap
# PAPER = DATA (AxiS 2026-07-10: "whats the point of having a loss limit on
# paper? we need data"): a live-style daily stop starves the sample — any
# stop level can be applied to collected data retrospectively, but unsampled
# trades are gone forever. The remaining halt is a RUNAWAY-BUG backstop
# (broken loop machine-gunning losses), not a market-risk control.
DAILY_LOSS_STOP_USD = float(os.environ.get("RH_PAPER_DAILY_STOP", "-250"))
# Rug-guard port (2026-07-10 session-1 autopsy: Halp -90% + TREAT -17% = rugs
# that passed the unguarded v1 gates; the Solana probe's edge IS its guards):
MIN_LIQ_USD = 30_000.0      # PARITY with the live probe (was 10k -> rug pond)
# DEAD-SET ENTRY BLOCK (2026-07-21): known-unsellable tokens, published by
# the 30-min fidelity refresh (which quotes every fleet token). Entries into
# members are blocked fleet-wide with reason "dead_token" — equally across
# every A/B arm, so paired verdicts survive. FAIL-OPEN: empty set or a set
# older than 36h blocks nothing (a stale corpse list is its own illusion).
DEAD_TOKENS: set = set()
DEAD_TOKENS_TS: float = 0.0
DEAD_TOKENS_MAX_AGE_S = 36 * 3600.0

# DISTRIBUTOR WATCH (2026-07-21 get-ahead doctrine, the ONE verified survivor):
# an operator crew is live — 4 sell-only distributor wallets dumping $62k-$350k
# on ~zero buys, refilled by bridge 0xf70da9 (funded through 07-20). Their
# FIRST-SELL in a pool caught 93% of distribution events at 0s delay, walking
# forward across eras — the only forward-valid, decision-time offense trigger.
# SHADOW ONLY here: stamp dist_active on entries so the corpse/net-$ separation
# can be graded (needs the entry<->tape join fix + n>=30 before any gate). The
# 564-wallet address registry was REFUTED (burners rotate daily); ONLY these
# persistent-core distributors are watched. Env override keeps rotation cheap.
DISTRIBUTOR_WATCH = {a.lower() for a in os.environ.get(
    "RH_DISTRIBUTOR_WATCH",
    "0xcaf681,0x65050a,0x578980,0x243a17").split(",") if a.strip()}

MIN_POOL_AGE_H = 1.0        # dev-armed launch window: no fresh-pool entries
LP_DRAIN_WINDOW_S = 900.0   # liq-delta lookback (mirrors lp_delta_15m_pct)
LP_DRAIN_ENTRY_PCT = -15.0  # recent drain >= 15% -> no entry (RH v1: no data
                            # yet to refute a veto here, unlike Solana)
LP_DRAIN_EXIT_PCT = -30.0   # liq collapses while holding -> immediate full exit
# Exit-impact leak fix (AxiS 2026-07-10: winners netted +5-15 after sell
# impact, losers realized -9..-23 on -5..-11 decisions — we decide on
# buy-side prices and fill on sell-side into dying books):
MAX_RT_COST_PCT = 6.0       # entry gate: quoted round-trip (buy->sell NOW)
                            # charging more than this = friction eats the edge
DIP_TRIGGER_PCT = -12.0     # entry: price >=12% off the 10-min high
PRICE_WINDOW_S = 600.0
DEMAND_WINDOW_S = 30.0      # demand turn: net inflow over last 30s
DEMAND_MIN_BUY_USD = 50.0
HOT_TTL_S = 120.0           # pool is "hot" if traded within 2 min
MAX_HOT_QUOTES = 8          # quote budget per cycle (~130ms/call)
STRAT_TICK_S = 2.0
REENTRY_COOLDOWN_S = 300.0
GAS_USD_PER_SIDE = 0.01     # measured RH gas ~ $0.005; round up
# ── LIVE FILL PROBE (2026-07-12, AxiS: establish live buy+sell infra with
# fill times TODAY). One racer (rh_fill_probe) measures EXECUTION, not edge:
# $7.50 entries, <=4 buys/UTC-day, one position at a time, permissive
# young-dip gates, the standard exit ladder — real fills on BOTH legs are
# the deliverable. Its entries/exits route through
# core.rh_live_execution.RhLiveExecutor when and ONLY when FOUR conditions
# hold (env read at CALL time, never cached — see live_route_open):
#   RH_LIVE_CONFIRMED=true AND RH_PAPER_MODE=false AND RH_PRIVATE_KEY set
#   (the triple gate) AND the bot_id is listed in RH_LIVE_PROBE_BOTS.
# Any leg missing -> the racer is PURE PAPER and the whole lane is
# byte-identical. Live legs still book the normal paper ledger row (marked
# live=true) so every analysis reads both modes the same way; per-leg
# fill-time telemetry lands on the row AND in rh_live_fills.jsonl.
PROBE_SIZE_USD = float(os.environ.get("RH_PROBE_SIZE_USD", "7.50"))
PROBE_MAX_BUYS_DAY = int(os.environ.get("RH_PROBE_MAX_BUYS_DAY", "4"))
LIVE_FILLS = os.path.join(OUT_DIR, "rh_live_fills.jsonl")
LIVE_SELL_RETRY_COOLDOWN_S = 60.0   # a failed live exit retries no faster
                                    # than this (a reverted retry costs real
                                    # gas every attempt; the sell canary owns
                                    # halting BUYS on a broken sell path)
# ── Rug-defense SHADOW stamps (2026-07-11 HOODLANA port) ─────────────────────
# core/rh_rug_signals per-entry stamp: pool share of supply, top-holder
# structure (top1/top10/shoulder_11_20/visible float) and LP custody, appended
# as {"ev":"rug_signals"} ledger rows. SHADOW forensics — the buy DECISION does
# not read these rows (they are computed post-fill for offline grading). The
# ENFORCED concentration gate reads a SEPARATE arm-time Blockscout PREWARM (see
# RUG_PREWARM_ENABLED / _prewarm_rug); this full eth_getLogs stamp stays shadow
# because it is up to 90s (unusable inline). The labeled-outcome pipeline
# (post-exit +6h checks / cohort labeler) grades the stamps offline. Zero latency
# budget: computed on a daemon thread AFTER the paper fill books; single-flight
# lock + internal pacing so the stamper never contends with the strategy loop for
# the shared public RPC. Retro-validated
# on CASHCATGAME/MONSIEUR/Halp/TREAT/KUNA vs 4 aged survivors (costs measured
# 15-40 RPC calls/stamp; scratchpad/_rh_rug_port.md).
RUG_STAMP_ENABLED = os.environ.get("RH_RUG_STAMP", "1") != "0"
RUG_STAMP_CACHE_S = 600.0   # re-entries within 10 min reuse the computation
                            # (a row is still written per entry, flagged
                            # cached=True — the outcome join stays per-entry
                            # while the RPC cost stays per-pool)
# ── ARM-TIME RUG-GATE PREWARM (2026-07-13; scratchpad/_rh_rug_enforce_0713.md)
# The ENFORCE path for the concentration dump-class gate (rug_gate_verdict). The
# eth_getLogs holder replay is up to 90s (unusable inline), so instead the lane
# fires the CHEAP Blockscout holder fetch (core.rh_blockscout.blockscout_stamp,
# 2 calls / ~1-6s cold, 0 on cache hit) ON A DAEMON THREAD when a pool ARMS into
# the quoted watch set (_quote_hot). The result lands in self._bs_prewarm; the
# entry decision (_consider_entries) reads that warm verdict from a pure dict —
# ZERO added latency on the detect->fill path. FAIL-OPEN: no warm data yet ->
# the verdict blocks nothing (rug_gate_verdict({}) => block=False, source none).
# Kill switch RH_RUG_PREWARM=0 (default on); the gate mode is RH_RUG_GATE.
RUG_PREWARM_ENABLED = os.environ.get("RH_RUG_PREWARM", "1") != "0"
RUG_PREWARM_TTL_S = 600.0        # a warm bs_ verdict is good for 10 min (= the
                                 # Blockscout per-token cache TTL)
RUG_PREWARM_FAIL_TTL_S = 45.0    # an empty/failed fetch re-arms sooner so a
                                 # transient Blockscout blip doesn't fail-open a
                                 # pool for the full 10 min
MAX_BS_PREWARM_INFLIGHT = 4      # bound concurrent Blockscout prewarm threads
                                 # (candidate set is small; this caps a burst)
# ── ANTI-RUG holder-base floor (2026-07-14) ──────────────────────────────────
# Tokens with fewer than this many on-chain holders are rug-prone tiny bases.
# Validated on barbell's full history: blocks 4/6 rugs at only 6/44 winner cost
# (+$30 -> +$65 net). The other 2 rugs are LP-pulls that look like winners on
# holders (best handled by an exit-side LP-drain bail, not a pre-buy gate).
# Enforced FAIL-CLOSED on the LIVE buy path ONLY: a live buy that cannot verify
# holders >= floor is REFUSED (this is the GOATAI hole — fail-open on a fresh
# unprewarmed token let a 25-holder / 58%-top1 rug through). Paper is untouched.
RH_MIN_HOLDERS = int(os.environ.get("RH_MIN_HOLDERS", "50"))

# ── AGED-POOL cohort thresholds (2026-07-11) — every number set FROM DATA ────
# Sources: scratchpad/_rh_history_decode.md + scratchpad/rh_history/
# {decode_results,hour_rulebook}.json, plus a trip-level distribution rerun
# over the same decode dataset (details in _rh_aged_pool_racer_spec_notes.md):
# audited day-robust winners' per-(maker,pool) CLOSED trips by entry pool age:
#   age<1h: n=91  win 88%            1-6h:  n=26  win 62%  (weakest band)
#   6-24h:  n=9   win 78%            >24h:  n=335 win 73%  sum +$12,950
#   >24h WINNING trips: ret p25 +5.9 / p50 +15.6 / p75 +46.5
#                       hold_m p50 18.9 / p75 924 (fat tail)
AGED_MIN_POOL_AGE_H = 6.0       # decode actionable #2 band (6-24h+, the
                                # Solana adolescent_absorb mirror); sits above
                                # the LOSER cohort's median entry age 3.7h
                                # (decode_results profile_losers.med_age_m
                                # 223.5) and above the weakest trip band
                                # (1-6h). NOTE: the feed prunes pools >24h
                                # (rh_chain_feed MAX_AGE_H=24) so the >24h
                                # band — where the trips concentrate — needs a
                                # feed widen first (spec notes, follow-up).
AGED_TP1_PCT = 6.0              # p25 of >24h winning-trip returns (+5.9)
AGED_TP2_PCT = 16.0             # p50 of >24h winning-trip returns (+15.6)
AGED_TRAIL_PP = 10.0            # ride toward the p75 tail (+46.5); the 3pp
                                # BotConfig default trail is scalp-timescale
                                # (77s median holds — the wrong clock for this
                                # thesis). Partly judgment — flagged in the
                                # pre-registration for its own A/B.
DERISK_AFTER_S = 1200.0         # population census: median pool time-to-
                                # death 20 min (p25 5m / p75 80m, n=1129,
                                # _rh_history_decode.md) — bank down BEFORE
                                # the long-hold window starts.
DERISK_MAX_FRAC = 0.25          # post-window exposure cap (rug-tail defense):
                                # one -98% LP pull costs <=0.25*$25=$6.25
                                # (~4 median wins), not the -$24.4/position
                                # rh_wide_ladder paid on CASHCATGAME.
# ── LOW-VARIANCE cohort (2026-07-12 variance-reduction mine) — the two levers
# that cut per-trip P&L variance WITHOUT dropping trades or touching entry size
# (provenance: scratchpad/_variance_reduction.md). Both are already-built
# machinery re-pointed at the SCALP (young) admission, where the tail lives:
#   Lever 2 (catastrophe cap): force exposure down to 25% EARLY (5 min, not the
#     aged cohort's 20 min) so a later LP-drain/rug gap hits a quarter position.
#     RH realized: flooring the left tail at -20 cut per-trip stdev 20% AND
#     lifted mean -0.12 -> +0.63 (100% of trades kept — it reshapes the EXIT).
#   Lever 3 (hold-time box): the >10-min-hold scalp cohort carries higher stdev
#     (RH 18.7 vs 12.6, Solana 21.4 vs 17.3) and NEGATIVE edge — boxing it cuts
#     stdev ~6-10% and LIFTS mean, at ~8-19% volume cost.
LOWVAR_DERISK_AFTER_S = 300.0   # 5 min: scalp pops die ~20-min median but the
                                # rug/LP-drain tail lands later — bank to 25%
                                # well before it. (vs aged cohort's 1200s.)
LOWVAR_BOX_MINUTES = 10.0       # hold-box on the scalp cohort (Lever 3).

REENTRY_MIN_DIP_PCT = -26.0     # live boundary (session-7 MONSIEUR/QUANT):
                                # -12..-25% re-buys were slaughtered (-5.9..
                                # -18.8); -26..-38% paid +8..+15 (deepest
                                # live re-buy -31.6% took TP1 +15.1%).
REENTRY_MIN_VOL_M5 = 500.0      # the EXISTING bail floor (BotConfig
                                # pre_stop_bail_vol_m5_max=500); MONSIEUR's
                                # dead tape at the cascade was vol_m5 $109.
REENTRY_LOSS_WINDOW_S = 1200.0  # depth gate applies within 20 min of a
                                # LOSING exit (the observed re-entry cascade
                                # was minutes; the 20-min median-death clock
                                # bounds the danger episode) — NO flat
                                # cooldown (it would have blocked the deep
                                # winner; spec notes defect #2).
SIBLING_STOP_WINDOW_S = 1200.0  # cross-sibling exclusion window after a
                                # LOSING stop = the same 20-min median-death
                                # clock (MONSIEUR: 5+ racers re-entered the
                                # fleet-stopping token within minutes).
REGIME_BOT_ERA_POOLS_H = 200.0  # decode chain facts: human era 800-2,600
                                # pools/day (33-108/h) vs bot era 14k-20k/day
                                # (583-833/h); 200/h splits the gap. Feeds
                                # the discovery-regime STAMP (core/rh_regime
                                # .discovery_regime; rulebook v1: young-band
                                # bot-burst windows carry ~2x rug rate in all
                                # four halves — stamped, NOT paper-gated).
# REGIME v1 (2026-07-11, scratchpad/_rh_regime_system.md): the v0 human-era
# 14-23 UTC hour block is REFUTED by outcomes — 39,132 mined dip trips show
# human-era 02-07 UTC was the BEST young cell (volume != outcome; the v0 rule
# gated on volume). The one hour rule that passed the two-window bar is the
# aged-band 19-21 UTC block (core/rh_regime.aged_hour_gate_ok, provenance on
# the constant). regime_hours racers now enforce THAT, era-unconditional.
REGIME_MIN_UPTIME_S = 600.0     # discovery-rate warm-up: unknown rate for
                                # the first 10 min -> stamp reads None (the
                                # v1 hour gate no longer consumes the rate).


# ── fleet configs (the RACING ROSTER — selection instrument) ─────────────────
@dataclass(frozen=True)
class LaneBot:
    """One racer's config. Entry thresholds gate the SHARED per-pool facts;
    exit params feed its own PerBotPositionManager (the probe exit engine).
    Defaults = the current single-config lane verbatim, so
    LaneBot(bot_id="rh_young_v1") IS the control."""
    bot_id: str
    # entry MODE: "dip" = the young-dip trigger (dip + demand-turn);
    # "launch_strength" = the 2026-07-11 wallet-decode repeat-winner profile
    # (fresh pool, positive 120s net inflow, price ABOVE its 10-min open —
    # strength, not dip). The guard stack (micro/liq/age/drain/honeypot/
    # rt-cost/cooldown/bites/hours) applies identically to both modes.
    entry_mode: str = "dip"
    # entry thresholds (applied to shared pool facts)
    dip_trigger_pct: float = DIP_TRIGGER_PCT
    min_liq_usd: float = MIN_LIQ_USD
    min_pool_age_h: float = MIN_POOL_AGE_H
    max_pool_age_h: Optional[float] = None      # None = no ceiling (launch
                                                # scalp caps at 20 min)
    demand_min_buy_usd: float = DEMAND_MIN_BUY_USD
    launch_min_inflow_usd: float = 150.0        # launch_strength: 120s net
    max_rt_cost_pct: float = MAX_RT_COST_PCT
    reentry_cooldown_s: float = REENTRY_COOLDOWN_S
    max_bites_per_token: Optional[int] = None   # None = uncapped re-entries
    first_touch_only: bool = False              # never re-enter a token
    allowed_hours_utc: Optional[tuple] = None   # None = 24/7; else UTC hours
    max_concurrent: int = MAX_CONCURRENT
    # exit ladder (PerBotPositionManager via BotConfig)
    tp1_pct: float = 6.0
    tp1_sell_fraction: float = 0.75
    tp2_pct: float = 12.0
    tp2_sell_fraction: float = 0.25
    hard_stop_pct: float = -15.0
    time_stop_minutes: Optional[float] = None
    moonbag_fraction: float = 0.0
    moonbag_floor_pct: float = 0.0
    moonbag_trail_pp: Optional[float] = None
    trail_pp: Optional[float] = None            # None = BotConfig default
                                                # (3.0, the scalp trail)
    # STRENGTH-TRAIL exit (2026-07-12 RH winner-behavior decode,
    # scratchpad/_rh_winner_behavior.md): replace the partial TP ladder with an
    # ALL-OUT peak trail armed from a LOW threshold — the shape the 93 audited
    # RH winners run (all-out single-leg sell into strength; 55% of their trips
    # never peak past +6, so the scalp's fixed +6 TP1 misses the median mover).
    # See BotConfig.strength_trail_exit. Default OFF = byte-identical.
    strength_trail_exit: bool = False
    strength_trail_arm_pct: float = 2.0
    strength_trail_gap_pp: float = 3.0
    # LOSS-SIDE LADDER "SL1" (2026-07-17 dollar-conversion mine). The fleet's
    # leak is STRUCTURAL, not regime: median trade beats the tape every day yet
    # net $ is red every day (07-13/-15/-16: -$195/-$187/-$404), because wins
    # bank in partials (+$1,739) while losses close FULL-SIZE (-$2,525;
    # HARD_STOP alone -$1,641 at -$5.18/leg = 4.5x the avg win leg). SL1
    # mirrors TP1 on the way DOWN: pre-TP1 (nothing sold yet), first touch of
    # sl1_pct sells sl1_sell_fraction; only the tail rides to the hard stop.
    # Napkin on the 317 hard-stops: saves ~$1.13/trade; the cost — sl1-touchers
    # that would have recovered to TP — is unmeasurable from the ledger (no
    # mae), so it is priced by A/B RACE vs the parent, never assumed.
    # None = OFF (byte-identical for every other bot).
    sl1_pct: Optional[float] = None
    sl1_sell_fraction: float = 0.75
    # PHOENIX ENTRY (2026-07-18 post-exit bounce backtest): RH losers' tokens
    # bounce a MEDIAN +16.2% above our exit within 6h (64% >=+5%, 59% recover
    # more than the whole loss, only 28% true deaths) — and the fleet's
    # sibling_stop exclusion actively BLOCKS re-entry into exactly that class
    # (3 re-entry cycles in 5 days). A phoenix bot INVERTS the rule: it may
    # ONLY enter pools where a fleet bot loss-stopped within phoenix_window_s
    # — the stop-out is the bottom-marker; the normal dip/demand gates then
    # confirm the turn (the lane's base-confirmation equivalent).
    phoenix_entry: bool = False
    phoenix_window_s: float = 3600.0
    # phoenix2: required reclaim ABOVE the stop print (%) before entry.
    # None = v1 behavior (further-dip entry — the refuted shape).
    phoenix_reclaim_pct: Optional[float] = None
    # ── AGED-POOL racer machinery (2026-07-11; all default OFF so every
    # pre-existing racer is byte-identical — their A/B is mid-flight) ────────
    # cross-sibling token exclusion (Solana young_pond mirror): racers sharing
    # an exclusion_group take DISTINCT tokens — a token held, or LOSS-stopped
    # within sibling_stop_window_s, by any sibling is off-limits.
    exclusion_group: Optional[str] = None
    sibling_stop_window_s: float = SIBLING_STOP_WINDOW_S
    # depth+volume re-entry gate: after a LOSING exit on a pool, re-entry is
    # allowed ONLY on a dip at/below reentry_min_dip_pct with vol_m5 alive.
    # None = gate off. Racers using it should set reentry_cooldown_s=0 (the
    # flat cooldown would block the deep winners the gate exists to catch).
    reentry_min_dip_pct: Optional[float] = None
    reentry_min_vol_m5_usd: float = REENTRY_MIN_VOL_M5
    # KNIFE SKIP (2026-07-19 entry-source memo, verified finding: 19.1% of
    # replay entries never bounce >=3% before dropping >=10% more and carry
    # 177% of ALL drag; their t0 signature = still-sell-dominated 30s tape
    # OR shallow dip inside a deep unrecovered drawdown — the seller is not
    # finished). Gate: skip when s30>b30, or when dip>-8 while arc<-15
    # (arc = pct vs session-first px, the lane's athdd proxy). NO
    # wait-for-lower-price rule — proximity-to-low gating tested
    # ANTI-predictive in the same verification.
    knife_skip: bool = False
    # MANUFACTURER VETO (2026-07-21 flat-camouflage mine): skip entries while
    # the window reads manufacturer-active (recent entries committing to
    # corpses > 8%). Opt-in A/B arm; every entry is shadow-stamped mfr_active
    # regardless, so the counterfactual is measurable fleet-wide before this
    # gates anything. None/False = no behavior change.
    mfr_veto: bool = False
    # POOL LOSS LOCKOUT (2026-07-20, the phoenix postmortem — FLEET-WIDE
    # default ON). Phoenix died of exposure inversion: 148 buys on 28 pools
    # (5.3/pool) because bleeding pools keep re-generating entry signals
    # while clean bouncers leave the population — event-weighted 59% bounce
    # collapsed to 21% exposure-weighted. The structural fix for EVERY bot:
    # after N losing FULL closes on the same pool inside a rolling 6h
    # window, that pool is locked out. Caps every bot's exposure to a
    # bleeder at N stakes instead of "as long as it keeps bleeding".
    # None = off (measured mechanical cooldown — the allowed exception).
    pool_loss_lockout_n: Optional[int] = 2
    # CONVICTION BAND (2026-07-19 professional-shape panel): 1.5x entry size
    # when the entry dip falls inside (lo, hi) — the replay's monotone
    # payoff band only (-40..-30: +$0.55-1.18/e); outside it the gradient
    # breaks, so tiers never apply below -40. None = flat sizing.
    conviction_band: Optional[tuple] = None
    conviction_mult: float = 1.5
    # rug-tail defense: derisk_after_s into a hold, force remaining exposure
    # down to derisk_max_frac (per-position catastrophe cap). None = off.
    derisk_after_s: Optional[float] = None
    derisk_max_frac: float = DERISK_MAX_FRAC
    # regime hour gate v1 (2026-07-11 mine; replaces the REFUTED v0
    # human-era-14-23 rule): aged-band (>24h) pools blocked 19-21 UTC — the
    # one hour rule that passed the two-window bar. Opt-in per racer: aged
    # racers default ON, scalp racers stay OFF (their A/B is mid-flight).
    regime_hours: bool = False
    # ── CANDIDATE-FACTORY gates (2026-07-12 full-history mine; all default
    # OFF so every pre-existing racer is byte-identical). Provenance:
    # scratchpad/_rh_candidate_factory.md + rh_factory/{winner_delta.json,
    # sweep_results.json} ─────────────────────────────────────────────────
    # dip DEPTH CAP (winner-delta: <1h winners buy MODERATE pullbacks, p50
    # -8.6; losers buy deep flushes, p50 -15.6 — a dip deeper than the cap
    # is the LOSER profile, not a better entry): dip must be AT OR ABOVE
    # this (e.g. -25.0 blocks dip=-30). None = no cap.
    dip_max_depth_pct: Optional[float] = None
    # demand BREADTH floor: >= this many buy prints in the 30s demand window
    # (sweep nb30 axis; tape-count proxy for distinct buyers). None = off.
    min_buys_30s: Optional[int] = None
    # launch-ARC cap (winner-delta: losers buy LATE in the arc — px already
    # 12x+ off first print): px vs the lane's FIRST-SEEN quote px for the
    # pool must be <= this pct. Fail-OPEN when no first px (pool discovered
    # mid-life — the signal doesn't exist). None = off.
    max_arc_pct: Optional[float] = None
    # POP-RETRACE family: entry allowed ONLY within this many seconds after
    # a detected pop (latest px >= 1.35x the 10-min window min, 600s pop
    # cooldown — the mine's detector verbatim). None = off.
    require_pop_within_s: Optional[float] = None
    # PROVEN-VOLUME floor (winner-delta: winners buy pools with ~$16k median
    # volume BEFORE entry; losers ~$6.6k): lifetime observed USD volume for
    # the pool must be >= this. None = off.
    min_session_vol_usd: Optional[float] = None
    # ── FACTORY NO-FIRE FIX (2026-07-12, scratchpad/_rh_factory_nofire.md) ──
    # demand-turn NET requirement: the shared dip-mode demand gate demands
    # buys >= floor AND (buys - sells) > 0. The net>0 leg is NOT part of the
    # mined d25 cells (factory_sweep DEMANDS: d25 = b30 only; ~26-30% of the
    # mined triggers had net <= 0) — cell-verbatim racers switch it off.
    # Default True = every pre-existing racer byte-identical.
    demand_net_required: bool = True
    # session-anchor requirement: the u10m factory cells' session facts
    # (cum volume from launch, arc vs first print) only EXIST for pools whose
    # tape is creation-anchored (session_seed backfill, or first tape row at
    # age <= SESSION_ANCHOR_MAX_AGE_H). A pool promoted mid-arc reads
    # cum_vol~0 (structurally wrong thin_session_vol) and arc~0 (silently
    # ADMITS the late-arc loser profile the cell excluded — measured 07-10/11:
    # 22 of 69 promotion-onward "passes" were true-arc>300 false admits).
    # True -> unanchored pools block with the EXPLICIT `untracked_session`
    # reason and their arc/vol gates are not consulted. Default False.
    require_session_anchor: bool = False
    # ── LIVE FILL PROBE plumbing (2026-07-12; both default None = every
    # pre-existing racer is byte-identical) ──────────────────────────────────
    entry_usd: Optional[float] = None           # None = lane ENTRY_USD ($25)
    # exit memo #2 (2026-07-20): PRE_STOP_BAIL fraction (1.0 = full close,
    # the fleet default; the A/B arm banks 0.75 and lets the tail ride).
    pre_stop_bail_sell_fraction: float = 1.0
    max_buys_per_day: Optional[int] = None      # UTC-day entry cap; None=off

    def bot_config(self) -> BotConfig:
        kw = {}
        if self.trail_pp is not None:
            kw["trail_pp"] = self.trail_pp
        if self.pre_stop_bail_sell_fraction != 1.0:
            kw["pre_stop_bail_sell_fraction"] = self.pre_stop_bail_sell_fraction
        return BotConfig(
            bot_id=self.bot_id, display_name=self.bot_id,
            tp1_pct=self.tp1_pct, tp1_sell_fraction=self.tp1_sell_fraction,
            tp2_pct=self.tp2_pct, tp2_sell_fraction=self.tp2_sell_fraction,
            hard_stop_pct=self.hard_stop_pct,
            time_stop_minutes=self.time_stop_minutes,
            moonbag_fraction=self.moonbag_fraction,
            moonbag_floor_pct=self.moonbag_floor_pct,
            moonbag_trail_pp=self.moonbag_trail_pp,
            strength_trail_exit=self.strength_trail_exit,
            strength_trail_arm_pct=self.strength_trail_arm_pct,
            strength_trail_gap_pp=self.strength_trail_gap_pp,
            max_concurrent_positions=self.max_concurrent,
            **kw,
        )


LEGACY_BOT_ID = "rh_young_v1"   # pre-fleet single-config state migrates here

# SCALP-FLEET UNIVERSE PIN (phase 2, 2026-07-11): the 10 pre-aged racers were
# tuned on a feed that structurally capped pool age at 24h (rh_chain_feed
# MAX_AGE_H default ages pools out of the watch set). When the feed widens
# for the aged cohort (RH_FEED_MAX_AGE_H > 24, aged-mode liq ranking) their
# candidate universe must NOT silently widen mid-A/B — the previously
# implicit ceiling is now EXPLICIT on each racer. Zero behavior change while
# the feed default (24h) is in force.
SCALP_MAX_POOL_AGE_H = 24.0

# The 8 racers (2026-07-11): control + the hypotheses the 07-10 ledger raised.
ROSTER = (
    # 1. control — the shipped config verbatim; inherits the legacy state.
    LaneBot(bot_id="rh_young_v1", max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 2. deep flushes only — today's deep entries outperformed the shallow.
    LaneBot(bot_id="rh_deep_only", dip_trigger_pct=-25.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 3. one bite per token ever — the repeat-bite decay hypothesis.
    LaneBot(bot_id="rh_first_touch", first_touch_only=True,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 4. bite-curve cap: at most 2 entries per token.
    LaneBot(bot_id="rh_bites2", max_bites_per_token=2,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 5. friction-adjusted ladder — RH round trip costs 2-3x Solana, so the
    #    exits need more room than the Solana-parity +6/+12.
    LaneBot(bot_id="rh_wide_ladder", tp1_pct=10.0, tp1_sell_fraction=0.75,
            tp2_pct=20.0, max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 6. house-money moonbag shape on RH (young_v1 + 10% moonbag, breakeven
    #    floor, 20pp trail).
    LaneBot(bot_id="rh_moonbag", moonbag_fraction=0.10, moonbag_floor_pct=0.0,
            moonbag_trail_pp=20.0, max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 7. stronger demand confirmation — tonight's fades were weak-demand entries.
    LaneBot(bot_id="rh_demand_heavy", demand_min_buy_usd=150.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 8. does depth pay for itself via cheaper exits?
    LaneBot(bot_id="rh_liq40", min_liq_usd=40_000.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 9. prime hours only (2026-07-11 hour rulebook: 17-21 UTC green; the
    #    22:00-01:00 stretch flipped the SAME tokens +0.60 -> -0.70/trip).
    LaneBot(bot_id="rh_prime_hours", allowed_hours_utc=(17, 18, 19, 20, 21),
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 10. launch-strength scalp (2026-07-11 wallet decode: the AUDITED
    #     repeat-winner profile — fresh-pool strength buyers, 4.4 min median
    #     holds). Different ENTRY MODE, same guard stack: age 0.5-20 min,
    #     120s net inflow >= $150, price above its 10-min open. Tight exits:
    #     tp1 +5/0.90, hard stop -8, 10-min time box.
    LaneBot(bot_id="rh_launch_scalp", entry_mode="launch_strength",
            min_pool_age_h=0.5 / 60.0, max_pool_age_h=20.0 / 60.0,
            launch_min_inflow_usd=150.0,
            tp1_pct=5.0, tp1_sell_fraction=0.90,
            tp2_pct=10.0, tp2_sell_fraction=0.10,
            hard_stop_pct=-8.0, time_stop_minutes=10.0),
    # ── AGED-POOL RACERS (2026-07-11) — the full-history-decode thesis:
    # launch-scalp RETRACTED (66 vs 65%); the day-robust edge = AGED/
    # established pools + LONGER holds (winners med hold 19.2m vs losers
    # 2.6m; trip-level: >24h pools n=335 trips, 73% win, +$12,950).
    # PRE-REGISTRATION: grade each racer at n>=30 CLOSES vs the 10-racer
    # scalp fleet above as CONTROL; DISTINCT-TOKEN count is the throughput
    # metric; judge per-token medians (tokmed), not sums. Axes under test:
    #   rh_aged_hold   = the pure thesis (aged admission + long-hold ladder);
    #   rh_aged_derisk = + principal-banking TP1 slice (0.75 @ +6 banks 79.5%
    #                    of principal early) + 20-min exposure cap (rug-tail
    #                    defense — does giving up tail size pay for itself?);
    #   rh_aged_deep   = + depth-gated loss re-entry, NO flat cooldown (deep
    #                    re-buys paid, shallow slaughtered — session-7 live).
    # All three: cross-sibling token exclusion (exclusion_group="aged",
    # MONSIEUR defect #1 — one racer per token, never the whole cohort) and
    # the v1 regime hour gate (aged-band 19-21 UTC block). Thresholds:
    # see the AGED_*/DERISK_*/REENTRY_*/REGIME_* constants — each cites its
    # data source. trail_pp=10.0 is the one partly-judgment number (flagged
    # above); NO time box on any of the three — winning-trip holds are
    # fat-tailed (p50 18.9m, p75 924m) and a box would amputate the tail
    # that carries the p75 (+46.5) return; tail RISK is handled by the
    # derisk cap + LP-drain guard + hard stop instead.
    LaneBot(bot_id="rh_aged_hold",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            exclusion_group="aged", regime_hours=True),
    # ── EXIT-MEMO A/B PAIR (2026-07-20, EXIT_SOURCE_MEMO ship list #1+#2;
    # both verified survivors). PRE-REGISTERED bars in the memo.
    #   rh_bailfrac_ab = aged_hold clone + PRE_STOP_BAIL banks 0.75 (the
    #     bail = 10x SL1's event count, full-size, median -4.76%/61s —
    #     inside the +16.2% bounce zone). OWN group -> paired same-token
    #     A/B vs parent. Grade: bail-fractionalized positions net-$ vs the
    #     parent's full-close cohort; kill if tail-cohort net < full-close.
    #   rh_young_agedladder_ab = young_v1 clone + the aged TP side (tp2 16,
    #     trail 10 — memo #1: short-ladder bots close winners at 1.4-2.3min
    #     capturing 7-21% of MFE; +0.45-0.84pp/trade, 11/11 days). Loss
    #     side untouched. Kill: ex-top-2 negative or delta<0 at n>=30.
    LaneBot(bot_id="rh_bailfrac_ab",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            pre_stop_bail_sell_fraction=0.75,
            regime_hours=True),
    LaneBot(bot_id="rh_young_agedladder_ab",
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30,
            trail_pp=10.0),
    LaneBot(bot_id="rh_aged_derisk",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.75,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.15,
            trail_pp=AGED_TRAIL_PP,
            derisk_after_s=DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            exclusion_group="aged", regime_hours=True, max_concurrent=1),
    LaneBot(bot_id="rh_aged_deep",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            reentry_cooldown_s=0.0,
            reentry_min_dip_pct=REENTRY_MIN_DIP_PCT,
            exclusion_group="aged", regime_hours=True),
    # ── CANDIDATE-FACTORY RACERS (2026-07-12) — mined from the FULL
    # replayable history (10.36M swaps, 07-01..11) with REALISTIC exits
    # (ladder sims mirroring the PM, entry +1%/exit -1% haircuts, dead pools
    # booked -90) and graded per half of the four-half discipline (chrono
    # W1/W2 x odd/even) against the Phase-1 bar (n>=20 distinct pools/half,
    # tokmed ex-top2 green, cat<=1/20). Each racer mirrors ONE 4/4-surviving
    # cell VERBATIM. min_liq_usd=5k = the FEED's watch floor (RH_FEED_MIN_LIQ)
    # — NOT a mined axis: the substrate (rh_history sweep) was the chain-wide
    # unfiltered swap log; the cell's cat rate priced its rug tail with NO liq
    # gate, and the lane can only trade watched pools anyway (honeypot/
    # rt-cost/LP-drain still guard). All five: neighborhood-GREEN in every
    # perturbation notch, >=5 distinct days, exclusion_group "factory" (one
    # racer per token), 600s cooldown = the mine's per-pool trigger cooldown.
    # Provenance + per-half tables: scratchpad/_rh_candidate_factory.md;
    # rh_factory/{sweep_results,adversarial,winner_delta}.json.
    # PRE-REGISTERED: backtest earned the RACE seat, never a live seat —
    # each racer must CONFIRM at n>=30 closes (tokmed ex-top2 green,
    # cat<=1/20, direction = its cell) or it retires to the kills list.
    # NO-FIRE DIAGNOSIS (2026-07-12, scratchpad/_rh_factory_nofire.md): the
    # sweep's 65-100 qualifying pools/day are measured on the chain-wide tape
    # from pool CREATION; the lane sees a pool only from watch PROMOTION
    # (median ~4-7 min age vs the cells' median trigger at 2.4 min) and its
    # feed ever watches ~53-83% of qualifying pools. Measured reachable
    # throughput for the u10m cells: ~0.8 fires/lane-hour (~18 pools per
    # 22 lane-hours, 07-10/11) — NOT the sweep's ~3-4/hour. The n>=30 confirm
    # bar stands; only the time-to-n expectation changes.
    # 1. THE winner-delta cell: <10min pools, SHALLOW pullback (-6..-12,
    #    deeper = the loser profile), proven volume >=$4.8k, EARLY arc
    #    (<=+300% of first-seen px), wide aged ladder. Backtest: 658 pools,
    #    tokmed_ex2 +$2.46 (min-half +$1.97), cat 0.6%, net +$738.
    #    demand_net_required=False: cell-verbatim d25 (b30>=$25, NO net leg).
    #    require_session_anchor: session facts must be creation-anchored
    #    (seed backfill / early first tape row) or the pool blocks with the
    #    explicit untracked_session reason.
    LaneBot(bot_id="rh_f_pullback",
            dip_trigger_pct=-6.0, dip_max_depth_pct=-12.0,
            min_pool_age_h=0.0, max_pool_age_h=10.0 / 60.0,
            min_liq_usd=5_000.0, min_session_vol_usd=4_800.0,
            demand_min_buy_usd=25.0, demand_net_required=False,
            max_arc_pct=300.0, require_session_anchor=True,
            reentry_cooldown_s=600.0,
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30, trail_pp=10.0,
            exclusion_group="factory"),
    # 2. Same admission shape, MODERATE band (-6..-25) + scalp ladder:
    #    1,025 pools, tokmed_ex2 +$1.97 (min-half +$1.85), cat 0.4%, +$337.
    LaneBot(bot_id="rh_f_arc_scalp",
            dip_trigger_pct=-6.0, dip_max_depth_pct=-25.0,
            min_pool_age_h=0.0, max_pool_age_h=10.0 / 60.0,
            min_liq_usd=5_000.0, min_session_vol_usd=4_800.0,
            demand_min_buy_usd=25.0, demand_net_required=False,
            max_arc_pct=300.0, require_session_anchor=True,
            reentry_cooldown_s=600.0,
            exclusion_group="factory"),
    # 3. POP-RETRACE family (the 31,208-pop mine): deep dip within 30 min
    #    of a detected pop on a fresh pool, scalp ladder. 976 pools,
    #    tokmed_ex2 +$1.94 (min-half +$1.92), cat 0.0% — the cleanest
    #    catastrophe profile of the sweep; stale-stress green 4/4.
    LaneBot(bot_id="rh_f_popret",
            dip_trigger_pct=-12.0,
            min_pool_age_h=0.0, max_pool_age_h=10.0 / 60.0,
            min_liq_usd=5_000.0, min_session_vol_usd=480.0,
            demand_min_buy_usd=50.0, require_pop_within_s=1800.0,
            reentry_cooldown_s=600.0,
            exclusion_group="factory"),
    # 4. AGED RELOAD (>24h band — the decode thesis under realistic exits):
    #    deep flush (<=-25) on PROVEN aged pools (>=$16k lifetime volume =
    #    the winners' vol_pre median), aged ladder. 605 pools, tokmed_ex2
    #    +$1.78 (min-half +$1.08), cat 0.0%, net +$1,285, dead 0.2%.
    #    DORMANT until RH_FEED_MAX_AGE_H widens past 24h (feed prunes the
    #    band); arms automatically when it does. regime_hours deliberately
    #    OFF: the cell passed 4/4 WITH 19-21 UTC included (cell-verbatim;
    #    the hour gate is the existing aged racers' A/B, not this one's).
    #    demand_net_required=False: cell-verbatim d25. NO session anchor:
    #    a >24h pool is never creation-anchored on practical uptimes — its
    #    vol floor reads OBSERVED lifetime volume, a conservative lower
    #    bound of the mined cum_eth (under-fires, never falsely admits).
    LaneBot(bot_id="rh_f_reload24",
            dip_trigger_pct=-25.0,
            min_pool_age_h=24.0,
            min_liq_usd=5_000.0, min_session_vol_usd=16_000.0,
            demand_min_buy_usd=25.0, demand_net_required=False,
            reentry_cooldown_s=600.0,
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30, trail_pp=10.0,
            exclusion_group="factory"),
    # 5. MID-BAND reload (6-24h — reachable on TODAY'S feed): same deep-
    #    flush trigger, $50-net demand. 362 pools, tokmed_ex2 +$0.93
    #    (min-half +$0.76), cat 0.5%, net +$214 — the weakest of the five
    #    but the only aged-thesis cell that fires before the feed widens.
    LaneBot(bot_id="rh_f_reload_mid",
            dip_trigger_pct=-25.0,
            min_pool_age_h=6.0, max_pool_age_h=24.0,
            min_liq_usd=5_000.0, min_session_vol_usd=480.0,
            demand_min_buy_usd=50.0,
            reentry_cooldown_s=600.0,
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30, trail_pp=10.0,
            exclusion_group="factory"),
    # ── DEEP-EXIT racer (2026-07-12; scratchpad/_deep_exit_optimization.md) —
    # the EXIT-SHAPE deliverable for the deep-capitulation cohort. Same full-
    # history replay harness (scratchpad/deep_exit/rh_deepexit_sweep.py: real
    # forward tape, PM-mirrored ladders, +1%/-1% haircuts, dead pools -90),
    # restricted to DEEP-flush entries (dip<=-20) and swept over 22 exit
    # ladders x 3 depth bands x 4 halves (33,557 candidate entries, 7,024
    # pools). The finding that shapes this racer:
    #   The deep-flush BOUNCE TAIL rises with depth (MFE>=50: 30.4% at -20..-30
    #   -> 38.9% at <=-45; p90 MFE +148 -> +260). So on real tape the
    #   EXPECTANCY-optimal exit gets MORE patient with depth (patient mean beats
    #   fast by +4.5pp in <=-45), REFUTING the prior "deeper -> faster harvest"
    #   intuition — giveback risk does rise with depth, but bounce magnitude
    #   rises FASTER. The ROBUST median still favors fast harvest at every depth
    #   (the median trade never reaches the tail: fast5_all tokmed_ex2 +5.0).
    #   The BARBELL resolves the tension: harvest the bulk fast (locks the
    #   green robust median) + keep a HOUSE-MONEY runner (moonbag, breakeven
    #   floor => ~zero giveback after TP2) for the fat tail. This EXACT shape
    #   was re-simulated with the runtime moonbag floor (rh_moonbag_sweep.py,
    #   26,881 dip<=-25 entries): tokmed_ex2 +2.51 (min-half +2.33, GREEN 4/4),
    #   mean -1.18, med +4.53, wr 62%, cat 2.2% — DOMINATES the scalp exit on
    #   BOTH axes (scalp tokmed +1.93 / mean -2.51) and recovers +1.9pp of the
    #   expectancy a pure fast harvest (fast5_all tokmed +4.90 / mean -3.09)
    #   discards by clipping the +150..+260 tail. The floor is STRICTLY better
    #   than a -15 runner stop (a first-pass proxy scored only +2.19/-1.74), so
    #   the live moonbag (runner rides live quotes past the tape sample end) is
    #   at least this good. Harvest 0.60 @ +5, 0.10 @ +12 (tp2 sells remainder-
    #   minus-moonbag), moonbag 0.30 rides breakeven-floored with a 12pp trail.
    # PRE-REGISTERED (same bar as the factory racers): earns a RACE seat, never
    # a live seat — CONFIRM at n>=30 closes (tokmed ex-top2 green, cat<=1/20,
    # direction = the barbell cell: median green + a fat-tail lift over a pure
    # scalp control) or it retires to the kills list. exclusion_group="deepexit"
    # (one racer per token; distinct from "factory"). CAVEAT: replay cannot
    # model continuation perfectly (the runtime runner rides live quotes past
    # where the tape sample ended); the moonbag's breakeven floor bounds the
    # downside of that uncertainty.
    # rh_deep_barbell RETIRED 2026-07-17 (AxiS: "retire"). The cautionary
    # control served its purpose: fidelity-honest −$1,031 lifetime (+$16 paper
    # = the fleet's biggest illusion, $1,047), −$65 on its final day — it
    # single-handedly flipped the fleet's daily sign (fleet −$22, ex-barbell
    # +$43). Root cause was always ADMISSION: deep flushes on THIN pools
    # ($5k floor) = the deep+thin WORST cell; it live-bled (NOXA gap-through)
    # and drew the GOATAI rug. Its thesis survives in rh_deep_barbell_capped
    # (deep+LIQUID+catastrophe-cap), which stays racing as the fixed version.
    # rh_deep_barbell_capped (2026-07-12) — the FULL synthesis: the barbell
    # bled live (NOXA -20.1% gap-through-stop, n=9) because it took deep flushes
    # on THIN pools ($5k) with only a price stop. This merges all three fleet
    # findings the split racers each had only ONE of: (1) DEEP entry (-25),
    # (2) LIQUID pool min_liq 30k — the SOL combo mine proved deep+liq=GREEN /
    # deep+thin=WORST cell, (3) BARBELL exit (floored runner captures the depth-
    # growing bounce tail), (4) CATASTROPHE CAP (early de-risk to 25% so a
    # gap-through stop can't hit full size — the variance mine's #1 lever).
    # Pre-registered n>=30 vs rh_deep_barbell (the un-capped/thin control).
    LaneBot(bot_id="rh_deep_barbell_capped",
            dip_trigger_pct=-25.0,
            min_pool_age_h=0.0, max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            min_liq_usd=30_000.0,
            demand_min_buy_usd=25.0,
            reentry_cooldown_s=600.0,
            tp1_pct=5.0, tp1_sell_fraction=0.60,
            tp2_pct=12.0, tp2_sell_fraction=0.10,
            moonbag_fraction=0.30, moonbag_floor_pct=0.0, moonbag_trail_pp=12.0,
            hard_stop_pct=-15.0,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            exclusion_group="deepexit"),
    # ── LIVE FILL PROBE (2026-07-12) — measures EXECUTION, not edge: the
    # standard young dip trigger with PERMISSIVE gates (min_liq 30k and the
    # always-on guard stack only — no vol/arc/pop/breadth extras), $7.50
    # entries (RH_PROBE_SIZE_USD), <=4 buys/day (RH_PROBE_MAX_BUYS_DAY), one
    # position at a time, the full normal exit ladder (TP1/TP2/trail/stop)
    # so BOTH legs produce live fills. Routes through RhLiveExecutor only
    # when the triple gate is open AND RH_LIVE_PROBE_BOTS lists this bot_id
    # (see the LIVE FILL PROBE constants block); otherwise pure paper.
    LaneBot(bot_id="rh_fill_probe",
            min_liq_usd=30_000.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_concurrent=1,
            entry_usd=PROBE_SIZE_USD,
            max_buys_per_day=PROBE_MAX_BUYS_DAY,
            exclusion_group="fill_probe"),
    # ── LOW-VARIANCE RACERS (2026-07-12 variance-reduction mine) — control
    # (young_v1) admission, entry size UNTOUCHED ($25 default), every signal
    # still taken (volume kept). exclusion_group="lowvar" adds cross-sibling
    # de-clustering (Lever 4 lite: the two never pile the SAME token, so a
    # single-token rug can't hit both at once) on top of each racer's own
    # variance lever. PRE-REGISTERED: grade at n>=30 closes vs rh_young_v1 as
    # CONTROL — WIN = lower per-trip stdev AND lower worst-trip with mean not
    # worse; judge stdev/downside-stdev, not sums.
    # A. catastrophe cap (Lever 2): 5-min derisk to 25% + tighter -12 hard stop.
    LaneBot(bot_id="rh_lowvar_catstop",
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            hard_stop_pct=-12.0,
            exclusion_group="lowvar"),
    # B. hold-time box (Lever 3): full close at 10 min regardless of pnl.
    LaneBot(bot_id="rh_lowvar_box",
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            time_stop_minutes=LOWVAR_BOX_MINUTES,
            exclusion_group="lowvar"),
    # ── DEEP-SYNTH CONSOLIDATED (2026-07-12; scratchpad/_rh_deep_decode.md) —
    # the decode of today's GREEN racers (rh_deep_only +4.65, rh_bites2 +4.56,
    # rh_f_arc_scalp +3.08) vs the RED (rh_demand_heavy -14.61 WORST,
    # rh_wide_ladder -4.38, rh_moonbag -2.49). CAUSAL LEVER is NOT entry depth
    # (green bites2's -15 median dip is SHALLOWER than red demand_heavy's -18):
    # it is (1) the tight SCALP EXIT all three greens share verbatim (bank
    # +6/0.75, +12/0.25, -15 stop, 3pp trail, NO moonbag, NO time box) and
    # (2) selecting entries by PRICE STRUCTURE, never chasing demand strength.
    # The three RED racers each BREAK exactly one of those: wide_ladder loosens
    # the exit to +10/+20 (RH fades revert before +10 -> winners become
    # trail/stop; observed TP2-reach 13% vs greens' ~21%); moonbag holds a 10%
    # residual on a 0%-floor 20pp trail (the tail bleeds toward the rug, giving
    # back the banked TP1); demand_heavy raises demand_min_buy 50->150, filtering
    # DEMAND-AT-THE-MOMENT — which the RH winner-delta says does NOT separate
    # winners/losers and the SOL selection mine says INVERTS (bigger buyers =
    # MORE red = chasing strength). This racer fuses the three GREEN edges and
    # keeps the anti-chase discipline:
    #   entry = deep_only's capitulation dip (-25, the cross-chain "deep beats
    #           chasing strength" thesis) + f_arc_scalp's PROVEN-VOLUME floor
    #           ($4.8k; require_session_anchor OFF so it reads OBSERVED lifetime
    #           volume, a conservative lower bound like rh_f_reload24 — defends
    #           the thin-flush LOSER profile) + bites2's 2-bite cap. demand_min
    #           stays at the DEFAULT $50, never raised (the anti-chase lesson).
    #   exit  = THE shared scalp ladder (LaneBot defaults verbatim).
    # No new gate logic — every gate here is an already-tested existing knob.
    # PRE-REGISTERED (backtest earns a RACE seat, never a live seat): grade at
    # n>=30 CLOSED positions vs the scalp fleet as control, per-token medians
    # (ex-top-2), never sums. CONFIRM = tokmed ex-top2 green AND cat<=1/20 AND
    # direction = deep-capitulation; FAIL = retire to the kills list, no re-tune
    # on the same tape. CUT-CANDIDATE flagged by this decode: rh_demand_heavy.
    LaneBot(bot_id="rh_deep_consolidated",
            dip_trigger_pct=-25.0,
            min_session_vol_usd=4_800.0,
            max_bites_per_token=2,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            exclusion_group="deepsynth"),
    # ── STRENGTH-TRAIL EXIT (2026-07-12; scratchpad/_rh_winner_behavior.md) —
    # the EXIT-shape deliverable of the winner-BEHAVIOR decode. Reconstructing
    # 846 closed trips across the 93 audited day-robust winners found the #1
    # thing our racers lack is the exit SHAPE, not entry/breadth/re-entry:
    #   - 55.4% of winner trips NEVER peak past +6% (max-favorable-excursion
    #     p50 = +3.6%) — so the scalp's FIXED +6 TP1 sits ABOVE the median RH
    #     mover and misses it entirely (then rides the fade to trail/stop).
    #   - winners bank those movers by selling ALL-OUT in a SINGLE leg (n_sells
    #     p50=1; first sell banks 100%) into RISING price (74.2% of sells) near
    #     the local top (median sell = 97.4% of the trip peak; ~2.6% give-back).
    #   - realized per-trip p50 +3.7% / p75 +19.8% / p90 +57% — small fast median
    #     win with an INTACT fat tail because the whole position rides one trail.
    # This racer ISOLATES that one lever: entry/universe = a VERBATIM rh_deep_only
    # clone (deep -25 capitulation, SCALP_MAX_POOL_AGE_H, default $50 demand, all
    # shared guards), differing ONLY in the exit engine — an ALL-OUT single-leg
    # peak trail armed from +2% (~breakeven+fees, NOT +6) with a 3pp give-back
    # (matches the winners' 2.6% median), catastrophic hard stop -15 kept, bite
    # cap 2 (re-entry is a modest fat-tail add — median re-entry trip ≈ breakeven,
    # 24% of winner profit — so cap, don't build a re-entry strategy). No new gate
    # logic; strength_trail_exit owns the ladder. Own exclusion_group so it takes
    # DISTINCT tokens from the scalp control it is measured against.
    # PRE-REGISTERED (backtest/decode earns a RACE seat, never a live seat): grade
    # at n>=30 CLOSED positions vs the scalp fleet (rh_deep_only) as CONTROL,
    # per-token medians (ex-top-2), NEVER sums. CONFIRM = tokmed ex-top2 GREEN AND
    # beats the scalp control's tokmed AND cat<=1/20 AND direction = the sub-+6
    # movers the scalp misses get banked; FAIL = retire to the documented-kills
    # list, no re-tune on the same tape.
    LaneBot(bot_id="rh_strength_trail",
            dip_trigger_pct=-25.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_bites_per_token=2,
            strength_trail_exit=True,
            strength_trail_arm_pct=2.0,
            strength_trail_gap_pp=3.0,
            hard_stop_pct=-15.0,
            exclusion_group="strengthexit"),
    # ── DEEP+DEMAND STACK (2026-07-13; scratchpad/_rh_winner_decode2_0713.md) —
    # the decode of the ACCUMULATED (append-mode-fixed) ledger. Reverses the
    # 07-12 one-day snapshot call: with n grown, rh_demand_heavy is now the
    # BEST ex-top-2 racer (+$8.54 ex2, 70% green, 12 tokens) and rh_deep_only is
    # green-ish (62% green, retMed +6.0, 10 tokens). The two GREEN racers isolate
    # TWO INDEPENDENT, STACKING entry levers vs the red control (rh_young_v1,
    # -$13.60 ex2, 52% green) — the shared SCALP EXIT is NOT the lever (the
    # control runs it verbatim and is red):
    #   (1) DEMAND confirmation: demand_heavy = young_v1 at the SAME median entry
    #       depth (-18.2 vs -18.8) and the SAME exit, differing ONLY in the
    #       demand floor ($150 vs $50) -> green. Mechanism: the $150 buy-side
    #       floor selects dips with real follow-through (demand_heavy TP2-reach
    #       38%, the highest in the fleet) instead of dead-cat knives. This
    #       DIRECTLY CONTRADICTS the 07-12 decode ("demand-at-the-moment is
    #       non-separating/inverting"); the larger accumulated sample refutes it.
    #   (2) DEPTH: pooled across all six scalp-exit racers, deeper entries are
    #       monotonically greener (dip<=-25: retMed +6.0 / 63% green; -12..-18:
    #       -1.3 / 48%). deep_only's -25 trigger IS this lever.
    #   The levers STACK: within demand_heavy, the deep subset (dip<=-18) is
    #   +$8.42 ex2 / 76% green vs the shallow subset -$2.04 / 64%.
    # HONEST LOW-N / DIRECTIONAL: 10-12 distinct tokens each; ex-top-2 is FRAGILE
    # (odd/even OOS flips one half negative for BOTH greens). The signals that
    # SURVIVE the OOS split are green-RATE (64-76%) and retMed (~+6), not ex2 —
    # so these push the DIRECTION, graded at n>=30 on green-rate + tokmed.
    # All three: SCALP exit verbatim (the proven exit — LaneBot defaults, NO
    # moonbag / NO time box), default liq 30k, max_pool_age 24h, no new gate
    # logic (every knob already tested/wired), all facts inside the <=2s
    # detect->fill budget (dip off 10-min high + 30s buy sums/prints). Own
    # exclusion_group=None like their scalp PARENTS (demand_heavy/deep_only) so
    # each accrues INDEPENDENT n toward the confirm bar fastest.
    # PRE-REGISTERED (paper race seat, never a live seat): grade at n>=30 CLOSED
    # positions vs the scalp fleet as control, per-token medians (ex-top-2) AND
    # green-rate, NEVER sums. CONFIRM = tokmed ex-top2 green (or clearly beats
    # rh_young_v1) AND green-rate >= the parent's AND cat<=1/20 AND direction =
    # deep/demand; FAIL = retire to the documented-kills list, no re-tune on the
    # same tape. Throughput caveat: the deep+demand cell is ~1/5 of demand_heavy's
    # rate (10 of 50 demand_heavy trips were dip<=-25) — time-to-n is longer.
    # 1. THE combined stack: deep_only's -25 capitulation + demand_heavy's $150
    #    demand floor. The direct "push both proven levers together."
    LaneBot(bot_id="rh_deepdemand",
            dip_trigger_pct=-25.0,
            demand_min_buy_usd=150.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 2. DEMAND QUALITY: demand_heavy's $150 floor + a BREADTH floor (>=3 buy
    #    prints in the 30s window) so the $150 is real demand from multiple
    #    buyers, not a single whale print (the "one big buy = late-arc top" trap
    #    the 07-12 decode warned of). Keeps the shallow -12 trigger for
    #    throughput (breadth, not depth, is the lever under test here).
    LaneBot(bot_id="rh_demand_broad",
            demand_min_buy_usd=150.0,
            min_buys_30s=3,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # 3. TAIL-DEFENDED STACK: the #1 racer + the variance mine's #1 lever
    #    (early catastrophe cap: 5-min derisk to 25%) + a 2-bite cap. Deep
    #    flushes carry the gap-through-stop / rug left tail (demand_heavy booked
    #    6 HARD_STOP + 7 PRE_STOP_BAIL of 50; the deep bands still show a red ex2
    #    tail) — this tests whether flooring that tail lifts the FRAGILE ex-top-2
    #    the OOS split exposed, without touching the green median.
    LaneBot(bot_id="rh_deepdemand_capped",
            dip_trigger_pct=-25.0,
            demand_min_buy_usd=150.0,
            max_bites_per_token=2,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H),
    # ── HARVEST-FASTER exit (2026-07-13; scratchpad/_rh_exit_rug_0713.md) — the
    # EXIT-side deliverable of the capture-efficiency + abandoned-tail analysis.
    # Findings that shape it:
    #   (1) Capture efficiency (realized / peak-proxy) is ~0.73 median across the
    #       WHOLE fleet (greens 0.76-0.80, control 0.73) — the exit engine is NOT
    #       the greens' edge (they win on ENTRY: more dips reach TP1/TP2). The
    #       ~27% leak is fleet-wide and shared.
    #   (2) The leak is NOT recoverable by riding MORE of the position: a FAITHFUL
    #       fraction reallocation (price paths fixed, only the held fraction
    #       varies) leaves ex-top-2 tokmed FLAT (-4.72) across TP1 fracs 0.0-0.90,
    #       and trip-median IMPROVES as you bank MORE (f=0.90 tripMed -0.96 vs
    #       f=0.50 -1.76). The value of riding more lives entirely in the top-2
    #       fat tail that ex-top-2 discards.
    #   (3) ABANDONED-TAIL (+6h post-exit price, n=270): the median token is DOWN
    #       -56% six hours after we exit (only 27% run up); POST_TP1_TRAIL exits
    #       are followed by a -59% median further fall. So fast harvest is CORRECT
    #       — the position is not left early; it dies. The only exit-side money
    #       left is the rare fat tail (mean +58%, MOONBAG_TRAIL cases +690%).
    # This racer isolates "harvest FASTER + keep a floored lotto for the tail":
    # a VERBATIM rh_deep_only entry clone (deep -25 capitulation, SCALP_MAX age,
    # default $50 demand, all shared guards — so it is measured against deep_only
    # as CONTROL), differing ONLY in the exit — bank 0.90 at TP1 +6 (the robust-
    # median-best bank fraction from the reallocation) and ride a small 0.10
    # BREAKEVEN-FLOORED moonbag on a 12pp trail (captures the +690% tail with ~0
    # giveback after TP1, since the floor bounds it at breakeven). Distinct from
    # rh_deep_barbell (0.30 runner) and rh_strength_trail (all-out) — it is the
    # BANK-HEAVY end of the harvest-aggressiveness axis, the end the abandoned-
    # tail finding favors. bite cap 2 (re-entry is a modest fat-tail add).
    # HONEST: the reallocation shows fraction does NOT lift ex-top-2 (flat) — so
    # this is a DIRECTIONAL fat-tail-vs-robust-median test, NOT a proven ex-top-2
    # lift. PRE-REGISTERED (paper race seat, never a live seat): grade at n>=30
    # CLOSED positions vs rh_deep_only, per-token medians (ex-top-2) AND
    # green-rate, NEVER sums. CONFIRM = tokmed ex-top2 >= deep_only's AND
    # green-rate >= deep_only's AND the fat tail (mean / p90) is recovered AND
    # cat<=1/20; FAIL = retire to the documented-kills list, no re-tune.
    LaneBot(bot_id="rh_bankfast",
            dip_trigger_pct=-25.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_bites_per_token=2,
            tp1_pct=6.0, tp1_sell_fraction=0.90,
            tp2_pct=12.0, tp2_sell_fraction=0.0,
            moonbag_fraction=0.10, moonbag_floor_pct=0.0, moonbag_trail_pp=12.0,
            hard_stop_pct=-15.0,
            exclusion_group="bankfast"),
    # ── STABLE-3 (2026-07-13; scratchpad/_rh_stable3_0713.md) — the STABILITY
    # deliverable. AxiS: "both sides show extreme volatility with profit AND loss
    # from each individual bot; we need stability." The diagnosis, confirmed on the
    # accumulated ledger: the top-WR racers ALREADY bank small wins consistently
    # (per-sell-leg WR demand_heavy 78% / deep_only 72% / aged_deep 71%; trip-WR
    # 70/63/55%). Their instability is the LEFT TAIL — a few big-loser/rug tokens
    # dominate the per-TOKEN return and blow up cumulative P&L (aged_deep = 71%
    # leg-WR but −4.7 token-median; deep_only booked a CASHCATWIF −100 that alone
    # drove its trip-return stdev to 23.0). So the stability lever is CAPPING THE
    # LEFT TAIL on the already-high-WR entries, NOT changing entries.
    #
    # THE QUANTIFIED CAP (ledger decomposition, _rh_stable3_0713.md): a hard
    # downside cap floored at −15 is a PURE stability win — it cut trip-return stdev
    # (deep_only 23.0→10.2, demand_heavy 11.7→10.1, control 14.0→10.3), zeroed the
    # catastrophic-token rate (all racers <-20% tok → 0), and left win-rate,
    # token-median AND throughput UNCHANGED (medians ignore tail magnitude; the cap
    # only compresses losers already past the floor). Dispersion DOWN + catastrophic
    # DOWN while WR/median HOLD = exactly the stability signature, and it is FREE.
    #
    # WHAT IS BAKED IN (all latency-free config knobs, no new gate logic):
    #   1. hard_stop_pct=−10 (SCALP racers; aged racer keeps −15) — the price stop.
    #      TAIL-CAP NET OPTIMIZATION (2026-07-13, scratchpad/_rh_tailcap_net_0713.md):
    #      swept the stop over −8/−10/−12/−15/−20 on the accumulated ledger and
    #      measured net-$/position PER DAY (07-10/11/12 = regime robustness). A
    #      TIGHTER stop monotonically lifts net with ~$0 measurable winner-kill —
    #      the partial-TP ladder banks 0.75 on any pop BEFORE the drawdown, so a
    #      tighter stop only clips the underwater remainder (the deepest observed
    #      NON-terminal exit is POST_TP1_TRAIL −7.0, so nothing green is stopped).
    #      Realistic (slip-aware, reproduces the actual ledger at −15) fleet net:
    #      −15 −$12 → −12 +$29 → −10 +$74 → −8 +$135, and the BAD day (07-11)
    #      −213 → −183 → −154 → −116; the GOOD day (07-12) is NEVER worse. The
    #      −10 win is DIVERSIFIED (222 saved trades, top token only 26% of the
    #      gain — NOT one-token overfit). Chose −10 (not the higher-net −8): it
    #      clears the bar on ALL 3 days in both the idealized and slip bounds, and
    #      keeps a 2pp buffer vs the UNMEASURABLE pre-TP1 knife-through (the ledger
    #      has no intra-trip price path; deep −25 entries wiggle inside an −8 band).
    #      Pre-registered: watch forward winner-kill at n≥30 — if it stays ~0,
    #      tighten toward −8; −12 (= lowvar_catstop) is the conservative fallback.
    #   2. derisk_after_s + derisk_max_frac=0.25 — the catastrophe cap (variance
    #      mine's #1 lever): force exposure to 25% EARLY so a LATER rug/LP-drain gap
    #      hits a quarter position. This is the ONLY realizable defense against
    #      gap-through (a −90 rug at t>window → ~−22.5% on position, ≈ floor@−20).
    #      SCALP racers use the 5-min window (LOWVAR_DERISK_AFTER_S); the AGED racer
    #      uses 20 min (DERISK_AFTER_S) so it does NOT amputate the fat-tailed aged
    #      holds (p75 924m) the aged thesis rides.
    #   3. max_bites_per_token=2 — bounds single-token concentration. demand_heavy
    #      put 17 of 50 trips (34%) into ONE concentrated token (CASHCATWIF, rug-gate
    #      flagged top1 10.6/top10 50.6); a bot that can dump a third of its activity
    #      into one token is structurally capable of the extreme swing AxiS named.
    #      HONEST in-sample cost (flagged for the confirm): on the token-poor ledger
    #      the cap cuts n hard and nudges deep_only's token-median negative — a
    #      low-n artifact (forward, more distinct tokens = diversification, not
    #      starvation); demand_heavy stays green (+5.37 tokmed).
    #   4. exclusion_group="stable" — cross-sibling de-cluster: the three never pile
    #      the SAME token, so one rug can't hit all three at once (fleet stability;
    #      ~zero n-cost given distinct triggers).
    # RUG-GATE (ENFORCED 2026-07-13 per AxiS — RH_RUG_GATE=enforce default): the
    # pre-buy defense for the concentrated-DUMP class (CASHCATWIF/CASHCATGAME) is
    # core.rh_rug_signals.rug_gate_verdict (top1>=9 OR top10>=30; catches the two
    # catastrophic dumps at 0/22 winner-kill / 0/4 loss-hit). It BLOCKS the entry
    # via the arm-time Blockscout PREWARM (_prewarm_rug/_rug_gate_lookup): the
    # cheap holder verdict is warmed on a daemon thread when a pool arms into the
    # quoted set, and the entry reads it from cache (0 added latency; NEVER the
    # 90s eth_getLogs recon). FAIL-OPEN — no warm data blocks nothing. Reversible
    # with RH_RUG_GATE=shadow (stamp-only). The STAGED-drain exit defense is
    # fast_liq_bail_verdict (still SHADOW). The single-block LP-pull class (Halp
    # −90, 10s) is OUT OF SCOPE here — holder-invisible (top1 1.6), fenced instead
    # by MIN_LIQ 30k + MIN_POOL_AGE 1h (Halp was $17k/7min) / the LP-custody stamp.
    #
    # PRE-REGISTERED (paper race seat, never a live seat): grade each at n>=30 CLOSED
    # positions vs its PARENT (demand_heavy / deep_only / aged_deep) as control. The
    # STABILITY bar (not a higher ceiling): trip-return stdev DOWN vs parent AND
    # catastrophic-token rate (<-20%) <= 5% AND token-median NOT worse AND green in a
    # MAJORITY of OOS windows (odd/even by DISTINCT TOKEN). HONEST LOW-N: 7-12 tokens
    # per parent today — DIRECTIONAL. All levers are computed from tape already in
    # hand each tick (<=2s detect->fill budget); the cap adds ZERO latency.
    # 1. demand $150 entry (demand_heavy) + tail-cap. The healthiest parent
    #    (tokmed +5.5 / 75% green); the cap protects its 34%-single-token exposure.
    #    hard_stop −10 (tightened from −15, 2026-07-13 net optimization): realistic
    #    net +$36→+$47, BAD-day 07-11 −12.8→−1.9, GOOD-day 07-12 unchanged. Its tail
    #    is STAGED QUANT bleeds (savable by the stop), diversified across trades.
    LaneBot(bot_id="rh_stable_demand",
            demand_min_buy_usd=150.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_bites_per_token=2,
            hard_stop_pct=-10.0,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            exclusion_group="stable"),
    # 2. deep −25 entry (deep_only) + tail-cap. hard_stop −10 (from −15) adds the
    #    staged-bleed savings; its real catastrophe (CASHCATWIF −100 LP_DRAIN @ 109m)
    #    is a SINGLE-BLOCK pull the stop CANNOT catch — the DERISK-to-25% cap owns
    #    that (−100 → ~−25 on a quarter position). HONEST CONCENTRATION FLAG: the
    #    parent's whole observed tail benefit is that ONE token on ONE day, so the
    #    deep racer's $ lift is NOT projectable — the mechanism is sound, the
    #    magnitude is one-trade. This is also the highest pre-TP1 knife-through risk
    #    of the two (−25 entries wiggle deep) → the −10 stop is the one to watch.
    LaneBot(bot_id="rh_stable_deep",
            dip_trigger_pct=-25.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_bites_per_token=2,
            hard_stop_pct=-10.0,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            exclusion_group="stable"),
    # 3. aged-deep entry (aged_deep: 6-24h pools, aged exit ladder, depth-gated
    #    loss re-entry) + tail-cap with the AGED (20-min) derisk window so the
    #    fat-tailed aged holds are preserved. HONEST: this is the weakest/most-
    #    directional parent (n=11, 43% token-green, red median) and it has NO
    #    catastrophic tail to cap today — its instability is thin-n + a red median,
    #    which the tail-cap does NOT fix; the cap is forward insurance for when it
    #    eventually hits a deep-aged rug. Needs the most n before any read.
    # LIVE SEAT (2026-07-15, AxiS: "swap live to rh_stable_ageddeep"). Chosen on
    # FIDELITY-honest numbers: +$17.24 real with ZERO illusion over 56 entries —
    # the only clean, positive, non-thin bot in the fleet (only 4/32 are honest-
    # positive; the incumbent rh_aged_derisk is -$0.06 real, its +$23.90 paper is
    # pure illusion). max_concurrent=1 is a LIVE-SAFETY cap, not a thesis change:
    # the hot wallet is ~$38 total (~$13 free + an open position), so the default
    # MAX_CONCURRENT=2 x $25 = $50 would just bounce off insufficient balance.
    # Mirrors the same cap put on rh_aged_derisk when IT held the live seat.
    LaneBot(bot_id="rh_stable_ageddeep",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            reentry_cooldown_s=0.0,
            reentry_min_dip_pct=REENTRY_MIN_DIP_PCT,
            max_bites_per_token=2,
            hard_stop_pct=-15.0,
            derisk_after_s=DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            regime_hours=True, max_concurrent=1,
            exclusion_group="stable"),
    # ── SL1 LOSS-LADDER A/B RACERS (2026-07-17 dollar-conversion mine).
    # PRE-REGISTRATION: each clones a ZERO-ILLUSION parent verbatim and differs
    # ONLY in sl1 (bank 0.75 at -6% pre-TP1; tail rides the parent's stop).
    # Parents (fidelity 07-15): stable_ageddeep +$17.24/56e, aged_hold -$3.46
    # flat at the fleet's highest honest throughput (121e — the rescue test),
    # stable_demand +$8.07/51e. Own exclusion_group ("slcut") so racer and
    # parent CAN hold the same token = paired A/B on the same tape. Grade at
    # n>=30 closes vs the parent as control: net $/entry + avg loss-leg $ +
    # win-rate (does SL1 kill recoverers?). Kill whichever side loses.
    LaneBot(bot_id="rh_slcut_ageddeep",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            reentry_cooldown_s=0.0,
            reentry_min_dip_pct=REENTRY_MIN_DIP_PCT,
            max_bites_per_token=2,
            hard_stop_pct=-15.0,
            derisk_after_s=DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            regime_hours=True, max_concurrent=1,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            exclusion_group="slcut"),
    LaneBot(bot_id="rh_slcut_agedhold",
            min_pool_age_h=AGED_MIN_POOL_AGE_H,
            tp1_pct=AGED_TP1_PCT, tp1_sell_fraction=0.50,
            tp2_pct=AGED_TP2_PCT, tp2_sell_fraction=0.30,
            trail_pp=AGED_TRAIL_PP,
            regime_hours=True,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            exclusion_group="slcut"),
    LaneBot(bot_id="rh_slcut_demand",
            demand_min_buy_usd=150.0,
            max_pool_age_h=SCALP_MAX_POOL_AGE_H,
            max_bites_per_token=2,
            hard_stop_pct=-10.0,
            derisk_after_s=LOWVAR_DERISK_AFTER_S, derisk_max_frac=DERISK_MAX_FRAC,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            exclusion_group="slcut"),
    # ── PHOENIX RACER (2026-07-18 post-exit bounce backtest, n=801 losing
    # closes with 6h coverage: median bounce +16.2% above our exits; 59%
    # recover more than the whole loss; 28% true deaths). Entry = INVERTED
    # sibling-stop: only pools a fleet bot loss-stopped within 60min (the
    # stop is the bottom-marker), then the normal dip + demand-turn gates
    # confirm the turn. Loose dip (-8: post-stop price is already dipped),
    # broad admission (liq>=10k, no age band — bounces measured fleet-wide).
    # Exits shaped to the measured bounce (median +16 -> TP2 +14) with the
    # validated SL1 stacked (the 28% deaths ride only 25% past -6). NO
    # exclusion_group: sibling-stop semantics must not re-block it.
    # PRE-REGISTERED: grade at n>=30 closes on net $/entry + win-rate +
    # cat-rate; kill if the deaths eat the bounces.
    # PHOENIX2 (2026-07-20, replaces v1 — AxiS: "fix it instead of killing").
    # v1 died at fid -$122/n=57, wr 25%: exposure inversion (148 buys/28
    # pools — bleeding pools re-emit stop signals; clean bouncers leave the
    # population) + further-dip entry (conditioning on continued decline
    # selects never-bouncers; fast bouncers never re-dip through the gate).
    # The BOUNCE ITSELF stayed verified (+16.2% median above exits). Fixes:
    # (1) RECLAIM entry — price back >=2% ABOVE the stop print (bounce has
    # started; the pro-seat panel's reclaim/base-confirm logic); (2) ONE bite
    # per pool ever (max_bites=1 + first_touch) on top of the fleet lockout;
    # (3) demand-turn still required. Same exits + SL1. PRE-REGISTERED: same
    # kill lines as v1 (fid<-$20 at n>=30, deaths eat bounces) — fresh clock.
    LaneBot(bot_id="rh_phoenix2",
            phoenix_entry=True, phoenix_window_s=3600.0,
            phoenix_reclaim_pct=2.0,
            first_touch_only=True, max_bites_per_token=1,
            dip_trigger_pct=-8.0,
            min_liq_usd=10_000.0,
            min_pool_age_h=0.0,
            tp1_pct=6.0, tp1_sell_fraction=0.60,
            tp2_pct=14.0, tp2_sell_fraction=0.25,
            trail_pp=6.0,
            hard_stop_pct=-12.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    # ── ENTRY-SOURCE A/B QUARTET (2026-07-19 memo, verified findings #2+#3:
    # 19.1% strict-knives carry 177% of the replay's drag [s30>b30 or
    # shallow-dip-in-deep-drawdown = seller not finished, 21/26 days]; and
    # blanket age<1h dominates the flow-refined carve-out [-1.050 vs -1.226
    # honest, quiet-young pools WORSE at -3.07]). Four arms on ONE common
    # broad-age dip population isolate each filter and their overlap —
    # ctrl / knife-only / young-only / both. arc<-15 proxies the replay's
    # athdd (drawdown vs session-first px; the lane has no true ATH).
    # PRE-REGISTERED: fidelity-corrected grading; success for knife = bleed
    # halved (kept lane may stay mildly red); n>=30 affected slots, >=5 days,
    # kept-beats-skipped >=70% of days. Shadow: skipped entries are visible
    # as knife_* block reasons in the ledger (the counterfactual arm).
    LaneBot(bot_id="rh_dipall_ctrl",
            dip_trigger_pct=-8.0, min_liq_usd=10_000.0,
            min_pool_age_h=0.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    LaneBot(bot_id="rh_dipall_knife",
            dip_trigger_pct=-8.0, min_liq_usd=10_000.0,
            min_pool_age_h=0.0, knife_skip=True,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    LaneBot(bot_id="rh_dipall_young1h",
            dip_trigger_pct=-8.0, min_liq_usd=10_000.0,
            min_pool_age_h=1.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    LaneBot(bot_id="rh_dipall_both",
            dip_trigger_pct=-8.0, min_liq_usd=10_000.0,
            min_pool_age_h=1.0, knife_skip=True,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    # ── MANUFACTURER-VETO A/B (2026-07-21 flat-camouflage mine: 82% of fleet
    # bleed is FLAT-drift-window entries the drift sensor can't see; the
    # per-bot slice showed removing them takes the fleet -$3,823 -> -$700).
    # Byte-identical to rh_dipall_ctrl EXCEPT it skips entries while the
    # window reads manufacturer-active (recent entries committing to corpses
    # >8%). Paired vs rh_dipall_ctrl = the clean forward measure of the
    # backward -$230->~$0 conversion (some of the backward number is the
    # dead-rebook->flat mechanical confound; THIS arm settles it live).
    # PRE-REGISTERED: n>=30 vetoed entries, net-$ vs ctrl + drop-top-2; the
    # veto goes fleet-wide only if it beats ctrl at the bar.
    LaneBot(bot_id="rh_mfrveto_ab",
            dip_trigger_pct=-8.0, min_liq_usd=10_000.0,
            min_pool_age_h=0.0, mfr_veto=True,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=2),
    # ── THE ESTABLISHED-DIP SEAT (2026-07-22, AxiS "buy THOSE dips"). The
    # good-dip population mine (liq>=30k, fidelity-honest) was unambiguous:
    #   0-1h  -$13.0/e | 1-6h -$5.6/e | 6-24h -$0.29/e | 24h+ +$0.46/e GREEN.
    # We were drowning the green 24h+ population (~19/day, 21 tokens) in
    # 362/day of 1-6h churn. This seat buys ONLY established-pool dips: age
    # >=24h + real liquidity >=30k, normal dip trigger (NOT the pro seat's
    # deep-flush requirement that starved it to 1/day), SL1, mfr_veto on
    # (belt+braces). Own group = paired vs the fleet. PRE-REGISTERED: the
    # career-mode go-live candidate if it holds green at n>=30/5d/20tok +
    # drop-top-2. This is the "buy the dips winners actually buy" seat.
    LaneBot(bot_id="rh_established_dip",
            dip_trigger_pct=-8.0,
            min_pool_age_h=24.0,
            min_liq_usd=30_000.0,
            mfr_veto=True,
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30,
            trail_pp=10.0, hard_stop_pct=-15.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=3),
    # ── THE YOUNG-RIGHT-TIME SEAT (2026-07-22, AxiS "we were buying at the
    # wrong times on the younger ones"). The young-timing mine (26,348 clean
    # entries, ret20 forward, rug-stripped, outlier-capped) was decisive on
    # TWO independent axes:
    #   PROVEN VOLUME: thin<2eth med -6.5%/25%win | proven>10eth med +1.8%/48%
    #   ENTRY MOMENT:  young DIP/pullback 18%win | young STRENGTH(arc>0) 45%
    # We were buying young DIPS on THIN pools — the two worst cells stacked.
    # The right time = a young pool with PROVEN cumulative volume, bought on
    # STRENGTH not weakness. launch_strength entry mode + proven-volume floor
    # (>10eth ~= $20k cum) + session anchor (cum_vol only valid creation-
    # anchored). mfr_veto on. Own group; SL1. Career candidate at the bar.
    LaneBot(bot_id="rh_young_proven",
            entry_mode="launch_strength",
            min_pool_age_h=0.0, max_pool_age_h=6.0,
            launch_min_inflow_usd=150.0,
            min_session_vol_usd=20_000.0,
            require_session_anchor=True,
            min_liq_usd=10_000.0,
            mfr_veto=True,
            tp1_pct=6.0, tp1_sell_fraction=0.60,
            tp2_pct=14.0, tp2_sell_fraction=0.25,
            trail_pp=6.0, hard_stop_pct=-10.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75,
            max_concurrent=3),
    # ── THE PROFESSIONAL-SHAPE SEAT (2026-07-19 judge-panel synthesis:
    # 3 Fable designers x 3 adversarial judges -> rh_pro_agedflush). The
    # concentrated seat: aged pools (>=24h, the honest band), deep flushes
    # (<=-25, NO depth floor — sub--40 verified weakly-positive with zero
    # dead exposure; the rug gate covers rug-shaped distribution), demand
    # must be REAL (>=$50 buys AND net-buy 30s tape; knife_skip = drift
    # insurance), session proven (>=16k vol), 15-buy/day budget, one bite
    # per pool per day. Conviction 1.5x ONLY in the monotone -40..-30 band.
    # Exits = the triple-validated aged_sl1 ladder. Replay: n=131, +$1.54/e,
    # wr 66%, dt2 +$172, 13/16 days green; PLAN ON THE P10 (+$113/16d), not
    # the headline. Forward honest range: +$0.25-0.90/e at 5-10 e/day.
    # PRE-REGISTERED KILLS: any surviving dead-token win = instrument halt;
    # fid <= -$40 wk1 or 4 red days; wr<40% @ n>=25; entries/day >20 or <2
    # for 3d (population mismatch — fix or kill, do NOT grade); vol/liq
    # gates blocking >50% of passers = recalibrate + restart clock.
    # Gate-decision visibility = the lane's block_hist + ledger reasons
    # (population-drift audit is load-bearing, not telemetry).
    LaneBot(bot_id="rh_pro_agedflush",
            dip_trigger_pct=-25.0,
            min_pool_age_h=24.0,
            min_liq_usd=30_000.0,
            demand_min_buy_usd=50.0, min_buys_30s=1,
            knife_skip=True,
            min_session_vol_usd=16_000.0,
            max_buys_per_day=15,
            reentry_cooldown_s=86_400.0,
            max_concurrent=3, max_rt_cost_pct=6.0,
            conviction_band=(-40.0, -30.0),
            tp1_pct=6.0, tp1_sell_fraction=0.50,
            tp2_pct=16.0, tp2_sell_fraction=0.30,
            trail_pp=10.0, hard_stop_pct=-15.0,
            sl1_pct=-6.0, sl1_sell_fraction=0.75),
)


# ── quote-leg latency levers (2026-07-13; all env-gated, default = current) ──
def _rt_combined() -> bool:
    """RH_RT_COMBINED: fold the buy quote + RT-cost sell quote into ONE batched
    POST (halves the quote-leg round trips). Default OFF -> the exact two-POST
    path. See _paper_buy / core.rh_execution.build_roundtrip_quote_batch. The
    OTHER two levers live in rh_execution and need no lane flag: RH_QUOTE_TIMEOUT_S
    (fast-fail the RPC tail) and RH_QUOTE_FALLBACK=none (skip the slow sequential
    sweep on a batch miss)."""
    return str(os.environ.get("RH_RT_COMBINED", "0")).strip().lower() \
        in ("1", "true", "on", "yes")


def _deadtape_shadow() -> bool:
    """RH_DEADTAPE_SHADOW: forward-grade the DEAD-TAPE entry gate in shadow.

    The 2026-07-13 tape-reconstruction study (10 RH paper sessions, 07-10..07-12,
    228 entry events, features rebuilt on interpolated per-line timestamps and fed
    through the EXACT core.retrace_microstructure windows) found that the confirmed
    Solana sell-distribution / net-flow-persistence signals do NOT transfer to the
    RH lane: sell_rate_60/sell_traj/cum_nf_60/pos_subwins flip sign or go flat
    across the three regime days (pooled AUC is a magnitude confound). The ONE
    entry-time separator that pointed the SAME way on all three days was cruder:
    whether there was live trade flow at all in the 60s pre-entry window
    (n_trades_60). Dip into a LIVE tape -> higher run-rate (07-10 +17pp, 07-11
    +32pp, 07-12 +46pp); dip into a DEAD tape (HOODBIRD signature: attention/
    liquidity already gone) -> tends to die. A ~15-45pp odds-shift, i.e. a
    SIZE/downsize lever, NOT a deterministic filter. SHADOW-ONLY: stamps
    deadtape_would_skip on the entry; never blocks or resizes."""
    return str(os.environ.get("RH_DEADTAPE_SHADOW", "0")).strip().lower() \
        in ("1", "true", "on", "yes")


def _deadtape_min_trades() -> int:
    """RH_DEADTAPE_MIN_TRADES: n_trades_60 floor below which the shadow gate marks
    would_skip (default 3 = the retrace_microstructure fail-open cutoff)."""
    try:
        return int(os.environ.get("RH_DEADTAPE_MIN_TRADES", "3"))
    except (TypeError, ValueError):
        return 3


# ── pure signal logic (unit-tested, no network) ─────────────────────────────
def price_from_quote(amount_in_wei: int, amount_out_atomic: int,
                     token_decimals: int) -> float:
    """RhQuote buy -> ETH per token, decimals-adjusted. 0.0 on empty quote."""
    if not amount_in_wei or not amount_out_atomic or token_decimals is None:
        return 0.0
    return (amount_in_wei / 1e18) / (amount_out_atomic / 10 ** token_decimals)


def dip_pct(series: list, now: float, window_s: float = PRICE_WINDOW_S):
    """[(ts, price)] -> pct off the window max, using the latest point.
    None when the window has <3 points (no basis for a dip claim)."""
    pts = [(t, p) for t, p in series if now - t <= window_s and p > 0]
    if len(pts) < 3:
        return None
    hi = max(p for _, p in pts)
    cur = pts[-1][1]
    if hi <= 0:
        return None
    return (cur - hi) / hi * 100.0


def flow_sums(rows: list, now: float,
              window_s: float = DEMAND_WINDOW_S) -> tuple:
    """Tape rows -> (buy_usd, sell_usd) over the window. The SHARED demand
    fact: summed once per pool per tick; each config then applies its own
    demand_min_buy_usd threshold to the same sums."""
    buys = sells = 0.0
    for r in rows:
        ts = r.get("_epoch")
        if ts is None or now - ts > window_s:
            continue
        v = float(r.get("volume_usd") or 0)
        if r.get("kind") == "buy":
            buys += v
        elif r.get("kind") == "sell":
            sells += v
    return buys, sells


def demand_turn(rows: list, now: float, window_s: float = DEMAND_WINDOW_S,
                min_buy_usd: float = DEMAND_MIN_BUY_USD) -> bool:
    """Tape rows -> True when recent flow is net-positive AND buys are real
    dollars (a dip nobody is buying is a knife, not an entry)."""
    buys, sells = flow_sums(rows, now, window_s)
    return buys >= min_buy_usd and (buys - sells) > 0


def rise_from_open_pct(series: list, now: float,
                       window_s: float = PRICE_WINDOW_S):
    """[(ts, price)] -> pct of the latest point vs the FIRST in-window point
    (the 10-min 'open') — the launch-strength trigger reads price ABOVE its
    open, the mirror image of dip_pct's off-the-high read. None when the
    window has <3 points (same no-basis rule as dip_pct)."""
    pts = [(t, p) for t, p in series if now - t <= window_s and p > 0]
    if len(pts) < 3:
        return None
    o = pts[0][1]
    if o <= 0:
        return None
    return (pts[-1][1] - o) / o * 100.0


def launch_trigger_blocks(rise_open_pct, net_inflow_usd,
                          min_inflow_usd: float = 150.0) -> list:
    """launch_strength trigger -> block reasons ([] = trigger fires).
    Replaces the dip-mode (no_dip, no_demand_turn) pair; the rest of the
    guard stack still applies via entry_verdict(trigger_blocks=...)."""
    blocks = []
    if rise_open_pct is None or rise_open_pct <= 0.0:
        blocks.append("no_strength")
    if net_inflow_usd < min_inflow_usd:
        blocks.append("weak_inflow")
    return blocks


def bite_gate(first_touch_only: bool, max_bites: Optional[int],
              prior_bites: int) -> Optional[str]:
    """Repeat-bite policy -> block reason or None (enter allowed).
    first_touch_only: a token is entered at most ONCE ever (persisted).
    max_bites: lifetime entry cap per token (None = uncapped)."""
    if first_touch_only and prior_bites >= 1:
        return "first_touch"
    if max_bites is not None and prior_bites >= max_bites:
        return "bites_cap"
    return None


def hour_allowed(allowed_hours_utc, hour_utc: int) -> bool:
    """None = 24/7; else the UTC hour must be in the allowed set."""
    return allowed_hours_utc is None or int(hour_utc) in allowed_hours_utc


def regime_hour_ok(hour_utc: int, age_h) -> bool:
    """Regime hour gate v1 (2026-07-11 full-history mine, replaces the
    REFUTED v0 human-era-14-23 rule — see the REGIME v1 comment block by the
    constants): block ONLY aged-band (>24h) pools in 19-21 UTC — the one
    hour rule that held in BOTH chrono halves AND BOTH day-parity halves of
    the 10.36M-swap history. Young/mid pools and unknown age/hour pass
    (fail-OPEN). Thin wrapper over core.rh_regime.aged_hour_gate_ok so the
    rule, its numbers and its provenance live in ONE place."""
    return aged_hour_gate_ok(hour_utc, age_h)


def dip_depth_block(dip, max_depth_pct) -> Optional[str]:
    """CANDIDATE-FACTORY depth cap -> block reason or None. A dip DEEPER
    (more negative) than max_depth_pct is the <1h LOSER profile (winner-delta
    2026-07-12: winners p50 -8.6 vs losers -15.6; our old all-buys p50 -17.2
    sat on the loser side). No dip reading = the trigger already blocks
    (no_dip) — this gate only rules on a real reading. None = gate off."""
    if max_depth_pct is None or dip is None:
        return None
    return "dip_too_deep" if dip < max_depth_pct else None


def buys_breadth_block(n_buys_30s: int, min_buys) -> Optional[str]:
    """Demand-breadth floor (sweep nb30 axis) -> block reason or None.
    Tape buy-print count over the 30s demand window; a $50 'demand turn'
    made of ONE print is a single actor, not demand. None = gate off."""
    if min_buys is None:
        return None
    return "demand_breadth" if n_buys_30s < int(min_buys) else None


def arc_pct(first_px, px_now):
    """Launch-arc position: pct of px_now vs the FIRST price the lane ever
    saw for this pool. None when either side is missing/invalid (pool
    discovered mid-life -> the signal doesn't exist; gates fail OPEN)."""
    if not first_px or not px_now or first_px <= 0:
        return None
    return (px_now / first_px - 1.0) * 100.0


def arc_block(arc, max_arc_pct) -> Optional[str]:
    """Arc cap -> block reason or None (winner-delta: losers buy LATE in the
    launch arc — median +1240% vs winners +540%). Fail-OPEN on no reading."""
    if max_arc_pct is None or arc is None:
        return None
    return "arc_late" if arc > max_arc_pct else None


POP_FRAC = 1.35             # pop detector: latest px >= 1.35x the window min
POP_COOLDOWN_S = 600.0      # one pop event per pool per 10 min (mine parity)


def pop_fired(series, now: float, window_s: float = PRICE_WINDOW_S,
              pop_frac: float = POP_FRAC):
    """Pop detector (the factory mine's, verbatim semantics): the LATEST
    in-window price at/above pop_frac x the window MIN -> pop magnitude pct;
    else None. <3 in-window points = no basis (same rule as dip_pct)."""
    pts = [(t, p) for t, p in series if now - t <= window_s and p > 0]
    if len(pts) < 3:
        return None
    lo = min(p for _, p in pts)
    cur = pts[-1][1]
    if lo <= 0 or cur < lo * pop_frac:
        return None
    return (cur / lo - 1.0) * 100.0


def proven_vol_block(cum_vol_usd: float, min_usd) -> Optional[str]:
    """PROVEN-VOLUME floor -> block reason or None. Lifetime observed USD
    volume (lane cum_vol, persisted) vs the racer's floor. None = gate off."""
    if min_usd is None:
        return None
    return "thin_session_vol" if (cum_vol_usd or 0.0) < min_usd else None


# ── session anchoring (2026-07-12 factory no-fire fix) ───────────────────────
# A pool's session facts (cum volume / arc / dip basis) are CREATION-FAITHFUL
# only when the lane's view is anchored at (or backfilled to) pool creation.
# Natural anchor: first tape row lands within this age (missed volume bounded
# to <=2 min of a launch). Otherwise the feed's session_seed backfill anchors
# retroactively; with neither, anchor-requiring racers block explicitly.
SESSION_ANCHOR_MAX_AGE_H = 120.0 / 3600.0


def session_anchor_block(anchored: bool, required: bool) -> Optional[str]:
    """Session-anchor gate -> "untracked_session" | None. Named EXPLICIT
    block for pools discovered mid-life: their cum_vol reads ~0 and their
    arc basis starts mid-arc — wrong values, not weak signals. Racers that
    don't require the anchor (require_session_anchor=False) never see it."""
    return None if (anchored or not required) else "untracked_session"


def demand_ok(buys_usd: float, sells_usd: float, min_buy_usd: float,
              net_required: bool = True) -> bool:
    """Dip-mode demand turn. net_required mirrors the pre-fix shared gate
    (buys >= floor AND net inflow positive); the mined d25 factory cells
    gate on the buy-side sum ONLY (factory_sweep.py DEMANDS['d25'])."""
    if buys_usd < min_buy_usd:
        return False
    return (buys_usd - sells_usd) > 0 if net_required else True


def merge_session_seed(seed, first_quote_px: float, now: float,
                       window_s: float = PRICE_WINDOW_S):
    """Feed session_seed + the pool's FIRST live quote px ->
    (first_px_scaled, recent_pts, cum_eth) | None.

    Seed prices are swap-derived and ATOMIC-relative; quote prices are
    decimals-adjusted ETH/token. The constant factor between them cancels in
    ratio math, so everything is rescaled onto the quote basis via
    scale = first_quote_px / median(last 3 seed px) (median = phantom-print
    guard: V2 |wnet|/|tnet| glitch rows print 1e6x). recent_pts = the seed
    points inside the live dip window, ready to prepend to the quote series;
    first_px_scaled = the pool's FIRST print on the quote basis (the mine's
    arc anchor, verbatim). None = unusable seed (caller stays unanchored)."""
    rows = [r for r in ((seed or {}).get("rows") or [])
            if r and len(r) >= 4 and r[3] and r[3] > 0]
    if not rows or not first_quote_px or first_quote_px <= 0:
        return None
    tail = sorted(r[3] for r in rows[-3:])
    ref = tail[len(tail) // 2]
    if ref <= 0:
        return None
    scale = first_quote_px / ref
    fp = float((seed.get("first_px") or 0.0)) * scale
    pts = [(float(r[0]), r[3] * scale) for r in rows
           if now - float(r[0]) <= window_s and float(r[0]) < now]
    return (fp if fp > 0 else None, pts,
            float(seed.get("cum_eth") or 0.0))


def pop_recency_block(last_pop_ts, now: float,
                      require_within_s) -> Optional[str]:
    """POP-RETRACE gate -> block reason or None: entry allowed only within
    require_within_s of the pool's last detected pop. None = gate off."""
    if require_within_s is None:
        return None
    if last_pop_ts is None or (now - last_pop_ts) > require_within_s:
        return "no_recent_pop"
    return None


def reentry_depth_gate(had_recent_loss: bool, dip, vol_m5_usd,
                       min_dip_pct: float = REENTRY_MIN_DIP_PCT,
                       min_vol_m5_usd: float = REENTRY_MIN_VOL_M5):
    """Depth+volume re-entry gate -> block reason or None (enter allowed).
    Applies ONLY to re-entries after a recent LOSING exit (had_recent_loss);
    first entries and post-win re-entries pass untouched. Deep flushes with
    live tape re-enter; shallow dips and dead tape are blocked (session-7
    live: -12..-25% re-buys slaughtered, -26..-38% paid; MONSIEUR's dead
    tape was vol_m5 $109). No dip reading = no depth evidence = block."""
    if not had_recent_loss:
        return None
    if dip is None or dip > min_dip_pct:
        return "reentry_shallow"
    if vol_m5_usd is None or vol_m5_usd < min_vol_m5_usd:
        return "reentry_dead_tape"
    return None


def derisk_slice(remaining_frac: float, age_s: float, derisk_after_s,
                 derisk_max_frac: float = DERISK_MAX_FRAC) -> float:
    """Rug-tail defense: fraction of the ORIGINAL position to sell so that
    exposure past the derisk window is capped at derisk_max_frac. 0.0 while
    inside the window, when the cap is already satisfied (e.g. TP1 sold
    more), or when the feature is off (derisk_after_s None)."""
    if derisk_after_s is None or age_s < derisk_after_s:
        return 0.0
    return max(0.0, remaining_frac - derisk_max_frac)


def sibling_exclusion_keys(states, self_bot_id: str, group: str, now: float,
                           stop_window_s: float = SIBLING_STOP_WINDOW_S) -> set:
    """Cross-sibling token exclusion (Solana young_pond mirror): the set of
    pool AND token addresses that are off-limits to `self_bot_id` because a
    SIBLING in the same exclusion group (a) currently holds them, or (b)
    LOSS-stopped them within stop_window_s. Winning exits free the token
    immediately; the racer's OWN history never excludes it (its re-entry is
    governed by its own cooldown/depth gates)."""
    keys = set()
    for st in states:
        b = st.bot
        if b.bot_id == self_bot_id or b.exclusion_group != group:
            continue
        for pool, meta in st.pos_meta.items():
            keys.add(pool)
            tok = meta.get("token")
            if tok:
                keys.add(tok)
        for pool, info in st.exit_book.items():
            if (info.get("loss")
                    and (now - float(info.get("ts") or 0)) <= stop_window_s):
                keys.add(pool)
                tok = info.get("token")
                if tok:
                    keys.add(tok)
    return keys


def dedupe_group_entries(entering_states):
    """Same-tick exclusion-group arbitration: when several racers of ONE
    exclusion group pass the gates on the SAME pool in the same tick, only
    one may take it (siblings hold DISTINCT tokens). Winner = fewest open
    positions (balances sample collection), tie -> roster order. Returns
    (kept_states, blocked_states); racers without a group always pass."""
    kept, blocked = [], []
    winner_by_group = {}
    for st in entering_states:
        g = st.bot.exclusion_group
        if not g:
            kept.append(st)
            continue
        cur = winner_by_group.get(g)
        if cur is None:
            winner_by_group[g] = st
            kept.append(st)
        elif len(st.pos_meta) < len(cur.pos_meta):
            kept[kept.index(cur)] = st
            blocked.append(cur)
            winner_by_group[g] = st
        else:
            blocked.append(st)
    return kept, blocked


def ledger_iso(now: float, seq: int) -> str:
    """iso_utc + a synthetic .%03d millisecond field. The dashboard ingest
    de-dups rows on (ts, ev, pool) — bot_id is NOT part of that key — so two
    racers trading the same pool in the same second MUST NOT share a ts
    string. seq is a lane-global monotonic counter; the date-prefix day-P&L
    aggregation (ts[:10]) and the dashboard table are unaffected."""
    base = iso_utc(now)                       # %Y-%m-%dT%H:%M:%S+00:00
    return f"{base[:19]}.{seq % 1000:03d}{base[19:]}"


def sell_slice(remaining_frac: float, req_frac: float):
    """Exit-engine sell_fraction semantics: fraction of the ORIGINAL size,
    clamped to what's left. Returns (frac_of_original_sold, new_remaining).
    Cost basis MUST use the clamped fraction — booking the requested fraction
    overstates cost on post-TP1 exits (the BILLY -75% phantom, 2026-07-10)."""
    f = max(0.0, min(req_frac, remaining_frac))
    return f, remaining_frac - f


# ── LIVE FILL PROBE routing glue (2026-07-12) — pure, unit-tested ────────────
def live_probe_bots() -> set:
    """RH_LIVE_PROBE_BOTS env (comma-separated bot_ids) -> set. Read at CALL
    time, exactly like the triple gate — never cached at import."""
    raw = os.environ.get("RH_LIVE_PROBE_BOTS", "") or ""
    return {s.strip() for s in raw.split(",") if s.strip()}


def live_route_open(bot_id: str) -> bool:
    """FOUR conditions or nothing: the rh_live triple gate (RH_LIVE_CONFIRMED
    =true AND RH_PAPER_MODE=false AND RH_PRIVATE_KEY present) AND this bot_id
    opted in via RH_LIVE_PROBE_BOTS. Any condition missing -> False -> the
    racer books pure paper (dormant default). FAIL-CLOSED both ways."""
    if bot_id not in live_probe_bots():
        return False
    ok, _ = rh_live.rh_live_gate()
    return bool(ok)


def daily_buys_block(n_today: int, cap) -> Optional[str]:
    """Per-UTC-day entry cap -> block reason or None. None cap = gate off
    (every pre-existing racer). Feeds entry_verdict via extra_blocks."""
    if cap is None:
        return None
    return "daily_buys_cap" if int(n_today) >= int(cap) else None


_LIVE_TX_RE = re.compile(r"tx=(0x[0-9a-fA-F]{64})")


def classify_live_error(err: Exception) -> str:
    """Live-exec failure -> what actually happened to the MONEY:
      'pre_send'      — refused before any tx (gate / containment / no route
                        / paper-only executor): nothing spent, safe to skip.
      'reverted'      — a tx MINED and reverted (swap or approve): gas spent,
                        NO position change; safe to skip booking and retry.
      'unknown_spend' — a tx was broadcast and its outcome is UNKNOWN
                        (receipt timeout / transport loss after send): the
                        Solana E1b class (dip_scanner 2026-06-02 audit) —
                        money may be gone in an untrackable position. Callers
                        MUST log LOUDLY + flag manual reconcile.
    RhExecutor wraps every post-quote failure in RhSwapError whose message
    ends 'tx=<hash>' ('tx=None' when signing/sending never happened), so the
    hash presence is the send/no-send discriminator. Non-executor exception
    types can only arise before any send (the executor's money path always
    wraps) -> pre_send."""
    if isinstance(err, (rh_live.RhLiveGateError, rh_live.RhContainmentError)):
        return "pre_send"
    if isinstance(err, rh_live.RhSwapError):
        msg = str(err)
        if _LIVE_TX_RE.search(msg) is None:
            return "pre_send"          # no tx was ever broadcast
        if "revert (status=0)" in msg or "approve reverted" in msg:
            return "reverted"          # mined + reverted: state is KNOWN
        return "unknown_spend"         # broadcast, outcome unknown (E1b)
    return "pre_send"


def fill_telemetry(rec: dict, decision_ts, quote_ts, order_sent_ts,
                   landed_wall_ts, tx_landed_ts=None) -> dict:
    """Per-leg fill-time telemetry: wall-clock stamps for every hop of the
    live leg plus the executor's own numbers. rec = the
    RhLiveExecutor.live_buy/live_sell return record (rh_live_swaps.jsonl
    shape). tx_landed_ts = the RECEIPT BLOCK timestamp (chain truth, seconds
    resolution); decision_to_landed_ms uses the wall clocks (ms resolution).
    Pure; never raises (telemetry must never block a booking)."""
    try:
        total_ms = round((float(landed_wall_ts) - float(decision_ts))
                         * 1000.0, 1)
    except Exception:
        total_ms = None
    return {
        "decision_ts": decision_ts,
        "quote_ts": quote_ts,
        "order_sent_ts": order_sent_ts,
        "landed_wall_ts": landed_wall_ts,
        "tx_landed_ts": tx_landed_ts,
        "decision_to_landed_ms": total_ms,
        "exec_latency_ms": rec.get("total_latency_ms"),
        "fill_vs_quote_pct": rec.get("fill_vs_mid_slippage_pct"),
        "gas_cost_eth": rec.get("gas_cost_eth"),
        "route": rec.get("route"),
        "fee_tier": rec.get("fee_tier"),
        "tx": rec.get("tx_signature"),
    }


def lp_drain_pct(liq_series, now: float, window_s: float = LP_DRAIN_WINDOW_S):
    """[(ts, liq_usd)] -> pct change from the window's max liq to the latest
    sample (0 or negative = drain). None when <2 in-window samples (fail-OPEN
    for the entry stamp, but the entry gate then simply has no drain signal)."""
    pts = [(t, x) for t, x in liq_series if now - t <= window_s and x and x > 0]
    if len(pts) < 2:
        return None
    hi = max(x for _, x in pts)
    cur = pts[-1][1]
    if hi <= 0:
        return None
    return (cur - hi) / hi * 100.0


def entry_verdict(dip, demand, micro, liq_usd, honeypot_ok,
                  open_count, cooldown_ok, daily_pnl_usd,
                  age_h=None, drain_pct=None,
                  dip_trigger_pct=DIP_TRIGGER_PCT,
                  min_liq_usd=MIN_LIQ_USD,
                  min_pool_age_h=MIN_POOL_AGE_H,
                  max_pool_age_h=None,
                  max_concurrent=MAX_CONCURRENT,
                  daily_loss_stop_usd=DAILY_LOSS_STOP_USD,
                  hour_ok=True, bite_block=None,
                  trigger_blocks=None, extra_blocks=None) -> dict:
    """Combine every gate -> {enter: bool, blocks: [..]} (all reasons kept
    so the ledger shows WHY, not just whether). Thresholds default to the
    module constants (= rh_young_v1); the fleet passes each racer's own
    (dip/liq/age/concurrency come from its LaneBot). hour_ok/bite_block are
    the per-config trading-window and repeat-bite verdicts, pre-computed by
    the caller (hour_allowed / bite_gate). trigger_blocks replaces the
    dip-mode trigger pair (no_dip/no_demand_turn) for alternate entry modes
    (launch_trigger_blocks); the guard stack below applies either way.
    extra_blocks appends caller-computed per-racer verdicts (sibling
    exclusion / re-entry depth gate / regime hour gate) without changing the
    shared guard stack."""
    blocks = []
    if trigger_blocks is None:
        if dip is None or dip > dip_trigger_pct:
            blocks.append("no_dip")
        if not demand:
            blocks.append("no_demand_turn")
    else:
        blocks.extend(trigger_blocks)
    if micro and micro.get("avoid_block"):
        blocks.append("retrace_micro_avoid")
    if liq_usd < min_liq_usd:
        blocks.append("liq_floor")
    if age_h is not None and age_h < min_pool_age_h:
        blocks.append("age_floor")
    if (max_pool_age_h is not None and age_h is not None
            and age_h > max_pool_age_h):
        blocks.append("age_ceiling")
    if drain_pct is not None and drain_pct <= LP_DRAIN_ENTRY_PCT:
        blocks.append("lp_drain")
    if not honeypot_ok:
        blocks.append("honeypot")
    if open_count >= max_concurrent:
        blocks.append("max_concurrent")
    if not cooldown_ok:
        blocks.append("cooldown")
    if not hour_ok:
        blocks.append("hour_window")
    if bite_block:
        blocks.append(bite_block)
    if extra_blocks:
        blocks.extend(extra_blocks)
    if daily_pnl_usd <= daily_loss_stop_usd:
        blocks.append("daily_loss_stop")
    return {"enter": not blocks, "blocks": blocks}


# ── per-config trading state ─────────────────────────────────────────────────
class BotState:
    """Everything that must differ per RACER. Per-POOL facts (tape, quote
    prices, liq history, honeypot verdicts, decimals) stay on the lane —
    they are facts about the pool, not about any config."""

    def __init__(self, bot: LaneBot):
        self.bot = bot
        self.pm = PerBotPositionManager(bot.bot_config())
        self.pos_meta = {}       # pool -> {qty, token, sym, entry stamps}
        self.daily_pnl_usd = 0.0
        self.n_entries = 0
        self.n_exits = 0
        self.block_hist = {}     # block reason -> count (why it isn't firing)
        self.last_exit = {}      # pool -> ts (re-entry cooldown)
        self.bites = {}          # pool -> lifetime entry count (persisted;
                                 # drives first_touch_only / max_bites_per_token)
        self.exit_book = {}      # pool -> {ts, loss, token} of the last FULL
                                 # close (position-level realized sign, all
                                 # legs summed). Drives the depth re-entry
                                 # gate + cross-sibling loss-stop exclusion.
                                 # In-memory only: both windows are 20 min,
                                 # so a restart fails OPEN (like last_exit).
        self.day_buys = 0        # entries booked this UTC day (persisted
                                 # same-day like daily_pnl_usd; drives the
                                 # fill probe's max_buys_per_day cap)
        self.pool_loss_closes = {}  # pool -> [ts of losing FULL closes]
                                 # (rolling 6h lockout window; in-memory
                                 # like exit_book — restart fails OPEN)
        self.recent_realized = []  # last <=50 FULL-close realized $ (regime
                                 # layer: rolling-expectancy DIAL stamp —
                                 # STAMP ONLY, never a paper buy-halt;
                                 # persisted so the dial's record survives
                                 # restarts)


# ── the lane ─────────────────────────────────────────────────────────────────
class PaperLane:
    def __init__(self, feed: Feed, executor=None, registry=None, bots=None):
        self.feed = feed
        self.ex = executor          # RhExecutor (lazy if None)
        # pool -> {token, fee, ...}: feed.watch does NOT carry the token
        # (candidate dict is popped at promotion) — the Firehose registry does.
        self.registry = registry if registry is not None else {}
        self.q = queue.Queue()      # (pool, row) from the firehose hook
        self.tape = {}              # pool -> [rows] (rolling)
        self.prices = {}            # pool -> [(ts, price_eth)]
        # candidate-factory shared facts (2026-07-12):
        self.first_px = {}          # pool -> first quote px ever seen (arc
                                    # basis; persisted — see save_state)
        self.cum_vol = {}           # pool -> lifetime observed USD volume
        # MANUFACTURER-ACTIVE read (2026-07-21 flat-camouflage mine: 82% of
        # fleet bleed is FLAT-drift-window entries; the drift sensor is blind
        # to manufacturers seeding corpses in calm windows). Rolling 4h list
        # of (ts, token) for ALL fleet entries; _mfr_active_now checks what
        # fraction have SINCE landed in the published dead set — a live read
        # that the manufacturers are operating NOW, even at drift 0.
        self._recent_entries = []
                                    # (proven-volume gate basis; persisted)
        self.session_anchor = {}    # pool -> True when session facts are
                                    # creation-anchored (seed backfill applied
                                    # or first tape row at age <= 2 min);
                                    # persisted — require_session_anchor
                                    # racers block unanchored pools with the
                                    # explicit untracked_session reason
        self.first_tape_age = {}    # pool -> age_h at FIRST tape row (natural
                                    # -anchor test; in-memory: the persisted
                                    # session_anchor carries the verdict)
        self.pop_book = {}          # pool -> (ts, mag_pct) of last detected
                                    # pop (pop-retrace gate; in-memory, the
                                    # gate window is minutes-scale)
        self.liq_hist = {}          # pool -> [(ts, liq_usd)] (lp-drain guard)
        self.last_trade = {}        # pool -> ts (hot tracking)
        self.decimals = {}          # token -> int
        self.honeypot = {}          # token -> verdict dict
        self.n_quotes = 0           # fire-evidence: quotes actually made
        self.n_evals = 0            # fire-evidence: entry gates actually run
        self._ledger_seq = 0        # ledger ts uniquifier (see ledger_iso)
        # regime detection (regime-conditional hour gate): observed pool-
        # discovery rate. Initialized lazily on the first tick so the startup
        # backfill flood (6h of pre-existing candidates) doesn't count.
        self._regime_known = None   # set of pool addrs already seen
        self._regime_seen = []      # discovery timestamps (rolling 1h)
        self._regime_t0 = None      # first-tick ts (warm-up clock)
        # sell-path canary (live-mode only; see _canary_tick)
        self._canary = None
        self._last_canary_ts = 0.0
        # LIVE FILL PROBE executor (lazy: constructed only when a live route
        # actually opens — a dormant lane never touches rh_live executors)
        self._live = None
        # rug-signal SHADOW stamper (see _stamp_rug_signals)
        self._rug_lock = threading.Lock()   # single-flight on the RPC
        self._rug_cache = {}                # pool -> (computed_ts, stamp)
        self._rug_rpc = None                # dedicated Rpc (lazy)
        # ARM-TIME RUG-GATE PREWARM (see _prewarm_rug / _rug_gate_lookup): the
        # warm Blockscout verdict cache the ENFORCE gate reads at 0 latency.
        self._bs_lock = threading.Lock()    # guards _bs_prewarm + _bs_inflight
        self._bs_prewarm = {}               # token -> (fetched_ts, bs_stamp)
        self._bs_inflight = set()           # tokens with a prewarm thread live
        self._rug_blocked_pools = set()     # pools already logged as gate-blocked
                                            # (dedupe the per-tick block ledger row)
        # regime layer (core/rh_regime): feed-wide 30-min demand-composition
        # window, fed from the tape drain; stamped on every entry ledger row.
        self.comp = CompositionTracker()
        # FLEET-WIDE realized record (regime-SIZING read, 2026-07-13): last <=50
        # FULL-close realized $ across ALL racers, in close order. The rolling
        # expectancy dial over THIS is the real-time "is today working?" regime
        # read (per-racer dials are too sparse to warm up within a bad day). Fed
        # to regime_stamp as size_dial; SHADOW-stamps regime_score + would_size.
        # Lane-level (not per-bot); persisted in save_state.
        self.fleet_realized = []
        # FLEET: default = single-config control (back-compat for callers/
        # tests that predate the fleet); main() passes the full ROSTER.
        self.bots = tuple(bots) if bots else (LaneBot(bot_id=LEGACY_BOT_ID),)
        assert len({b.bot_id for b in self.bots}) == len(self.bots), \
            "duplicate bot_id in roster"
        self.state = {b.bot_id: BotState(b) for b in self.bots}
        self.stop = threading.Event()

    # ── single-config back-compat surface (the FIRST roster entry is the
    # lane's "primary"; pre-fleet tests and callers read these) ─────────────
    @property
    def _st0(self) -> BotState:
        return self.state[self.bots[0].bot_id]

    @property
    def pm(self):
        return self._st0.pm

    @property
    def pos_meta(self):
        return self._st0.pos_meta

    @property
    def block_hist(self):
        return self._st0.block_hist

    @property
    def last_exit(self):
        return self._st0.last_exit

    @property
    def daily_pnl_usd(self):
        return self._st0.daily_pnl_usd

    @daily_pnl_usd.setter
    def daily_pnl_usd(self, v):
        self._st0.daily_pnl_usd = float(v)

    @property
    def n_entries(self):
        return sum(st.n_entries for st in self.state.values())

    @property
    def n_exits(self):
        return sum(st.n_exits for st in self.state.values())

    def _ledger_ts(self, now: float) -> str:
        self._ledger_seq += 1
        return ledger_iso(now, self._ledger_seq)

    def _held_pools(self) -> dict:
        """UNION of held pools across configs -> largest remaining qty. The
        sell-exec ticking quotes each held pool ONCE; when two configs hold
        the same pool the quote is sized by the LARGER remaining qty and the
        price is shared (approximation: the smaller holder's own sell would
        fill at an equal-or-better price, so its shared tick is slightly
        conservative — never optimistic)."""
        held = {}
        for st in self.state.values():
            for pool, meta in st.pos_meta.items():
                rem = meta["qty_orig"] * meta["remaining_frac"]
                if rem > held.get(pool, 0.0):
                    held[pool] = rem
        return held

    # ── durable open positions (parity with the Solana bot_state stores:
    # a crash/restart mid-hold must never orphan a position) ────────────────
    def save_state(self):
        """Per-config keyed state file: {"day": ..., "bots": {bot_id: {...}}}.
        bites (lifetime per-token entry counts) persist so first_touch/
        bites-cap policies survive restarts."""
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "day": time.strftime("%Y-%m-%d", time.gmtime()),
                    # lane-level facts (candidate-factory): the arc gate's
                    # first-seen px basis must survive restarts or the gate
                    # silently fails open on every pre-existing pool.
                    "first_px": self.first_px,
                    "cum_vol": {k: round(v, 2)
                                for k, v in self.cum_vol.items()},
                    "session_anchor": [k for k, v in
                                       self.session_anchor.items() if v],
                    # fleet-wide realized record for the regime-SIZING dial
                    "fleet_realized": self.fleet_realized[-50:],
                    "bots": {bid: {"pos_meta": st.pos_meta,
                                   "daily_pnl_usd": st.daily_pnl_usd,
                                   "day_buys": st.day_buys,
                                   "pm_state": st.pm.to_state_list(),
                                   "bites": st.bites,
                                   "recent_realized": st.recent_realized}
                             for bid, st in self.state.items()},
                }, f)
            os.replace(tmp, STATE)
        except Exception as e:
            print(f"[rh-paper] state save failed: {e}", flush=True)

    def restore_state(self):
        """Reload open positions from a prior session/crash. Same-day daily
        P&L carries over (it's a day counter, not a session counter).
        LEGACY MIGRATION: the pre-fleet single-config file (top-level
        pos_meta/pm_state/daily_pnl_usd) belongs to rh_young_v1 — the control
        IS that config, so it inherits the state verbatim."""
        try:
            if not os.path.exists(STATE):
                return
            raw = json.load(open(STATE, encoding="utf-8"))
            same_day = raw.get("day") == time.strftime("%Y-%m-%d", time.gmtime())
            self.first_px = {k: float(v) for k, v in
                             (raw.get("first_px") or {}).items()
                             if isinstance(v, (int, float)) and v > 0}
            self.cum_vol = {k: float(v) for k, v in
                            (raw.get("cum_vol") or {}).items()
                            if isinstance(v, (int, float)) and v >= 0}
            self.session_anchor = {k: True for k in
                                   (raw.get("session_anchor") or [])
                                   if isinstance(k, str)}
            self.fleet_realized = [
                float(x) for x in (raw.get("fleet_realized") or [])
                if isinstance(x, (int, float))][-50:]
            per_bot = raw.get("bots")
            if per_bot is None:  # legacy single-config shape
                per_bot = {LEGACY_BOT_ID: {
                    "pos_meta": raw.get("pos_meta") or {},
                    "daily_pnl_usd": raw.get("daily_pnl_usd") or 0.0,
                    "pm_state": raw.get("pm_state") or [],
                    "bites": {}}}
                print(f"[rh-paper] legacy single-config state -> "
                      f"{LEGACY_BOT_ID}", flush=True)
            n_restored = 0
            for bid, blob in per_bot.items():
                st = self.state.get(bid)
                if st is None:
                    print(f"[rh-paper] state for unknown bot '{bid}' ignored",
                          flush=True)
                    continue
                if not isinstance(blob, dict):
                    continue
                st.pos_meta = blob.get("pos_meta") or {}
                if same_day:
                    st.daily_pnl_usd = float(blob.get("daily_pnl_usd") or 0.0)
                    st.day_buys = int(blob.get("day_buys") or 0)
                st.bites = {k: int(v) for k, v in (blob.get("bites") or {}).items()
                            if isinstance(v, (int, float))}
                st.recent_realized = [
                    float(x) for x in (blob.get("recent_realized") or [])
                    if isinstance(x, (int, float))][-50:]
                st.pm.load_state_list(blob.get("pm_state") or [])
                # drop meta whose pm twin didn't restore (and vice versa)
                st.pos_meta = {p: m for p, m in st.pos_meta.items()
                               if st.pm.get_position(p) is not None}
                n_restored += len(st.pos_meta)
            if n_restored:
                print(f"[rh-paper] restored {n_restored} open position(s) "
                      f"across {len(self.state)} config(s)", flush=True)
        except Exception as e:
            print(f"[rh-paper] state restore failed (starting clean): {e}",
                  flush=True)

    # firehose hook (ws thread) — cheap, non-blocking
    def on_row(self, pool: str, row: dict):
        self.q.put((pool, row))

    def _token_for(self, pool: str):
        """Token address for a pool: firehose registry first (authoritative),
        then any config's open-position meta, then feed.watch (fallback)."""
        tok = (self.registry.get(pool) or {}).get("token")
        if tok:
            return tok
        for st in self.state.values():
            tok = (st.pos_meta.get(pool) or {}).get("token")
            if tok:
                return tok
        return (self.feed.watch.get(pool) or {}).get("token")

    def _executor(self):
        if self.ex is None:
            from core.rh_execution import RhExecutor
            self.ex = RhExecutor()
            self.ex.connect()
        return self.ex

    # ── LIVE FILL PROBE plumbing (2026-07-12) ────────────────────────────────
    def _live_executor(self):
        """The rh_live policy executor (gas cap + canary + caps + daily stop
        live inside it). Lazy — only ever constructed when live_route_open
        already said yes, or when a live-bought position needs its exit."""
        if self._live is None:
            self._live = rh_live.RhLiveExecutor()
        return self._live

    def _tx_landed_ts(self, tx_hash):
        """RECEIPT BLOCK timestamp (chain truth) for a landed tx. FAIL-OPEN:
        telemetry only — any problem returns None, never raises, never
        blocks a booking."""
        try:
            if not tx_hash:
                return None
            w3 = self._live_executor()._executor().w3
            rcpt = w3.eth.get_transaction_receipt(tx_hash)
            blk = w3.eth.get_block(rcpt["blockNumber"])
            return int(blk["timestamp"])
        except Exception:
            return None

    def _log_live_error(self, leg: str, bot_id: str, pool: str, token,
                        err: Exception, cls: str, now: float):
        """Named ledger event for ANY live-exec failure (FAIL-SAFE clause).
        unknown_spend = the Solana E1b class: money may be gone and the
        position is untrackable -> LOUD print + manual_reconcile flag."""
        try:
            _append(LEDGER, {"ev": "rh_live_exec_error",
                             "ts": self._ledger_ts(now), "leg": leg,
                             "bot_id": bot_id, "pool": pool, "token": token,
                             "err_type": type(err).__name__,
                             "err": str(err)[:300], "class": cls,
                             "manual_reconcile": cls == "unknown_spend"})
        except Exception as e:
            print(f"[rh-paper] live-error ledger append failed: {e}",
                  flush=True)
        if cls == "unknown_spend":
            print(f"[rh-paper] *** LIVE {leg.upper()} MONEY MAY BE SPENT but "
                  f"untrackable (bot={bot_id} token={token} "
                  f"err={str(err)[:120]}) — MANUAL RECONCILE (E1b class) ***",
                  flush=True)
        else:
            print(f"[rh-paper] LIVE {leg.upper()} FAILED ({cls}) "
                  f"bot={bot_id} {str(err)[:120]}", flush=True)

    def _holder_floor_verdict(self, token, pool):
        """(blocked, holders, reason) — the anti-rug holder-base floor for LIVE
        buys. Prefers the arm-time prewarm stamp; on a MISS (fresh token) does a
        SYNCHRONOUS Blockscout fetch (~1s, affordable for a live buy — this is
        the fix for the GOATAI fail-open hole). FAIL-CLOSED: an unverifiable
        holder count REFUSES the buy. RH_MIN_HOLDERS<=0 disables it."""
        if RH_MIN_HOLDERS <= 0:
            return (False, None, "disabled")
        def _hc(s):
            hc = (s or {}).get("bs_holders_count")
            return hc if hc is not None else (s or {}).get("bs_n_holders_ranked")
        hc = _hc(self._bs_prewarm_read(token))
        if hc is None:                       # prewarm miss -> synchronous verify
            try:
                from core.rh_blockscout import blockscout_stamp
                hc = _hc(blockscout_stamp(token, pool_addr=pool) or {})
            except Exception as e:
                return (True, None, f"verify-failed:{type(e).__name__}")
        if hc is None:
            return (True, None, "unverifiable(fail-closed)")
        if hc < RH_MIN_HOLDERS:
            return (True, hc, f"holders={hc}<{RH_MIN_HOLDERS}")
        return (False, hc, f"holders={hc}")

    def _token_gone(self, token) -> bool:
        """True iff the wallet now holds ~0 of `token` — the position was
        SIPHONED away (a honeypot backdoor drained it right after the buy, the
        CCPEPE class). Keyless on-chain read via the shared quote executor.
        FAIL to FALSE: an unverifiable balance keeps the normal retry behaviour
        (never force a false total-loss booking on a read we couldn't make)."""
        try:
            w = os.environ.get("RH_WALLET_ADDRESS")
            if not w:
                return False
            return float(self._executor().token_balance(token, w)) <= 0
        except Exception:
            return False

    def _live_buy_leg(self, bot_id, pool, token, size_usd, t_decide, t_quote,
                      now):
        """Execute ONE live buy through RhLiveExecutor (routing glue). The
        paper decision machinery upstream is untouched — this replaces only
        the FILL. Returns {px, qty, tel, gas_usd} on a decoded landed fill,
        else None (the error is already ledgered via _log_live_error and the
        caller books NOTHING for this racer)."""
        # ── ANTI-RUG holder-base floor: REFUSE tiny-holder-base rugs (fail-
        # closed). The single highest-value pre-buy rug filter (see RH_MIN_
        # HOLDERS). Runs before any money moves.
        h_block, h_n, h_reason = self._holder_floor_verdict(token, pool)
        if h_block:
            self._log_live_error("buy", bot_id, pool, token,
                                 RuntimeError(f"holder-floor {h_reason}"),
                                 "holder_floor_block", now)
            print(f"[rh-live] BUY BLOCKED {bot_id} {token[:10]} "
                  f"rug-gate holder-floor ({h_reason})", flush=True)
            return None
        t_sent = time.time()
        try:
            rec = self._live_executor().live_buy(token, size_usd,
                                                 self.feed.eth_price)
        except Exception as e:
            self._log_live_error("buy", bot_id, pool, token, e,
                                 classify_live_error(e), now)
            return None
        t_landed = time.time()
        tel = fill_telemetry(rec, t_decide, t_quote, t_sent, t_landed,
                             self._tx_landed_ts(rec.get("tx_signature")))
        qty_atomic = rec.get("amount_out") or rec.get("quoted_out") or 0
        px = rec.get("real_fill_price") or rec.get("decision_mid_price")
        if not qty_atomic or not px:
            # tx LANDED but the fill is undecodable: money spent, position
            # untrackable — the E1b class again (never book a guess).
            self._log_live_error(
                "buy", bot_id, pool, token,
                RuntimeError("landed fill undecodable "
                             f"tx={rec.get('tx_signature')}"),
                "unknown_spend", now)
            return None
        gas_usd = (float(rec.get("gas_cost_eth") or 0.0)
                   * float(self.feed.eth_price or 0.0))
        _append(LIVE_FILLS, {"leg": "buy", "ts": iso_utc(now),
                             "bot_id": bot_id, "pool": pool, "token": token,
                             "usd": size_usd, **tel})
        print(f"[rh-live] BUY {bot_id} {token[:10]} ${size_usd:.0f} "
              f"discovery->fill={tel.get('decision_to_landed_ms')}ms "
              f"fill_vs_quote={tel.get('fill_vs_quote_pct')}% "
              f"gas=${gas_usd:.3f} exec_lat={tel.get('exec_latency_ms')}ms "
              f"tx={tel.get('tx')}", flush=True)
        return {"px": px, "qty": qty_atomic / 10 ** self._token_decimals(token),
                "tel": tel, "gas_usd": gas_usd}

    def _token_decimals(self, token: str) -> int:
        if token not in self.decimals:
            try:
                self.decimals[token] = self._executor().token_decimals(token)
            except Exception:
                self.decimals[token] = 18
        return self.decimals[token]

    def _est_token_out(self, pool: str, token: str, eth_in_wei: int):
        """Estimated atomic token output for eth_in_wei, from the pool's most
        recent quote px (ETH/token). Feeds the RH_RT_COMBINED single-POST
        round trip's SELL leg only (the rt-cost gate input) — never the booked
        fill. None when there is no px basis (caller uses the exact path)."""
        s = self.prices.get(pool)
        if not s:
            return None
        px = s[-1][1]
        if not px or px <= 0:
            return None
        try:
            dec = self._token_decimals(token)
            est = int((eth_in_wei / 1e18) / px * (10 ** dec))
            return est if est > 0 else None
        except Exception:
            return None

    def _honeypot_ok(self, token: str, pool: str = None) -> bool:
        # NOTE (2026-07-14): a transfer-graph "siphon drainer" signal was tried
        # and REJECTED — the top high-fan-in receiver on CCPEPE was the legit
        # UniversalRouter, so the signal flags routers, not siphons (false
        # positives that would block good tokens). Do NOT re-add without a
        # feature that separates routers/pools from real drainers.
        v = self.honeypot.get(token)
        if v is None:
            from core.rh_honeypot import simulate_sell
            v = simulate_sell(token, executor=self._executor())
            self.honeypot[token] = v
            if not v.get("sellable"):
                print(f"[rh-paper] HONEYPOT BLOCK {token[:10]} "
                      f"({v.get('reason','')[:60]})", flush=True)
        return bool(v.get("sellable"))

    def _drain(self, now: float):
        while True:
            try:
                pool, row = self.q.get_nowait()
            except queue.Empty:
                return
            row["_epoch"] = now  # seen time; good enough for 30s flow windows
            # regime layer: feed-wide demand-composition window sees EVERY
            # tape row (O(1) ingest, pure in-memory)
            self.comp.ingest(now, pool, row.get("kind"),
                             row.get("volume_usd"))
            # candidate-factory PROVEN-VOLUME fact: lifetime observed USD
            # volume per pool (persisted). For pools discovered at creation
            # (the young bands) this IS session-from-launch volume -- the
            # winner-delta's vol_pre / the mine's cum_eth axis.
            self.cum_vol[pool] = (self.cum_vol.get(pool, 0.0)
                                  + float(row.get("volume_usd") or 0))
            # session-anchor test at FIRST tape row: a pool whose tape starts
            # within SESSION_ANCHOR_MAX_AGE_H of creation is naturally
            # creation-anchored (missed volume bounded to that window even
            # when the seed backfill failed). Later anchoring can still come
            # from the seed in _note_px.
            if pool not in self.first_tape_age:
                w0 = self.feed.watch.get(pool)
                a0 = self._pool_age_h(w0) if w0 else None
                self.first_tape_age[pool] = a0
                if (a0 is not None and a0 <= SESSION_ANCHOR_MAX_AGE_H
                        and pool not in self.session_anchor):
                    self.session_anchor[pool] = True
            buf = self.tape.setdefault(pool, [])
            buf.append(row)
            # 2000-row cap (was 400): the runner_score exit stamp reads a
            # 10-min decision window + 10-min pre-run baseline — hot pools
            # burn >400 rows in that span and the stamp would go blind.
            if len(buf) > 2000:
                del buf[:1000]
            self.last_trade[pool] = now

    def _quote_hot(self, now: float):
        """Refresh quote-derived price: OPEN POSITIONS FIRST and unbudgeted
        (exit-blindness fix, 2026-07-10 trail-width analysis: positions were
        sorted into the shared budget by trade recency, so a quiet position
        could be crowded out of quotes exactly when its exit mattered —
        LOCKIN gapped through its trail to the hard stop). Entry candidates
        then fill the remaining budget.

        FLEET: held pools = the UNION across configs, quoted ONCE each (see
        _held_pools for the shared-price approximation when two configs hold
        the same pool). Entry candidates are also quoted once for the whole
        fleet — the quote budget does NOT scale with the roster size."""
        held = self._held_pools()   # pool -> largest remaining qty
        hot = [p for p, t in self.last_trade.items()
               if now - t <= HOT_TTL_S and p in self.feed.watch
               and p not in held]
        hot.sort(key=lambda p: -(self.last_trade.get(p, 0)))
        budget = max(0, MAX_HOT_QUOTES - len(held))
        for pool in list(held) + hot[:budget]:
            token = self._token_for(pool)
            if not token:
                continue
            try:
                rem_qty = held.get(pool)
                if rem_qty is not None:
                    # EXIT-IMPACT FIX (2026-07-10): held pools tick on the
                    # SELL-side EXECUTABLE price of our actual remaining size
                    # — TP/stop/bail thresholds then fire on what we'd GET,
                    # not on an optimistic buy-side probe (decisions were
                    # landing 3-14pp above fills in thin books).
                    dec = self._token_decimals(token)
                    q = self._executor().quote_sell(
                        token, int(rem_qty * 10 ** dec))
                    px = ((q.amount_out / 1e18) / rem_qty
                          if (q and q.amount_out and rem_qty > 0) else 0.0)
                    if q and q.amount_out:
                        self.n_quotes += 1
                    if px > 0:
                        self._note_px(pool, now, px)
                    continue
                # ARM-TIME RUG-GATE PREWARM: this pool is a live entry
                # candidate (recently traded, watched, not held) — kick the
                # cheap Blockscout holder prewarm on a daemon thread so the
                # concentration verdict is warm BEFORE the entry can fire.
                # Deduped/bounded/fail-open; adds ZERO latency to this quote.
                self._prewarm_rug(pool, token, now)
                q = self._executor().quote_buy(token, int(ENTRY_USD / max(
                    self.feed.eth_price or 1e9, 1e-9) * 1e18))
                if q and q.amount_out:
                    self.n_quotes += 1
                    px = price_from_quote(q.amount_in, q.amount_out,
                                          self._token_decimals(token))
                    if px > 0:
                        self._note_px(pool, now, px)
            except Exception as e:
                print(f"[rh-paper] quote {pool[:10]} {type(e).__name__}",
                      flush=True)

    def _note_px(self, pool: str, now: float, px: float):
        """Record one quote-derived price sample: rolling series (600-cap),
        FIRST-SEEN px (arc basis, candidate-factory) and pop detection over
        the refreshed series (one pop event per POP_COOLDOWN_S).

        SESSION SEED (2026-07-12 factory no-fire fix): on the pool's FIRST
        live quote, the feed's creation-backfill (watch[pool]['session_seed'])
        is merged in — first_px becomes the pool's true first print (the
        mine's arc anchor), the dip window is pre-loaded with the rescaled
        creation-era prints, and cum_vol gains the creation->promotion volume
        the watch filter never taped (tiny same-cycle overlap with the first
        polled swaps accepted as bounded). `pool not in first_px` is the
        once-guard: first_px persists, so a restart never re-applies the seed."""
        s = self.prices.setdefault(pool, [])
        if pool not in self.first_px:
            seed = (self.feed.watch.get(pool) or {}).get("session_seed")
            m = merge_session_seed(seed, px, now) if seed else None
            if m:
                fp, pts, cum_eth = m
                if pts and not s:
                    s.extend(pts)
                if fp:
                    self.first_px[pool] = fp
                self.cum_vol[pool] = (
                    self.cum_vol.get(pool, 0.0)
                    + cum_eth * float(self.feed.eth_price or 0.0))
                self.session_anchor[pool] = True
        s.append((now, px))
        if len(s) > 600:
            del s[:300]
        if pool not in self.first_px:
            self.first_px[pool] = px
        mag = pop_fired(s, now)
        if mag is not None:
            last = self.pop_book.get(pool)
            if last is None or (now - last[0]) > POP_COOLDOWN_S:
                self.pop_book[pool] = (now, round(mag, 1))

    def _sample_liq(self, now: float):
        """Feed the lp-drain tracker from the maintenance liq refresher."""
        for pool in set(list(self.last_trade) + list(self._held_pools())):
            w = self.feed.watch.get(pool)
            if not w:
                continue
            liq = float(w.get("liq") or 0)
            if liq <= 0:
                continue
            h = self.liq_hist.setdefault(pool, [])
            if not h or h[-1][1] != liq or now - h[-1][0] > 60:
                h.append((now, liq))
                if len(h) > 200:
                    del h[:100]

    def _pool_age_h(self, w):
        try:
            return self.feed.age_h(w["created_block"])
        except Exception:
            return None  # unknown age -> gate has no signal (fail-open)

    def _mfr_active_now(self, now: float) -> int:
        """Manufacturer-active regime read (2026-07-21 flat-camouflage mine).
        1 when the recent entry stream is committing to corpses at a rate
        that says an operator is staging pumps in this window — the FLAT-
        window leak the drift sensor cannot see. Reads the fraction of the
        trailing 4h fleet entries whose token has SINCE been marked dead by
        the sellability sweep. FAIL-OPEN: needs a fresh dead set (<36h) and
        >=15 samples, else 0 (never blocks on thin/stale data)."""
        if not (DEAD_TOKENS and (now - DEAD_TOKENS_TS) <= DEAD_TOKENS_MAX_AGE_S):
            return 0
        recent = [t for (ts_, t) in self._recent_entries if now - ts_ <= 14400]
        if len(recent) < 15:
            return 0
        into_dead = sum(1 for t in recent if t in DEAD_TOKENS)
        return 1 if (into_dead / len(recent)) > 0.08 else 0

    def _distributor_active(self, pool: str) -> int:
        """Get-ahead doctrine SHADOW (2026-07-21): 1 if any watched
        distributor wallet has printed a SELL in this pool's tape (their
        first-sell caught 93% of distribution at 0s delay). Prefix match
        (the doctrine gave address prefixes; full addresses = the pending
        funding-parent data job). Never gates — annotation for the n>=30
        corpse/net-$ grade once the entry<->tape join is fixed."""
        if not DISTRIBUTOR_WATCH:
            return 0
        try:
            for r in self.tape.get(pool, []):
                if r.get("kind") != "sell":
                    continue
                mk = str(r.get("maker") or "").lower()
                if any(mk.startswith(p) for p in DISTRIBUTOR_WATCH):
                    return 1
        except Exception:
            pass
        return 0

    def _recycled_flow_flags(self, pool: str, now: float,
                             window_s: float = 600.0):
        """Entry memo #6 SHADOW stamp (never blocks): buy-side authenticity
        of the trailing tape window. wash_n = makers printing BOTH sides;
        rt_buy_usd = buy volume from makers with a PRIOR sell in the tracked
        tape (round-trippers). None when the tape has no makers (quiet tapes
        PASSED in the verified analysis — absence of flow is not a flag)."""
        try:
            rows = self.tape.get(pool, [])
            buys_by, sells_by = {}, {}
            first_sell_ts = {}
            for r in rows:
                mk, ts_ = r.get("maker"), r.get("_epoch")
                if not mk or ts_ is None:
                    continue
                v = float(r.get("volume_usd") or 0)
                if r.get("kind") == "sell":
                    first_sell_ts.setdefault(mk, ts_)
                if now - ts_ > window_s:
                    continue
                if r.get("kind") == "buy":
                    buys_by[mk] = buys_by.get(mk, 0.0) + v
                elif r.get("kind") == "sell":
                    sells_by[mk] = sells_by.get(mk, 0.0) + v
            if not buys_by and not sells_by:
                return None
            wash = set(buys_by) & set(sells_by)
            rt_usd = sum(v for mk, v in buys_by.items()
                         if mk in first_sell_ts
                         and first_sell_ts[mk] < now - 1)
            return {"wash_n": len(wash),
                    "wash_usd": round(sum(buys_by[m] + sells_by[m]
                                          for m in wash), 2),
                    "rt_buy_usd": round(rt_usd, 2),
                    "n_buyers": len(buys_by)}
        except Exception:
            return None   # shadow stamp must never break the buy path

    # ── regime detection (pool-discovery rate -> bot era vs human era) ──────
    def _track_new_pools(self, now: float):
        """Count NEWLY-discovered candidate/watched pools. First call seeds
        the known set from the startup backfill (those are not fresh
        discoveries) and starts the warm-up clock."""
        pools = (list(getattr(self.feed, "cand", None) or {})
                 + list(self.feed.watch))
        if self._regime_known is None:
            self._regime_known = set(pools)
            self._regime_t0 = now
            return
        for p in pools:
            if p not in self._regime_known:
                self._regime_known.add(p)
                self._regime_seen.append(now)
        cutoff = now - 3600.0
        i = 0
        while i < len(self._regime_seen) and self._regime_seen[i] < cutoff:
            i += 1
        if i:
            del self._regime_seen[:i]

    def new_pools_per_hour(self, now: float):
        """Observed discovery rate extrapolated to /hour; None during the
        REGIME_MIN_UPTIME_S warm-up (regime_hour_ok fails open on None)."""
        if self._regime_t0 is None:
            return None
        uptime = now - self._regime_t0
        if uptime < REGIME_MIN_UPTIME_S:
            return None
        window = min(3600.0, uptime)
        return len(self._regime_seen) * 3600.0 / max(window, 1.0)

    # ── sell-path canary (RH analog of the Solana 07-10 incident rule) ──────
    def _canary_tick(self, now: float):
        """Periodic exit-quote health probe through the EXACT sell-path code
        (quote_sell -> batch quoter) on every open position (transport probe
        when flat). Writes the cross-process halt flag the entry path and
        core.rh_live_execution.RhLiveExecutor.buys_halted both read. Canary
        mode OFF (paper default) -> pure no-op (byte-identical lane)."""
        if not rh_live.canary_mode_on():
            return
        if now - self._last_canary_ts < rh_live.canary_interval_s():
            return
        self._last_canary_ts = now
        if self._canary is None:
            self._canary = rh_live.RhSellCanary()
        holdings = []
        for pool, qty in self._held_pools().items():
            token = self._token_for(pool)
            if token and qty > 0:
                holdings.append(
                    (token, int(qty * 10 ** self._token_decimals(token))))
        ok = rh_live.probe_exit_quotes(self._executor(), holdings)
        self._canary.record(ok, now)
        self._canary.write_flag()
        if not self._canary.healthy(now):
            print(f"[rh-paper] SELL-CANARY RED — buys halted "
                  f"({self._canary.status_line(now)})", flush=True)

    def _sweep_orphan_dust(self, now: float):
        """Sweep any on-chain token the lane holds NO open position in — the
        RESIDUAL/DUST class: a mid-ladder tail orphaned when a redeploy re-arm
        loses lane state (Railway ephemeral FS), or a partial close left behind.
        Nothing else clears it, so it piles up in the hot wallet (bcat kept
        reappearing, 2026-07-14). Live-gated. Open positions are NEVER touched
        (excluded via _held_pools, which restore_state() populates before the
        loop). Each SELLABLE orphan is sold "all" through the exact live path @
        10% slippage so it fills; routeless/dead bags (rugs, e.g. GOATAI) are
        skipped and logged once. GFOF/Cmoon excluded by the enumerator. Never
        raises — a sweep error must not break the strategy loop."""
        try:
            gate_open, _ = rh_live.rh_live_gate()
            if not gate_open:
                return                              # paper = no on-chain bags
            ex = self._executor()
            wallet = getattr(ex, "wallet_address", None) or \
                os.environ.get("RH_WALLET_ADDRESS")
            if not wallet:
                return
            held = rh_live._held_meme_positions(wallet, ex, self.feed.eth_price)
            if not held:
                return
            open_tokens = set()
            for pool in self._held_pools():
                tok = self._token_for(pool)
                if tok:
                    open_tokens.add(tok.lower())
            if not hasattr(self, "_dust_skip"):
                self._dust_skip = set()             # routeless bags already logged
            swept = 0
            for pos in held:
                tok = (pos.get("token") or "").lower()
                if not tok or tok in open_tokens:
                    continue                        # active position — never sweep
                # ROUTELESS (quote returned $0 -> dead rug, e.g. GOATAI) can't be
                # sold; skip-list it once. ANY routed value (even $0.01 bcat dust)
                # IS cleared — a clean wallet, not a "reduced to a penny" wallet
                # (RH gas is ~free). Failed low-value sells just retry next cycle.
                if (pos.get("value_usd") or 0) <= 0:
                    if tok not in self._dust_skip:
                        self._dust_skip.add(tok)
                        print(f"[rh-paper] DUST-SWEEP skip {pos.get('sym')} "
                              f"routeless/dead ($0)", flush=True)
                    continue
                try:
                    rec = self._live_executor().live_sell(
                        pos["token"], "all", max_slippage_bps=1000)
                    tx = rec.get("tx_signature") if isinstance(rec, dict) else "?"
                    print(f"[rh-paper] DUST-SWEEP sold orphan {pos.get('sym')} "
                          f"~${pos.get('value_usd')} tx={tx}", flush=True)
                    swept += 1
                except Exception as e:
                    print(f"[rh-paper] DUST-SWEEP {pos.get('sym')} failed: "
                          f"{type(e).__name__}: {str(e)[:100]}", flush=True)
            if swept:
                print(f"[rh-paper] DUST-SWEEP: {swept} orphan bag(s) cleared",
                      flush=True)
        except Exception as e:
            print(f"[rh-paper] DUST-SWEEP error: {type(e).__name__}: "
                  f"{str(e)[:100]}", flush=True)

    def _consider_entries(self, now: float):
        # sell-path canary halt: no working exit -> no new entries (buys
        # only; exits in _manage_exits are NEVER gated by this). Paper
        # default: rh_canary_entry_block() is None (canary mode off).
        canary_block = rh_live.rh_canary_entry_block(now)
        if canary_block:
            for st in self.state.values():
                st.block_hist[canary_block] = (
                    st.block_hist.get(canary_block, 0) + 1)
            return
        hour = time.gmtime(now).tm_hour
        self._track_new_pools(now)   # discovery-rate tracker (regime STAMP;
        # the v1 hour gate is age-band keyed and no longer consumes the rate)
        # cross-sibling exclusion sets, built ONCE per tick per grouped racer
        excl_keys = {b.bot_id: sibling_exclusion_keys(
                         list(self.state.values()), b.bot_id,
                         b.exclusion_group, now, b.sibling_stop_window_s)
                     for b in self.bots if b.exclusion_group}
        # PHOENIX fact: latest FLEET loss-stop per pool (any bot's exit_book),
        # built once per tick; each phoenix bot applies its own window.
        fleet_loss_stops = {}
        for st_ in self.state.values():
            for pool_, info_ in st_.exit_book.items():
                if info_.get("loss"):
                    ts_ = float(info_.get("ts") or 0)
                    if ts_ > fleet_loss_stops.get(pool_, (0.0, None))[0]:
                        fleet_loss_stops[pool_] = (ts_, info_.get("px"))
        for pool, series in list(self.prices.items()):
            w = self.feed.watch.get(pool)
            if not w:
                continue
            states = [st for st in self.state.values()
                      if pool not in st.pos_meta]
            if not states:
                continue
            # ── shared per-POOL facts: computed at most ONCE per candidate,
            # regardless of roster size (the quote/CPU budget invariant) ────
            rows = self.tape.get(pool, [])
            d = dip_pct(series, now)
            micro = retrace_micro_eval(
                [{"kind": r.get("kind"), "volume_usd": r.get("volume_usd"),
                  "ts": r.get("_epoch")} for r in rows], now)
            buys, sells = flow_sums(rows, now)
            liq = float(w.get("liq") or 0)
            age_h = self._pool_age_h(w)
            drain = lp_drain_pct(self.liq_hist.get(pool, []), now)
            # launch_strength shared facts (pure CPU on in-memory data)
            rise_open = rise_from_open_pct(series, now)
            b120, s120 = flow_sums(rows, now, window_s=120.0)
            inflow_120s = b120 - s120
            # candidate-factory shared facts (2026-07-12): demand breadth
            # (buy prints in the 30s window), launch-arc position vs the
            # first-seen quote px, last detected pop (pop_book fed by
            # _note_px on every quote sample)
            n_buys_30s = sum(1 for r in rows
                             if r.get("kind") == "buy"
                             and now - (r.get("_epoch") or 0)
                             <= DEMAND_WINDOW_S)
            arc = arc_pct(self.first_px.get(pool),
                          series[-1][1] if series else None)
            anchored = bool(self.session_anchor.get(pool))
            pop_last = self.pop_book.get(pool)
            pop_ts = pop_last[0] if pop_last else None
            # vol_m5 (tape liveness) — shared fact for the depth re-entry gate
            vol_m5 = sum(float(r.get("volume_usd") or 0) for r in rows
                         if now - (r.get("_epoch") or 0) <= 300)
            cand_token = self._token_for(pool)  # dict lookups only (cheap)
            # dead-set block (shared fact — applies to every arm equally)
            dead_block = bool(
                cand_token and cand_token in DEAD_TOKENS
                and (now - DEAD_TOKENS_TS) <= DEAD_TOKENS_MAX_AGE_S)
            mfr_active = self._mfr_active_now(now)   # shared regime read
            self.n_evals += 1
            # ── per-CONFIG thresholds against those shared facts ────────────
            entering = []
            for st in states:
                bot = st.bot
                demand = demand_ok(buys, sells, bot.demand_min_buy_usd,
                                   bot.demand_net_required)
                cooldown_ok = ((now - st.last_exit.get(pool, 0))
                               > bot.reentry_cooldown_s)
                trig = (launch_trigger_blocks(rise_open, inflow_120s,
                                              bot.launch_min_inflow_usd)
                        if bot.entry_mode == "launch_strength" else None)
                # per-racer aged-cohort verdicts (all default-off for the
                # pre-existing scalp fleet)
                extra = []
                if dead_block:
                    extra.append("dead_token")
                if bot.mfr_veto and mfr_active:
                    extra.append("mfr_veto")
                if bot.pool_loss_lockout_n is not None:
                    _ll = [t for t in st.pool_loss_closes.get(pool, ())
                           if now - t <= 6 * 3600.0]
                    if len(_ll) >= bot.pool_loss_lockout_n:
                        extra.append("pool_loss_lockout")
                if bot.knife_skip:
                    # verified knife signature (memo #2): seller not finished.
                    if sells > buys:
                        extra.append("knife_sell_dom")
                    elif (d is not None and d > -8.0
                          and isinstance(arc, (int, float)) and arc < -15.0):
                        extra.append("knife_shallow_deep")
                if bot.phoenix_entry:
                    # inverted sibling-stop rule: REQUIRE a recent fleet
                    # loss-stop on this pool (the bottom-marker)
                    ts_stop, stop_px = fleet_loss_stops.get(pool, (0.0, None))
                    if not ts_stop or (now - ts_stop) > bot.phoenix_window_s:
                        extra.append("no_recent_stop")
                    elif bot.phoenix_reclaim_pct is not None:
                        # PHOENIX2 (2026-07-20 postmortem): enter on RECLAIM,
                        # never on further weakness. v1 required a further dip
                        # below the stop — conditioning on continued decline
                        # selected the never-bouncers (21% exposure-weighted
                        # wr). Reclaim = price back ABOVE the stop print by
                        # this % — the bounce has STARTED. Fail-closed when
                        # either price is unknown.
                        px_now = series[-1][1] if series else None
                        if not (px_now and stop_px and px_now
                                >= stop_px * (1 + bot.phoenix_reclaim_pct
                                              / 100.0)):
                            extra.append("no_reclaim")
                if bot.exclusion_group:
                    ek = excl_keys.get(bot.bot_id) or set()
                    if pool in ek or (cand_token and cand_token in ek):
                        extra.append("sibling_excl")
                if bot.reentry_min_dip_pct is not None:
                    info = st.exit_book.get(pool) or {}
                    had_loss = bool(
                        info.get("loss")
                        and (now - float(info.get("ts") or 0))
                        <= REENTRY_LOSS_WINDOW_S)
                    rb = reentry_depth_gate(had_loss, d, vol_m5,
                                            bot.reentry_min_dip_pct,
                                            bot.reentry_min_vol_m5_usd)
                    if rb:
                        extra.append(rb)
                if bot.regime_hours and not regime_hour_ok(hour, age_h):
                    extra.append("hour_regime")
                db = daily_buys_block(st.day_buys, bot.max_buys_per_day)
                if db:
                    extra.append(db)
                # candidate-factory per-racer gates (all None = no-ops).
                # Session-anchored facts (arc / cum-vol) are consulted ONLY
                # when the pool is creation-anchored or the racer doesn't
                # require it; an unanchored pool under require_session_anchor
                # blocks with the single EXPLICIT untracked_session reason
                # (its arc/vol values are structurally wrong, not weak).
                sa = session_anchor_block(anchored, bot.require_session_anchor)
                gates = [dip_depth_block(d, bot.dip_max_depth_pct),
                         buys_breadth_block(n_buys_30s, bot.min_buys_30s),
                         pop_recency_block(pop_ts, now,
                                           bot.require_pop_within_s)]
                if sa:
                    gates.append(sa)
                else:
                    gates += [arc_block(arc, bot.max_arc_pct),
                              proven_vol_block(self.cum_vol.get(pool, 0.0),
                                               bot.min_session_vol_usd)]
                for blk in gates:
                    if blk:
                        extra.append(blk)
                # honeypot LAST (network call), only when a config passes
                v = entry_verdict(
                    d, demand, micro, liq, True,
                    len(st.pos_meta), cooldown_ok, st.daily_pnl_usd,
                    age_h=age_h, drain_pct=drain,
                    dip_trigger_pct=bot.dip_trigger_pct,
                    min_liq_usd=bot.min_liq_usd,
                    min_pool_age_h=bot.min_pool_age_h,
                    max_pool_age_h=bot.max_pool_age_h,
                    max_concurrent=bot.max_concurrent,
                    hour_ok=hour_allowed(bot.allowed_hours_utc, hour),
                    bite_block=bite_gate(bot.first_touch_only,
                                         bot.max_bites_per_token,
                                         st.bites.get(pool, 0)),
                    trigger_blocks=trig, extra_blocks=extra)
                if v["enter"]:
                    entering.append(st)
                else:
                    for b in v["blocks"]:
                        st.block_hist[b] = st.block_hist.get(b, 0) + 1
            # same-tick sibling arbitration: one racer per token per group
            entering, dropped = dedupe_group_entries(entering)
            for st in dropped:
                st.block_hist["sibling_excl"] = (
                    st.block_hist.get("sibling_excl", 0) + 1)
            if not entering:
                continue
            token = cand_token
            # RUG GATE (concentration dump-class, CASHCATWIF/CASHCATGAME): a
            # 0-latency read of the arm-time Blockscout prewarm verdict. ENFORCE
            # -> block the whole pool for every entering config (it is a per-TOKEN
            # property); SHADOW -> stamp the verdict on the fill only. FAIL-OPEN:
            # no warm bs_ data -> block=False (never a veto on absent data).
            rug_v = self._rug_gate_lookup(token)
            if (rug_v is not None and rug_v.get("rug_gate_block")
                    and rug_gate_enforcing()):
                for st in entering:
                    st.block_hist["rug_gate"] = (
                        st.block_hist.get("rug_gate", 0) + 1)
                self._log_rug_block(pool, token, w, rug_v, now,
                                    [st.bot.bot_id for st in entering])
                continue
            if not token or not self._honeypot_ok(token, pool):
                continue
            self._paper_buy(pool, token, w, d, micro, now, rows,
                            states=entering, age_h=age_h, rug_v=rug_v)

    def _paper_buy(self, pool, token, w, dip, micro, now, rows, states=None,
                   age_h=None, rug_v=None):
        """Fill the entry for every entering config off ONE buy quote + ONE
        rt-cost sell quote: the fill price is a per-POOL fact (every racer
        bets the same $25), so the fleet does not multiply QuoterV2 calls."""
        if states is None:   # pre-fleet call shape (tests): all non-holders
            states = [st for st in self.state.values()
                      if pool not in st.pos_meta]
        if not states:
            return
        t_decide = time.time()
        trigger_lag = rows[-1].get("lag_secs") if rows else None
        eth_in_wei = int((ENTRY_USD / self.feed.eth_price) * 1e18)
        # QUOTE-LEG LATENCY (2026-07-13): the stamped lat_quote_s (median
        # ~1.06s, the leg that pushes 51% of fills over the 1.71s Solana-parity
        # budget) is TWO sequential batched QuoterV2 POSTs — the buy quote then
        # the RT-cost sell quote of the buy's EXACT output. They are dependent,
        # so RH_RT_COMBINED collapses them to ONE POST via an estimated sell
        # amount (the booked fill still uses the exact buy quote; only the
        # rt-cost gate reads the estimate). Default OFF = the exact two-POST
        # path below, byte-identical to before. t_buy_done splits the two legs
        # for the ledger (lat_quote_buy_s / lat_quote_rt_s attribution).
        q = None
        rt_cost = 100.0
        t_buy_done = None
        quote_mode = "split"
        if _rt_combined():
            est = self._est_token_out(pool, token, eth_in_wei)
            rr = None
            if est:
                try:
                    rr = self._executor().quote_roundtrip_batched(
                        token, eth_in_wei, est)
                except Exception:
                    rr = None
            if rr is not None:
                q, eth_back = rr
                t_buy_done = time.time()
                quote_mode = "combined"
                if q and q.amount_in:
                    rt_cost = (1.0 - eth_back / q.amount_in) * 100.0
        if q is None:   # combined OFF or unavailable -> exact two-quote path
            try:
                q = self._executor().quote_buy(token, eth_in_wei)
            except Exception as e:
                print(f"[rh-paper] buy-quote failed {pool[:10]}: {e}",
                      flush=True)
                return
            if not q or not q.amount_out:
                return
            t_buy_done = time.time()
            # ROUND-TRIP COST GATE (exit-impact leak): quote the sell of exactly
            # what this buy returns, NOW. If the pool charges more than a
            # config's max_rt_cost_pct for the round trip, friction eats the
            # edge — no entry for THAT config. Uses real quotes (fee + impact
            # both ways), not heuristics; quoted ONCE, gated per config.
            # Fail-closed on a reverted sell quote (one-way pool = honeypot
            # signature anyway).
            try:
                sq = self._executor().quote_sell(token, q.amount_out)
                eth_back = (sq.amount_out if (sq and sq.amount_out) else 0)
                rt_cost = (1.0 - eth_back / q.amount_in) * 100.0
            except Exception:
                rt_cost = 100.0
        if not q or not q.amount_out:
            return
        takers = []
        for st in states:
            if rt_cost > st.bot.max_rt_cost_pct:
                st.block_hist["rt_cost"] = st.block_hist.get("rt_cost", 0) + 1
            else:
                takers.append(st)
        if not takers:
            print(f"[rh-paper] RT-COST BLOCK {pool[:10]} "
                  f"round-trip {rt_cost:.1f}%", flush=True)
            return
        t_fill = time.time()
        dec = self._token_decimals(token)
        px = price_from_quote(q.amount_in, q.amount_out, dec)
        qty = q.amount_out / 10 ** dec
        lat_total = (None if trigger_lag is None
                     else round(trigger_lag + (t_fill - t_decide), 2))
        lat_quote = round(t_fill - t_decide, 3)
        # per-leg attribution (2026-07-13): buy quote vs RT-cost sell quote.
        # combined mode books both in one POST -> the rt leg reads ~0.
        lat_quote_buy = (round(t_buy_done - t_decide, 3)
                         if t_buy_done is not None else None)
        lat_quote_rt = (round(t_fill - t_buy_done, 3)
                        if t_buy_done is not None else None)
        # REGIME STAMP (core/rh_regime, fleet-wide ALWAYS): the shared parts
        # (hour, discovery rate, 30-min feed composition, ETH px, age band)
        # are per-POOL/tick facts computed once; the expectancy DIAL is
        # per-racer (its own realized record). Pure in-memory — no RPC.
        comp_snap = self.comp.snapshot(now)
        npph = self.new_pools_per_hour(now)
        hour_utc = time.gmtime(now).tm_hour
        # DEAD-TAPE shadow gate (2026-07-13): the only regime-robust runner/dier
        # entry separator found was n_trades_60 (live vs dead tape in the 60s
        # pre-entry window). SHADOW-ONLY — computed once per entry event, stamped
        # on every racer's row, NEVER blocks or resizes. Forward-grades on the
        # exact stamped value as outcomes accrue. See _deadtape_shadow().
        dt_shadow = _deadtape_shadow()
        dt_min = _deadtape_min_trades()
        nt60 = (micro.get("sell") or {}).get("n_trades_60")
        deadtape_skip = (bool(nt60 < dt_min)
                         if (dt_shadow and nt60 is not None) else None)
        if dt_shadow and deadtape_skip:
            print(f"[rh-shadow] deadtape would_skip {w['sym']:<12} "
                  f"n_trades_60={nt60} < {dt_min} (dip={dip:.1f}% "
                  f"liq=${w.get('liq') or 0:,.0f})", flush=True)
        for st in takers:
            # LIVE FILL PROBE routing: when the four conditions hold for this
            # racer, the FILL comes from RhLiveExecutor (real tx); the paper
            # decision machinery above is identical either way. Paper racers
            # book the shared quote fill (scaled to their entry size).
            size_usd = (st.bot.entry_usd if st.bot.entry_usd is not None
                        else ENTRY_USD)
            if (st.bot.conviction_band and dip is not None
                    and st.bot.conviction_band[0] <= dip
                    <= st.bot.conviction_band[1]):
                size_usd *= st.bot.conviction_mult
            bot_px, bot_qty, live_leg = px, qty * (size_usd / ENTRY_USD), None
            if live_route_open(st.bot.bot_id):
                live_leg = self._live_buy_leg(st.bot.bot_id, pool, token,
                                              size_usd, t_decide, t_fill, now)
                if live_leg is None:
                    continue    # live leg refused/failed: book NOTHING
                bot_px, bot_qty = live_leg["px"], live_leg["qty"]
            st.pm.open_position(token=pool, entry_price=bot_px,
                                size_usd=size_usd, entry_time=now,
                                address=token)
            meta = {"qty_orig": bot_qty, "remaining_frac": 1.0,
                    "token": token, "sym": w["sym"],
                    "entry_px": bot_px, "entry_ts": now,
                    "usd_size": size_usd,
                    # fast LP-pull bail baseline (2026-07-13): liquidity AT
                    # ENTRY — the fixed reference the per-tick fast_liq_bail
                    # verdict compares current reserves against.
                    "entry_liq": w.get("liq")}
            if live_leg is not None:
                meta["live"] = True
                meta["tx_buy"] = live_leg["tel"].get("tx")
                meta["buy_gas_usd"] = live_leg["gas_usd"]
            st.pos_meta[pool] = meta
            st.n_entries += 1
            st.day_buys += 1
            st.bites[pool] = st.bites.get(pool, 0) + 1
            # feed the manufacturer-active read (capital-weighted: a pool the
            # fleet piles into counts more, which is correct). Trim to 4h.
            self._recent_entries.append((now, token))
            if len(self._recent_entries) > 3000:
                self._recent_entries = [
                    (t, tk) for (t, tk) in self._recent_entries
                    if now - t <= 14400]
            rec = {"ev": "buy", "ts": self._ledger_ts(now),
                   "bot_id": st.bot.bot_id, "pool": pool, "token": token,
                   "sym": w["sym"], "usd": size_usd, "price_eth": bot_px,
                   "qty": bot_qty,
                   "dip_pct": (round(dip, 2) if dip is not None else None),
                   "age_h": (round(age_h, 2) if age_h is not None else None),
                   "entry_mode": st.bot.entry_mode, "liq": w.get("liq"),
                   # Stamp the RAW demand-trajectory, not just the binary flags
                   # (2026-07-13, AxiS "you can always tell them apart"): the
                   # HOODBIRD-dies vs PONS-runs tell is the trade-flow SHAPE at
                   # entry (sell acceleration + net-flow persistence). We compute
                   # it and were discarding the gradient — keeping only the leaky
                   # binaries. Stamp the raw values so the runner/topper separator
                   # can be built and OOS-graded forward.
                   "micro": {
                       "avoid_block": micro.get("avoid_block"),
                       "flow_confirm": micro.get("flow_confirm"),
                       "sell_rate_60": (micro.get("sell") or {}).get("sell_rate_60"),
                       "sell_traj": (micro.get("sell") or {}).get("sell_traj"),
                       "n_trades_60": (micro.get("sell") or {}).get("n_trades_60"),
                       "cum_nf_60": (micro.get("flow") or {}).get("cum_nf_60"),
                       "pos_subwins": (micro.get("flow") or {}).get("pos_subwins"),
                       # DEAD-TAPE shadow gate (never blocks): would_skip = live
                       # tape flow (n_trades_60) below the floor. Only stamped
                       # when RH_DEADTAPE_SHADOW is on so paper rows stay
                       # byte-identical by default.
                       "deadtape_would_skip": deadtape_skip,
                       "deadtape_min_trades": (dt_min if dt_shadow else None),
                   },
                   # RECYCLED-FLOW SHADOW (2026-07-19 entry memo #6 — LOG
                   # ONLY, no gate: the finding verified only weak (93.5% of
                   # tape evidence was one day). Stamp per-entry so the
                   # cross-day regrade needs no new tape infra: over the
                   # trailing 600s of this pool's tape, (a) wash = makers on
                   # BOTH sides, (b) rt_buy_usd = buy volume from makers with
                   # a PRIOR sell in the tracked tape. If it confirms at
                   # n>=30 across >=3 tape days, it becomes the demand
                   # gates' fix (34/36 live entries were flagged).
                   "flow_flags": self._recycled_flow_flags(pool, now),
                   "dist_active": self._distributor_active(pool),
                   "mfr_active": self._mfr_active_now(now),
                   "lat_trigger_lag_s": trigger_lag,
                   "lat_quote_s": lat_quote,
                   "lat_quote_buy_s": lat_quote_buy,
                   "lat_quote_rt_s": lat_quote_rt,
                   "quote_mode": quote_mode,
                   "lat_total_s": lat_total, "fee_tier": q.fee,
                   "regime": regime_stamp(
                       hour_utc, npph, comp_snap,
                       dial=expectancy_dial(st.recent_realized),
                       eth_usd=self.feed.eth_price, age_h=age_h,
                       size_dial=expectancy_dial(self.fleet_realized))}
            if live_leg is not None:   # keys only on live rows: paper rows
                rec["live"] = True     # stay byte-identical to pre-probe
                rec["fill"] = live_leg["tel"]
            # RUG-GATE verdict on the fill (grading): present only when the gate
            # is not off (rug_v is None -> byte-identical). In ENFORCE mode a
            # booked fill always carries block=False (a blocking verdict never
            # reaches _paper_buy); in SHADOW mode it may carry block=True (the
            # would-block the grader scores) since shadow does not skip.
            if rug_v is not None:
                rec["rug_gate"] = {
                    "block": rug_v.get("rug_gate_block"),
                    "reason": rug_v.get("rug_gate_reason"),
                    "source": rug_v.get("rug_gate_source"),
                    "top1": rug_v.get("rug_gate_top1"),
                    "top10": rug_v.get("rug_gate_top10"),
                    "mode": rug_v.get("rug_gate_mode")}
            _append(LEDGER, rec)
            print(f"[rh-paper] BUY{' LIVE' if live_leg else ''}  "
                  f"{st.bot.bot_id:<16} {w['sym']:<12} "
                  f"${size_usd:.2f} "
                  f"dip={('%.1f%%' % dip) if dip is not None else '-'} "
                  f"lat_total={lat_total}s "
                  f"(trigger {trigger_lag}s + quote {lat_quote}s)", flush=True)
        self.save_state()
        # SHADOW rug-signal stamp: AFTER every fill is booked and persisted —
        # the entry path above never waits on it (fail-open, background).
        self._stamp_rug_signals(pool, token, w, now,
                                [st.bot.bot_id for st in takers])

    # ── rug-defense SHADOW stamper (2026-07-11 HOODLANA port) ───────────────
    def _rug_stamp_row(self, pool, token, sym, entry_ts, bot_ids,
                       created_block, dex, head_block):
        """Compute (or reuse a fresh cached) stamp and append the ledger row.
        Runs on the stamper thread; synchronous-callable in tests. FAIL-OPEN:
        any error prints and returns — never into a trading path."""
        try:
            cached = self._rug_cache.get(pool)
            if cached and (time.time() - cached[0]) < RUG_STAMP_CACHE_S:
                stamp, is_cached = cached[1], True
            else:
                with self._rug_lock:   # single-flight on the shared RPC
                    if self._rug_rpc is None:
                        self._rug_rpc = Rpc(self.feed.rpc.url)
                    stamp = compute_entry_stamp(
                        self._rug_rpc, pool, token,
                        created_block=created_block,
                        head_block=head_block, dex=dex)
                self._rug_cache[pool] = (time.time(), stamp)
                is_cached = False
            _append(LEDGER, {"ev": "rug_signals",
                             "ts": self._ledger_ts(time.time()),
                             "sym": sym, "bot_ids": list(bot_ids),
                             "entry_ts": iso_utc(entry_ts),
                             "cached": is_cached, **stamp})
            print(f"[rh-paper] rug-stamp {sym}: pool={stamp.get('pool_pct_of_supply')}% "
                  f"top1={stamp.get('top1_pct')}% top10={stamp.get('top10_pct')}% "
                  f"shoulder={stamp.get('shoulder_11_20_pct')}% "
                  f"lpEOA={stamp.get('lp_any_eoa_owner')} "
                  f"cost={stamp.get('cost')}{' (cached)' if is_cached else ''}",
                  flush=True)
        except Exception as e:
            print(f"[rh-paper] rug-stamp failed {pool[:10]}: "
                  f"{type(e).__name__}: {e}", flush=True)

    def _stamp_rug_signals(self, pool, token, w, entry_ts, bot_ids):
        """Spawn the SHADOW stamp worker for one booked entry. Never blocks:
        all RPC work happens on the daemon thread behind _rug_lock."""
        if not RUG_STAMP_ENABLED:
            return
        try:
            threading.Thread(
                target=self._rug_stamp_row,
                args=(pool, token, w.get("sym"), entry_ts, bot_ids,
                      w.get("created_block"), w.get("dex") or "v3",
                      self.feed.latest_block),
                daemon=True, name=f"rug-stamp-{pool[:8]}").start()
        except Exception as e:   # thread-spawn failure must not touch entries
            print(f"[rh-paper] rug-stamp spawn failed: {e}", flush=True)

    # ── ARM-TIME RUG-GATE PREWARM + ENFORCE (2026-07-13) ────────────────────
    # OFF the hot path: the prewarm fetches the cheap Blockscout holder verdict
    # on a daemon thread when a pool arms into the quoted watch set, so the
    # entry decision reads a WARM verdict from a pure dict (0 added latency).
    def _prewarm_fresh(self, hit, now) -> bool:
        """A cached prewarm entry is 'fresh' (skip re-fetch) if it is a good
        stamp within the 10-min TTL, or an empty/failed stamp still inside the
        short fail-retry window (so a Blockscout blip re-arms, not fails-open
        for the full TTL)."""
        if not hit:
            return False
        age = now - hit[0]
        ttl = (RUG_PREWARM_TTL_S if hit[1].get("bs_source_ok")
               else RUG_PREWARM_FAIL_TTL_S)
        return age < ttl

    def _prewarm_rug(self, pool, token, now):
        """ARM a Blockscout holder prewarm for one watch candidate. Non-blocking,
        deduped (per-token in-flight + TTL cache), and concurrency-bounded. No-op
        when the gate is off or prewarm is disabled. NEVER touches the hot path."""
        if not RUG_PREWARM_ENABLED or _rug_gate_mode() == "off" or not token:
            return
        k = token.lower()
        with self._bs_lock:
            if self._prewarm_fresh(self._bs_prewarm.get(k), now):
                return
            if k in self._bs_inflight:
                return
            if len(self._bs_inflight) >= MAX_BS_PREWARM_INFLIGHT:
                return
            self._bs_inflight.add(k)
        try:
            threading.Thread(target=self._prewarm_rug_worker, args=(pool, k),
                             daemon=True, name=f"rug-prewarm-{k[:8]}").start()
        except Exception as e:   # spawn failure must never touch the arm loop
            with self._bs_lock:
                self._bs_inflight.discard(k)
            print(f"[rh-paper] rug-prewarm spawn failed: {e}", flush=True)

    def _prewarm_rug_worker(self, pool, token):
        """Daemon worker: fetch the Blockscout bs_ stamp (fail-open — never
        raises) and store it as the warm verdict source. Also warms the
        blockscout_stamp internal cache the post-fill shadow stamper reuses."""
        stamp = {}
        try:
            from core.rh_blockscout import blockscout_stamp
            stamp = blockscout_stamp(token, pool_addr=pool) or {}
        except Exception as e:
            print(f"[rh-paper] rug-prewarm {token[:10]} "
                  f"{type(e).__name__}", flush=True)
        with self._bs_lock:
            self._bs_prewarm[token] = (time.time(), stamp)
            self._bs_inflight.discard(token)

    def _bs_prewarm_read(self, token) -> dict:
        """Pure, 0-latency read of the warm bs_ stamp for `token` ({} if absent
        or stale). This is the ONLY thing the entry path calls — never network."""
        if not token:
            return {}
        with self._bs_lock:
            hit = self._bs_prewarm.get(token.lower())
        if hit and (time.time() - hit[0]) < RUG_PREWARM_TTL_S:
            return dict(hit[1])
        return {}

    def _rug_gate_lookup(self, token):
        """The concentration rug verdict for a candidate token, read from the
        arm-time prewarm cache (0 latency, no network). None when the gate is
        off. FAIL-OPEN: no warm data -> rug_gate_verdict({}) => block=False."""
        if _rug_gate_mode() == "off":
            return None
        try:
            return rug_gate_verdict(self._bs_prewarm_read(token))
        except Exception:
            return None   # never let the gate raise into the entry path

    def _log_rug_block(self, pool, token, w, rug_v, now, bot_ids):
        """Emit the ENFORCED-block ledger row ONCE per pool (the entry re-arms
        every ~2s; one row per blocked pool keeps the grader join clean)."""
        if pool in self._rug_blocked_pools:
            return
        self._rug_blocked_pools.add(pool)
        try:
            _append(LEDGER, {"ev": "rug_gate_block",
                             "ts": self._ledger_ts(now), "pool": pool,
                             "token": token, "sym": w.get("sym"),
                             "bot_ids": list(bot_ids),
                             "rug_gate_block": rug_v.get("rug_gate_block"),
                             "rug_gate_reason": rug_v.get("rug_gate_reason"),
                             "rug_gate_source": rug_v.get("rug_gate_source"),
                             "rug_gate_top1": rug_v.get("rug_gate_top1"),
                             "rug_gate_top10": rug_v.get("rug_gate_top10"),
                             "rug_gate_thr": rug_v.get("rug_gate_thr")})
        except Exception as e:
            print(f"[rh-paper] rug-gate ledger append failed: {e}", flush=True)
        print(f"[rh-paper] RUG-GATE BLOCK {w.get('sym')} "
              f"({rug_v.get('rug_gate_reason')}) src={rug_v.get('rug_gate_source')} "
              f"bots={list(bot_ids)}", flush=True)

    def _manage_exits(self, now: float):
        for st in self.state.values():
            for pool, meta in list(st.pos_meta.items()):
                series = self.prices.get(pool) or []
                if not series:
                    continue
                px = series[-1][1]
                rows = self.tape.get(pool, [])
                # LP-DRAIN EXIT (rug-guard port): pool liquidity collapsing
                # under us = get out NOW, don't wait for the price path.
                _drain = lp_drain_pct(self.liq_hist.get(pool, []), now)
                if _drain is not None and _drain <= LP_DRAIN_EXIT_PCT:
                    from types import SimpleNamespace
                    self._paper_sell(pool, meta, SimpleNamespace(
                        kind="LP_DRAIN", sell_fraction=1.0,
                        reason="lp drain %.1f%% in %ds (liq collapse)" % (
                            _drain, int(LP_DRAIN_WINDOW_S))), now, st=st)
                    continue
                # FAST LP-PULL BAIL (2026-07-13, scratchpad/_rh_exit_rug_0713.md):
                # per-tick reserves vs the AT-ENTRY baseline — the fast complement
                # to LP_DRAIN (which needs a 900s window + 2 samples). SHADOW by
                # default: stamps a would-fire row ONCE per position and changes
                # nothing; RH_FAST_LIQ_BAIL=block makes it an immediate full exit.
                if (_fast_liq_bail_mode() != "off"
                        and meta.get("entry_liq") and not meta.get("_flb_stamped")):
                    _w = self.feed.watch.get(pool) or {}
                    _fv = fast_liq_bail_verdict(meta.get("entry_liq"),
                                                float(_w.get("liq") or 0))
                    if _fv.get("fast_liq_bail_block"):
                        meta["_flb_stamped"] = True
                        _append(LEDGER, {"ev": "fast_liq_bail",
                                         "ts": self._ledger_ts(now),
                                         "bot_id": st.bot.bot_id, "pool": pool,
                                         "sym": meta.get("sym"),
                                         "held_s": round(now - meta["entry_ts"], 1),
                                         **_fv})
                        if _fv.get("fast_liq_bail_mode") == "block":
                            from types import SimpleNamespace
                            self._paper_sell(pool, meta, SimpleNamespace(
                                kind="FAST_LIQ_BAIL", sell_fraction=1.0,
                                reason=_fv.get("fast_liq_bail_reason")), now, st=st)
                            continue
                # DERISK CAP (rug-tail defense, aged cohort): past the 20-min
                # median-death window, cap remaining exposure so one -98% LP
                # pull can't erase many +6% wins. Fires at most the slice
                # that exceeds the cap; a TP1 that already banked more is a
                # no-op (derisk_slice returns 0).
                if st.bot.derisk_after_s is not None:
                    dfrac = derisk_slice(meta["remaining_frac"],
                                         now - meta["entry_ts"],
                                         st.bot.derisk_after_s,
                                         st.bot.derisk_max_frac)
                    if dfrac > 1e-9:
                        from types import SimpleNamespace
                        self._paper_sell(pool, meta, SimpleNamespace(
                            kind="DERISK_CAP", sell_fraction=dfrac,
                            reason="derisk cap %.0fs held: exposure -> "
                                   "%.2f of original (rug-tail defense)" % (
                                       now - meta["entry_ts"],
                                       st.bot.derisk_max_frac)), now, st=st)
                        if pool not in st.pos_meta:
                            continue
                # SL1 LOSS-SIDE LADDER: pre-TP1 (nothing sold yet), first touch
                # of sl1_pct banks sl1_sell_fraction — the DOWN-side mirror of
                # TP1, so losses stop riding full-size to the hard stop. Fires
                # ONCE (latched), only while remaining_frac is still ~1.0 (any
                # prior partial — TP1/DERISK — stands down; first partial wins).
                if (st.bot.sl1_pct is not None
                        and not meta.get("_sl1_done")
                        and meta.get("remaining_frac", 1.0) >= 0.999
                        and meta.get("entry_px")):
                    _sl1_pnl = (px / meta["entry_px"] - 1.0) * 100.0
                    if _sl1_pnl <= st.bot.sl1_pct:
                        meta["_sl1_done"] = True
                        from types import SimpleNamespace
                        self._paper_sell(pool, meta, SimpleNamespace(
                            kind="SL1_DERISK",
                            sell_fraction=st.bot.sl1_sell_fraction,
                            reason="sl1 loss-ladder pnl=%.2f%% <= %.1f%% "
                                   "(bank %.0f%%, tail rides)" % (
                                       _sl1_pnl, st.bot.sl1_pct,
                                       100 * st.bot.sl1_sell_fraction)),
                            now, st=st)
                        if pool not in st.pos_meta:
                            continue
                vol_m5 = sum(float(r.get("volume_usd") or 0) for r in rows
                             if now - (r.get("_epoch") or 0) <= 300)
                for d in st.pm.tick(token=pool, current_price=px, now=now,
                                    vol_m5_usd=vol_m5):
                    self._paper_sell(pool, meta, d, now, st=st)
                    if pool not in st.pos_meta:
                        break

    def _paper_sell(self, pool, meta, decision, now, st=None):
        if st is None:           # pre-fleet call shape (tests): the primary
            st = self._st0
        token, dec = meta["token"], self._token_decimals(meta["token"])
        frac, new_remaining = sell_slice(meta["remaining_frac"],
                                         decision.sell_fraction)
        if frac <= 0:
            return
        sell_qty = meta["qty_orig"] * frac
        t_decide = time.time()
        try:
            q = self._executor().quote_sell(token, int(sell_qty * 10 ** dec))
            eth_out = (q.amount_out / 1e18) if (q and q.amount_out) else 0.0
        except Exception:
            eth_out = 0.0
        t_quote = time.time()
        # ── LIVE FILL PROBE exit leg: a live-bought position exits through
        # RhLiveExecutor.live_sell (triple gate only — sells are never gated
        # by canary/caps; the RH_LIVE_PROBE_BOTS opt-in was consumed at buy
        # time via meta["live"]). Partial legs sell the exact atomic amount;
        # a FULL close sells "all" (sweeps rounding dust so no corpse ERC20
        # sits in the wallet). FAIL-SAFE per the executor's actual state:
        #   pre_send/reverted -> nothing changed on-chain: book NOTHING,
        #     keep the position, retry next ladder tick (>=60s apart);
        #   unknown_spend (E1b) -> tx broadcast, outcome unknown: book the
        #     close on the paper-quote ESTIMATE, LOUD manual_reconcile flag
        #     (wallet-truth is the honest number; keeping it open would
        #     machine-gun 'nothing to sell' if the tx actually landed).
        live_tel, live_unconfirmed, live_gas_usd = None, False, 0.0
        if meta.get("live"):
            if now - meta.get("live_sell_fail_ts", 0) < \
                    LIVE_SELL_RETRY_COOLDOWN_S:
                return
            amount = ("all" if new_remaining <= 1e-9
                      else int(sell_qty * 10 ** dec))
            t_sent = time.time()
            try:
                lrec = self._live_executor().live_sell(token, amount)
            except Exception as e:
                cls = classify_live_error(e)
                self._log_live_error("sell", st.bot.bot_id, pool, token, e,
                                     cls, now)
                if cls != "unknown_spend":
                    # A pre_send revert normally means "nothing changed on-chain,
                    # retry on a later ladder tick". BUT if the wallet now holds
                    # ~0 of the token, the position was SIPHONED away (honeypot
                    # backdoor — the CCPEPE class) and will NEVER be sellable.
                    # Book it as a TOTAL LOSS now (eth_out=0 -> the eth_out<=0
                    # branch below) instead of leaving a phantom open position
                    # that silently under-counts realized P&L until a state-wipe
                    # erases it (the -$4.83-reported-vs--$27-real bug).
                    if self._token_gone(token):
                        print(f"[rh-live] SIPHONED {st.bot.bot_id} "
                              f"{token[:10]}: balance=0 after sell revert — "
                              f"booking TOTAL LOSS", flush=True)
                        eth_out = 0.0
                    else:
                        meta["live_sell_fail_ts"] = now
                        return
                else:
                    live_unconfirmed = True   # book on the quote estimate below
            else:
                t_landed = time.time()
                live_tel = fill_telemetry(
                    lrec, t_decide, t_quote, t_sent, t_landed,
                    self._tx_landed_ts(lrec.get("tx_signature")))
                out_wei = lrec.get("amount_out") or lrec.get("quoted_out") or 0
                eth_out = out_wei / 1e18   # REAL proceeds override the quote
                live_gas_usd = (float(lrec.get("gas_cost_eth") or 0.0)
                                * float(self.feed.eth_price or 0.0))
                _append(LIVE_FILLS, {"leg": "sell", "ts": iso_utc(now),
                                     "bot_id": st.bot.bot_id, "pool": pool,
                                     "token": token, "kind": decision.kind,
                                     "frac": frac, **live_tel})
                print(f"[rh-live] SELL {st.bot.bot_id} {token[:10]} "
                      f"frac={frac:.2f} {decision.kind} "
                      f"discovery->fill={live_tel.get('decision_to_landed_ms')}ms "
                      f"fill_vs_quote={live_tel.get('fill_vs_quote_pct')}% "
                      f"gas=${live_gas_usd:.3f} tx={live_tel.get('tx')}",
                      flush=True)
        if eth_out <= 0:  # unquotable at exit = rug/honeypot turned on: mark 0
            usd_out = 0.0
            exit_px = meta["entry_px"] * 1e-9
        else:
            usd_out = eth_out * self.feed.eth_price
            exit_px = (eth_out / sell_qty) if sell_qty else 0.0
        res = st.pm.close_position(pool, exit_price=max(exit_px, 1e-18),
                                   exit_time=now, reason=decision.reason,
                                   sell_fraction=decision.sell_fraction)
        cost = meta.get("usd_size", ENTRY_USD) * frac
        if meta.get("live"):
            # real friction on live legs: this leg's real sell gas + the
            # buy leg's real gas amortized by the fraction sold
            gas_usd = meta.get("buy_gas_usd", 0.0) * frac + live_gas_usd
        else:
            gas_usd = 2 * GAS_USD_PER_SIDE * frac
        pnl_usd = usd_out - cost - gas_usd
        pnl_pct = pnl_usd / cost * 100 if cost else 0.0
        st.daily_pnl_usd += pnl_usd
        meta["remaining_frac"] = new_remaining
        # position-level realized (legs summed) — the depth re-entry gate and
        # sibling loss-stop exclusion judge the WHOLE position, never one leg
        meta["realized_usd"] = meta.get("realized_usd", 0.0) + pnl_usd
        fully = getattr(res, "fully_closed", new_remaining <= 1e-9)
        if fully:
            st.pos_meta.pop(pool, None)
            st.last_exit[pool] = now
            st.exit_book[pool] = {"ts": now,
                                  "loss": meta["realized_usd"] < 0.0,
                                  "token": token,
                                  "px": exit_px}
            if meta["realized_usd"] < 0.0:
                ll = st.pool_loss_closes.setdefault(pool, [])
                ll.append(now)
                del ll[:-10]
            # expectancy-DIAL record (regime layer): position-level realized,
            # newest last; capped at 50 (the dial reads the last 20)
            st.recent_realized.append(round(meta["realized_usd"], 2))
            del st.recent_realized[:-50]
            # FLEET-WIDE realized record (regime-SIZING dial): same value into the
            # lane-level series, in close order across all racers.
            self.fleet_realized.append(round(meta["realized_usd"], 2))
            del self.fleet_realized[:-50]
            st.n_exits += 1
            _append(POSTEXIT_PENDING, {
                "pool": pool, "token": token, "sym": meta["sym"],
                "bot_id": st.bot.bot_id,
                "exit_px_eth": exit_px, "exit_kind": decision.kind,
                "exit_pnl_pct": round(pnl_pct, 2), "close_ts": now,
                "due_ts": now + POSTEXIT_DELAY_S})
        self.save_state()
        # RUNNER-SCORE SHADOW stamp (2026-07-10 monster-vs-regular decode):
        # tape-shape score over the last 10 min of this pool's live tape
        # (pre-run = the 10 min before that when buffered). Stamped on EVERY
        # exit leg; NO decision reads it — validation is offline against
        # realized peak at n>=30. Fail-open: no/thin tape -> None.
        runner_sc, runner_rs = None, None
        try:
            rows = self.tape.get(pool) or []
            runner_sc, runner_rs = score_at_exit(
                [{"kind": r.get("kind"), "volume_usd": r.get("volume_usd"),
                  "ts": (r.get("_epoch") if r.get("_epoch") is not None
                         else r.get("ts")),
                  "maker": r.get("maker")} for r in rows], now)
        except Exception:
            runner_sc, runner_rs = None, None
        sell_rec = {"ev": "sell", "ts": self._ledger_ts(now),
                    "bot_id": st.bot.bot_id, "pool": pool,
                    "sym": meta["sym"], "kind": decision.kind,
                    "reason": decision.reason[:100], "frac": frac,
                    "usd_out": round(usd_out, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "pnl_pct": round(pnl_pct, 2), "fully": fully,
                    "runner_score": runner_sc,
                    "runner_reasons": runner_rs,
                    "daily_pnl_usd": round(st.daily_pnl_usd, 2)}
        if meta.get("live"):        # keys only on live rows (see buy path)
            sell_rec["live"] = True
            if live_tel is not None:
                sell_rec["fill"] = live_tel
            if live_unconfirmed:
                sell_rec["live_unconfirmed"] = True
                sell_rec["manual_reconcile"] = True
            try:   # realized live P&L feeds the executor's $25 daily stop
                self._live_executor().record_realized(pnl_usd)
            except Exception as e:
                print(f"[rh-paper] live daily-pnl record failed: {e}",
                      flush=True)
        _append(LEDGER, sell_rec)
        print(f"[rh-paper] SELL{' LIVE' if meta.get('live') else ''} "
              f"{st.bot.bot_id:<16} {meta['sym']:<12} "
              f"{decision.kind} {frac*100:.0f}% pnl={pnl_pct:+.1f}% "
              f"(day {st.daily_pnl_usd:+.2f}) {decision.reason[:50]}",
              flush=True)

    def _check_postexit(self, now: float):
        """Complete due +6h post-exit checks: one quote per due token, result
        row written, pending rewritten. Unquotable at +6h = died (post_px 0)."""
        try:
            if not os.path.exists(POSTEXIT_PENDING):
                return
            rows = []
            with open(POSTEXIT_PENDING, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
            keep = []
            for r in rows:
                if now < float(r.get("due_ts") or 0):
                    keep.append(r)
                    continue
                post_px = 0.0
                try:
                    q = self._executor().quote_buy(
                        r["token"], int(ENTRY_USD / max(
                            self.feed.eth_price or 1e9, 1e-9) * 1e18))
                    if q and q.amount_out:
                        post_px = price_from_quote(
                            q.amount_in, q.amount_out,
                            self._token_decimals(r["token"]))
                except Exception:
                    pass
                ex = float(r.get("exit_px_eth") or 0)
                vs = ((post_px - ex) / ex * 100.0) if (ex > 0 and post_px > 0) else None
                _append(POSTEXIT_RESULTS, {**r, "post6h_px_eth": post_px,
                                           "post6h_vs_exit_pct": (round(vs, 1)
                                                                  if vs is not None else None),
                                           "died": post_px <= 0,
                                           "checked_ts": now})
                print(f"[rh-paper] post-exit +6h {r['sym']}: "
                      f"{'DEAD' if post_px <= 0 else '%+.1f%% vs exit' % vs}",
                      flush=True)
            tmp = POSTEXIT_PENDING + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            os.replace(tmp, POSTEXIT_PENDING)
        except Exception as e:
            print(f"[rh-paper] post-exit sweep failed: {e}", flush=True)

    def strategy_loop(self):
        print(f"[rh-paper] fleet armed: {len(self.bots)} racer(s) "
              f"[{', '.join(b.bot_id for b in self.bots)}] — "
              f"${ENTRY_USD:.0f}/entry, max {MAX_CONCURRENT}/config, "
              f"daily stop {DAILY_LOSS_STOP_USD}, shared quote budget "
              f"{MAX_HOT_QUOTES}/cycle", flush=True)
        while not self.stop.is_set():
            t0 = time.time()
            try:
                now = self.feed.rpc.now()
                self._drain(now)
                self._sample_liq(now)
                self._quote_hot(now)
                self._manage_exits(now)
                self._canary_tick(now)
                self._consider_entries(now)
                if now - getattr(self, "_last_dust_sweep", 0) > DUST_SWEEP_S:
                    self._last_dust_sweep = now
                    self._sweep_orphan_dust(now)
                if now - getattr(self, "_last_pe_sweep", 0) > POSTEXIT_SWEEP_S:
                    self._last_pe_sweep = now
                    self._check_postexit(now)
            except Exception as e:
                print(f"[rh-paper] loop {type(e).__name__}: {e}", flush=True)
            self.stop.wait(max(0.2, STRAT_TICK_S - (time.time() - t0)))

    def summary(self):
        """Fleet header + one line per racer (printed every 60s cycle)."""
        open_total = sum(len(st.pos_meta) for st in self.state.values())
        lines = [f"[rh-paper] session: entries={self.n_entries} "
                 f"exits={self.n_exits} open={open_total} | "
                 f"quotes={self.n_quotes} evals={self.n_evals}"]
        for bot in self.bots:
            st = self.state[bot.bot_id]
            top = sorted(st.block_hist.items(), key=lambda kv: -kv[1])[:4]
            blocks = " ".join(f"{k}={v}" for k, v in top) or "-"
            lines.append(f"[rh-paper] {bot.bot_id}: entries={st.n_entries} "
                         f"exits={st.n_exits} day=${st.daily_pnl_usd:+.2f} | "
                         f"blocks: {blocks}")
        return "\n".join(lines)

    def refresh_wallet_truth(self):
        """Keyless on-chain hot-wallet balance read on the status cadence
        (~5 min). NO-OP unless RH_WALLET_ADDRESS is set — the whole feature is
        env-driven and dormant until AxiS sets the address. Runs REGARDLESS of
        paper/live mode (read-only; no private key, no signing) so the balance
        shows in paper mode today, pre-live. rh_wallet_truth() writes
        rh_wallet_truth.json to the rh_state_dir (= OUT_DIR by default); the
        dashboard uploader ships it. FAIL-OPEN: any error is logged and
        swallowed. Called OFF the event loop from orchestrate's maintenance
        cadence, so it never contends with the strategy loop."""
        if not os.environ.get("RH_WALLET_ADDRESS"):
            return
        try:
            wt = rh_live.rh_wallet_truth(eth_price_usd=self.feed.eth_price)
            if wt.get("ok"):
                tot = wt.get("total_eth")
                print(f"[rh-paper] wallet-truth {wt.get('wallet')} "
                      f"total={tot} ETH delta={wt.get('delta_eth')}", flush=True)
            else:
                print(f"[rh-paper] wallet-truth read: {wt.get('error')}",
                      flush=True)
        except Exception as e:
            print(f"[rh-paper] wallet-truth refresh failed: "
                  f"{type(e).__name__}: {e}", flush=True)


def _upload_ledger():
    """Ship the lane's ledger + wallet-truth to the dashboard.

    2026-07-15 ROOT-CAUSE FIX. scripts/rh_paper_upload.py was documented "run
    per-session" and NOTHING called it, so the dashboard's RH ledger froze the
    moment a human session ended — on 07-15 it showed ZERO rows while the lane
    did 50 on-chain txs / 24 live $25 buys / $603 deployed. Every downstream
    number (fidelity, n_closed, entries, block counts) is computed on that book,
    so a dead uploader silently corrupts EVERY decision made from it — including
    'which bot holds live money'. The lane's local ledger is also EPHEMERAL and
    resets on redeploy, so un-uploaded rows are lost FOREVER; running this often
    is what bounds the loss window. Idempotent (append-mode, dedups on ts/ev/pool).
    FAIL-OPEN: an upload error must never touch trading."""
    try:
        t0 = time.time()
        from scripts import rh_paper_upload as _up
        _up.main()
        print(f"[rh-paper] LEDGER-UPLOAD ok in {time.time() - t0:.1f}s", flush=True)
    except Exception as e:
        print(f"[rh-paper] LEDGER-UPLOAD failed: {type(e).__name__}: "
              f"{str(e)[:140]}", flush=True)


def _refresh_fleet_fidelity():
    """Re-run the fleet FIDELITY correction and push it to the dashboard so the
    displayed 'real P&L' is honest instead of frozen at the last hand-run.

    2026-07-15: the column had been stale for 20h and read as 'we haven't traded
    in 16 hours' while the lane was trading normally — a dashboard that silently
    serves stale numbers actively misleads. Runs OFF the event loop (it quotes
    every unique token, paced), reads are public, the WRITE uses DASH_AUTH.
    FAIL-OPEN: a refresh error must never touch trading."""
    try:
        t0 = time.time()
        from scripts import rh_fleet_fidelity as _fid
        _fid.main()
        # DEAD-SET ENTRY BLOCK feed (2026-07-21 desk-review #1 action): the
        # refresh just quoted every fleet token's sellability — publish the
        # dead set so the ENTRY path can refuse to stake into known corpses.
        # (The loss-lockout can't catch these: dead tokens book phantom WINS,
        # so no losing close ever registers. $825 staked into known-dead in
        # 7h on 07-21, one token re-bought 6x in 40min.)
        try:
            DEAD_TOKENS.clear()
            DEAD_TOKENS.update(t for t, d in _fid._sell_cache.items() if d)
            globals()["DEAD_TOKENS_TS"] = time.time()
            print(f"[rh-paper] dead-set published: {len(DEAD_TOKENS)} tokens",
                  flush=True)
        except Exception:
            pass
        print(f"[rh-paper] FIDELITY-REFRESH ok in {time.time() - t0:.0f}s",
              flush=True)
    except Exception as e:
        print(f"[rh-paper] FIDELITY-REFRESH failed: {type(e).__name__}: "
              f"{str(e)[:140]}", flush=True)


async def orchestrate(fh: Firehose, lane: PaperLane, max_minutes: float):
    t_end = time.time() + max_minutes * 60
    ws_task = asyncio.create_task(fh.run_ws(t_end))
    strat = threading.Thread(target=lane.strategy_loop, daemon=True)
    strat.start()
    last_scanned = fh.feed.latest_block
    t0 = time.time()
    last_stats = t0
    last_wt = 0.0    # wallet-truth refresh clock (fires immediately, then ~5min)
    last_fid = 0.0   # fleet-fidelity refresh clock (fires immediately, then ~30min)
    last_up = 0.0    # ledger upload clock (fires immediately, then ~2min)
    try:
        while time.time() < t_end and not ws_task.done():
            last_scanned = await asyncio.to_thread(fh.maintenance, last_scanned)
            if time.time() - last_stats > 60.0:
                print(fh.stats_line(time.time() - t0), flush=True)
                print(lane.summary(), flush=True)
                last_stats = time.time()
            # keyless on-chain wallet-truth refresh (no-op unless
            # RH_WALLET_ADDRESS is set); OFF the event loop so a slow RPC
            # balance read never stalls firehose maintenance.
            if time.time() - last_wt > WALLET_TRUTH_REFRESH_S:
                last_wt = time.time()
                await asyncio.to_thread(lane.refresh_wallet_truth)
            # ledger -> dashboard. The local ledger is EPHEMERAL (resets on every
            # redeploy), so anything not yet uploaded is lost for good; upload
            # often to bound that window. Idempotent, off the event loop.
            if LEDGER_UPLOAD_S > 0 and time.time() - last_up > LEDGER_UPLOAD_S:
                last_up = time.time()
                await asyncio.to_thread(_upload_ledger)
            # fleet-fidelity auto-refresh: keeps the dashboard's "real P&L" column
            # HONEST instead of frozen at whenever it was last hand-run. OFF the
            # event loop (it quotes every unique token) and fail-open.
            if FIDELITY_REFRESH_S > 0 and \
                    time.time() - last_fid > FIDELITY_REFRESH_S:
                last_fid = time.time()
                await asyncio.to_thread(_refresh_fleet_fidelity)
            await asyncio.sleep(MAINT_SECS)
    finally:
        lane.stop.set()
        # FINAL FLUSH before the container dies: the local ledger is EPHEMERAL and
        # a redeploy wipes it, so any rows since the last upload would be lost for
        # good (that is exactly how 07-15's 24 live fills vanished from the book).
        # Best-effort — _upload_ledger never raises.
        _upload_ledger()
        ws_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):
            pass


def main():
    max_minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    feed = Feed(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))
    cid = int(feed.rpc.call("eth_chainId", []), 16)
    if cid != RH_CHAIN_ID:
        print(f"[rh-paper] FATAL: chain_id={cid} != {RH_CHAIN_ID}", flush=True)
        sys.exit(1)
    feed.sync_head()
    feed.refresh_eth_price()
    if feed.eth_price is None:
        print("[rh-paper] FATAL: no ETH/USD price", flush=True)
        sys.exit(1)
    lookback = int(LOOKBACK_H * 3600 / max(feed.spb, 0.02))
    feed.backfill_discovery(lookback)
    fh = Firehose(feed)
    lane = PaperLane(feed, registry=fh.registry, bots=ROSTER)
    lane.restore_state()
    fh.on_row = lane.on_row
    _gate_open, _gate_reason = rh_live.rh_live_gate()
    _probe_bots = os.environ.get("RH_LIVE_PROBE_BOTS", "").strip()
    if _gate_open and _probe_bots:
        _mode = f"LIVE PROBE [{_probe_bots}] — {_gate_reason}"
    else:
        _mode = f"PAPER-ONLY ({_gate_reason})"
    print(f"[rh-paper] chain {cid} eth=${feed.eth_price:,.2f} "
          f"candidates={len(feed.cand)} {_mode}",
          flush=True)
    # ── ONE-SHOT ORPHAN-TOKEN RECOVERY (2026-07-14): sell a stranded token
    # (e.g. a live position orphaned when a kill-redeploy wiped lane state) via
    # the live executor. Set RH_SELL_ORPHAN=<addr>; triple-gated like any live
    # sell; generous 10% slippage so it FILLS (the lane's default 3% reverted).
    # UNSET the env once it succeeds so it does not retry on future restarts.
    _orphan = os.environ.get("RH_SELL_ORPHAN", "").strip()
    if _orphan:
        try:
            print(f"[rh-paper] ORPHAN-SELL: selling FULL balance of {_orphan} "
                  f"@ 10% max slippage...", flush=True)
            _rec = rh_live.RhLiveExecutor().live_sell(_orphan, "all",
                                                      max_slippage_bps=1000)
            _out = (_rec.get("amount_out") or _rec.get("quoted_out") or 0) \
                if isinstance(_rec, dict) else 0
            _tx = _rec.get("tx_signature") if isinstance(_rec, dict) else "?"
            print(f"[rh-paper] ORPHAN-SELL DONE: recovered "
                  f"~{float(_out)/1e18:.6f} ETH  tx={_tx}", flush=True)
        except Exception as _e:
            print(f"[rh-paper] ORPHAN-SELL FAILED: {type(_e).__name__}: "
                  f"{str(_e)[:160]}", flush=True)
    asyncio.run(orchestrate(fh, lane, max_minutes))
    print(lane.summary(), flush=True)


if __name__ == "__main__":
    main()
