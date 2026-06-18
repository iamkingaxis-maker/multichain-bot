"""Profit-sweep executor (core/profit_sweeper) — the most sensitive code in the
bot (real-money transfer). Money-math + fail-closed + dry-run + the $5 cap."""
import os

import core.profit_sweeper as ps
from core.profit_sweeper import (
    compute_sweepable_sol, usd_to_sol, validate_destination, ratchet_target_sol,
    ProfitSweeper, auto_sweep_decision,
)


def _live_usd_floor_guard_allows(env):
    """Mirror of the trader.py:3265 LIVE USD-floor guard, driven by env, so the guard
    LOGIC is unit-tested without spinning up the async Trader. Returns True if a live
    USD-only-floor sweep would be ALLOWED. (A SOL-native floor bypasses this guard.)"""
    old = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(env)
        floor_sol_ovr = ps.floor_sol() or None
        if floor_sol_ovr:
            return True  # SOL-native floor -> price-risk-free -> always allowed
        has_bound = (ps.floor_price_buffer_frac() > 0) or (ps.max_per_sweep_usd() > 0)
        return bool(ps.price_risk_ack() and has_bound)
    finally:
        os.environ.clear()
        os.environ.update(old)

HOT = "5xot1111111111111111111111111111111111111H"   # placeholder, not used for pubkey-validity here
COLD = "DqdZwedYRwkHhsvX3s6Ae3aG876AF2Fy7wUXBkKP48C9"  # the real cold wallet (valid pubkey)


# ── compute_sweepable_sol ──
def test_below_floor_returns_zero():
    assert compute_sweepable_sol(1.0, 2.0, 0.05) == 0.0


def test_above_floor_subtracts_floor_and_gas():
    assert abs(compute_sweepable_sol(3.0, 2.0, 0.05) - 0.95) < 1e-9


def test_exactly_at_floor_plus_gas_is_zero():
    assert compute_sweepable_sol(2.05, 2.0, 0.05) == 0.0


# ── usd_to_sol ──
def test_usd_to_sol():
    assert abs(usd_to_sol(5.0, 200.0) - 0.025) < 1e-9


def test_usd_to_sol_bad_price_is_none():
    assert usd_to_sol(5.0, 0) is None
    assert usd_to_sol(5.0, None) is None


# ── validate_destination (FAIL-CLOSED) ──
def test_valid_destination_passes():
    assert validate_destination(COLD, HOT, COLD) is True


def test_empty_or_malformed_fails_closed():
    assert validate_destination("", HOT, COLD) is False
    assert validate_destination("not-a-pubkey!!", HOT, "not-a-pubkey!!") is False


def test_mismatched_configured_fails_closed():
    # a different (valid) pubkey than configured -> refused (config-redirect defense)
    other = "So11111111111111111111111111111111111111112"
    assert validate_destination(other, HOT, COLD) is False


def test_dest_equals_hot_fails_closed():
    assert validate_destination(COLD, COLD, COLD) is False


# ── ratchet ──
def test_ratchet_monotonic_and_target():
    hwm, target = ratchet_target_sol(realized_pnl_sol=10.0, profit_hwm_sol=8.0,
                                     total_swept_sol=3.0, fraction=1.0)
    assert hwm == 10.0 and target == 7.0  # 1.0*10 - 3 already swept


def test_ratchet_drawdown_does_not_pull_back():
    # realized fell to 6 but hwm was 10; never sweep negative
    hwm, target = ratchet_target_sol(6.0, 10.0, 10.0, 1.0)
    assert hwm == 10.0 and target == 0.0


# ── ProfitSweeper.sweep_once ──
def _sweeper(balance=10.0, price=200.0, sent_sig="SIG123", **kw):
    sends = []
    def send(dest, lamports):
        sends.append((dest, lamports)); return sent_sig
    s = ProfitSweeper(get_balance_sol=lambda: balance, send_transfer=send,
                      get_sol_price_usd=lambda: price, configured_dest=COLD, hot_addr=HOT,
                      floor=2.0, gas_buffer=0.05, threshold=1.0, **kw)
    return s, sends


def test_dry_run_does_not_send():
    s, sends = _sweeper()
    r = s.sweep_once(dry_run=True)
    assert r["sent"] is False and r["dry_run"] is True and sends == []


def test_live_send_with_5usd_cap_clamps_amount():
    # balance 10, floor 2, gas .05 -> 7.95 sweepable; $5 @ $200 = 0.025 SOL cap
    s, sends = _sweeper(balance=10.0, price=200.0)
    r = s.sweep_once(dry_run=False, max_usd=5.0, ignore_threshold=True)
    assert r["sent"] is True
    assert abs(r["amount_sol"] - 0.025) < 1e-6      # clamped to the $5 cap, not 7.95
    assert sends == [(COLD, int(0.025 * 1_000_000_000))]


