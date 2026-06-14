"""META CHAMELEON — fixed dynamic bots that retune to the day's meta
(AxiS 2026-06-12: "instead of spawning new bots, a few dynamic bots that can
ever evolve and be tweaked — that way there isn't a million bots clogging
things up").

The autonomy loop, no humans required and no bot proliferation:
  meta sensor (panel wallets' realized results, free PumpPortal stream)
    -> winning archetype's measured GEOMETRY (hold p75, win/loss medians)
    -> retune the chameleon's exit geometry IN PLACE
    -> the same registered bot now fishes the detected meta.

What retunes (the three geometry dials, decoded from wallets five times by
hand before this was automated — Dw5 -> timebox_probe was the prototype):
  time_stop_minutes  <- p75 panel hold   (clamped [10, 780])
  tp1_pct            <- median panel win (clamped [8, 60]; sell-ALL strength)
  hard_stop_pct      <- 1.2x median loss (clamped [-60, -10]; rug guard)

What NEVER retunes: size, capital, concurrency, lanes, filters, live flags —
those are frozen in config/bots/meta_chameleon.json. The chameleon changes
SHAPE, not exposure.

Safety rails:
  - QUIESCE: a new tune applies only when the bot has ZERO open positions
    (open positions keep the geometry they were entered under); pending tunes
    re-try each check until the book is flat.
  - CADENCE: at most one retune per RETUNE_MIN_SECS (6h) — metas are day-
    scale; hour-scale churn is noise-chasing.
  - HOLD: if no archetype qualifies (wr>=0.60, n>=8 over 6h), keep the
    current tune. The chameleon never resets to neutral mid-day.
  - Persisted overlay (DATA_DIR/chameleon_tune.json) re-applies at boot
    registration, so deploys don't amnesia the current meta.
  - Env kill switch META_CHAMELEON=off (default on).

BotConfig is a frozen dataclass; the evaluator and the position manager share
ONE instance per bot (dip_scanner: bc = ev.config -> PerBotPositionManager(bc)),
so object.__setattr__ on that instance retunes entry + exit sides atomically.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_TUNE_FILE = os.path.join(_DATA_DIR, "chameleon_tune.json")

CHAMELEON_PREFIX = "meta_chameleon"
CHECK_MIN_SECS = 900.0
QUALIFY_WR = 0.60
QUALIFY_N = 8

# Soft de-prioritization (2026-06-13, AxiS): archetypes whose edge is
# CONVICTION-HOLD (hold through deep drawdowns to recovery) don't transfer to a
# single $50 copy — thesis_holder produced the -$73 turning-tape cluster. Don't
# exclude them (thesis_holder won +$147 on good tape), but require a HIGHER board
# WR to wear them, so the chameleon biases toward copy-friendly exit-discipline
# archetypes (surgical/conviction) and only dons a hard-to-copy one when it's
# dominating decisively.
HARD_TO_COPY = {"thesis_holder"}
HARD_TO_COPY_WR = 0.75

# Own-fill veto (2026-06-13 watch): the 0.75 board bar CANNOT detect a tape
# turn — thesis_holder's panel WR stays ~1.0 (and med_loss_pct=None) because the
# conviction wallets HOLD through drawdowns to recovery: their closed roundtrips
# are all wins, their open underwater bags are invisible to the episode model
# (survivorship). So thesis_holder trivially clears 0.75 even as it bleeds in
# copy. The chameleon's OWN realized fills are the honest tape detector: on
# turning tape the $50 copies hit the -25 stop and gap-through to -32/-43%
# (the TURTLE/ICPX/SOCCERWOJAK cluster, ~-$75 over the last 6). VETO re-wearing a
# hard-to-copy archetype while its own recent fills are net-negative over
# >= OWN_FILL_VETO_N closes. Self-resetting: as other-archetype closes accumulate,
# the bad closes roll out of the 20-deep recent_closes window and the veto lifts
# — a cooldown that gives the tape time to turn, not a permanent ban. Extends
# tripwire #1 (own-fills dial) from a 1h ENTRY cooldown into a RE-WEAR veto.
OWN_FILL_VETO_N = 6


def _qualify_wr_for(arch: str) -> float:
    return HARD_TO_COPY_WR if arch in HARD_TO_COPY else QUALIFY_WR


def _own_fill_vetoed(rec: dict) -> frozenset:
    """Hard-to-copy archetypes whose OWN realized copies are net-negative over
    the last >= OWN_FILL_VETO_N closes — our money says this meta isn't
    transferring right now. Reversible (recovers as the window rolls forward)."""
    closes = rec.get("recent_closes") or []
    vetoed = set()
    for arch in HARD_TO_COPY:
        rows = [c for c in closes if c.get("archetype") == arch][-OWN_FILL_VETO_N:]
        if len(rows) >= OWN_FILL_VETO_N and sum(c.get("net", 0.0) for c in rows) < 0:
            vetoed.add(arch)
    return frozenset(vetoed)


# ── Own-realized-edge selection (2026-06-14, AxiS: "rotate into REAL winners") ──
# The board WR is survivorship-inflated (hold-through-drawdown archetypes show ~1.0
# closed-WR while bleeding in our fast-exit copy), so the chameleon kept rotating into
# the LEAST copyable setups. Fix: rank the wear decision by what wins for OUR COPY —
# the chameleon's own realized $/trade per archetype — not the board WR. Need a real
# own-fill sample (>=OWN_EDGE_MIN_N) to trust it; bootstrap unproven archetypes by board
# WR (cautious exploration), skip proven copy-losers outright, stand down when none win.
OWN_EDGE_MIN_N = 4


def _own_edge(own_closes, arch):
    """The chameleon's OWN realized $/trade for `arch` over recent closes — the
    honest copy-edge the survivorship board WR can't give. Returns (edge or None, n)."""
    rows = [c for c in (own_closes or []) if c.get("archetype") == arch]
    n = len(rows)
    if n == 0:
        return None, 0
    return sum(c.get("net", 0.0) for c in rows) / n, n

