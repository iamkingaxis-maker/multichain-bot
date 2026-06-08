"""Transient price-glitch guard for the multi-bot exit path.

External price sources (the DexScreener pair endpoint in particular) occasionally
return a single bad print. TROLL 2026-05-26 is the reference incident: the real
price was ~$0.092 with $3.6M liquidity, but one tick read ~$0.021 (−77%). That
single garbage print tripped the −15% hard stop across *every* bot holding the
token in the same management cycle — ~$286 of phantom losses on a token that was
actually flat (it printed normal prices again the next tick).

A real crash/rug looks different from a glitch: it STAYS down. PTAI 2026-05-26
genuinely rugged (−98%, liquidity collapsed to $4k, kept falling) — that stop was
correct and must still fire. So the discriminator is *persistence*, not magnitude.

This guard defers acting on a CATASTROPHIC adverse single-cycle move until the
NEXT cycle corroborates it:

  • normal moves (incl. an ordinary −15% stop) are < ``max_drop`` → pass straight
    through, acted on immediately;
  • a drop beyond ``max_drop`` from the last known-good price → "suspect": this
    cycle acts on the last-good price (no phantom stop), and the suspect price is
    held pending;
  • if the NEXT cycle still reads a corroborating low → confirmed real move,
    accept it (stop fires, one cycle ~seconds late);
  • if the next cycle reverts to a normal price → it was a glitch, discarded.

Call :func:`guarded_exit_price` exactly once per token per management cycle.
"""

from typing import Dict, Optional

# A single-cycle drop beyond this fraction below the last known-good price is
# treated as suspect (needs confirmation).
#
# Calibration: ordinary hard stops fire by GRADUAL drift (small tick-to-tick
# moves down to −15%), so they are NEVER deferred regardless of this threshold;
# only a SUDDEN single-cycle gap is deferred one cycle for corroboration. A real
# fast dump/rug simply confirms next cycle and fires ~one cycle late — cheap.
# So the only cost of a tighter threshold is one cycle of latency on genuine
# violent moves, while the benefit is catching phantom bad-tick prints.
#
# 2026-05-27 GIGA incident: real price was ~flat (−3.5% h24, $1.8M liq) but a
# single bad print read −32% from the last-good price, tripping the −15% stop
# across ~56 bots in one cycle for ~$452 of phantom losses. The previous 0.40
# threshold let it through (−32% < −40%). Lowered to 0.22 so the −20%..−40%
# phantom band (where most bad ticks land) is corroborated before it can fire
# every bot's stop. Still well above normal tick-to-tick volatility.
EXIT_GUARD_MAX_DROP = 0.22

# A single-cycle RISE beyond this fraction above the last known-good price is
# treated as suspect (needs confirmation) — the mirror of EXIT_GUARD_MAX_DROP.
#
# A bad print can be absurdly HIGH as well as low, and that case is *more*
# dangerous: a phantom upward tick trips TP and books a fake WIN that corrupts
# the bot's balance, whereas a phantom low only fires a stop.
#
# 2026-05-27 EURC incident: no_filters bought EURC (a EUR-pegged stablecoin,
# real price ~$1.16) and one bad print read $6,199.37 — a 5,316x glitch — which
# tripped TP1+TP2 and booked +$106,334 of phantom profit on a $20 position,
# corrupting the whole fleet leaderboard. The drop-only guard let it straight
# through. +100% in one cycle is already extreme for a token we hold; a real
# moon simply confirms next cycle (or via cross-source) and is captured one
# cycle late — cheap, symmetric to the drop case.
EXIT_GUARD_MAX_RISE = 1.0

# A confirming read must be within this fraction of the pending suspect price
# to count as corroboration (a drop's low / a rise's high has to roughly hold,
# not be a lone wick).
EXIT_GUARD_CONFIRM_TOL = 0.10

