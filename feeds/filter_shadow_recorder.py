"""Pre-gate filter shadow recorder.

Persists every recorded filter decision (BLOCK or PASS) with the candidate's
DexScreener-level feature snapshot, so we can retrospectively compute the
realized P&L of tokens blocked by each filter — the "what would have happened"
audit that drives carve-out / shadow-promotion decisions.

Output: /data/filter_shadow_log.jsonl, append-only, one JSON line per record.
Each line is small (~600 bytes) — at ~10 filter checks/cycle and 120 cycles/hr
that's ~70 MB/day, fine for the Railway volume.

Schema:
    ts                  ISO timestamp (UTC)
    token_address       base token mint
    token_symbol        DS symbol
    pair_address        pool address (for forward candle fetch)
    filter_name         "filter_chasing_bounce" etc.
    verdict             "BLOCK" or "PASS"
    block_reasons       free-text from the filter logger line
    pc_h24, pc_h6, pc_h1, pc_m5   priceChange percentages
    bs_h6, bs_h1, bs_m5            buy/sell ratios (DS txns)
    vol_h24             24h volume USD
    liquidity_usd       current LP USD
    mcap                market cap USD

Outcome stamping happens later via a separate scanner that pulls forward
30/60min candles from DS/GT and computes realized strategy-cap P&L per record.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DISK_GUARD_FREE_PCT = 0.05
_WARN_THROTTLE_S = 300.0


def _safe_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class FilterShadowRecorder:
    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self._last_disk_warn = 0.0
        self.records_written = 0

    def _disk_has_space(self) -> bool:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            total, _used, free = shutil.disk_usage(str(self.log_path.parent))
            if total > 0 and free / total < _DISK_GUARD_FREE_PCT:
                now = time.time()
                if now - self._last_disk_warn > _WARN_THROTTLE_S:
                    logger.warning(
                        f"[FilterShadow] disk <5% free; dropping writes"
                    )
                    self._last_disk_warn = now
                return False
            return True
        except Exception:
            return True

    def record(
        self,
        token_address: str,
        token_symbol: str,
        pair: dict,
        filter_name: str,
        verdict: str,
        block_reasons: str = "",
    ) -> bool:
        """Write one record. verdict in {"BLOCK", "PASS"}."""
        if not self._disk_has_space():
            return False
        try:
            pc = pair.get("priceChange") or {}
            txns = pair.get("txns") or {}
            vol = pair.get("volume") or {}
            liq = pair.get("liquidity") or {}

            def _bs(window: str) -> Optional[float]:
                t = txns.get(window) or {}
                b = int(t.get("buys") or 0)
                s = int(t.get("sells") or 0)
                if s > 0:
                    return b / s
                if b > 0:
                    return float("inf")
                return 0.0

            bsh6 = _bs("h6")
            bsh1 = _bs("h1")
            bsm5 = _bs("m5")
            rec = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "token_address": token_address,
                "token_symbol": token_symbol,
                "pair_address": pair.get("pairAddress") or "",
                "filter_name": filter_name,
                "verdict": verdict,
                "block_reasons": block_reasons,
                "pc_h24": _safe_float(pc.get("h24")),
                "pc_h6": _safe_float(pc.get("h6")),
                "pc_h1": _safe_float(pc.get("h1")),
                "pc_m5": _safe_float(pc.get("m5")),
                "bs_h6": None if bsh6 == float("inf") else bsh6,
                "bs_h1": None if bsh1 == float("inf") else bsh1,
                "bs_m5": None if bsm5 == float("inf") else bsm5,
                "vol_h24": _safe_float(vol.get("h24")),
                "liquidity_usd": _safe_float(liq.get("usd")),
                "mcap": _safe_float(pair.get("marketCap")),
            }
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            self.records_written += 1
            return True
        except Exception as e:
            logger.debug(f"[FilterShadow] record err: {e}")
            return False


_singleton: Optional[FilterShadowRecorder] = None


def get_recorder() -> FilterShadowRecorder:
    global _singleton
    if _singleton is None:
        data_dir = os.environ.get("DATA_DIR", "/data")
        path = os.path.join(data_dir, "filter_shadow_log.jsonl")
        _singleton = FilterShadowRecorder(log_path=path)
    return _singleton
