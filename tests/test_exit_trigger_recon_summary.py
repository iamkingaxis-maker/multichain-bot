"""Tests for core/exit_trigger_recon_summary.summarize_exit_trigger_recon."""
from core.exit_trigger_recon_summary import summarize_exit_trigger_recon


def _rec(stale_reason, fresh_reason, stale_pnl, fresh_pnl, ts="2026-06-28T00:00:00"):
    return {
        "ts": ts,
        "bot": "badday_flush",
        "token": "FOO",
        "addr": "FooAddr111",
        "stale_reason": stale_reason,
        "stale_detail": None,
        "stale_pnl": stale_pnl,
        "fresh_reason": fresh_reason,
        "fresh_detail": None,
        "fresh_pnl": fresh_pnl,
        "agree": stale_reason == fresh_reason,
        "pnl_delta": round(fresh_pnl - stale_pnl, 4),
        "secs_stale": 150.0,
        "stale_price": 1.0,
        "fresh_price": 1.0,
        "peak_pnl_pct": 0.0,
        "tp1_hit": False,
        "secs_since_entry": 120,
    }


def test_agree_and_disagree_counts_and_rate():
    recs = [
        _rec("HOLD", "HOLD", -1.0, -1.0),       # agree
        _rec("HOLD", "HOLD", 2.0, 2.0),         # agree
        _rec("peak", "peak", 5.0, 5.0),         # agree
        _rec("tp1_hit", "HOLD", 6.0, -2.0),     # disagree, stale > fresh
    ]
    out = summarize_exit_trigger_recon(recs)
    assert out["n"] == 4
    assert out["agree_n"] == 3
    assert abs(out["agree_rate"] - 0.75) < 1e-9
    assert out["meta"]["n_total"] == 4
    assert out["meta"]["n_disagree"] == 1


def test_pnl_delta_sign_and_value_paper_overstates():
    # tp1 fired stale (booked +6%) but fresh re-tick says HOLD (only -2%): paper
    # overstates the exit pnl by stale - fresh = 6 - (-2) = +8.
    recs = [
        _rec("HOLD", "HOLD", 1.0, 1.0),         # agree -> excluded from delta
        _rec("tp1_hit", "HOLD", 6.0, -2.0),     # disagree, delta = +8
        _rec("trail", "stop", 4.0, 0.0),        # disagree, delta = +4
    ]
    out = summarize_exit_trigger_recon(recs)
    assert out["pnl_delta"]["n"] == 2
    # mean of [8, 4] = 6.0 ; median = 6.0
    assert abs(out["pnl_delta"]["mean"] - 6.0) < 1e-9
    assert abs(out["pnl_delta"]["median"] - 6.0) < 1e-9
    assert out["direction"] == "paper_OVERSTATES"
    # the tp1-fired-stale / no-fire-fresh signature is visible in by_transition
    assert out["by_transition"].get("tp1_hit->HOLD") == 1
    assert out["by_transition"].get("trail->stop") == 1


def test_paper_understates_when_delta_negative():
    # stale says HOLD (-3%) but fresh fires stop (-8%): paper UNDERSTATES the loss.
    recs = [
        _rec("HOLD", "stop", -3.0, -8.0),       # delta = -3 - (-8) = +5? check sign
    ]
    # delta = stale - fresh = -3 - (-8) = +5 -> that's OVERSTATES. Use a true
    # understate case: stale books smaller pnl than fresh.
    recs = [
        _rec("HOLD", "peak", -5.0, 3.0),        # delta = -5 - 3 = -8 (understates)
    ]
    out = summarize_exit_trigger_recon(recs)
    assert out["pnl_delta"]["mean"] < 0
    assert out["direction"] == "paper_UNDERSTATES"


def test_empty_list_no_crash():
    out = summarize_exit_trigger_recon([])
    assert out["n"] == 0
    assert out["agree_n"] == 0
    assert out["agree_rate"] is None
    assert out["pnl_delta"]["n"] == 0
    assert out["pnl_delta"]["mean"] is None
    assert out["direction"] == "neutral"
    assert out["by_transition"] == {}
    assert out["meta"]["n_total"] == 0


def test_malformed_records_skipped():
    recs = [
        {"garbage": 1},                          # no reasons -> skipped
        "not a dict",                            # non-dict -> skipped
        {"stale_reason": "HOLD"},                # missing fresh_reason -> skipped
        _rec("tp1_hit", "HOLD", 6.0, -2.0),      # valid disagreement
        # disagreement with non-numeric pnl -> counted in n/transition, not delta
        {"ts": "2026-06-28T01:00:00", "stale_reason": "trail",
         "fresh_reason": "stop", "stale_pnl": "oops", "fresh_pnl": None},
    ]
    out = summarize_exit_trigger_recon(recs)
    assert out["n"] == 2                          # two records had both reasons
    assert out["agree_n"] == 0
    assert out["meta"]["n_disagree"] == 2
    assert out["by_transition"].get("tp1_hit->HOLD") == 1
    assert out["by_transition"].get("trail->stop") == 1
    assert out["pnl_delta"]["n"] == 1             # only the numeric one
    assert abs(out["pnl_delta"]["mean"] - 8.0) < 1e-9


def test_neutral_when_disagree_delta_zero():
    recs = [
        _rec("tp1_hit", "tp2_hit", 5.0, 5.0),     # disagree but delta = 0
    ]
    out = summarize_exit_trigger_recon(recs)
    assert out["pnl_delta"]["mean"] == 0.0
    assert out["direction"] == "neutral"
