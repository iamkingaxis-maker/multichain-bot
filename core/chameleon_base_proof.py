"""chameleon_base_proof (2026-06-16) — slow, conservative SHADOW logger for whether a NEW bot
should earn the CHAMELEON_STATIC_BASE off the incumbent (badday_flush).

Phase A+B proved fast AND slow best-bot TRACKERS lose (leadership is non-persistent, corr ~0.1).
So the static base must change ONLY on rigorous, time-separated proof — never a trailing-best
chase. This accumulates per-bot per-UTC-day size-normalized pnl_pct (already per-dollar) and logs
an actionable WOULD-SWAP only when a challenger beats the incumbent by >= EDGE_MIN per-day across
>= MIN_DAYS distinct days where BOTH cleared MIN_DAY_N trades, pooled n >= MIN_POOLED_N. SHADOW
only (CHAMELEON_BASEPROOF_MODE); the swap itself is a MANUAL env flip. Cooldown throttles logs;
a LANE flag surfaces the population trap (a non-badday challenger needs microcap_mandate to
reproduce its sub-floor trades). Fed pnl_pct per sell leg from dip_scanner. Nothing ever mutates."""
import os
import json
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_DATA_DIR = os.environ.get("DATA_DIR", ".")
_FILE = os.path.join(_DATA_DIR, "chameleon_base_proof.json")
MIN_DAY_N = int(os.environ.get("BASEPROOF_MIN_DAY_N", "30"))
MIN_DAYS = int(os.environ.get("BASEPROOF_MIN_DAYS", "2"))
MIN_POOLED_N = int(os.environ.get("BASEPROOF_MIN_POOLED_N", "60"))
EDGE_MIN = float(os.environ.get("BASEPROOF_EDGE_MIN", "0.5"))         # pnl_pct per trade
COOLDOWN_SECS = float(os.environ.get("BASEPROOF_COOLDOWN_SECS", str(7 * 86400)))
KEEP_DAYS = 30

_rollup = None        # {bot_id: {utc_day: [sum_pnl_pct, n]}}
_last_save = 0.0
_last_log_ts = 0.0


def _load():
    global _rollup
    if _rollup is None:
        try:
            _rollup = json.load(open(_FILE))
        except Exception:
            _rollup = {}
    return _rollup


def _save():
    global _last_save
    try:
        json.dump(_rollup, open(_FILE, "w"))
        _last_save = time.time()
    except Exception:
        pass


def record(bot_id, pnl_pct, ts=None):
    """Accumulate one sell leg's size-normalized pnl_pct into the per-bot per-UTC-day rollup.
    Phantom-guarded (|pnl_pct|>300 dropped). Fail-soft; periodic persist."""
    try:
        if pnl_pct is None or abs(float(pnl_pct)) > 300:
            return
        r = _load()
        day = datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d")
        d = r.setdefault(str(bot_id), {})
        cell = d.setdefault(day, [0.0, 0])
        cell[0] += float(pnl_pct)
        cell[1] += 1
        if len(d) > KEEP_DAYS:
            for k in sorted(d)[:-KEEP_DAYS]:
                del d[k]
        if time.time() - _last_save > 300:
            _save()
    except Exception:
        pass


def check(incumbent, eligible=None):
    """Return [{challenger, beat_days, pooled_n}] for challengers that beat the incumbent by
    >= EDGE_MIN per-day on >= MIN_DAYS distinct days where BOTH cleared MIN_DAY_N, pooled
    n >= MIN_POOLED_N. eligible(bot_id)->bool filters the challenger pool. Pure (no side effects)."""
    r = _load()
    inc = r.get(incumbent)
    if not inc:
        return []
    inc_day_mean = {day: s / n for day, (s, n) in inc.items() if n >= MIN_DAY_N}
    if not inc_day_mean:
        return []
    out = []
    for bot, d in r.items():
        if bot == incumbent:
            continue
        if eligible is not None and not eligible(bot):
            continue
        beat_days = []
        pooled_n = 0
        for day, (s, n) in d.items():
            if n < MIN_DAY_N or day not in inc_day_mean:
                continue
            pooled_n += n
            if (s / n) - inc_day_mean[day] >= EDGE_MIN:
                beat_days.append(day)
        if len(beat_days) >= MIN_DAYS and pooled_n >= MIN_POOLED_N:
            out.append({"challenger": bot, "beat_days": sorted(beat_days), "pooled_n": pooled_n})
    return out


def maybe_log(incumbent, eligible=None, lane_compatible=None):
    """SHADOW: log actionable would-swaps (cooldown-throttled). lane_compatible(bot)->bool flags
    the population trap. CHAMELEON_BASEPROOF_MODE=off disables. NEVER mutates anything."""
    global _last_log_ts
    if os.environ.get("CHAMELEON_BASEPROOF_MODE", "shadow").strip().lower() == "off":
        return
    if time.time() - _last_log_ts < COOLDOWN_SECS:
        return
    try:
        findings = check(incumbent, eligible)
    except Exception:
        return
    for f in findings:
        ok = lane_compatible is None or lane_compatible(f["challenger"])
        lane = "compat" if ok else "RISK(needs microcap_mandate to reproduce sub-floor pop)"
        logger.info("[ChameleonBaseProof] WOULD-SWAP %s -> %s: beat days=%s pooled_n=%d lane=%s "
                    "(SHADOW, manual env flip only)", incumbent, f["challenger"],
                    f["beat_days"], f["pooled_n"], lane)
    if findings:
        _last_log_ts = time.time()