def test_cap_refuses_when_no_price():
    s, sends = _sweeper(price=0)
    r = s.sweep_once(dry_run=False, max_usd=5.0, ignore_threshold=True)
    assert r["sent"] is False and r["reason"] == "no_sol_price" and sends == []


def test_below_threshold_skips():
    # sweepable 0.5 (< threshold 1.0), no cap, threshold enforced
    s, sends = _sweeper(balance=2.55)
    r = s.sweep_once(dry_run=False)
    assert r["sent"] is False and r["reason"] == "below_threshold" and sends == []


def test_bad_destination_fails_closed():
    sends = []
    s = ProfitSweeper(get_balance_sol=lambda: 10.0,
                      send_transfer=lambda d, l: sends.append((d, l)) or "SIG",
                      get_sol_price_usd=lambda: 200.0,
                      configured_dest="garbage!!", hot_addr=HOT,
                      floor=2.0, gas_buffer=0.05, threshold=1.0)
    r = s.sweep_once(dry_run=False, ignore_threshold=True)
    assert r["sent"] is False and r["reason"] == "bad_destination" and sends == []


def test_balance_fetch_failure_skips():
    s = ProfitSweeper(get_balance_sol=lambda: None, send_transfer=lambda d, l: "SIG",
                      get_sol_price_usd=lambda: 200.0, configured_dest=COLD, hot_addr=HOT)
    r = s.sweep_once(dry_run=False)
    assert r["sent"] is False and r["reason"] == "balance_fetch_failed"


# ── auto_sweep_decision (production fixed-floor, USD-pegged) ──
def test_auto_sweep_keeps_floor_sweeps_excess():
    # balance 10 SOL @ $200 = $2000; floor $1000 = 5 SOL; gas 0.05 -> sweep ~4.95 SOL (~$990)
    d = auto_sweep_decision(balance_sol=10.0, sol_price=200.0, floor_usd=1000.0,
                            gas_buffer_sol=0.05, min_increment_usd_v=5.0)
    assert d["should_sweep"] is True
    assert abs(d["sweepable_sol"] - 4.95) < 1e-6
    assert abs(d["sweepable_usd"] - 990.0) < 0.5


def test_auto_sweep_below_increment_skips():
    # balance 5.02 SOL @ $200 = $1004; floor $1000 = 5 SOL; excess ~0.02 SOL minus gas
    # -> sweepable_usd < $5 -> skip
    d = auto_sweep_decision(5.04, 200.0, 1000.0, 0.05, 5.0)
    assert d["should_sweep"] is False and d["reason"] == "below_increment"


def test_auto_sweep_at_floor_skips():
    # exactly at floor + gas -> nothing sweepable
    d = auto_sweep_decision(5.05, 200.0, 1000.0, 0.05, 5.0)
    assert d["should_sweep"] is False


def test_auto_sweep_no_floor_fails_closed():
    # floor 0 would drain the float -> never sweep
    d = auto_sweep_decision(10.0, 200.0, 0.0, 0.05, 5.0)
    assert d["should_sweep"] is False and d["reason"] == "no_floor_set"


def test_auto_sweep_implausible_price_fails_closed():
    assert auto_sweep_decision(10.0, 5.0, 1000.0, 0.05, 5.0)["reason"] == "implausible_sol_price"
    assert auto_sweep_decision(10.0, 9999.0, 1000.0, 0.05, 5.0)["reason"] == "implausible_sol_price"


def test_auto_sweep_usd_peg_recomputes_floor():
    # same $1000 floor at $100/SOL = 10 SOL floor; balance 12 -> ~1.95 SOL sweepable
    d = auto_sweep_decision(12.0, 100.0, 1000.0, 0.05, 5.0)
    assert d["should_sweep"] is True and abs(d["floor_sol"] - 10.0) < 1e-6
    assert abs(d["sweepable_sol"] - 1.95) < 1e-6


# ── flaw-fix guards/knobs (2026-06-13, AxiS "all of them") ──────────────────────
def test_sol_floor_override_banks_pure_sol_no_usd_leak():
    # #3: SOL-native floor used directly; USD floor ignored. Keep 5 SOL, bal 8 -> sweep ~3.
    d = auto_sweep_decision(8.0, 200.0, floor_usd=99999.0, gas_buffer_sol=0.0,
                            min_increment_usd_v=5.0, floor_sol_override=5.0)
    assert d["should_sweep"] is True
    assert abs(d["sweepable_sol"] - 3.0) < 1e-6
    assert abs(d["floor_sol"] - 5.0) < 1e-9   # not floor_usd/price