# ── Cadence (2026-06-12, AxiS: "6h may be too strict — it's crypto") ─────────
# Evidence-based, not clock-based: STABILITY needs no reason, CHANGE needs
# evidence. A retune fires when ANY of:
#   - soft cadence elapsed (RETUNE_SOFT_SECS) and a qualifier improves on us
#   - the CURRENT archetype DETERIORATED (its 6h wr < DETERIORATE_WR or its
#     board dried up below DETERIORATE_N) -> respond NOW, market turned
#   - a CHALLENGER dominates (wr >= current + CHALLENGER_WR_EDGE with
#     n >= CHALLENGER_N) -> clear regime change, don't wait out the clock
# Hard floor RETUNE_FLOOR_SECS between retunes prevents thrash either way.
RETUNE_SOFT_SECS = 6 * 3600.0
RETUNE_FLOOR_SECS = 3600.0
DETERIORATE_WR = 0.45
DETERIORATE_N = 4
CHALLENGER_WR_EDGE = 0.15
CHALLENGER_N = 12

# ── Signal provenance / consensus (AxiS: "which wallets send the signals?") ──
# A qualifying archetype must be a LABELED style (never the mixed "unlabeled"
# bucket — incoherent geometry) composed of >= MIN_WALLETS distinct wallets
# with no single wallet supplying more than MAX_TOP_SHARE of the episodes
# (one hyper-active wallet must not BE the meta). The contributing wallets are
# recorded on every tune (chameleon_tune.json + /api/meta-sensor).
MIN_WALLETS = 2
MAX_TOP_SHARE = 0.75

# A queued tune older than this applies even with an open book (quiesce must
# not starve a market response forever — max_conc 6 x 4h boxes rarely go flat).
PENDING_FORCE_SECS = 2 * 3600.0

# ── META-DEATH ACCELERATORS (2026-06-12, AxiS: "how does it know BEFORE we
# lose money?"). The 6h closed-episode board detects a dying meta HOURS late
# (holds must finish + idle-close + average must fall). Three faster tripwires,
# any ONE of which puts entries on standby (re-qualification still runs on the
# full board):
#  1. OWN-FILLS DIAL (fastest feedback on OUR money; AxiS-tuned 2-of-3): if 2
#     of the chameleon's last 3 closed positions under the WORN meta lost,
#     pause entries for OWN_FILLS_PAUSE_SECS. Catches the meta-works-for-them-
#     not-for-us case (the P2b selection gap that bit timebox tonight).
#  2. BUY-RATE COLLAPSE (leading): panel wallets stop ENTERING a dying meta
#     before any loss closes. Recent 30min entry rate < 30% of trailing norm
#     (with a real norm) -> standby.
#  3. FRESH-WINDOW WR (fast lagging): last-90min WR of the worn archetype
#     < 0.35 on n>=5 -> standby even while the 6h average still looks alive.
OWN_FILLS_WINDOW = 3
OWN_FILLS_LOSSES = 2
OWN_FILLS_PAUSE_SECS = 3600.0
BUYRATE_COLLAPSE_FRAC = 0.30
BUYRATE_MIN_NORM = 2.0          # per-30min trailing norm below this = no signal
FRESH_WINDOW_SECS = 5400.0
FRESH_WR_FLOOR = 0.35
FRESH_MIN_N = 5

CLAMPS = {
    "time_stop_minutes": (10.0, 780.0),
    "tp1_pct": (8.0, 60.0),
    "hard_stop_pct": (-60.0, -10.0),
    # POND dial (gap #1): the entry_gate's entry_age_hours ceiling — fish
    # where the archetype fishes, not just exit how it exits. Tuned only when
    # the sensor could place >= AGE_COVERAGE_MIN of the archetype's episodes.
    "entry_age_max_h": (6.0, 168.0),
}
AGE_COVERAGE_MIN = 0.5

