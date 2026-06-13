"""CT daily-loss boundary (2026-06-13 fix). The floor must roll at CT 00:00,
not UTC 00:00 — else a CT-evening session splits across two floor-days
(chameleon: +$42 wins UTC Jun-12 dropped, -$82 losses UTC Jun-13 kept -> false
lockout). Reproduces that exact scenario."""
from core.per_bot_capital import PerBotCapital, _ct_date_iso


def test_ct_date_groups_evening_session_same_day():
    # The chameleon's six trades: wins 23:39-23:57 UTC Jun-12, losses
    # 00:35-01:53 UTC Jun-13. In CT (UTC-5, CDT) all are Jun-12 evening.
    assert _ct_date_iso("2026-06-12T23:39:12+00:00") == "2026-06-12"
    assert _ct_date_iso("2026-06-13T01:53:14+00:00") == "2026-06-12"  # 20:53 CT
    # CT midnight roll: 05:00 UTC = 00:00 CDT
    assert _ct_date_iso("2026-06-13T04:59:00+00:00") == "2026-06-12"
    assert _ct_date_iso("2026-06-13T05:01:00+00:00") == "2026-06-13"


def test_floor_not_tripped_when_session_stays_in_one_ct_day():
    cap = PerBotCapital("meta_chameleon", 2000.0)
    seq = [  # (cost, proceeds, utc_iso) — reproduces the real chameleon ledger
        (50, 62.90, "2026-06-12T23:39:12+00:00"),   # +12.90 win
        (50, 69.43, "2026-06-12T23:51:49+00:00"),   # +19.43 win
        (50, 59.91, "2026-06-12T23:57:32+00:00"),   # +9.91 win
        (50, 14.17, "2026-06-13T00:35:02+00:00"),   # -35.83 loss (UTC next day)
        (50, 14.85, "2026-06-13T01:05:06+00:00"),   # -35.15 loss
        (50, 38.38, "2026-06-13T01:53:14+00:00"),   # -11.62 loss
    ]
    for cost, proceeds, iso in seq:
        cap.realize_sell(cost, proceeds, now_iso=iso)
    # net CT-day pnl = -40.35, well above the -60 floor -> NOT breached
    assert abs(cap.daily_pnl_usd - (-40.35)) < 0.05
    assert cap.daily_loss_breached(60.0, now_iso="2026-06-13T02:50:00+00:00") is False
    # (pre-fix this read -82.59 and DID breach)


def test_reset_daily_zeros_budget_and_stamps_cutoff():
    cap = PerBotCapital("x", 2000.0)
    cap.realize_sell(50, 10.0, now_iso="2026-06-13T02:00:00+00:00")  # -40
    assert cap.daily_pnl_usd < 0
    cap.reset_daily(now_iso="2026-06-13T02:30:00+00:00")
    assert cap.daily_pnl_usd == 0.0
    assert cap.reset_after_iso == "2026-06-13T02:30:00+00:00"
    assert cap.daily_loss_breached(60.0, now_iso="2026-06-13T02:31:00+00:00") is False