def test_below_floor_flag_set_when_capital_depleted():
    # #2: balance below the (USD) floor -> below_floor True, no sweep.
    d = auto_sweep_decision(2.0, 200.0, 1000.0, 0.05, 5.0)  # floor=5 SOL, bal=2
    assert d["below_floor"] is True
    assert d["should_sweep"] is False


def test_floor_below_min_sanity_refused():
    # #6: floor under the configured sanity minimum -> refuse.
    d = auto_sweep_decision(10.0, 200.0, 50.0, 0.05, 5.0, min_floor_usd_v=500.0)
    assert d["should_sweep"] is False and d["reason"] == "floor_below_min_sanity"


def test_floor_drop_from_hwm_refused():
    # #6: floor (200) far below its high-water (2000) -> fat-finger guard fires.
    d = auto_sweep_decision(50.0, 200.0, 200.0, 0.05, 5.0, floor_hwm_usd=2000.0,
                            floor_drop_frac=0.5)
    assert d["should_sweep"] is False and d["reason"] == "floor_dropped_suspicious"
    # within tolerance (floor 1500 vs hwm 2000) -> allowed
    d2 = auto_sweep_decision(50.0, 200.0, 1500.0, 0.05, 5.0, floor_hwm_usd=2000.0,
                             floor_drop_frac=0.5)
    assert d2["should_sweep"] is True


def test_max_per_sweep_clamp():
    # #6: a single sweep is clamped to the per-sweep cap (blast-radius bound).
    d = auto_sweep_decision(100.0, 200.0, 1000.0, 0.05, 5.0, max_per_sweep_usd_v=500.0)
    assert d["clamped"] is True
    assert abs(d["sweepable_usd"] - 500.0) < 1e-6
    assert abs(d["sweepable_sol"] - 2.5) < 1e-6   # 500/200


def test_opportunistic_flag():
    # #5: flagged when the sweep clears the opportunistic threshold.
    big = auto_sweep_decision(20.0, 200.0, 1000.0, 0.05, 5.0, opportunistic_usd_v=200.0)
    assert big["opportunistic"] is True          # ~$1990 swept >> $200
    # bal 5.30 SOL: floor 5 + gas 0.05 -> 0.25 SOL swept = $50 (>$5 incr, <$200 opp)
    small = auto_sweep_decision(5.30, 200.0, 1000.0, 0.05, 5.0, opportunistic_usd_v=200.0)
    assert small["should_sweep"] is True and small["opportunistic"] is False


def test_backward_compat_existing_signature_unchanged():
    # the original 5-arg call still works exactly as before (no regressions).
    d = auto_sweep_decision(10.0, 200.0, 1000.0, 0.05, 5.0)
    assert d["should_sweep"] is True and "below_floor" in d


# ── #4 over-sweep guards (2026-06-17, USD-floor SOL-price drain) ─────────────────
def test_usd_floor_high_then_low_price_cannot_drain_below_floor():
    """THE BUG: USD floor $884, balance enough to sweep. At a HIGH price tick the naive
    floor_sol is small -> a big sweep is authorized; when price reverts DOWN the SOL left
    is worth < $884. With the stressed-price haircut the kept SOL is valued at the LOW
    (stressed) price, so after a real drop the hot wallet is STILL >= the USD floor."""
    floor_usd = 884.0
    high_price = 250.0   # transiently high tick at decision time
    low_price = 200.0    # price reverts down after the sweep (= 20% drop)
    buf = (high_price - low_price) / high_price  # 0.20 -> stressed price == low price

    # naive (legacy, no buffer) WOULD over-sweep: keep only 884/250 = 3.536 SOL.
    bal = 10.0
    naive = auto_sweep_decision(bal, high_price, floor_usd, 0.0, 5.0,
                                floor_price_buffer_frac_v=0.0)
    kept_naive = bal - naive["sweepable_sol"]
    assert kept_naive * low_price < floor_usd - 0.01   # legacy DRAINS below $884 after revert

    # hardened: stressed-price haircut keeps 884/200 = 4.42 SOL.
    d = auto_sweep_decision(bal, high_price, floor_usd, 0.0, 5.0,
                            floor_price_buffer_frac_v=buf)
    assert d["should_sweep"] is True
    kept = bal - d["sweepable_sol"]
    # after the price reverts to the LOW price, the kept SOL is STILL worth >= the floor.
    assert kept * low_price >= floor_usd - 0.01