# Copy stop-floor (2026-06-13): a single copy can't hold through deep drawdowns
# like a diversified wallet; cap worst-case per-trade loss no looser than this,
# overriding an archetype's loose stop. Only bites the deep-loss tail.
COPY_STOP_FLOOR = -25.0

# Slow styles (swing/thesis: holds >= this) barely register on the 6h board
# because episodes score at CLOSE — qualify them off the 24h window instead
# (gap #3: the sensor must not structurally favor fast metas).
SLOW_HOLD_SECS = 4 * 3600.0

_last_check = 0.0


def enabled() -> bool:
    return os.environ.get("META_CHAMELEON", "on").strip().lower() not in ("off", "0", "false")


def _clamp(field: str, v: float) -> float:
    lo, hi = CLAMPS[field]
    return max(lo, min(hi, float(v)))


def tune_from_geometry(geo: dict) -> Optional[dict]:
    """Winning archetype's measured geometry -> the three exit dials."""
    try:
        hold = geo.get("p75_hold_secs") or geo.get("med_hold_secs")
        win = geo.get("med_win_pct")
        if not hold or not win or win <= 0:
            return None
        loss = geo.get("med_loss_pct")
        _stop = _clamp("hard_stop_pct",
                       (loss * 1.2) if isinstance(loss, (int, float)) and loss < 0
                       else -60.0)
        # COPY STOP-FLOOR (2026-06-13 watch, evidence-based, pre-committed): a
        # single $50 copy can't ride a deep drawdown to recovery the way the
        # diversified thesis-holder WALLET can — two deep losses materialized
        # (-41%, -39%) where the archetype's loose -60% stop let losers run past
        # what the time-box cut. Cap the copy's worst-case per-trade loss at
        # COPY_STOP_FLOOR. Regime-AGNOSTIC (copies gap deep on any tape) and
        # well-targeted: normal losses (-13..-24%) exit before -25% on their own,
        # so this only bites the deep tail. (The TP-side under-capture is held —
        # that one is regime-conditional, pending a choppy test.)
        _stop = max(COPY_STOP_FLOOR, _stop)
        tune = {
            "time_stop_minutes": _clamp("time_stop_minutes", hold / 60.0),
            "tp1_pct": _clamp("tp1_pct", win),
            "hard_stop_pct": _stop,
        }
        # POND: tune the age ceiling to ~2x the archetype's p75 entry age,
        # only when enough of its episodes could be age-placed.
        p75_age = geo.get("p75_age_h")
        if (isinstance(p75_age, (int, float)) and p75_age > 0
                and float(geo.get("age_coverage") or 0) >= AGE_COVERAGE_MIN):
            tune["entry_age_max_h"] = _clamp("entry_age_max_h", 2.0 * p75_age)
        return tune
    except Exception:
        return None


def _load_state() -> dict:
    try:
        return json.load(open(_TUNE_FILE))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    try:
        with open(_TUNE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=1)
    except Exception as e:
        logger.debug("[Chameleon] tune persist failed: %s", e)


def _apply(config, tune: dict) -> None:
    for k, v in tune.items():
        if k == "entry_age_max_h":
            # rebuild entry_gate with the new age ceiling, preserving every
            # other condition (wash screen, liquidity floor, ...)
            gate = [list(c) for c in (config.entry_gate or [])
                    if str(c[0]) != "entry_age_hours"]
            gate.append(["entry_age_hours", "<=", float(v)])
            object.__setattr__(config, "entry_gate", tuple(tuple(c) for c in gate))
            continue
        # COPY STOP-FLOOR enforced at the APPLICATION chokepoint (2026-06-13):
        # tune_from_geometry floors computed tunes, but a PERSISTED tune restored
        # by the boot overlay (or a pending tune) bypasses that path — so the
        # worn config could still carry a loose stop after a restart. Clamp here
        # so EVERY application (fresh/overlay/pending) caps the copy's loss.
        if k == "hard_stop_pct" and isinstance(v, (int, float)):
            v = max(COPY_STOP_FLOOR, float(v))
        object.__setattr__(config, k, v)


def apply_overlay(config) -> None:
    """Boot-time re-apply of the persisted tune (deploy-amnesia guard).
    Called at bot registration, BEFORE/as positions restore — restored
    positions were opened under this tune, so it's the correct geometry."""
    if not enabled():
        return
    st = _load_state().get(config.bot_id)
    if st and st.get("archetype") == RED_ARCHETYPE:
        # red mode re-derives from the LIVE regime on the next retune (<=15min);
        # don't restore a stale red geometry onto a fresh-from-JSON green entry_gate
        # (that would leave a green entry paired with a red exit for ~15min).
        logger.info("[Chameleon] %s boot: last state was RED-NIGHT MODE; re-deriving "
                    "from live regime (no stale overlay)", config.bot_id)
        return
    if st and isinstance(st.get("tune"), dict):
        try:
            _apply(config, {k: float(v) for k, v in st["tune"].items() if k in CLAMPS})
            logger.info("[Chameleon] %s boot overlay applied: %s (archetype=%s, tuned %s)",
                        config.bot_id, st["tune"], st.get("archetype"),
                        st.get("tuned_at_iso"))
        except Exception as e:
            logger.warning("[Chameleon] overlay apply failed for %s: %s", config.bot_id, e)


