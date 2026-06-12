"""META ALLOCATOR — SHADOW (2026-06-12, AxiS's meta-rotation thesis).

Observation (validated on the 05-30→06-12 multi-family ledger): which entry
FAMILY wins rotates day to day, with ZERO day-over-day predictability from
trailing performance (family-rank Spearman D→D+1 mean +0.02; backing each
morning's leader for the rest of the day LOSES to equal-weight). So a
performance-chasing rotator is dead on arrival — and this is also why mined
entry patterns keep dying (winner's curse: mined at the meta's peak, deployed
into the rotation).

What IS predictive is observable MARKET STATE, concurrently:
  - young family wins on SOL-red days (3/5 red days; matches the 49-day
    regime study: dip edge wants SOL modestly red, low downside breadth)
  - momentum family wins ONLY on SOL-green days and was the WORST family on
    five non-green days (-$4.86, -$4.59, -$19.07/close ...)

This module is the SHADOW: every scan cycle it ingests the observable state
(SOL h24, downside breadth of the scanned universe, badday flush-envelope
count, pp_launch firehose rate), snapshots an hourly state vector + the V1
state→family size multipliers it WOULD apply, and persists them to
DATA_DIR/meta_allocator_shadow.jsonl. Nothing reads the proposal at buy time —
sizing is UNCHANGED. scripts/meta_allocator_report.py joins the shadow log to
realized family $/close so the table earns (or fails) enforcement on forward
data. Pre-registered bar: >=14 shadow days, shadow-weighted family P&L beats
flat-weight, before any multiplier is enforced.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_SHADOW_FILE = os.path.join(_DATA_DIR, "meta_allocator_shadow.jsonl")

# Family = entry-archetype grouping. Single source of truth for allocator +
# offline report (scripts import this).
_FAMILY_PREFIXES = [
    ("young_probe", "young"), ("badday", "badday"), ("pond_", "pond"),
    ("pool_a", "pool_a"), ("pool_c", "pool_c"), ("champion", "champion"),
    ("champ_", "champion"), ("momentum", "momentum"), ("timebox", "timebox"),
    ("probe_swing", "swing"), ("smart_follow", "follow"),
    ("baseline", "baseline"),
]


def family_of(bot_id: Optional[str]) -> Optional[str]:
    if not bot_id:
        return None
    for pre, fam in _FAMILY_PREFIXES:
        if bot_id.startswith(pre):
            return fam
    return None


# V1 state→family table. PRE-REGISTERED 2026-06-12 from the 11-day board +
# the 06-08 day-level regime study — deliberately only THREE rules (the links
# with multi-day evidence), everything else 1.0. Tuning this table on the same
# days that suggested it = winner's curse; it changes only on FORWARD shadow
# evidence.
SOL_GREEN = 1.0     # sol_pc_h24 >= this → "green" tape
SOL_RED = -1.0      # sol_pc_h24 <= this → "red" tape
BREADTH_BROAD_RED = 0.75   # share of scanned universe with pc_h1<0 above this
                           # = broad-red (dip edge OFF per the 49-day study)


def propose(sol_h24: Optional[float], breadth_neg: Optional[float]) -> Dict[str, float]:
    """State → family size multipliers (the would-be dial; SHADOW only)."""
    mult: Dict[str, float] = {fam: 1.0 for _, fam in _FAMILY_PREFIXES}
    if sol_h24 is None:
        return mult   # state unknown → flat (fail-neutral)
    # Rule 1: momentum is a green-tape specialist — worst family on 5 of 5
    # recent non-green days, best on the 2 green ones.
    mult["momentum"] = 1.5 if sol_h24 >= SOL_GREEN else 0.5
    # Rule 2: young/dip edge wants SOL modestly red with NON-broad downside
    # breadth (49-day study: SOL-green/broad-red = edge off).
    if sol_h24 <= SOL_RED and (breadth_neg is None or breadth_neg <= BREADTH_BROAD_RED):
        mult["young"] = 1.5
    elif breadth_neg is not None and breadth_neg > BREADTH_BROAD_RED:
        mult["young"] = 0.5
    # Rule 3: badday family is the broad-red specialist (it was built for
    # exactly that tape; 06-12 dial-bad day: flush +$1.02/close while the
    # board bled).
    if breadth_neg is not None and breadth_neg >= BREADTH_BROAD_RED:
        mult["badday"] = 1.5
    return mult


class MetaAllocatorShadow:
    """Per-cycle state ingestion + hourly persisted snapshots. Measure-only."""

    SNAPSHOT_SECS = 3600.0

    def __init__(self, path: str = _SHADOW_FILE):
        self._path = path
        self._last_snapshot = 0.0
        # rolling within-hour accumulators (median-ish via simple lists, small)
        self._sols: list = []
        self._negs: list = []   # per-cycle breadth fractions
        self._flush_counts: list = []
        self._launch_counts: list = []

    def observe_cycle(self, sol_h24: Optional[float], breadth_neg: Optional[float],
                      flush_count: int = 0, launch_count: int = 0) -> None:
        """Called once per scan cycle with the cycle's observable state.
        Cheap; snapshots to disk at most hourly. Never raises."""
        try:
            if isinstance(sol_h24, (int, float)):
                self._sols.append(float(sol_h24))
            if isinstance(breadth_neg, (int, float)):
                self._negs.append(float(breadth_neg))
            self._flush_counts.append(int(flush_count))
            self._launch_counts.append(int(launch_count))
            now = time.time()
            if now - self._last_snapshot >= self.SNAPSHOT_SECS and (self._sols or self._negs):
                self._snapshot(now)
        except Exception as e:   # shadow must never hurt the scanner
            logger.debug("[MetaAllocator] observe failed: %s", e)

    @staticmethod
    def _med(v):
        if not v:
            return None
        s = sorted(v)
        return s[len(s) // 2]

    def _snapshot(self, now: float) -> None:
        sol = self._med(self._sols)
        neg = self._med(self._negs)
        rec = {
            "ts": now,
            "sol_h24": sol,
            "breadth_neg_h1": neg,
            "flush_envelope_per_cycle": self._med(self._flush_counts),
            "launch_candidates": self._med(self._launch_counts),
            "proposal": propose(sol, neg),
        }
        try:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            logger.info(
                "[MetaAllocator] SHADOW snapshot sol_h24=%s breadth_neg=%s -> %s",
                None if sol is None else round(sol, 2),
                None if neg is None else round(neg, 2),
                {k: v for k, v in rec["proposal"].items() if v != 1.0} or "flat",
            )
        except Exception as e:
            logger.debug("[MetaAllocator] snapshot write failed: %s", e)
        self._last_snapshot = now
        self._sols.clear()
        self._negs.clear()
        self._flush_counts.clear()
        self._launch_counts.clear()
