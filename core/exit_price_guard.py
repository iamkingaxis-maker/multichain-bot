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


def guarded_exit_price(
    guard: Dict[str, dict],
    token: str,
    price: float,
    max_drop: float = EXIT_GUARD_MAX_DROP,
    max_rise: float = EXIT_GUARD_MAX_RISE,
    confirm_tol: float = EXIT_GUARD_CONFIRM_TOL,
    confirm_fn=None,
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
    """
    g = guard.get(token)
    if not g or g.get("last_good", 0.0) <= 0.0:
        # First observation for this token — nothing to compare against; seed it.
        guard[token] = {"last_good": price, "pending": None}
        return price

    last = g["last_good"]
    suspect_drop = price < last * (1.0 - max_drop)
    suspect_rise = price > last * (1.0 + max_rise)
    if suspect_drop or suspect_rise:
        # ── Suspect single-cycle gap (down OR up). Prefer an immediate opinion. ──
        midpoint = (price + last) / 2.0
        if confirm_fn is not None:
            second = None
            try:
                second = confirm_fn()
            except Exception:
                second = None
            if isinstance(second, (int, float)) and second > 0:
                # Corroborated when the independent source agrees the move is real:
                # past the midpoint in the same direction as the suspect print.
                corroborated = (second <= midpoint) if suspect_drop else (second >= midpoint)
                if corroborated:
                    # Independent source corroborates → real move; act now.
                    g["last_good"] = price
                    g["pending"] = None
                    return price
                # Independent source says price is healthy → primary feed glitch.
                # Ignore the bad print and act on last-good; do NOT poison last_good
                # with the glitch, so a PERSISTENT bad source keeps being rejected.
                g["pending"] = None
                return last
            # second source unavailable → fall through to temporal confirmation.
        pending = g.get("pending")
        if pending is not None:
            # Corroborate only if the prior suspect was in the SAME direction
            # (pending below last_good == a drop; above == a rise) and the extreme
            # roughly holds within tol — avoids a drop-then-spike glitch pair
            # wrongly confirming each other.
            pending_was_drop = pending < last
            pending_was_rise = pending > last
            if (suspect_drop and pending_was_drop and price <= pending * (1.0 + confirm_tol)):
                g["last_good"] = price
                g["pending"] = None
                return price
            if (suspect_rise and pending_was_rise and price >= pending * (1.0 - confirm_tol)):
                g["last_good"] = price
                g["pending"] = None
                return price
        # First suspect cycle (or the extreme hasn't held) → defer: act on the last
        # known-good price this cycle and hold the suspect pending for next time.
        g["pending"] = price
        return last

    # Normal move or recovery → accept and clear any pending suspect.
    g["last_good"] = price
    g["pending"] = None
    return price