# PRIMARY rise check tolerance: a suspect upward print is accepted only if it is
# within this fraction ABOVE the token's real recent OHLC high (a small margin for
# the just-forming candle / feed lag). A glitch prints far above the real high
# (BhTPX SPCX 2026-06-01: real 24h high 0.00141, glitch exit 0.00384 = 2.7x above
# → rejected). A genuine spike sits within the high (or confirms next cycle as the
# candle catches up). 15% margin is well below any real glitch's overshoot.
EXIT_GUARD_HIGH_TOL = 0.15

# PRIMARY drop check tolerance: a suspect downward print is accepted (stop fires)
# only if it is within this fraction BELOW the token's real recent OHLC low. A
# glitch prints far below the real low (E6ifp2 SPCX 2026-06-01: real recent low
# 0.00313, glitch stop fills 0.0003-0.0008 = 4-10x below → rejected, +$437 phantom
# losses avoided). A genuine fast dump's low enters the current minute candle within
# ~1 cycle → fires then (one cycle late, by design). 15% margin for feed lag.
EXIT_GUARD_LOW_TOL = 0.15

# ABSOLUTE-move triggers (relative to the position's ENTRY, via ``ref_price``).
#
# The single-cycle suspect triggers above only fire on a SUDDEN tick-to-tick gap.
# 2026-06-02 showed two glitches that evade them entirely:
#   • SPCX booked +374% — a bad feed climbed GRADUALLY (0.00076→0.0015→0.0029→0.0039,
#     each step <+100%), so ``suspect_rise`` never fired, ``high_fn`` was never
#     consulted, ``last_good`` tracked up to the glitch, and TP booked at 4.7x entry.
#   • Buttcoin booked −100% — a near-zero print with GeckoTerminal down (``low_fn``
#     →None) was confirmed on temporal-only over two cycles.
# When the caller passes ``ref_price`` (the entry), a move beyond these fractions
# FROM ENTRY is treated as suspect regardless of the per-cycle delta, so the OHLC
# bound is always consulted on a big absolute move. To bound egress on a sustained
# real winner/loser, the absolute trigger fires only on a NEW high-/low-water mark
# (a plateau re-uses the prior decision and makes no extra OHLC call).
EXIT_GUARD_ABS_RISE = 0.5   # price > entry*(1+this) → validate every new high vs OHLC high
# A drop beyond this fraction below entry is CATASTROPHIC: like a rise it is then
# NEVER accepted on temporal-only — it requires OHLC-low or cross-source
# corroboration (a real rug's low corroborates immediately; a sticky zero-glitch
# with the OHLC source down no longer books a phantom −100%).
EXIT_GUARD_ABS_DROP = 0.5   # price < entry*(1-this) → catastrophic (no temporal-only)


