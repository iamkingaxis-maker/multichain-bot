"""LIVE-SLOT RACE aggregation (dashboard/web_dashboard.compute_race).

Mirrors scripts/bot_leaderboard.py math: sells joined per bot per token,
weight = pnl_pct * sell_fraction, SCRUB = exclude sells with pnl_pct>0 AND
hold_secs<10, per-day green = mean of per-token nets > 0. Live bar =
per-token mean >= +2.0pp on >= 5 days AND >= 30 distinct tokens.
Pure-function style (no server), same as the compute_honest_book scrub tests.
"""
from datetime import datetime, timedelta, timezone

from dashboard.web_dashboard import compute_race


def _day(offset=0):
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _sell(bot, token, pnl, day_off=0, frac=1.0, hold=120.0, **kw):
    t = {"type": "sell", "bot_id": bot, "token": token, "pnl_pct": pnl,
         "sell_fraction": frac, "hold_secs": hold,
         "time": f"{_day(day_off)}T12:00:00"}
    t.update(kw)
    return t


def _bot(payload, bot_id):
    return next(b for b in payload["bots"] if b["bot_id"] == bot_id)


def test_per_day_means_and_green_flags():
    trades = [
        # today: token X sold in two halves (+10 * .5) + (+4 * .5) = +7 net
        _sell("badday_a", "X", 10.0, day_off=0, frac=0.5),
        _sell("badday_a", "X", 4.0, day_off=0, frac=0.5),
        # today: token Y full sell -3 -> day mean = (7 + -3)/2 = +2.0, green
        _sell("badday_a", "Y", -3.0, day_off=0),
        # yesterday: token Z -5 -> mean -5.0, red
        _sell("badday_a", "Z", -5.0, day_off=1),
        # a buy record must be ignored entirely
        {"type": "buy", "bot_id": "badday_a", "token": "X",
         "time": f"{_day(0)}T11:00:00"},
    ]
    out = compute_race(trades)
    assert out["ok"] is True
    b = _bot(out, "badday_a")
    by_day = {r["day"]: r for r in b["per_day"]}
    today, yday = _day(0), _day(1)
    assert by_day[today]["tokens"] == 2
    assert by_day[today]["mean_per_token"] == 2.0
    assert by_day[today]["green"] is True
    assert by_day[yday]["tokens"] == 1
    assert by_day[yday]["mean_per_token"] == -5.0
    assert by_day[yday]["green"] is False
    assert b["green_days"] == 1 and b["day_count"] == 2
    assert b["distinct_tokens_7d"] == 3
    # per-token nets: X +7, Y -3, Z -5 -> mean = -1/3
    assert b["mean_per_token_7d"] == round((7.0 - 3.0 - 5.0) / 3, 2)
    # exactly one day at >= +2.0
    assert b["live_bar"]["met_days"] == 1
    assert b["live_bar"]["n_ok"] is False
    assert b["live_bar"]["pace"] is False


def test_scrub_excludes_latency_spike_but_keeps_fast_losers():
    trades = [
        _sell("badday_a", "X", 50.0, hold=3.0),    # spike: pnl>0, hold<10 -> OUT
        _sell("badday_a", "X", -4.0, hold=3.0),    # fast LOSS stays (scrub is one-sided)
        _sell("badday_a", "Y", 12.0, hold=45.0),   # slow winner stays
    ]
    out = compute_race(trades)
    b = _bot(out, "badday_a")
    day = {r["day"]: r for r in b["per_day"]}[_day(0)]
    # X net = -4 (spike dropped), Y = +12 -> mean = +4.0
    assert day["mean_per_token"] == 4.0
    assert day["green"] is True
    assert b["distinct_tokens_7d"] == 2


def test_multi_bot_token_counted_per_bot():
    # Same token traded by two bots: each bot gets its OWN per-token net.
    trades = [
        _sell("badday_a", "TOK", 6.0),
        _sell("badday_b", "TOK", -2.0),
    ]
    out = compute_race(trades)
    a, b = _bot(out, "badday_a"), _bot(out, "badday_b")
    assert a["mean_per_token_7d"] == 6.0 and a["per_day"][0]["green"] is True
    assert b["mean_per_token_7d"] == -2.0 and b["per_day"][0]["green"] is False
    # sorted desc by 7d mean/token
    means = [r["mean_per_token_7d"] for r in out["bots"]]
    assert means == sorted(means, reverse=True)


def test_window_and_scope_filters():
    trades = [
        _sell("badday_a", "OLD", 9.0, day_off=10),   # outside 7d window
        _sell("follow_x", "TOK", 9.0),               # not a badday_ bot
        _sell("badday_a", "BAD", None),              # unparseable pnl -> skipped
        _sell("badday_a", "IN", 3.0, day_off=6),     # oldest in-window day kept
    ]
    out = compute_race(trades)
    assert [b["bot_id"] for b in out["bots"]] == ["badday_a"]
    b = out["bots"][0]
    assert b["distinct_tokens_7d"] == 1
    assert b["per_day"][0]["day"] == _day(6)
    assert len(out["window_days"]) == 7


def test_live_bar_pace_when_both_legs_met():
    # 30 distinct tokens over 5 days, every day mean +3 (>= +2.0).
    trades = []
    n = 0
    for d in range(5):
        for _ in range(6):
            trades.append(_sell("badday_champ", f"T{n}", 3.0, day_off=d))
            n += 1
    out = compute_race(trades)
    b = _bot(out, "badday_champ")
    assert b["distinct_tokens_7d"] == 30
    assert b["live_bar"] == {"met_days": 5, "n_ok": True, "pace": True}


def test_enabled_ids_restrict_and_emit_idle_rows():
    trades = [
        _sell("badday_a", "X", 5.0),
        _sell("badday_retired", "Y", 5.0),
    ]
    out = compute_race(trades, enabled_ids={"badday_a", "badday_idle", "other_bot"})
    ids = {b["bot_id"] for b in out["bots"]}
    # retired bot filtered out; idle enabled badday_ bot shows a zero row
    assert ids == {"badday_a", "badday_idle"}
    idle = _bot(out, "badday_idle")
    assert idle["day_count"] == 0 and idle["distinct_tokens_7d"] == 0
    assert idle["mean_per_token_7d"] == 0.0
    assert idle["live_bar"]["pace"] is False