# ── RED-NIGHT MODE (2026-06-14, AxiS: "the chameleon is MEANT to survive red
# nights — adapt and copy the red-winners") ──────────────────────────────────
# It was regime-AGNOSTIC: it wore the top board-WR archetype regardless of tape.
# On 06-14's red night (fleet -$302) the top board archetype was time_boxer
# (MOMENTUM) and it DUMPED (timebox_probe -$138, the worst bleeder). The red-window
# mine showed the SURVIVOR is DEEP-FLUSH CAPITULATION: deepflush_timebox went +$45
# @ $4.50/tr (7/10W) buying deep-drawdown + volume-burst entries with a fast
# time-box — converges with the 06-13 drawdown-winner decode (the DaxfeJKe 'Dw5'
# 6-min boxer). So in a RED regime the chameleon DROPS its momentum-prone board
# behavior and ADOPTS the deep-flush profile (deep-capitulation entry + fast box);
# it reverts to normal board-wearing when the tape isn't red. RED = the
# regime_size_dial BAD verdict (broad downside breadth h1neg>=40, or SOL euphoria)
# — the same 49-day day-level study signal the size dial already uses.
RED_ARCHETYPE = "deepflush_red"
# Red-tape entry profile, REFINED 2026-06-14 from the red-window winning-wallet mine
# (17 net-positive wallets, 305 entries, 111 winners >=+30% fwd vs 26 losers <=0%).
# The winners' entries: deep dip (-20% off 90m high), DECENT liq (~$29k, NOT thin
# $8.5k), MID mcap (~$124k, NOT sub-$20k micro), younger (~12h), and — counter to the
# deepflush_timebox config — MODEST volume (~1.5x). The deepflush `1m_volume_spike>=3`
# gate was buying the LOSERS (panic-volume flushes keep dumping; loser median vol 2.1x)
# -> DROPPED. Added an mcap floor to skip the micro-cap losers. entry_gate keeps the
# chameleon's wash + liq(>=25k) safety (already matching the winners) and adds the
# deep-dip condition; fast-box exit retained from the deepflush decode.
RED_ENTRY_ADD = (("shape_90m_drawdown_from_max_pct", "<=", -18.0),)   # deep dip; NO vol gate (it selected losers)
RED_TRIGGERS = ("volume_burst_runner", "deep_1h_dip", "power_dip_runner")
RED_TUNE = {"time_stop_minutes": 6.0, "tp1_pct": 6.0, "hard_stop_pct": -12.0}  # fast box (raw via _apply; stop floored by COPY_STOP_FLOOR)
RED_EXTRA = {"tp1_sell_fraction": 0.8, "tp2_pct": 12.0, "tp2_sell_fraction": 0.2,
             "trail_pp": 2.0, "mcap_min": 50000.0}   # mcap floor: red-winners ~$124k vs loser ~$10k micro
_GREEN_SNAP: Dict[str, dict] = {}   # bot_id -> snapshot of the fields red mode overrides


def _regime_is_red(scanner) -> bool:
    """Red night per the day-level regime study (regime_size_dial BAD verdict):
    broad downside capitulation (h1neg>=40) or SOL euphoria. Reads the scan
    cycle's regime snapshot stashed on the scanner. Fail-safe: False (normal)."""
    try:
        from core.regime_size_dial import regime_size_verdict, BAD_MULT
        meta = getattr(scanner, "_cycle_regime", None)
        if not isinstance(meta, dict):
            return False
        return regime_size_verdict(meta)[0] == BAD_MULT
    except Exception:
        return False


def _apply_red_profile(config) -> None:
    """Adopt the deep-flush capitulation profile (entry_gate + triggers + fast-box
    geometry). Snapshots the green fields once so the off-red restore is exact."""
    bid = config.bot_id
    if bid not in _GREEN_SNAP:
        _GREEN_SNAP[bid] = {
            "entry_gate": config.entry_gate,
            "triggers_allowed": config.triggers_allowed,
            **{k: getattr(config, k, None) for k in RED_EXTRA},
        }
    # keep every NON-deep-flush entry condition (wash/liq safety; drop any stale
    # copy of the deep-flush conditions), then add the capitulation gate.
    _keep = tuple(tuple(c) for c in (config.entry_gate or [])
                  if str(c[0]) not in ("shape_90m_drawdown_from_max_pct", "1m_volume_spike"))
    object.__setattr__(config, "entry_gate", _keep + RED_ENTRY_ADD)
    object.__setattr__(config, "triggers_allowed", list(RED_TRIGGERS))
    for k, v in RED_EXTRA.items():
        object.__setattr__(config, k, v)
    _apply(config, RED_TUNE)


