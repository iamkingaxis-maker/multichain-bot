"""
UptrendScanner — green-tape companion to DipScanner.

PHASE 1: SHADOW ONLY. Emits "WOULD-FIRE" / "WOULD-BLOCK" decisions to a JSONL
log without executing any buys. Use accumulated forward data to validate
triggers before promoting to live.

STRATEGY THESIS
---------------
DipScanner targets transitional/mixed regime (mtf=mixed, 5m_state=downtrend
within bullish higher TFs). It is silent in pure-bull regimes because
filter_chasing_top correctly rejects tokens with mtf=bull AND 5m_state=uptrend.

UptrendScanner fills that gap: it targets the SAME tokens dip_scanner rejects
as "chasing the top", but adds its own gates to avoid late entries.

The two strategies are complementary:
  - dip_buy fires when the bot expects MEAN REVERSION (pullback in uptrend)
  - uptrend fires when the bot expects TREND CONTINUATION (breakout / new HH)

DESIGN INVARIANTS
-----------------
1. Re-uses existing infra — no new HTTP calls. Operates on the chart_ctx_dict
   already computed by the dip_scanner pipeline. Hook point: right after
   chart_reader runs in feeds/dip_scanner.py (~line 2370).

2. SHADOW only — never blocks or buys anything. Emits log lines and writes
   structured JSONL records for offline analysis.

3. Phantom parity — each entry trigger has a mirror in
   scripts/live_forward_test.py COMBOS (GG/HH/II) so we can validate against
   external forward data, not just our internal recorder.

GATES (pre-filters — must ALL pass to even consider triggers)
-------------------------------------------------------------
  G1 chart_mtf_alignment in {bull, strong_bull}        — must be in confirmed uptrend regime
  G2 chart_structure_5m_state == "uptrend"             — 5m must be trending up
  G3 peak_h24 (lifecycle_pct_from_h24_peak) >= -50     — must be within 50% of 24h high (not past)
  G4 lifecycle_h24_ratio >= 0.70                       — price within 30% of recent peak
  G5 chart_score >= 50                                 — basic chart-quality floor

If any gate fails -> WOULD-BLOCK with gate_block_reasons; do NOT evaluate triggers.

TRIGGERS (Phase 1 — 3 minimum-viable entry patterns)
----------------------------------------------------
T1 breakout_resist: chart_structure_5m_recent_bos_dir == "up"
                    AND 1m_volume_spike >= 1.5
                    AND chart_vp_above_poc == True

T2 range_expansion: chart_trendline_5m_breakout_up == True
                    AND chart_pattern_5m_dir == "bullish"
                    AND chart_pattern_5m_conf >= 60

T3 continuation:    chart_score >= 60
                    AND chart_reaccum_verdict in {"accum", "trending"}
                    AND chart_sr_5m_at_resistance == False
                    AND chart_vp_above_poc == True

If any trigger fires -> WOULD-FIRE; record all triggers + gate features.

PHASE 1 OUTPUT
--------------
1. Per-decision log line:
   [UptrendScanner] WOULD-FIRE: TOKEN triggers=T1,T2 reasons=...
   [UptrendScanner] WOULD-BLOCK: TOKEN gate=G2 (5m not uptrend)
   [UptrendScanner] NO-TRIGGER: TOKEN — gates passed, no entry trigger matched

2. JSONL record per evaluation appended to {DATA_DIR}/uptrend_shadow.jsonl.
   Schema:
     {
       "ts": iso8601 UTC,
       "token": symbol,
       "address": addr,
       "outcome": "WOULD-FIRE" | "WOULD-BLOCK" | "NO-TRIGGER",
       "triggers_fired": [...],
       "gate_blocks": [...],
       "features": {chart features captured at decision time},
     }

PHASE 2 (NOT IMPLEMENTED YET)
-----------------------------
After ~24-48h of shadow data:
  - Pair each WOULD-FIRE with the token's forward outcome (price action 5min,
    15min, 1h ahead). Define "win" as forward_max_pct >= 5% before -10% drop.
  - Tune triggers if WR < 50%.
  - Promote to live with conservative sizing once forward WR >= 55%.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class UptrendScannerShadow:
    """Stateful shadow evaluator. Stateless logic, but holds an output path
    + record counter for visibility in logs."""

    def __init__(self, output_path: str | None = None) -> None:
        if output_path is None:
            data_dir = os.environ.get("DATA_DIR", ".")
            output_path = os.path.join(data_dir, "uptrend_shadow.jsonl")
        self._output_path = Path(output_path)
        try:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        self._records_written = 0

    # ── Gates ───────────────────────────────────────────────────────────
    @staticmethod
    def _check_gates(
        chart_ctx: dict,
        peak_h24_6h_pct: float | None,
        lifecycle_h24_ratio: float | None,
    ) -> list[str]:
        """Return list of failed gate reasons. Empty list = all gates passed."""
        blocks: list[str] = []

        mtf = chart_ctx.get("chart_mtf_alignment")
        if mtf not in ("bull", "strong_bull"):
            blocks.append(f"G1 mtf={mtf} not in (bull, strong_bull)")

        state = chart_ctx.get("chart_structure_5m_state")
        if state != "uptrend":
            blocks.append(f"G2 5m_state={state} != uptrend")

        # G3: not "past the move" — peak_h24 measures pct gain from low,
        # but we want to know if we're at the top of a big move. Use the
        # lifecycle ratio for that (closer to 1.0 = near peak = late).
        # Skipping G3 in favor of G4 which uses the cleaner signal.

        if isinstance(lifecycle_h24_ratio, (int, float)) and lifecycle_h24_ratio < 0.70:
            blocks.append(f"G4 lifecycle_h24_ratio={lifecycle_h24_ratio:.2f}<0.70")

        score = chart_ctx.get("chart_score")
        if isinstance(score, (int, float)) and score < 50:
            blocks.append(f"G5 chart_score={score:.1f}<50")

        return blocks

    # ── Triggers ────────────────────────────────────────────────────────
    @staticmethod
    def _trigger_breakout_resist(chart_ctx: dict, m1_features: dict) -> tuple[bool, str]:
        bos_dir = chart_ctx.get("chart_structure_5m_recent_bos_dir")
        vol_spike = m1_features.get("1m_volume_spike")
        above_poc = chart_ctx.get("chart_vp_above_poc")
        ok = (
            bos_dir == "up"
            and isinstance(vol_spike, (int, float)) and vol_spike >= 1.5
            and above_poc is True
        )
        return ok, f"bos=up AND 1m_vol_spike={vol_spike}>=1.5 AND vp_above_poc=True"

    @staticmethod
    def _trigger_range_expansion(chart_ctx: dict) -> tuple[bool, str]:
        breakout_up = chart_ctx.get("chart_trendline_5m_breakout_up")
        pat_dir = chart_ctx.get("chart_pattern_5m_dir")
        pat_conf = chart_ctx.get("chart_pattern_5m_conf")
        ok = (
            breakout_up is True
            and pat_dir == "bullish"
            and isinstance(pat_conf, (int, float)) and pat_conf >= 60
        )
        return ok, f"trendline_break_up=True AND pattern=bullish@{pat_conf}>=60"

    @staticmethod
    def _trigger_continuation(chart_ctx: dict) -> tuple[bool, str]:
        score = chart_ctx.get("chart_score")
        reaccum = chart_ctx.get("chart_reaccum_verdict")
        at_resist = chart_ctx.get("chart_sr_5m_at_resistance")
        above_poc = chart_ctx.get("chart_vp_above_poc")
        ok = (
            isinstance(score, (int, float)) and score >= 60
            and reaccum in ("accum", "trending")
            and at_resist is not True
            and above_poc is True
        )
        return ok, f"score={score}>=60 AND reaccum={reaccum} AND at_resist=False AND above_poc=True"

    # ── Main entry point ────────────────────────────────────────────────
    def evaluate(
        self,
        token_symbol: str,
        token_address: str,
        chart_ctx_dict: dict | None,
        m1_features: dict | None,
        peak_h24_6h_pct: float | None = None,
        lifecycle_h24_ratio: float | None = None,
    ) -> dict[str, Any]:
        """Evaluate one token for uptrend-entry. SHADOW ONLY — never blocks."""
        chart_ctx = chart_ctx_dict or {}
        m1 = m1_features or {}

        # Gate check
        gate_blocks = self._check_gates(chart_ctx, peak_h24_6h_pct, lifecycle_h24_ratio)
        if gate_blocks:
            return self._record(
                token_symbol, token_address, chart_ctx, m1,
                outcome="WOULD-BLOCK", triggers_fired=[], gate_blocks=gate_blocks,
            )

        # Trigger evaluation
        triggers_fired: list[str] = []
        trigger_reasons: list[str] = []
        for name, fn in (
            ("breakout_resist", lambda: self._trigger_breakout_resist(chart_ctx, m1)),
            ("range_expansion", lambda: self._trigger_range_expansion(chart_ctx)),
            ("continuation", lambda: self._trigger_continuation(chart_ctx)),
        ):
            try:
                ok, reason = fn()
                if ok:
                    triggers_fired.append(name)
                    trigger_reasons.append(f"{name}: {reason}")
            except Exception:
                continue

        outcome = "WOULD-FIRE" if triggers_fired else "NO-TRIGGER"
        return self._record(
            token_symbol, token_address, chart_ctx, m1,
            outcome=outcome, triggers_fired=triggers_fired, gate_blocks=[],
            trigger_reasons=trigger_reasons,
        )

    # ── Record + log ────────────────────────────────────────────────────
    def _record(
        self,
        token_symbol: str,
        token_address: str,
        chart_ctx: dict,
        m1: dict,
        outcome: str,
        triggers_fired: list[str],
        gate_blocks: list[str],
        trigger_reasons: list[str] | None = None,
    ) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        # Capture a compact snapshot of decision-time features
        features = {
            "mtf": chart_ctx.get("chart_mtf_alignment"),
            "5m_state": chart_ctx.get("chart_structure_5m_state"),
            "bos_dir": chart_ctx.get("chart_structure_5m_recent_bos_dir"),
            "chart_score": chart_ctx.get("chart_score"),
            "reaccum": chart_ctx.get("chart_reaccum_verdict"),
            "trendline_break_up": chart_ctx.get("chart_trendline_5m_breakout_up"),
            "pattern_5m": chart_ctx.get("chart_pattern_5m"),
            "pattern_5m_dir": chart_ctx.get("chart_pattern_5m_dir"),
            "pattern_5m_conf": chart_ctx.get("chart_pattern_5m_conf"),
            "vp_above_poc": chart_ctx.get("chart_vp_above_poc"),
            "sr_5m_at_resist": chart_ctx.get("chart_sr_5m_at_resistance"),
            "1m_vol_spike": m1.get("1m_volume_spike"),
            "1m_last_close_pct": m1.get("1m_last_close_pct"),
        }

        rec = {
            "ts": ts,
            "token": token_symbol,
            "address": token_address,
            "outcome": outcome,
            "triggers_fired": triggers_fired,
            "gate_blocks": gate_blocks,
            "trigger_reasons": trigger_reasons or [],
            "features": features,
        }

        # Log line — visible in Railway logs for quick inspection
        if outcome == "WOULD-FIRE":
            logger.info(
                f"[UptrendScanner] WOULD-FIRE: {token_symbol} "
                f"triggers={','.join(triggers_fired)} "
                f"mtf={features['mtf']} 5m={features['5m_state']} "
                f"score={features['chart_score']}"
            )
        elif outcome == "WOULD-BLOCK":
            logger.info(
                f"[UptrendScanner] WOULD-BLOCK: {token_symbol} "
                f"gates={','.join(gate_blocks)}"
            )
        # NO-TRIGGER is silent (would be too verbose — most tokens land here)

        # Persist
        try:
            try:
                from core.jsonl_rotation import cap_jsonl
                cap_jsonl(self._output_path)
            except Exception:
                pass
            with self._output_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            self._records_written += 1
            if self._records_written % 100 == 0:
                logger.info(
                    f"[UptrendScanner] {self._records_written} records written"
                )
        except Exception as e:
            logger.debug(f"[UptrendScanner] write error: {e}")

        return rec


# Module-level singleton — lazy init
_INSTANCE: UptrendScannerShadow | None = None


def get_instance() -> UptrendScannerShadow:
    """Return module-level singleton. Lazy-init."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = UptrendScannerShadow()
    return _INSTANCE