def test_post_conversion_floor_assertion_never_oversweeps():
    # #3: at the conversion price used, post-sweep balance is ALWAYS >= floor_sol.
    for bal, price, floor_usd in [(10.0, 200.0, 1000.0), (12.0, 100.0, 1000.0),
                                  (50.0, 180.0, 884.0), (8.0, 250.0, 884.0)]:
        d = auto_sweep_decision(bal, price, floor_usd, 0.05, 5.0)
        if d.get("should_sweep"):
            floor_sol = d["floor_sol"]
            assert bal - d["sweepable_sol"] >= floor_sol - 1e-9


def test_sol_native_floor_never_moves_with_price():
    # #3: a SOL-native floor keeps the SAME SOL regardless of price (no USD-rate leak).
    lo = auto_sweep_decision(8.0, 100.0, 99999.0, 0.0, 5.0, floor_sol_override=5.0)
    hi = auto_sweep_decision(8.0, 300.0, 99999.0, 0.0, 5.0, floor_sol_override=5.0)
    assert lo["floor_sol"] == hi["floor_sol"] == 5.0
    assert lo["sweepable_sol"] == hi["sweepable_sol"]  # kept SOL identical at any price


def test_buffer_haircut_keeps_more_sol_than_naive():
    # the haircut ALWAYS keeps >= the naive amount (never sweeps more than legacy).
    naive = auto_sweep_decision(10.0, 200.0, 884.0, 0.0, 5.0, floor_price_buffer_frac_v=0.0)
    buffered = auto_sweep_decision(10.0, 200.0, 884.0, 0.0, 5.0, floor_price_buffer_frac_v=0.15)
    assert buffered["sweepable_sol"] <= naive["sweepable_sol"]
    assert buffered["floor_sol"] > naive["floor_sol"]


def test_buffer_zero_is_legacy_behavior():
    # buffer 0 (default) reproduces the exact legacy floor_usd/price math.
    d = auto_sweep_decision(10.0, 200.0, 1000.0, 0.05, 5.0, floor_price_buffer_frac_v=0.0)
    assert abs(d["floor_sol"] - 5.0) < 1e-9   # 1000/200, unchanged


# ── #4 hardened LIVE USD-floor guard (trader.py:3265 logic) ─────────────────────
def test_guard_refuses_usd_floor_with_max_per_sweep_alone():
    # THE FIX: a bare SWEEP_MAX_PER_SWEEP_USD no longer satisfies the guard (it is a
    # blast-radius cap, NOT price-risk protection).
    assert _live_usd_floor_guard_allows({
        "WORKING_CAPITAL_FLOOR_USD": "884", "SWEEP_MAX_PER_SWEEP_USD": "100",
    }) is False


def test_guard_refuses_usd_floor_with_ack_but_no_bound():
    # the ack alone is not enough — a bound is still required.
    assert _live_usd_floor_guard_allows({
        "WORKING_CAPITAL_FLOOR_USD": "884", "SWEEP_PRICE_RISK_ACK": "1",
    }) is False


def test_guard_allows_usd_floor_with_ack_and_buffer():
    assert _live_usd_floor_guard_allows({
        "WORKING_CAPITAL_FLOOR_USD": "884", "SWEEP_PRICE_RISK_ACK": "1",
        "SWEEP_FLOOR_PRICE_BUFFER_FRAC": "0.15",
    }) is True


def test_guard_allows_usd_floor_with_ack_and_cap():
    assert _live_usd_floor_guard_allows({
        "WORKING_CAPITAL_FLOOR_USD": "884", "SWEEP_PRICE_RISK_ACK": "1",
        "SWEEP_MAX_PER_SWEEP_USD": "100",
    }) is True


def test_guard_sol_native_floor_bypasses_price_risk():
    # the preferred path: a SOL-native floor is allowed with no ack/bound (no price risk).
    assert _live_usd_floor_guard_allows({
        "WORKING_CAPITAL_FLOOR_SOL": "4.5",
    }) is True


def test_floor_price_buffer_frac_clamped_and_default():
    old = dict(os.environ)
    try:
        os.environ.pop("SWEEP_FLOOR_PRICE_BUFFER_FRAC", None)
        assert ps.floor_price_buffer_frac() == 0.0   # off by default (legacy)
        os.environ["SWEEP_FLOOR_PRICE_BUFFER_FRAC"] = "5"   # absurd -> clamp to 0.9
        assert ps.floor_price_buffer_frac() == 0.9
        os.environ["SWEEP_FLOOR_PRICE_BUFFER_FRAC"] = "-1"  # negative -> 0
        assert ps.floor_price_buffer_frac() == 0.0
    finally:
        os.environ.clear()
        os.environ.update(old)
