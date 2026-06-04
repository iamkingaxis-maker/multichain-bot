"""Profit-sweep executor (core/profit_sweeper) — the most sensitive code in the
bot (real-money transfer). Money-math + fail-closed + dry-run + the $5 cap."""
from core.profit_sweeper import (
    compute_sweepable_sol, usd_to_sol, validate_destination, ratchet_target_sol,
    ProfitSweeper, auto_sweep_decision,
)

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
