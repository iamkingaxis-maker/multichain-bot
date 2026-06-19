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
        """Write one record. verdict in {"BLOCK", "PASS"}.

        Legacy single-write path (5 direct callers). Delegates to the pure
        ``build_record`` + the batched ``write_records`` so there is exactly
        ONE code path that touches disk.
        """
        try:
            rec = build_record(
                token_address, token_symbol, pair, filter_name, verdict,
                block_reasons,
            )
        except Exception as e:  # pragma: no cover - defensive; build is pure
            logger.debug(f"[FilterShadow] build err: {e}")
            return False
        return self.write_records([rec]) > 0

    def write_records(self, records) -> int:
        """Flush a BATCH of pre-built record dicts in ONE pass.

        ONE disk-space check + ONE cap_jsonl + ONE open("a") that writes every
        line. Returns the count actually written. FAIL-OPEN: never raises; on
        any error (no space, bad path, IO failure) returns the count written
        so far (0 on a hard failure). This is the SOLE disk writer.
        """
        if not records:
            return 0
        if not self._disk_has_space():
            return 0
        try:
            try:
                from core.jsonl_rotation import cap_jsonl
                cap_jsonl(self.log_path)
            except Exception:
                pass
            written = 0
            with open(self.log_path, "a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec) + "\n")
                    written += 1
            self.records_written += written
            return written
        except Exception as e:
            logger.debug(f"[FilterShadow] write_records err: {e}")
            return 0


def build_record(
    token_address: str,
    token_symbol: str,
    pair: dict,
    filter_name: str,
    verdict: str,
    reasons: str = "",
) -> dict:
    """Build ONE record dict — PURE CPU, NO file I/O (no open/disk_usage).

    Same field shape ``record()`` historically wrote. Cheap enough to call
    on the event loop; the actual disk write is batched off-loop via
    ``write_records``. ``verdict`` is stored verbatim here (callers normalize
    via ``_normalize_verdict`` before building).
    """
    pair = pair or {}
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
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "token_address": token_address,
        "token_symbol": token_symbol,
        "pair_address": pair.get("pairAddress") or "",
        "filter_name": filter_name,
        "verdict": verdict,
        "block_reasons": reasons,
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


def write_records(records) -> int:
    """Module-level batched writer — FAIL-OPEN. Delegates to the singleton
    recorder so the off-loop scanner flush has a single import target."""
    try:
        return get_recorder().write_records(records)
    except Exception:  # pragma: no cover - defensive; must never raise
        return 0


_singleton: Optional[FilterShadowRecorder] = None


def get_recorder() -> FilterShadowRecorder:
    global _singleton
    if _singleton is None:
        data_dir = os.environ.get("DATA_DIR", "/data")
        path = os.path.join(data_dir, "filter_shadow_log.jsonl")
        _singleton = FilterShadowRecorder(log_path=path)
    return _singleton


def _normalize_verdict(verdict: str) -> str:
    """Canonicalize a raw filter verdict to "BLOCK" or "PASS".

    Several filter sites emit non-canonical strings — notably
    filter_chasing_top's "SHADOW_BLOCK" — which the forward-candle scorer
    buckets literally and therefore drops from the PASS-vs-BLOCK diff. Any
    verdict that is not an explicit PASS is treated as a BLOCK (the
    conservative bucket: a "would-block" is a block for scoring purposes).
    """
    v = (verdict or "").strip().upper()
    if v == "PASS":
        return "PASS"
    return "BLOCK"


def record_verdict(
    token_address: str,
    token_symbol: str,
    pair: dict,
    filter_name: str,
    verdict: str,
    reasons: str = "",
) -> None:
    """Thin, FAIL-OPEN wrapper around the singleton recorder used to wire the
    ~35 previously-uncaptured filter_* would-block sites in one line each.

    * Gated by env FILTER_SHADOW_CAPTURE_MODE (default 'on'; 'off' = dormant,
      no recording at all — used to disable the expanded capture wholesale).
    * verdict is NORMALIZED to "BLOCK"/"PASS" (SHADOW_BLOCK -> BLOCK) so the
      scorer stops dropping non-canonical verdicts.
    * NEVER raises into the scan/buy path — any error is swallowed. This is
      pure observability; it must not alter what we trade.
    * ADDRESS-keyed: token_address is the join key — always pass the real mint,
      never symbol-only.

    NOTE: filter_stale_watch is owned by core/shadow_gate_log.py (a routing
    gate, trade-join scored) — do NOT record it here (would double-count).
    """
    try:
        if os.environ.get("FILTER_SHADOW_CAPTURE_MODE", "on").strip().lower() == "off":
            return
        get_recorder().record(
            token_address=token_address,
            token_symbol=token_symbol,
            pair=pair or {},
            filter_name=filter_name,
            verdict=_normalize_verdict(verdict),
            block_reasons=reasons or "",
        )
    except Exception:  # pragma: no cover - defensive; must never raise
        pass