def _restore_green(config) -> None:
    """Restore the pre-red entry/triggers/tp fields (exit geometry re-tunes off
    the board on the next qualifying read)."""
    snap = _GREEN_SNAP.pop(config.bot_id, None)
    if not snap:
        return
    object.__setattr__(config, "entry_gate", snap["entry_gate"])
    object.__setattr__(config, "triggers_allowed", snap["triggers_allowed"])
    for k in RED_EXTRA:
        # restore the EXACT original (incl None — e.g. mcap_min was null off-red;
        # an `is not None` guard would leave the red floor stuck on after restore).
        object.__setattr__(config, k, snap.get(k))


def best_qualifying(sensor, now: float, veto=frozenset(), own_closes=None):
    """(archetype, geometry) of the best qualifying 6h archetype, or (None, None).

    `veto` = archetypes to skip (own-fill veto; see _own_fill_vetoed) so the
    search falls through to the next-best copy-friendly meta.

    Qualification = WR/N bar + IDENTITY + CONSENSUS:
      - labeled archetype only ("unlabeled" pools many unrelated styles into
        one incoherent geometry — never tune to it; a winning unlabeled bucket
        is logged loudly as an UNIDENTIFIED META so the decode ritual labels it)
      - >= MIN_WALLETS distinct wallets composing the board
      - no single wallet supplying > MAX_TOP_SHARE of the episodes
    """
    try:
        windows = sensor.scoreboard(now).get("windows", {})
        board = dict(windows.get("6h", {}))
        # slow styles (gap #3): an archetype whose holds run >= SLOW_HOLD_SECS
        # closes too few episodes inside 6h to ever qualify there — give it
        # the 24h window it actually lives on.
        for arch, row in (windows.get("24h", {}) or {}).items():
            if arch in board or arch == "all":
                continue
            g24 = sensor.archetype_geometry(arch, now, window_secs=24 * 3600,
                                            min_n=QUALIFY_N)
            if g24 and (g24.get("med_hold_secs") or 0) >= SLOW_HOLD_SECS:
                board[arch] = row
    except Exception:
        return None, None
    best, best_geo = None, None
    candidates = []  # (arch, geo) passing all board bars; own-edge tiering picks below
    near_miss = []  # (arch, reason) for WR-passing candidates rejected downstream
    for arch, row in board.items():
        if arch == "all":
            continue
        if arch in veto:
            near_miss.append((arch, "own-fill-vetoed"))
            continue   # own-fill veto: our money rejects this hard-to-copy meta
        # per-archetype WR bar (hard-to-copy archetypes need a higher bar; see HARD_TO_COPY)
        if row.get("n", 0) < QUALIFY_N or row.get("wr", 0) < _qualify_wr_for(arch):
            continue
        geo = sensor.archetype_geometry(arch, now, min_n=QUALIFY_N)
        if not geo:
            geo = sensor.archetype_geometry(arch, now, window_secs=24 * 3600,
                                            min_n=QUALIFY_N)
            if not geo or (geo.get("med_hold_secs") or 0) < SLOW_HOLD_SECS:
                continue
        if arch == "unlabeled":
            logger.warning(
                "[Chameleon] UNIDENTIFIED META: unlabeled panel wallets running "
                "wr=%.0f%% n=%d (wallets=%s) — decode + label them "
                "(config/sensor_panel.json) so the chameleon can wear it.",
                geo["wr"] * 100, geo["n"], geo.get("wallets"))
            continue
        if geo.get("n_wallets", 1) < MIN_WALLETS:
            near_miss.append((arch, f"n_wallets={geo.get('n_wallets')}<{MIN_WALLETS}"))
            continue
        if geo.get("top_wallet_share", 1.0) > MAX_TOP_SHARE:
            near_miss.append((arch, f"top_share={geo.get('top_wallet_share'):.2f}>{MAX_TOP_SHARE}"))
            continue
        candidates.append((arch, geo))   # passed all board bars; own-edge tiering below
    # ── Own-realized-edge tiering (2026-06-14): pick what wins for OUR copy, not the
    #    board's survivorship WR. proven-positive (>=N own-fills, edge>0) ranked by edge;
    #    proven copy-losers SKIPPED even at high board WR; unproven explored by board WR.
    proven_pos, unproven = [], []
    for _arch, _geo in candidates:
        _edge, _n = _own_edge(own_closes, _arch)
        if _n >= OWN_EDGE_MIN_N:
            if _edge > 0:
                proven_pos.append((_edge, _arch, _geo))
            else:
                near_miss.append((_arch, f"own-edge {_edge:+.2f}/{_n} <=0 (copy-loser, skip)"))
        else:
            unproven.append((_geo.get("wr", 0), _geo.get("n", 0), _arch, _geo))
    if proven_pos:
        proven_pos.sort(key=lambda x: x[0], reverse=True)        # highest own-edge wins
        best, best_geo = proven_pos[0][1], proven_pos[0][2]
    elif unproven:
        unproven.sort(key=lambda x: (x[0], x[1]), reverse=True)  # bootstrap-explore best board WR
        best, best_geo = unproven[0][2], unproven[0][3]
    if best is None and near_miss:
        # Nothing qualified despite board candidates clearing the WR bar — surface
        # WHY (consensus/veto), since this is what keeps the chameleon in standby.
        logger.info("[Chameleon] no qualifying meta; near-misses: %s",
                    ", ".join(f"{a}({r})" for a, r in near_miss))
    return best, best_geo


