# core/young_token_probe.py
"""Young-token probe gating (2026-06-02, #4.1).

The universe mine found `age_hours <= 2` is the strongest cross-regime entry
separator (69-71% WR vs 36-40%, +24.9% forward peak), but the scanner's
`min_age_days=7.0` gate hard-skips young tokens in discovery, so the fleet's
median fill age is ~36 days — it never touches the runner-tail cohort.

This module gates a DELIBERATE, ISOLATED experiment to measure young-token
outcomes on REALISTIC dip-buy paths (the mine's signal is a 30-min forward-peak
proxy, not realized P&L) WITHOUT changing production:

- `YOUNG_TOKEN_PROBE` env flag (default OFF) -> when OFF, nothing changes: the
  scanner skips young tokens exactly as before.
- When ON: sub-min-age tokens that clear a liquidity floor are KEPT in discovery,
  but only `young_token_probe` bots may BUY them; production bots SKIP them. The
  probe bots trade YOUNG-ONLY (skip old) so the experiment is clean. So even with
  the flag on, production bots never start buying rug-prone fresh tokens.

All functions are pure (env read once at the edges) so they're unit-testable.
"""
from __future__ import annotations
import os
from typing import Optional


def _flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def probe_enabled() -> bool:
    return _flag("YOUNG_TOKEN_PROBE")


def max_age_hours() -> float:
    try:
        return float(os.environ.get("YOUNG_TOKEN_MAX_AGE_H", "2"))
    except (TypeError, ValueError):
        return 2.0


def min_liq_usd() -> float:
    try:
        return float(os.environ.get("YOUNG_TOKEN_MIN_LIQ_USD", "40000"))
    except (TypeError, ValueError):
        return 40000.0


def min_mcap_usd() -> float:
    """Young-token mcap floor for the probe path — LOWER than the fleet's $1M discovery
    floor, because young (<2h) tokens are inherently small-cap and rarely reach $1M that
    fast (that's exactly why the standard gate hides the whole cohort)."""
    try:
        return float(os.environ.get("YOUNG_TOKEN_MIN_MCAP", "150000"))
    except (TypeError, ValueError):
        return 150000.0


def is_young(age_hours, max_h: Optional[float] = None) -> bool:
    """True if the token's age (hours) is below the young threshold. None -> False."""
    if age_hours is None:
        return False
    if max_h is None:
        max_h = max_age_hours()
    try:
        return float(age_hours) < max_h
    except (TypeError, ValueError):
        return False


def keep_subminage_token(liq_usd, age_hours=None, probe_on: Optional[bool] = None,
                         min_liq: Optional[float] = None, max_h: Optional[float] = None) -> bool:
    """Discovery gate: a token younger than the fleet min_age — should it be KEPT
    (instead of skipped)? KEEP only when ALL hold: the probe is ON, the token is
    genuinely YOUNG (age < max_age_hours — NOT the whole sub-min-age range, so
    production's universe never expands to e.g. 2h-7d tokens), and it clears the young
    liquidity floor. Probe OFF -> always False -> skip as before (ZERO production change)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if not is_young(age_hours, max_h):
        return False
    if min_liq is None:
        min_liq = min_liq_usd()
    try:
        return (liq_usd or 0) >= min_liq
    except (TypeError, ValueError):
        return False


def is_young_probe_candidate(mcap, liq_usd, age_hours, max_mcap,
                             probe_on: Optional[bool] = None, max_h: Optional[float] = None,
                             min_liq: Optional[float] = None, min_mcap: Optional[float] = None) -> bool:
    """A token eligible for the young-probe discovery path: probe ON, genuinely YOUNG
    (age < max_age_hours), clears the young LIQUIDITY floor, and sits in the young MCAP
    band [young_min_mcap, max_mcap]. The lower young mcap floor is the fix for "few tokens
    reach the $1M fleet floor in <2h" — without it the whole young cohort is invisible.
    Probe OFF -> always False (the standard gates apply unchanged)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if not is_young(age_hours, max_h):
        return False
    if min_liq is None:
        min_liq = min_liq_usd()
    if min_mcap is None:
        min_mcap = min_mcap_usd()
    try:
        if (liq_usd or 0) < min_liq:
            return False
        m = mcap or 0
        if m < min_mcap:
            return False
        if max_mcap is not None and m > max_mcap:
            return False
        return True
    except (TypeError, ValueError):
        return False


def buy_gate_skip(is_young_tok: bool, is_probe_bot: bool,
                  probe_on: Optional[bool] = None) -> bool:
    """Per-bot BUY gate when the probe is on. Returns True if this bot should SKIP
    this token. Probe bots trade YOUNG-ONLY; production bots SKIP young. When the
    probe is OFF, never skips here (young tokens aren't surfaced anyway)."""
    if probe_on is None:
        probe_on = probe_enabled()
    if not probe_on:
        return False
    if is_probe_bot:
        return not is_young_tok      # probe bot: young-only
    return is_young_tok              # production bot: skip young


