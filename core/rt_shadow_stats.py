"""Running aggregate of RT shadow-mode observations (rt-trigger + rt-demand-turn).

Gives a TRUE running tally of the real-time-detection shadow divergences instead
of sampling the rolling log tail. In-memory O(1) accumulation, persisted to
DATA_DIR/rt_shadow_stats.json, exposed via GET /api/rt-shadow.

FAIL-OPEN: every public entry point swallows errors at debug level — this is
observability and MUST NEVER raise into the trading/scan path.

Key metric — `catastrophic_miss`: a token where the STALE snapshot would NOT see a
deep dip (snap_pc_h1 > threshold) but the FRESH price does (fresh_pc_h1 <= threshold).
That is exactly the deep-flush entry the stale trigger is blind to and the fresh
trigger catches (the HERALD class).
"""
import os
import json
import logging
import threading

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATS = None  # lazy-loaded dict
_SINCE_RECORDS = 0


def _dip_threshold() -> float:
    try:
        return float(os.environ.get("RT_SHADOW_DIP_THRESHOLD", "-16"))
    except (TypeError, ValueError):
        return -16.0


def _flush_every() -> int:
    try:
        return max(1, int(os.environ.get("RT_SHADOW_FLUSH_EVERY", "100")))
    except (TypeError, ValueError):
        return 100


def _path() -> str:
    return os.path.join(os.environ.get("DATA_DIR", "/data"), "rt_shadow_stats.json")


def empty_stats() -> dict:
    return {
        "since_ts": None,
        "updated_ts": None,
        "dip_threshold": _dip_threshold(),
        "trigger": {
            "n": 0,
            "n_catastrophic_miss": 0,   # stale > thr but fresh <= thr (fresh catches a deep dip stale misses)
            "n_catastrophic_false": 0,  # stale <= thr but fresh > thr (stale sees a deep dip that isn't real)
            "n_fresh_deeper": 0,        # fresh strictly more negative than stale
            "n_fresh_shallower": 0,     # fresh strictly less negative than stale
            "sum_abs_div": 0.0,         # sum |fresh - snap| (pct points)
            "max_abs_div": 0.0,
        },
        "demand": {
            "n": 0,
            "n_fetch_ok": 0,
            "n_turn_ok": 0,
            "sum_imb": 0.0,
        },
    }


def update_trigger(stats: dict, snap_pc, fresh_pc, thr: float) -> dict:
    """PURE: fold one rt-trigger observation into stats['trigger']. Never raises."""
    t = stats["trigger"]
    try:
        s = float(snap_pc)
        f = float(fresh_pc)
    except (TypeError, ValueError):
        return stats
    t["n"] += 1
    ad = abs(f - s)
    t["sum_abs_div"] += ad
    if ad > t["max_abs_div"]:
        t["max_abs_div"] = ad
    if s > thr and f <= thr:
        t["n_catastrophic_miss"] += 1
    elif s <= thr and f > thr:
        t["n_catastrophic_false"] += 1
    if f < s - 1e-9:
        t["n_fresh_deeper"] += 1
    elif f > s + 1e-9:
        t["n_fresh_shallower"] += 1
    return stats


def update_demand(stats: dict, fetch_ok, fresh_imb, turn_ok) -> dict:
    """PURE: fold one rt-demand-turn observation into stats['demand']. Never raises."""
    d = stats["demand"]
    d["n"] += 1
    if fetch_ok:
        d["n_fetch_ok"] += 1
    if turn_ok:
        d["n_turn_ok"] += 1
    try:
        d["sum_imb"] += float(fresh_imb)
    except (TypeError, ValueError):
        pass
    return stats


def derive(stats: dict) -> dict:
    """PURE: add derived rates to a COPY of stats for reporting. Never raises."""
    out = json.loads(json.dumps(stats))  # deep copy
    t = out.get("trigger", {})
    n = t.get("n", 0) or 0
    if n:
        t["mean_abs_div"] = round(t["sum_abs_div"] / n, 3)
        t["catastrophic_miss_rate"] = round(t["n_catastrophic_miss"] / n, 4)
        t["catastrophic_false_rate"] = round(t["n_catastrophic_false"] / n, 4)
    d = out.get("demand", {})
    dn = d.get("n", 0) or 0
    if dn:
        d["fetch_ok_rate"] = round(d["n_fetch_ok"] / dn, 4)
        d["turn_ok_rate"] = round(d["n_turn_ok"] / dn, 4)
        d["mean_imb"] = round(d["sum_imb"] / dn, 4)
    return out


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def load() -> dict:
    global _STATS
    if _STATS is not None:
        return _STATS
    try:
        with open(_path()) as f:
            _STATS = json.load(f)
        # forward-compat: backfill any missing keys
        base = empty_stats()
        for sect in ("trigger", "demand"):
            for k, v in base[sect].items():
                _STATS.setdefault(sect, base[sect])
                _STATS[sect].setdefault(k, v)
    except Exception:
        _STATS = empty_stats()
        _STATS["since_ts"] = _now_iso()
    return _STATS


def save() -> None:
    try:
        with _LOCK:
            s = _STATS if _STATS is not None else empty_stats()
            s["updated_ts"] = _now_iso()
            tmp = _path() + ".tmp"
            with open(tmp, "w") as f:
                json.dump(s, f, separators=(",", ":"))
            os.replace(tmp, _path())
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[rt-shadow-stats] save failed: %s", e)


def record_trigger(snap_pc, fresh_pc) -> None:
    """Public: record one rt-trigger observation. Fail-open; flushes every N."""
    global _SINCE_RECORDS
    try:
        with _LOCK:
            s = load()
            if s.get("since_ts") is None:
                s["since_ts"] = _now_iso()
            update_trigger(s, snap_pc, fresh_pc, _dip_threshold())
            _SINCE_RECORDS += 1
            _flush = _SINCE_RECORDS >= _flush_every()
            if _flush:
                _SINCE_RECORDS = 0
        if _flush:
            save()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[rt-shadow-stats] record_trigger failed: %s", e)


def record_demand(fetch_ok, fresh_imb, turn_ok) -> None:
    """Public: record one rt-demand-turn observation. Fail-open; flushes every N."""
    global _SINCE_RECORDS
    try:
        with _LOCK:
            s = load()
            if s.get("since_ts") is None:
                s["since_ts"] = _now_iso()
            update_demand(s, fetch_ok, fresh_imb, turn_ok)
            _SINCE_RECORDS += 1
            _flush = _SINCE_RECORDS >= _flush_every()
            if _flush:
                _SINCE_RECORDS = 0
        if _flush:
            save()
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("[rt-shadow-stats] record_demand failed: %s", e)


def snapshot() -> dict:
    """Public: derived running tally for /api/rt-shadow. Fail-open -> empty."""
    try:
        with _LOCK:
            return derive(load())
    except Exception:  # pragma: no cover - defensive
        return derive(empty_stats())