def _should_retune(now: float, rec: dict, sensor, cand_arch: str, cand_geo: dict) -> bool:
    """Evidence-based cadence: change needs a reason, one of three."""
    tuned_at = float(rec.get("tuned_at") or 0)
    if now - tuned_at < RETUNE_FLOOR_SECS:
        return False                       # hard anti-thrash floor
    if now - tuned_at >= RETUNE_SOFT_SECS:
        return True                        # soft cadence elapsed
    cur_arch = rec.get("archetype")
    if not cur_arch:
        return True                        # never tuned -> take the first read
    if cand_arch == cur_arch:
        return False                       # same meta, fresher numbers — hold
    # (1) current archetype deteriorated -> respond NOW
    cur_geo = sensor.archetype_geometry(cur_arch, now, min_n=1)
    if (not cur_geo or cur_geo.get("n", 0) < DETERIORATE_N
            or cur_geo.get("wr", 0.0) < DETERIORATE_WR):
        return True
    # (2) challenger dominates -> clear regime change, don't wait out the clock
    if (cand_geo.get("n", 0) >= CHALLENGER_N
            and cand_geo.get("wr", 0.0) >= cur_geo.get("wr", 0.0) + CHALLENGER_WR_EDGE):
        return True
    return False


def maybe_retune(scanner, now: Optional[float] = None) -> None:
    """Hourly-ish hook from the scan cycle. Never raises."""
    global _last_check
    try:
        if not enabled():
            return
        now = now or time.time()
        if now - _last_check < CHECK_MIN_SECS:
            return
        _last_check = now
        from core.meta_sensor import get_sensor
        sensor = get_sensor()
        if sensor is None:
            return
        st = _load_state()
        for bot_id, pm in (scanner.bot_position_managers or {}).items():
            if not bot_id.startswith(CHAMELEON_PREFIX):
                continue
            rec = st.get(bot_id) or {}
            pending = rec.get("pending")
            # 1) a deferred tune applies when the book is flat — or after
            #    PENDING_FORCE_SECS regardless (a busy chameleon must not
            #    starve a market-condition response forever; positions opened
            #    under the old tune close slightly differently, accepted).
            if pending:
                age = now - float(pending.get("queued_at") or now)
                if not list(pm.iter_positions()) or age >= PENDING_FORCE_SECS:
                    _apply(pm.config, pending["tune"])
                    rec.update({"tune": pending["tune"], "archetype": pending["archetype"],
                                "geometry": pending.get("geometry"),
                                "tuned_at": now, "tuned_at_iso": _iso(now), "pending": None})
                    st[bot_id] = rec
                    _save_state(st)
                    logger.info("[Chameleon] %s RETUNED (deferred%s) -> %s [archetype=%s]",
                                bot_id, ", forced" if age >= PENDING_FORCE_SECS else "",
                                pending["tune"], pending["archetype"])
                    continue
            # ── RED-NIGHT MODE: the regime OVERRIDES the board. On a red tape the
            #    chameleon becomes a deep-flush capitulation trader (the measured
            #    red-survivor); off the red tape it reverts to normal board-wearing.
            #    Bypasses quiesce like the own-fill force-switch — a regime turn is
            #    urgent (don't keep buying momentum into a red tape until flat).
            if _regime_is_red(scanner):
                if rec.get("archetype") != RED_ARCHETYPE:
                    _apply_red_profile(pm.config)
                    rec.update({"tune": dict(RED_TUNE), "archetype": RED_ARCHETYPE,
                                "geometry": {"red_mode": True,
                                             "regime": getattr(scanner, "_cycle_regime", None)},
                                "tuned_at": now, "tuned_at_iso": _iso(now), "pending": None})
                    st[bot_id] = rec
                    _save_state(st)
                    logger.info("[Chameleon] %s RED-NIGHT MODE on -> deep-flush capitulation "
                                "profile (regime=%s)", bot_id, getattr(scanner, "_cycle_regime", None))
                continue   # red mode set this cycle; skip normal board selection
            if rec.get("archetype") == RED_ARCHETYPE:
                _restore_green(pm.config)
                rec["archetype"] = None
                rec["pending"] = None
                st[bot_id] = rec
                _save_state(st)
                logger.info("[Chameleon] %s red tape lifted -> restored normal board mode", bot_id)
                # fall through to normal board selection below
            veto = _own_fill_vetoed(rec)
            worn_vetoed = rec.get("archetype") in veto
            arch, geo = best_qualifying(sensor, now, veto=veto,
                                        own_closes=rec.get("recent_closes"))
            if not arch:
                # If the worn meta is own-fill-vetoed AND nothing copy-friendly
                # qualifies (e.g. the only other board leader is one wallet's
                # style, failing the >=2-wallet consensus), STAND DOWN rather than
                # keep wearing the bleeder: clear the worn label so entries_allowed
                # returns standby (AxiS: "only buy when we KNOW the meta"). The
                # veto self-lifts as the bad closes roll out of recent_closes.
                if worn_vetoed and rec.get("archetype"):
                    logger.info("[Chameleon] %s STANDBY: worn '%s' own-fill-vetoed and "
                                "no copy-friendly meta qualifies -> standing down (no new buys)",
                                bot_id, rec.get("archetype"))
                    rec["archetype"] = None
                    rec["pending"] = None
                    st[bot_id] = rec
                    _save_state(st)
                continue   # HOLD tune / stay in standby — never reset mid-day
            # A worn archetype our OWN money is bleeding on (own-fill veto) is a
            # deterioration signal the survivorship board WR hides. Force the
            # switch: bypass the _should_retune hold AND the open-book queue
            # (open positions managing under the new geometry beats compounding
            # the bleed for up to PENDING_FORCE_SECS).
            if not worn_vetoed and not _should_retune(now, rec, sensor, arch, geo):
                continue
            tune = tune_from_geometry(geo)
            if not tune or tune == rec.get("tune"):
                continue
            if worn_vetoed:
                logger.info("[Chameleon] %s own-fill VETO: worn '%s' recent copies "
                            "net-negative -> forcing switch to '%s' (bypass queue)",
                            bot_id, rec.get("archetype"), arch)
            if list(pm.iter_positions()) and not worn_vetoed:
                # PRESERVE queued_at across re-queues of the SAME archetype so the
                # 2h force-apply (PENDING_FORCE_SECS) actually accumulates. (Bug
                # found 2026-06-13 watch: stamping queued_at=now every cycle reset
                # the clock to ~0, so a meta that keeps the book busy never let the
                # force fire — the backstop was silently defeated.) Refresh the
                # tune/geometry (fresher numbers) but keep the original clock; only
                # reset it when the pending archetype actually changes.
                _prev = rec.get("pending") or {}
                _qa = _prev.get("queued_at") if _prev.get("archetype") == arch else now
                rec["pending"] = {"tune": tune, "archetype": arch, "geometry": geo,
                                  "queued_at": _qa or now}
                st[bot_id] = rec
                _save_state(st)
                logger.info("[Chameleon] %s tune QUEUED (book not flat): %s [%s] "
                            "(queued_age=%.0fmin)", bot_id, tune, arch,
                            (now - (_qa or now)) / 60.0)
                continue
            _apply(pm.config, tune)
            rec.update({"tune": tune, "archetype": arch, "geometry": geo,
                        "tuned_at": now, "tuned_at_iso": _iso(now), "pending": None})
            st[bot_id] = rec
            _save_state(st)
            logger.info("[Chameleon] %s RETUNED -> %s [archetype=%s wr=%.0f%% n=%d "
                        "wallets=%d top_share=%.0f%%]",
                        bot_id, tune, arch, geo["wr"] * 100, geo["n"],
                        geo.get("n_wallets", 0), geo.get("top_wallet_share", 0) * 100)
    except Exception as e:
        logger.debug("[Chameleon] maybe_retune error: %s", e)


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