def holder_guard_mode() -> str:
    """YOUNG_HOLDER_GUARD_MODE: off | shadow | enforce (default enforce).

    Young-lane rug guard (2026-07-03). NEVER rugged -83% in 113s on the lane's
    first day; at entry its rugcheck holder features read top1=44.9 / top10=100.6
    while EVERY young winner that day sat at top1 20-24 / top10 54-64 (or missing
    -> fail-open). MENSA/KEVIN/the losing COTORO rebuy were also above the line:
    perfect win/loss separation on the day (+~108pp saved, 0 winner pp lost).
    One wallet holding a third of the float IS rug capability — causal, not
    curve-fit — so this ships enforce on the isolated young lane only."""
    return os.environ.get("YOUNG_HOLDER_GUARD_MODE", "enforce").strip().lower()


def holder_guard_max_top1() -> float:
    try:
        return float(os.environ.get("YOUNG_HOLDER_MAX_TOP1", "30"))
    except (TypeError, ValueError):
        return 30.0


def holder_guard_max_top10() -> float:
    try:
        return float(os.environ.get("YOUNG_HOLDER_MAX_TOP10", "70"))
    except (TypeError, ValueError):
        return 70.0


def tape_shadow_mode() -> str:
    """YOUNG_TAPE_SHADOW_MODE: off | on (default on). SHADOW-ONLY measurement
    (2026-07-03 launch-arc mine): records two trough signals on every young-probe
    buy candidate, fetched AFTER the buy decision so the fire path pays zero
    latency. Enforce is a later, separate decision on realized joins."""
    return os.environ.get("YOUNG_TAPE_SHADOW_MODE", "on").strip().lower()


def tape_absorption_metrics(ohlcv_rows, now_ts: float) -> dict:
    """Launch-arc trough signals from GT minute OHLCV rows [ts,o,h,l,c,v].

    - bars_printed_15: minute-bars that actually traded in the last 15 wall-clock
      minutes. Recoverers keep a live tape through the trough (15/15 printing);
      corpses go silent (1/15). Mine separator: >=8 = 100% precision/recall on
      the (thin) pooled set.
    - dd_from_peak_pct: last close vs the highest high in the supplied window
      (fetch 6h). Depth is ANTI-predictive: 0/9 troughs deeper than -85%
      recovered -> rug_floor flag at <= -85.
    Pure; returns {} on malformed/empty input (fail-soft)."""
    try:
        rows = [r for r in (ohlcv_rows or [])
                if isinstance(r, (list, tuple)) and len(r) >= 5]
        if not rows:
            return {}
        rows.sort(key=lambda r: r[0])
        cutoff = float(now_ts) - 15 * 60
        printed = sum(1 for r in rows if float(r[0]) >= cutoff)
        highs = [float(r[2]) for r in rows if r[2] is not None and float(r[2]) > 0]
        last_close = next((float(r[4]) for r in reversed(rows)
                           if r[4] is not None and float(r[4]) > 0), None)
        out = {"bars_printed_15": printed, "n_bars": len(rows)}
        if highs and last_close:
            dd = (last_close / max(highs) - 1.0) * 100.0
            out["dd_from_peak_pct"] = round(dd, 2)
            out["rug_floor"] = dd <= -85.0
        out["tape_dead"] = printed < 8
        # range_mean_60m (serial-swinger 2nd wave, 2026-07-05): mean per-bar
        # 1m range% over the last 60 bars — the discriminator study's #2
        # separator (>=12 = wide oscillation; spearman -0.87 with age, use as
        # AND-refinement). Free: same bars this fetch already pulled.
        try:
            last60 = rows[-60:]
            ranges = [(float(b[2]) - float(b[3])) / float(b[4]) * 100.0
                      for b in last60
                      if b[4] is not None and float(b[4]) > 0
                      and b[2] is not None and b[3] is not None]
            if ranges:
                out["range_mean_60m"] = round(sum(ranges) / len(ranges), 3)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return out
    except (TypeError, ValueError):
        return {}


def holder_guard_blocks(top1_holder_pct, top10_holder_pct,
                        max_top1: Optional[float] = None,
                        max_top10: Optional[float] = None) -> bool:
    """True when holder concentration marks a young entry as rug-capable:
    top1 >= max_top1 (default 30) OR top10 >= max_top10 (default 70).

    FAIL-OPEN on missing/garbage data (None/bool/NaN -> that axis passes) — the
    missing-data-read-as-zero bug-class rule: never block on fabricated values.
    LP + insider wallets are already excluded upstream (compute_holder_features)."""
    if max_top1 is None:
        max_top1 = holder_guard_max_top1()
    if max_top10 is None:
        max_top10 = holder_guard_max_top10()
    for val, cap in ((top1_holder_pct, max_top1), (top10_holder_pct, max_top10)):
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            continue  # missing/garbage -> this axis passes (fail-open)
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        if f >= cap:
            return True
    return False
