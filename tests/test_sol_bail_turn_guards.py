"""sol_bail CHURN FIX (2026-07-17): the stamp must be a macro-TURN detector,
not an "any red while macro down" trigger.

Incident: in a sustained SOL dump the fleet kept buying dips; every entry
showed instant spread-red and was stamped+bailed at median 5s hold, then
re-bought — 98 buys / 10 tokens / 84 bails / 4% win / -$167 in 3.7h. Guards:
min-hold (90s default) + turn-only (macro OK at entry-proxy, down now).
The SOL_MACRO_BAIL enforce gate acts on this stamp, so these tests pin the
enforcement population too.
"""
import types

from feeds.dip_scanner import DipScanner


class _Pos:
    def __init__(self, entry_time=0.0, entry_price=1.0, tp1_hit=False):
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.tp1_hit = tp1_hit
        self.state_blob = {}


def _scanner(sol_h6=None, sol_h1=None):
    fake = types.SimpleNamespace()
    fake._cycle_sol_features = {"sol_pc_h6": sol_h6, "sol_pc_h1": sol_h1}
    return fake


def _stamp(fake, pos, price, now):
    DipScanner._stamp_sol_bail_shadow(fake, pos, price, now)


def test_turn_stamps_after_min_hold():
    # macro OK at first evaluation, down later, held past min-hold -> stamps
    pos = _Pos(entry_time=0.0)
    ok = _scanner(sol_h6=0.5, sol_h1=0.1)
    _stamp(ok, pos, price=0.97, now=30.0)            # entry proxy captured
    assert pos.state_blob.get("sol_bail_entry_h6") == 0.5
    assert "sol_bail_shadow_pnl_pct" not in pos.state_blob
    down = _scanner(sol_h6=-1.2, sol_h1=-1.5)
    down._cycle_sol_features = down._cycle_sol_features
    _stamp(down, pos, price=0.97, now=200.0)         # past 90s min-hold
    assert pos.state_blob.get("sol_bail_shadow_pnl_pct") is not None


def test_no_stamp_inside_min_hold():
    # the 5s-churn class: red seconds after entry, macro down -> NO stamp
    pos = _Pos(entry_time=0.0)
    ok = _scanner(sol_h6=0.5, sol_h1=0.1)
    _stamp(ok, pos, price=1.0, now=1.0)              # entry proxy
    down = _scanner(sol_h6=-1.2, sol_h1=-1.5)
    _stamp(down, pos, price=0.95, now=5.0)           # 5s old, -5% spread red
    assert "sol_bail_shadow_pnl_pct" not in pos.state_blob


def test_no_stamp_when_entered_during_dump():
    # entered DURING the dump (entry proxy already down) -> never stamps for
    # that same dump; that case belongs to the entry-side macro gate
    pos = _Pos(entry_time=0.0)
    down = _scanner(sol_h6=-1.2, sol_h1=-1.5)
    _stamp(down, pos, price=1.0, now=30.0)           # entry proxy = down
    _stamp(down, pos, price=0.95, now=500.0)         # still down, deep red
    assert "sol_bail_shadow_pnl_pct" not in pos.state_blob


def test_winner_safe_green_never_stamped():
    pos = _Pos(entry_time=0.0)
    ok = _scanner(sol_h6=0.5, sol_h1=0.1)
    _stamp(ok, pos, price=1.0, now=30.0)
    down = _scanner(sol_h6=-1.2, sol_h1=-1.5)
    _stamp(down, pos, price=1.05, now=200.0)         # +5% green
    assert "sol_bail_shadow_pnl_pct" not in pos.state_blob


def test_stamp_is_once_only():
    pos = _Pos(entry_time=0.0)
    ok = _scanner(sol_h6=0.5, sol_h1=0.1)
    _stamp(ok, pos, price=1.0, now=30.0)
    down = _scanner(sol_h6=-1.2, sol_h1=-1.5)
    _stamp(down, pos, price=0.97, now=200.0)
    first = pos.state_blob["sol_bail_shadow_pnl_pct"]
    _stamp(down, pos, price=0.90, now=300.0)         # deeper later: unchanged
    assert pos.state_blob["sol_bail_shadow_pnl_pct"] == first