# ── STANDBY GATE (Option B, AxiS 2026-06-12: "we only buy when we KNOW the
# meta — to avoid unnecessary losses") ───────────────────────────────────────
# The chameleon opens NEW positions ONLY while wearing a meta that is still
# alive on the board. No qualifying meta -> no entries (open positions keep
# managing normally). HYSTERESIS: it takes QUALIFY_WR (0.60) to start wearing
# a meta, but entries stay allowed until the worn archetype decays below the
# DETERIORATE bar (0.45 / n<4) — a hard in/out line at 0.60 would flap entries
# on board jitter. Cached 60s (the buy path evaluates many candidates/cycle).
_entries_cache: Dict[str, tuple] = {}


def entries_allowed(bot_id: str, now: Optional[float] = None) -> tuple:
    """(allowed: bool, reason: str) — the chameleon buy gate."""
    if not enabled():
        return True, "META_CHAMELEON=off (static clone mode, no gate)"
    now = now or time.time()
    cached = _entries_cache.get(bot_id)
    if cached and now - cached[0] < 60:
        return cached[1]
    res = _compute_entries_allowed(bot_id, now)
    _entries_cache[bot_id] = (now, res)
    return res


def _compute_entries_allowed(bot_id: str, now: float) -> tuple:
    try:
        from core.meta_sensor import get_sensor
        sensor = get_sensor()
        if sensor is None:
            return False, "sensor not wired"
        rec = _load_state().get(bot_id) or {}
        arch = rec.get("archetype")
        if not arch:
            return False, "STANDBY: no meta worn yet (sensor board warming)"
        # tripwire 1 — OWN FILLS (2-of-3, AxiS-tuned): our money is the
        # fastest honest signal that this meta doesn't transfer to us.
        closes = [c for c in (rec.get("recent_closes") or [])
                  if c.get("archetype") == arch][-OWN_FILLS_WINDOW:]
        losses = [c for c in closes if not c.get("win")]
        if (len(closes) >= OWN_FILLS_WINDOW
                and len(losses) >= OWN_FILLS_LOSSES
                and now - max(c.get("ts", 0) for c in losses) < OWN_FILLS_PAUSE_SECS):
            return False, (f"STANDBY: own-fills dial — {len(losses)} of last "
                           f"{len(closes)} closes under '{arch}' lost (1h pause)")
        # RED-NIGHT MODE is a regime-driven meta (not a sensor-board archetype), so
        # the board-decay tripwires below don't apply to it. Entries are allowed —
        # the deep-flush profile IS the measured red-survivor — but the own-fills
        # dial above still pauses it if our own deep-flush copies start bleeding.
        if arch == RED_ARCHETYPE:
            return True, "red-night mode: deep-flush capitulation profile"
        # tripwire 2 — BUY-RATE COLLAPSE (leading): they stopped playing.
        if hasattr(sensor, "buy_rate"):
            recent, norm = sensor.buy_rate(arch, now)
            if norm >= BUYRATE_MIN_NORM and recent <= BUYRATE_COLLAPSE_FRAC * norm:
                return False, (f"STANDBY: '{arch}' buy-rate collapsed "
                               f"({recent} last 30min vs norm {norm:.1f})")
        # tripwire 3 — FRESH-WINDOW WR: the last 90min, undiluted by the 6h average.
        fresh = sensor.archetype_geometry(arch, now, window_secs=FRESH_WINDOW_SECS,
                                          min_n=FRESH_MIN_N)
        if fresh and fresh.get("wr", 1.0) < FRESH_WR_FLOOR:
            return False, (f"STANDBY: '{arch}' fresh-90min WR broke "
                           f"({fresh['wr']:.0%} on n={fresh['n']})")
        # baseline — the 6h board with deterioration hysteresis.
        geo = sensor.archetype_geometry(arch, now, min_n=1)
        if not geo or (geo.get("med_hold_secs") or 0) >= SLOW_HOLD_SECS:
            geo24 = sensor.archetype_geometry(arch, now, window_secs=24 * 3600, min_n=1)
            geo = geo24 or geo
        if (not geo or geo.get("n", 0) < DETERIORATE_N
                or geo.get("wr", 0.0) < DETERIORATE_WR):
            return False, (f"STANDBY: worn meta '{arch}' decayed "
                           f"(wr={geo.get('wr') if geo else None} "
                           f"n={geo.get('n') if geo else 0})")
        return True, (f"meta '{arch}' alive (wr={geo['wr']:.0%} n={geo['n']})")
    except Exception as e:
        return False, f"STANDBY (gate error: {e})"


def record_close(bot_id: str, token: str, pnl_usd: float, fully_closed: bool,
                 archetype: Optional[str]) -> None:
    """Per-leg sell hook (dip_scanner): accumulate legs per position; on full
    close push the position's NET outcome into the own-fills window. Never
    raises."""
    try:
        st = _load_state()
        rec = st.setdefault(bot_id, {})
        acc = rec.setdefault("_leg_acc", {})
        acc[token] = float(acc.get(token, 0.0)) + float(pnl_usd or 0.0)
        if not fully_closed:
            _save_state(st)
            return
        net = acc.pop(token, 0.0)
        closes = rec.setdefault("recent_closes", [])
        closes.append({"ts": time.time(), "win": net > 0,
                       "net": round(net, 2), "archetype": archetype or "default"})
        del closes[:-20]
        _save_state(st)
        # bust the entries cache so a fresh loss is felt immediately
        _entries_cache.pop(bot_id, None)
    except Exception as e:
        logger.debug("[Chameleon] record_close failed: %s", e)


def status() -> dict:
    """For the dashboard: current tune state of every chameleon."""
    return _load_state()
