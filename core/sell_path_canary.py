# core/sell_path_canary.py
"""Sell-path canary — "no working exit -> no new entries", mechanically.

2026-07-10 incident: every wallet balance read failed silently for 7+ hours
(over-quota RPC answering 200-with-error, no backups). Live SELLS could not
size while live BUYS kept working (Jupiter transport != trader RPC) — the
probe bought SMOLE 14 minutes AFTER the first failed mogdog sell. AxiS:
"dont ever let this happen again."

The canary periodically runs a REAL read through the exact sell-path code
(trader._get_token_balance_atomic -> getTokenAccountsByOwner jsonParsed) on a
fixed well-known mint. A genuine 0 balance IS a pass (the read worked); only
a FAILED read (-1) is a fail. While the canary is unhealthy, the live-buy
bridge refuses new live entries. Sells are NEVER gated by this — exits must
always be free to try.

SELL_PATH_CANARY_MODE=on|off (default ON — this is the incident's mandate).
"""
import os
import time

# USDC mint: guaranteed to exist forever; wallet balance usually 0 — that's
# fine, 0 proves the READ works. The canary tests the pipe, not the wallet.
CANARY_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_GRACE_SECS = 180.0     # post-spawn grace before "no data" counts as broken
_STALE_FACTOR = 4.0     # missed probes (loop wedged) -> unhealthy


def canary_mode_on() -> bool:
    return os.environ.get("SELL_PATH_CANARY_MODE", "on").strip().lower() not in (
        "off", "0", "false")


class SellPathCanary:
    def __init__(self, interval_secs: float = 60.0, spawned_at: float = None):
        self.interval_secs = float(interval_secs)
        self.spawned_at = time.time() if spawned_at is None else spawned_at
        self.last_ok_ts = None
        self.last_fail_ts = None
        self.consecutive_fails = 0

    def record(self, ok: bool, now: float = None) -> None:
        now = time.time() if now is None else now
        if ok:
            self.last_ok_ts = now
            self.consecutive_fails = 0
        else:
            self.last_fail_ts = now
            self.consecutive_fails += 1

    def healthy(self, now: float = None) -> bool:
        """True = live buys allowed. Fail-closed once past the boot grace."""
        if not canary_mode_on():
            return True
        now = time.time() if now is None else now
        if self.last_ok_ts is None and self.last_fail_ts is None:
            # no probe yet: allow only inside the boot grace window
            return (now - self.spawned_at) < _GRACE_SECS
        if self.last_ok_ts is None:
            return False                      # only failures ever seen
        if self.last_fail_ts is not None and self.last_fail_ts > self.last_ok_ts:
            return False                      # most recent probe failed
        # last probe passed — but a wedged/stopped loop must not count as
        # healthy forever: stale success ages out.
        return (now - self.last_ok_ts) < self.interval_secs * _STALE_FACTOR

    def status_line(self, now: float = None) -> str:
        now = time.time() if now is None else now
        return ("healthy=%s ok_age=%s fail_age=%s consec_fails=%d" % (
            self.healthy(now),
            "%.0fs" % (now - self.last_ok_ts) if self.last_ok_ts else "-",
            "%.0fs" % (now - self.last_fail_ts) if self.last_fail_ts else "-",
            self.consecutive_fails))
