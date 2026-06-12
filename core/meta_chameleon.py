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
RETUNE_MIN_SECS = 6 * 3600.0
CHECK_MIN_SECS = 900.0
QUALIFY_WR = 0.60
QUALIFY_N = 8

CLAMPS = {
    "time_stop_minutes": (10.0, 780.0),
    "tp1_pct": (8.0, 60.0),
    "hard_stop_pct": (-60.0, -10.0),
}

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
        return {
            "time_stop_minutes": _clamp("time_stop_minutes", hold / 60.0),
            "tp1_pct": _clamp("tp1_pct", win),
            "hard_stop_pct": _clamp("hard_stop_pct",
                                    (loss * 1.2) if isinstance(loss, (int, float)) and loss < 0
                                    else -60.0),
        }
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
        object.__setattr__(config, k, v)


def apply_overlay(config) -> None:
    """Boot-time re-apply of the persisted tune (deploy-amnesia guard).
    Called at bot registration, BEFORE/as positions restore — restored
    positions were opened under this tune, so it's the correct geometry."""
    if not enabled():
        return
    st = _load_state().get(config.bot_id)
    if st and isinstance(st.get("tune"), dict):
        try:
            _apply(config, {k: float(v) for k, v in st["tune"].items() if k in CLAMPS})
            logger.info("[Chameleon] %s boot overlay applied: %s (archetype=%s, tuned %s)",
                        config.bot_id, st["tune"], st.get("archetype"),
                        st.get("tuned_at_iso"))
        except Exception as e:
            logger.warning("[Chameleon] overlay apply failed for %s: %s", config.bot_id, e)


def best_qualifying(sensor, now: float):
    """(archetype, geometry) of the best 6h archetype, or (None, None)."""
    try:
        board = sensor.scoreboard(now).get("windows", {}).get("6h", {})
    except Exception:
        return None, None
    best, best_geo = None, None
    for arch, row in board.items():
        if arch == "all":
            continue
        if row.get("n", 0) < QUALIFY_N or row.get("wr", 0) < QUALIFY_WR:
            continue
        geo = sensor.archetype_geometry(arch, now, min_n=QUALIFY_N)
        if not geo:
            continue
        if best_geo is None or (geo["wr"], geo["n"]) > (best_geo["wr"], best_geo["n"]):
            best, best_geo = arch, geo
    return best, best_geo


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
            # 1) a deferred tune applies as soon as the book is flat
            if pending and not list(pm.iter_positions()):
                _apply(pm.config, pending["tune"])
                rec.update({"tune": pending["tune"], "archetype": pending["archetype"],
                            "geometry": pending.get("geometry"),
                            "tuned_at": now, "tuned_at_iso": _iso(now), "pending": None})
                st[bot_id] = rec
                _save_state(st)
                logger.info("[Chameleon] %s RETUNED (deferred) -> %s [archetype=%s]",
                            bot_id, pending["tune"], pending["archetype"])
                continue
            # 2) cadence gate for NEW tunes
            if now - float(rec.get("tuned_at") or 0) < RETUNE_MIN_SECS:
                continue
            arch, geo = best_qualifying(sensor, now)
            if not arch:
                continue   # HOLD current tune — never reset mid-day
            tune = tune_from_geometry(geo)
            if not tune or tune == rec.get("tune"):
                continue
            if list(pm.iter_positions()):
                rec["pending"] = {"tune": tune, "archetype": arch, "geometry": geo}
                st[bot_id] = rec
                _save_state(st)
                logger.info("[Chameleon] %s tune QUEUED (book not flat): %s [%s]",
                            bot_id, tune, arch)
                continue
            _apply(pm.config, tune)
            rec.update({"tune": tune, "archetype": arch, "geometry": geo,
                        "tuned_at": now, "tuned_at_iso": _iso(now), "pending": None})
            st[bot_id] = rec
            _save_state(st)
            logger.info("[Chameleon] %s RETUNED -> %s [archetype=%s wr=%.0f%% n=%d]",
                        bot_id, tune, arch, geo["wr"] * 100, geo["n"])
    except Exception as e:
        logger.debug("[Chameleon] maybe_retune error: %s", e)


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def status() -> dict:
    """For the dashboard: current tune state of every chameleon."""
    return _load_state()
