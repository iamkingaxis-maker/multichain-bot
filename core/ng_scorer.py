"""Rolling never-green entry scorer (production).

Validated 2026-05-30 (see reference_rolling_ng_scorer_validated_2026_05_30):
the never-green signal (peak_pnl_pct < 1% = a pure entry-quality failure) is
real but NON-STATIONARY. A frozen model fails; a model retrained on the trailing
window transfers forward (walk-forward token-AUC ~0.64 on trades / event-AUC 0.60
on 15k universe events, both null-clean). MODEST effect — a risk-reduction tail
gate, not trash elimination.

PARITY: trains on the bot's own completed trades (entry_meta features + the
position's peak label) and scores live from FeatureBundle.raw_meta — same key
space, so no train/serve feature mismatch. Uses sklearn HistGradientBoosting
(NaN-native, already in requirements; xgboost is NOT on Railway and HGB matched
its AUC 0.594 vs 0.600).

SAFETY: fail-open everywhere (missing model / data / feature -> never blocks).
Master switch env NG_SCORER_MODE in {off (default), shadow, enforce}. Per-bot
opt-in via BotConfig.ng_scorer_gate. Every decision is logged. Model retrains
lazily with a TTL (no thread plumbing) from the trade files in DATA_DIR.
"""
from __future__ import annotations
import json
import os
import time
import threading
import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# Feature keys that are NOT predictive inputs (verdicts / outcomes / ids / text /
# regime+identity confounds). Mirrors scripts/ps_scan confound logic.
_SKIP_SUBSTR = ("verdict", "reasons", "_match", "block", "triggers_fired",
                "cnn_image", "_n_with_ts", "ts_ms", "signal_ts")
_CONFOUND = ("sol_pc", "sol_macro", "btc_pc", "btc_macro", "regime_h", "_neg_pct",
             "macro30", "macro60", "_usd", "mcap", "vwap", "price", "support_level",
             "5m_high", "5m_low", "1h_low", "1h_high", "peak_h24", "lifecycle_peak",
             "nearest_psych_level")

# Trash label = peak_pnl_pct < this. Retargeted 1.0 -> 2.0 (2026-05-31): held-out
# peak-threshold sweep (scripts/ng_threshold_sweep.py) showed peak<2 STRICTLY
# dominates peak<1 — net loss-$ cut +$2433 vs +$531, winner-$ killed $5 vs $1085,
# big-winners killed 0 vs 1. A token that never clears +2% has ~no path to profit
# after fees, so it's a near-pure loss label that SHARPENS separation (peak<3+
# dilutes). Env-tunable to retune without a code change.
NG_PEAK_THRESHOLD = float(os.environ.get("NG_SCORER_PEAK_THRESHOLD", "2.0"))
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_BLOCK_RATE = 0.10
RETRAIN_TTL_SECS = 6 * 3600      # refresh model at most every 6h
MIN_TRAIN_ROWS = 200


def _is_feat(k: str, v) -> bool:
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    kl = k.lower()
    return not any(s in kl for s in _SKIP_SUBSTR) and not any(s in kl for s in _CONFOUND)


def _pair_completed(trades: list) -> list:
    """Pair buys->sells per (bot, token) into completed positions with peak label.

    Returns dicts {t (buy iso), tok, ng (0/1), f (entry_meta feature dict)}.
    A position closes on fully_closed or cumulative sell_fraction >= ~1.0.
    """
    trades = sorted(trades, key=lambda x: x.get("time", "") or "")
    ob = defaultdict(list)
    out = []
    for tr in trades:
        bot = tr.get("bot_id"); tok = tr.get("token"); ty = (tr.get("type") or "").lower()
        if not bot or not tok:
            continue
        k = (bot, tok)
        if ty == "buy":
            ob[k].append({"buy": tr, "rem": 1.0, "peak": None})
        elif ty == "sell" and ob[k]:
            x = ob[k][0]
            fr = tr.get("sell_fraction")
            x["rem"] -= float(fr) if fr is not None else x["rem"]
            pk = tr.get("peak_pnl_pct")
            if pk is not None and (x["peak"] is None or float(pk) > x["peak"]):
                x["peak"] = float(pk)
            if tr.get("fully_closed") or x["rem"] <= 0.01:
                em = x["buy"].get("entry_meta")
                em = em if isinstance(em, dict) else x["buy"]
                if x["peak"] is not None:
                    out.append({"t": x["buy"].get("time", "") or "", "tok": tok,
                                "ng": 1 if x["peak"] < NG_PEAK_THRESHOLD else 0, "f": em})
                ob[k].pop(0)
    return out