def guarded_exit_price(
    guard: Dict[str, dict],
    token: str,
    price: float,
    max_drop: float = EXIT_GUARD_MAX_DROP,
    max_rise: float = EXIT_GUARD_MAX_RISE,
    confirm_tol: float = EXIT_GUARD_CONFIRM_TOL,
    confirm_fn=None,
    high_fn=None,
    high_tol: float = EXIT_GUARD_HIGH_TOL,
    low_fn=None,
    low_tol: float = EXIT_GUARD_LOW_TOL,
    ref_price: Optional[float] = None,
    abs_rise: float = EXIT_GUARD_ABS_RISE,
    abs_drop: float = EXIT_GUARD_ABS_DROP,
) -> float:
    """Return the price the exit tick should act on, filtering one-tick glitches.

    ``guard`` is per-scanner mutable state keyed by token::

        {token: {"last_good": float, "pending": float | None}}

    It is mutated in place. Must be called once per token per management cycle
    (the temporal-confirmation fallback assumes consecutive calls are consecutive
    cycles).

    ``confirm_fn`` (optional): a zero-arg callable returning an INDEPENDENT
    second-source price (same USD units), or None if unavailable. It is invoked
    ONLY on a suspect drop (rare), so it costs no extra egress on normal ticks.
    Cross-source confirmation is stronger than temporal: it resolves a suspect
    drop in the SAME cycle and catches a *persistent* bad source (which the
    next-cycle temporal check would wrongly confirm). Decision on a suspect drop:

      • second source near the suspect low (<= midpoint of last-good and suspect)
        → corroborated → real move, act on it now;
      • second source still healthy (above that midpoint) → the primary feed is
        glitching → ignore it, act on last-good (no phantom stop), even if the
        bad print persists;
      • confirm_fn missing / returns None / raises → fall back to the temporal
        next-cycle confirmation.

    ``high_fn`` (optional): a zero-arg callable returning the token's REAL recent
    OHLC high (same USD units), or None. It is the PRIMARY check for a suspect RISE
    (invoked only on a suspect rise — rare): a print above the highest price the
    token ever traded cannot be a real fill (nobody bought there), so it's a glitch
    regardless of what any single-tick price source says. Decision on a suspect rise:

      • high_fn returns a real high and price <= high*(1+high_tol) → the price is
        within the token's traded range → genuine move → accept now;
      • price > high*(1+high_tol) → above the real high → glitch (or a not-yet-
        confirmed brand-new spike) → reject, act on last-good;
      • high_fn missing / None / raises → fall back to confirm_fn, then (for a rise)
        reject — a rise is NEVER accepted on temporal-only.

    ``low_fn`` (optional): the symmetric PRIMARY check for a suspect DROP — a zero-arg
    callable returning the token's REAL recent OHLC low (same USD units), or None. A
    stop fill below the lowest price the token recently traded can't be a real fill
    (the drop-side mirror of high_fn). Decision on a suspect drop:

      • low_fn returns a real low and price >= low*(1-low_tol) → the drop is within
        the token's recent range → genuine fast dump → act now (stop fires);
      • price < low*(1-low_tol) → below the real low → glitch (or a brand-new crash
        not yet in the candle) → reject; re-checked next cycle (a real crash's low
        enters the minute candle within ~1 cycle and then fires; a glitch never does);
      • low_fn missing / None / raises → fall back to confirm_fn, then the temporal
        next-cycle confirmation (a real persistent dump still fires — PTAI rug).
    """
    g = guard.get(token)
    if not g or g.get("last_good", 0.0) <= 0.0:
        # First observation for this token. Normally nothing to compare against, so
        # seed and accept. BUT cold guard state is exactly what a RESTART produces —
        # and the first post-restart tick can be a phantom (cold price feed). Blindly
        # seeding last_good=price then returning it books that phantom as a real exit.
        #   CDOF 2026-06-08: a 62x phantom print ~2 min after a deploy restart hit this
        #   seed path (guard state empty) and booked +$2456 across 2 bots — the OHLC
        #   high_fn was never consulted because suspect logic is below the early return.
        # If we know the position's entry (ref_price), seed last_good=ENTRY and fall
        # THROUGH to the suspect machinery so an extreme first print is validated against
        # the OHLC bound just like any other cycle. Without an entry ref there is nothing
        # to validate against, so seed-and-accept as before.
        _ref0 = ref_price if (ref_price is not None and ref_price > 0) else None
        if _ref0 is None:
            guard[token] = {"last_good": price, "pending": None,
                            "last_decision": {"raw": price, "ret": price, "reason": "seed"}}
            return price
        g = {"last_good": _ref0, "pending": None}
        guard[token] = g
        # fall through — `last` below is now the entry; an extreme first print trips
        # suspect_rise/abs_rise_hit and is checked against high_fn before acceptance.

    last = g["last_good"]
    # ABSOLUTE-from-entry triggers (only when ref_price known) — catch a gradual
    # multi-cycle climb / sticky deep drop the single-cycle thresholds miss. Gated to
    # a NEW extreme vs last_good (price>last / price<last) so a plateau makes no extra
    # OHLC call. A drop beyond ``abs_drop`` below entry is "catastrophic" (treated like
    # a rise: never accepted on temporal-only).
    ref = ref_price if (ref_price is not None and ref_price > 0) else None
    abs_rise_hit = ref is not None and price > last and price > ref * (1.0 + abs_rise)
    abs_drop_hit = ref is not None and price < last and price < ref * (1.0 - abs_drop)
    catastrophic_drop = abs_drop_hit
    suspect_drop = price < last * (1.0 - max_drop) or abs_drop_hit
    suspect_rise = price > last * (1.0 + max_rise) or abs_rise_hit

    # ── DECISION INSTRUMENTATION (2026-06-02) ──────────────────────────────────
    # Record WHY the guard returned what it did, into per-token state, so a phantom
    # that ever slips is diagnosable from the sell record (Railway logs retain only
    # ~30 min). dip_scanner stamps guard[token]["last_decision"] onto the sell. Pure
    # bookkeeping — no behavior change, no extra fetches (captures only what the
    # decision branches already computed).
    _dec = {"raw": price, "last_good": last, "suspect_rise": bool(suspect_rise),
            "suspect_drop": bool(suspect_drop), "abs_rise_hit": bool(abs_rise_hit),
            "abs_drop_hit": bool(abs_drop_hit), "catastrophic_drop": bool(catastrophic_drop),
            "high_val": None, "low_val": None, "second_val": None, "reason": None}

    def _rec(value, reason):
        _dec["ret"] = value
        _dec["reason"] = reason
        g["last_decision"] = _dec
        return value

    if suspect_drop or suspect_rise:
        # ── Suspect single-cycle gap (down OR up). Prefer an immediate opinion. ──
        midpoint = (price + last) / 2.0

        # ── PRIMARY rise check: the token's REAL recent OHLC high. ──
        # A print above the highest price the token ever traded can't be a real
        # fill — nobody bought there. This is a stronger, more direct discriminator
        # than a single-tick second source (which can itself be glitching or down).
        # 2026-06-01: BhTPX SPCX genuinely pumped +1159% (real high 0.00141) but a
        # bad print read 0.00384 (2.7x above the real high) and tripped TP1/TP2 for
        # +$64 phantom x3 bots — this check rejects exactly that.
        if suspect_rise and high_fn is not None:
            hi = None
            try:
                hi = high_fn()
            except Exception:
                hi = None
            _dec["high_val"] = hi
            if isinstance(hi, (int, float)) and hi > 0:
                if price <= hi * (1.0 + high_tol):
                    # within the token's real traded range → genuine → act now.
                    g["last_good"] = price
                    g["pending"] = None
                    return _rec(price, "rise_accepted_within_high")
                # above the real high → glitch (or unconfirmed new spike) → reject.
                g["pending"] = None
                return _rec(last, "rise_rejected_above_high")
            # high unavailable → fall through to cross-source, then reject.

        # ── PRIMARY drop check: the token's REAL recent OHLC low (mirror of rise). ──
        # A stop fill below the lowest price the token recently traded can't be real.
        # 2026-06-01: E6ifp2 SPCX real recent low 0.00313, glitch stops filled
        # 0.0003-0.0008 (4-10x below) → −$437 phantom stop losses. This rejects them
        # while a genuine fast dump (low within range, or its low enters the candle
        # next cycle) still fires.
        if suspect_drop and low_fn is not None:
            lo = None
            try:
                lo = low_fn()
            except Exception:
                lo = None
            _dec["low_val"] = lo
            if isinstance(lo, (int, float)) and lo > 0:
                if price >= lo * (1.0 - low_tol):
                    # within the token's real recent range → genuine dump → fire now.
                    g["last_good"] = price
                    g["pending"] = None
                    return _rec(price, "drop_accepted_within_low")
                # below the real low → glitch (or unconfirmed fresh crash) → reject;
                # re-checked next cycle (a real crash's low enters the candle → fires).
                g["pending"] = None
                return _rec(last, "drop_rejected_below_low")
            # low unavailable → fall through to cross-source / temporal.

        if confirm_fn is not None:
            second = None
            try:
                second = confirm_fn()
            except Exception:
                second = None
            _dec["second_val"] = second
            if isinstance(second, (int, float)) and second > 0:
                # Corroborated when the independent source agrees the move is real:
                # past the midpoint in the same direction as the suspect print.
                corroborated = (second <= midpoint) if suspect_drop else (second >= midpoint)
                if corroborated:
                    # Independent source corroborates → real move; act now.
                    g["last_good"] = price
                    g["pending"] = None
                    return _rec(price, "corroborated_crosssource")
                # Independent source says price is healthy → primary feed glitch.
                # Ignore the bad print and act on last-good; do NOT poison last_good
                # with the glitch, so a PERSISTENT bad source keeps being rejected.
                g["pending"] = None
                return _rec(last, "disconfirmed_glitch_crosssource")
            # second source unavailable → fall through (rise rejected; drop temporal).

        # ── No valid independent corroboration this cycle. ──
        # RISE is treated ASYMMETRICALLY from DROP: a phantom HIGH books a fake WIN
        # that corrupts balances + the whole leaderboard, whereas a phantom LOW only
        # fires a stop one cycle late. The temporal next-cycle check WRONGLY confirms
        # a PERSISTENT bad source (a sticky multi-cycle glitch), so a suspect rise is
        # NEVER accepted on temporal-only — it requires an independent second source
        # to agree. Cap at last-good without poisoning it; a real GRADUAL climb still
        # TPs normally (each <max_rise tick updates last_good), and a real sudden moon
        # is captured once the second source recovers/corroborates. Cost: a rare
        # genuine instant-moon is deferred (or capped) while the cross-source is down
        # — cheap vs. recurring phantom wins.
        #   2026-06-01: SPCX 0.00092→0.00384 (4.2x) — GeckoTerminal 429'd so confirm_fn
        #   returned None, and the temporal check confirmed a 2-cycle sticky bad print,
        #   booking +$64 fake wins across 3 premium bots. This closes that path.
        if suspect_rise:
            g["pending"] = None
            return _rec(last, "rise_capped_no_corroboration")

        # CATASTROPHIC DROP (beyond ``abs_drop`` below entry): mirror the rise rule —
        # never accept on temporal-only. A sticky near-zero glitch with the OHLC source
        # down (low_fn → None) and no cross-source must NOT book a phantom −100%; a
        # genuine rug's low corroborates via low_fn / confirm_fn and still fires.
        #   2026-06-02: Buttcoin entry 0.0148, a 2.35e-6 print (GT 429'd) was temporally
        #   confirmed over 2 cycles → −100% x4 bots. This closes that path.
        if catastrophic_drop:
            g["pending"] = None
            return _rec(last, "catastrophic_drop_no_corroboration")

        # DROP: fall back to next-cycle temporal confirmation. A real fast dump
        # confirms next cycle (stop fires one cycle late, by design); a single-tick
        # glitch reverts and is discarded.
        pending = g.get("pending")
        if pending is not None:
            pending_was_drop = pending < last
            if pending_was_drop and price <= pending * (1.0 + confirm_tol):
                g["last_good"] = price
                g["pending"] = None
                return _rec(price, "drop_confirmed_temporal")
        # First suspect drop cycle (or the low hasn't held) → defer: act on last-good
        # this cycle and hold the suspect pending for next time.
        g["pending"] = price
        return _rec(last, "drop_deferred_temporal")

    # Normal move or recovery → accept and clear any pending suspect.
    g["last_good"] = price
    g["pending"] = None
    return _rec(price, "normal")
