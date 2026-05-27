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

# A confirming read must be within this fraction ABOVE the pending suspect low
# to count as corroboration (the low has to roughly hold, not be a lone wick).
EXIT_GUARD_CONFIRM_TOL = 0.10


def guarded_exit_price(
    guard: Dict[str, dict],
    token: str,
    price: float,
    max_drop: float = EXIT_GUARD_MAX_DROP,
    confirm_tol: float = EXIT_GUARD_CONFIRM_TOL,
) -> float:
    """Return the price the exit tick should act on, filtering one-tick glitches.

    ``guard`` is per-scanner mutable state keyed by token::

        {token: {"last_good": float, "pending": float | None}}

    It is mutated in place. Must be called once per token per management cycle
    (the confirmation logic assumes consecutive calls are consecutive cycles).
    """
    g = guard.get(token)
    if not g or g.get("last_good", 0.0) <= 0.0:
        # First observation for this token — nothing to compare against; seed it.
        guard[token] = {"last_good": price, "pending": None}
        return price

    last = g["last_good"]
    if price < last * (1.0 - max_drop):
        pending = g.get("pending")
        if pending is not None and price <= pending * (1.0 + confirm_tol):
            # Second consecutive suspect cycle corroborates the low → real move.
            g["last_good"] = price
            g["pending"] = None
            return price
        # First suspect cycle (or the low hasn't held) → defer: act on the last
        # known-good price this cycle and hold the suspect pending for next time.
        g["pending"] = price
        return last

    # Normal move or recovery → accept and clear any pending suspect.
    g["last_good"] = price
    g["pending"] = None
    return price