class RollingNGScorer:
    def __init__(self, lookback_days=DEFAULT_LOOKBACK_DAYS, block_rate=DEFAULT_BLOCK_RATE):
        self.lookback_days = lookback_days
        self.block_rate = block_rate
        self.model = None
        self.feats: list[str] = []
        self.threshold = 1.0          # fail-open until trained
        self.trained_at = 0.0
        self.n_train = 0
        self._lock = threading.Lock()

    # ---- training -----------------------------------------------------------
    def _load_trades(self) -> list:
        data_dir = os.environ.get("DATA_DIR") or "/data"
        trades = []
        for name in ("trades_multi.json", "trades.json"):
            p = os.path.join(data_dir, name)
            try:
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                    trades += d if isinstance(d, list) else d.get("trades", [])
            except Exception as e:
                logger.warning(f"[ng_scorer] could not read {p}: {e}")
        return trades

    def train(self) -> bool:
        try:
            import numpy as np
            from sklearn.ensemble import HistGradientBoostingClassifier
            from sklearn.model_selection import cross_val_predict, GroupKFold
        except Exception as e:
            logger.warning(f"[ng_scorer] sklearn unavailable, scorer disabled: {e}")
            return False
        comp = _pair_completed(self._load_trades())
        if len(comp) < MIN_TRAIN_ROWS:
            logger.info(f"[ng_scorer] only {len(comp)} completed positions (<{MIN_TRAIN_ROWS}); fail-open")
            return False
        # trailing window by buy date
        days = sorted({c["t"][:10] for c in comp if c["t"]})
        keep = set(days[-self.lookback_days:]) if days else set()
        rows = [c for c in comp if c["t"][:10] in keep] or comp
        # feature universe = keys present in >=30% of rows
        cov = defaultdict(int)
        for c in rows:
            for k, v in c["f"].items():
                if _is_feat(k, v):
                    cov[k] += 1
        feats = sorted(k for k, n in cov.items() if n >= 0.30 * len(rows))
        if len(feats) < 8:
            logger.info(f"[ng_scorer] too few usable features ({len(feats)}); fail-open")
            return False
        X = np.array([[c["f"].get(k, np.nan) if isinstance(c["f"].get(k), (int, float))
                       else np.nan for k in feats] for c in rows], dtype=float)
        y = np.array([c["ng"] for c in rows])
        toks = np.array([c["tok"] for c in rows])
        if len(set(y)) < 2:
            return False
        m = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=150,
                                           l2_regularization=2.0, min_samples_leaf=20,
                                           random_state=0)
        m.fit(X, y)
        # OOS-calibrated threshold (grouped by token) -> blocks ~block_rate forward.
        try:
            g = min(5, max(2, len(set(toks))))
            oos = cross_val_predict(HistGradientBoostingClassifier(
                max_depth=3, learning_rate=0.05, max_iter=150, l2_regularization=2.0,
                min_samples_leaf=20, random_state=0), X, y, groups=toks,
                cv=GroupKFold(g), method="predict_proba")[:, 1]
        except Exception:
            oos = m.predict_proba(X)[:, 1]
        with self._lock:
            self.model = m
            self.feats = feats
            self.threshold = float(np.quantile(oos, 1.0 - self.block_rate))
            self.trained_at = time.time()
            self.n_train = len(rows)
        logger.info(f"[ng_scorer] trained on {len(rows)} positions, {len(feats)} feats, "
                    f"NG-rate {y.mean():.2f}, threshold {self.threshold:.3f}")
        return True

    def _ensure_fresh(self):
        if self.model is None or (time.time() - self.trained_at) > RETRAIN_TTL_SECS:
            try:
                self.train()
            except Exception as e:
                logger.warning(f"[ng_scorer] train failed (fail-open): {e}")

    # ---- scoring ------------------------------------------------------------
    def score(self, raw_meta: dict) -> Optional[float]:
        self._ensure_fresh()
        if self.model is None or not self.feats:
            return None
        try:
            import numpy as np
            x = np.array([[raw_meta.get(k) if isinstance(raw_meta.get(k), (int, float))
                           and not isinstance(raw_meta.get(k), bool) else np.nan
                           for k in self.feats]], dtype=float)
            return float(self.model.predict_proba(x)[0, 1])
        except Exception as e:
            logger.debug(f"[ng_scorer] score error (fail-open): {e}")
            return None

    def should_block(self, raw_meta: dict):
        """Return (block: bool, proba: float|None). Fail-open: never blocks on None."""
        p = self.score(raw_meta)
        if p is None:
            return False, None
        return p >= self.threshold, p


_SINGLETON: Optional[RollingNGScorer] = None
_SINGLETON_LOCK = threading.Lock()


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError, TypeError):
        return default


def get_scorer() -> RollingNGScorer:
    """Singleton. Block-rate + lookback are env-tunable live (no redeploy):
    NG_SCORER_BLOCK_RATE (default 0.10), NG_SCORER_LOOKBACK_DAYS (default 7).
    A changed block-rate takes effect on the next retrain (<=6h) or on restart."""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = RollingNGScorer(
                    lookback_days=int(_env_float("NG_SCORER_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
                    block_rate=_env_float("NG_SCORER_BLOCK_RATE", DEFAULT_BLOCK_RATE),
                )
    return _SINGLETON


def scorer_mode() -> str:
    """off | shadow | enforce  (env NG_SCORER_MODE, default off)."""
    return (os.environ.get("NG_SCORER_MODE") or "off").strip().lower()


DECISIONS_MAX_MB = 20


def log_decision(rec: dict) -> None:
    """Append one scorer decision to DATA_DIR/ng_scorer/decisions.jsonl.

    Enforced blocks leave NO trade record (a block = no buy) and Railway logs
    retain only ~30min, so without this we can't monitor the live gate. Append-only,
    fail-soft (never raises), rolling 20MB cap (drops oldest half). Exposed via the
    dashboard /api/ng-scorer-decisions endpoint. Outcome can be joined offline to the
    universe recorder's forward peaks by token+time for live precision/winner-kill.
    """
    try:
        d = os.path.join(os.environ.get("DATA_DIR") or "/data", "ng_scorer")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "decisions.jsonl")
        if os.path.exists(p) and os.path.getsize(p) > DECISIONS_MAX_MB * 1_000_000:
            with open(p, encoding="utf-8") as fh:
                lines = fh.readlines()
            with open(p, "w", encoding="utf-8") as fh:
                fh.writelines(lines[len(lines) // 2:])
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.debug(f"[ng_scorer] decision log failed: {e}")
