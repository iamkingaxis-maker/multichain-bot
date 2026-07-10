# tests/test_runner_signal.py
"""runner_score shadow scorer + HoldTape accumulator (2026-07-10 monster-vs-
regular decode). Pure logic, no network. The synthetic windows reproduce the
mined separation DIRECTION: monster-shaped tape (net inflow + accelerating
buys + upsized buys + fresh makers) must outscore regular-shaped tape
(balanced flow + decaying buys + unchanged size + returning makers)."""
import pytest

from core.runner_signal import (
    runner_score, score_at_exit, HoldTape, MIN_WINDOW_TRADES,
)

NOW = 1_760_000_000.0
WS = NOW - 600.0            # decision window start


def _t(dt, kind, usd, maker=""):
    """Trade at NOW+dt (dt negative = in the past)."""
    return {"kind": kind, "volume_usd": usd, "ts": NOW + dt, "maker": maker}


def _pre_run(n=25, usd=20.0):
    """Pre-run baseline: n buys of $usd from makers p0..p(n-1), spread over
    the 10 min BEFORE the decision window."""
    return [_t(-600 - 20 * (i + 1), "buy", usd, f"p{i}") for i in range(n)]


def monster_window():
    """Monster shape: buys accelerate (10 -> 20), upsize ($30 -> $40 vs $20
    pre-run median), sells tiny, buyers mostly NEW makers."""
    rows = []
    for i in range(10):     # first half: returning makers, modest size
        rows.append(_t(-580 + 25 * i, "buy", 30.0, f"p{i}"))
    for i in range(20):     # second half: fresh makers, bigger buys
        rows.append(_t(-290 + 14 * i, "buy", 40.0, f"m{i}"))
    for i in range(5):
        rows.append(_t(-500 + 100 * i, "sell", 20.0, f"s{i}"))
    return rows


def regular_window():
    """Regular-pop shape: buys DECAY (14 -> 7), size unchanged, flow
    balanced, buyers mostly the same pre-run wallets."""
    rows = []
    for i in range(14):     # first half, mostly returning makers
        rows.append(_t(-580 + 20 * i, "buy", 20.0, f"p{i}" if i < 11 else f"n{i}"))
    for i in range(7):      # second half fades
        rows.append(_t(-290 + 40 * i, "buy", 20.0, f"p{11 + i}"))
    for i in range(10):     # sells match buy dollars -> net_ratio ~ 0
        rows.append(_t(-560 + 55 * i, "sell", 42.0, f"s{i}"))
    return rows


class TestRunnerScoreSeparation:
    def test_monster_shape_scores_high(self):
        score, why = runner_score(monster_window(), WS, NOW,
                                  pre_run_trades=_pre_run())
        assert score is not None and score >= 0.9
        assert why["degraded"] == []
        assert set(why["subs"]) == {"flow", "accel", "size", "fresh"}

    def test_regular_shape_scores_low(self):
        score, why = runner_score(regular_window(), WS, NOW,
                                  pre_run_trades=_pre_run())
        assert score is not None and score <= 0.2

    def test_separation_direction(self):
        m, _ = runner_score(monster_window(), WS, NOW, pre_run_trades=_pre_run())
        r, _ = runner_score(regular_window(), WS, NOW, pre_run_trades=_pre_run())
        assert m > r + 0.3      # the mined gap (0.56 vs 0.38 medians) and then some

    def test_score_bounds(self):
        for win in (monster_window(), regular_window()):
            s, _ = runner_score(win, WS, NOW, pre_run_trades=_pre_run())
            assert 0.0 <= s <= 1.0


class TestRunnerScoreFeatures:
    def test_reasons_carry_raw_features(self):
        _, why = runner_score(monster_window(), WS, NOW,
                              pre_run_trades=_pre_run())
        assert why["net_ratio"] > 0.5
        assert why["bpm_accel"] == 2.0          # 20 buys 2nd half / 10 first
        assert why["med_buy_rel"] == 2.0        # $40 median vs $20 pre-run
        assert why["new_maker_frac"] > 0.6
        assert why["n_trades"] == 35

    def test_out_of_window_trades_excluded(self):
        rows = monster_window() + [_t(-5000, "sell", 10_000.0, "whale"),
                                   _t(+60, "sell", 10_000.0, "future")]
        s, why = runner_score(rows, WS, NOW, pre_run_trades=_pre_run())
        assert why["n_trades"] == 35            # the two outsiders dropped
        assert s >= 0.9                          # giant sells didn't leak in

    def test_iso_timestamps_accepted(self):
        from datetime import datetime, timezone

        def iso(dt):
            return datetime.fromtimestamp(NOW + dt, tz=timezone.utc).isoformat()
        rows = [{"kind": "buy", "volume_usd": 30.0, "ts": iso(-580 + 23 * i),
                 "maker": f"m{i}"} for i in range(25)]
        s, why = runner_score(rows, WS, NOW)
        assert s is not None and why["n_trades"] == 25


class TestRunnerScoreDegradation:
    """None-not-zero discipline (the read-as-zero bug class is CLOSED and
    stays closed): unreadable tape -> None; missing maker/pre-run data ->
    subset score with the degradation NAMED in reasons."""

    def test_thin_tape_returns_none_never_zero(self):
        rows = [_t(-580 + 30 * i, "buy", 30.0, f"m{i}")
                for i in range(MIN_WINDOW_TRADES - 1)]
        score, why = runner_score(rows, WS, NOW, pre_run_trades=_pre_run())
        assert score is None
        assert why["reason"] == "thin_tape"
        assert why["n_trades"] == MIN_WINDOW_TRADES - 1

    def test_no_buys_returns_none(self):
        rows = [_t(-580 + 25 * i, "sell", 30.0, f"s{i}") for i in range(25)]
        score, why = runner_score(rows, WS, NOW)
        assert score is None and why["reason"] == "thin_tape"

    def test_bad_window_returns_none(self):
        assert runner_score(monster_window(), NOW, WS)[0] is None      # inverted
        assert runner_score(monster_window(), None, NOW)[0] is None
        assert runner_score(monster_window(), WS, "garbage")[0] is None

    def test_no_prerun_degrades_to_subset(self):
        score, why = runner_score(monster_window(), WS, NOW,
                                  pre_run_trades=None)
        assert score is not None                # subset score, not None/0
        assert "fresh" not in why["subs"]       # no maker baseline -> dropped
        assert "no_prerun_makers" in why["degraded"]
        # size defaults med_buy_rel=1.0 -> subscore 0, and says so
        assert "no_prerun_size_baseline" in why["degraded"]
        assert why["subs"]["size"] == 0.0

    def test_maker_stripped_tape_drops_fresh_only(self):
        # GT fallback strips maker: window trades carry maker="" -> the
        # fresh subscore is dropped (subset mean), NEVER read as 0
        rows = [dict(t, maker="") for t in monster_window()]
        score, why = runner_score(rows, WS, NOW, pre_run_trades=_pre_run())
        assert score is not None
        assert "fresh" not in why["subs"]
        assert "no_maker_data" in why["degraded"]
        assert why["new_maker_frac"] is None
        # flow/accel/size still present
        assert set(why["subs"]) == {"flow", "accel", "size"}

    def test_never_raises_on_garbage(self):
        rows = [{"kind": None, "volume_usd": "x", "ts": object()},
                {"no": "keys"}, None and {}] + monster_window()
        s, _ = runner_score([r for r in rows if r is not None], WS, NOW,
                            pre_run_trades=_pre_run())
        assert s is not None


class TestScoreAtExit:
    def test_pre_run_sliced_from_same_buffer(self):
        rows = _pre_run() + monster_window()
        s_all, why_all = score_at_exit(rows, NOW)
        assert s_all is not None
        assert why_all["degraded"] == []        # pre-run found in the buffer
        assert why_all["med_buy_rel"] == 2.0

    def test_no_prerun_in_buffer_still_scores(self):
        s, why = score_at_exit(monster_window(), NOW)
        assert s is not None
        assert "no_prerun_makers" in why["degraded"]

    def test_bad_now_returns_none(self):
        assert score_at_exit(monster_window(), None)[0] is None


class TestHoldTape:
    def _trade(self, i, ts=None, maker=None, usd=None):
        return {"kind": "buy", "volume_usd": usd or (10.0 + i),
                "ts": ts or (NOW + i), "maker": maker or f"m{i}"}

    def test_dedupe_across_polls(self):
        ht = HoldTape()
        batch = [self._trade(i) for i in range(30)]
        assert ht.add("pool1", batch, NOW) == 30
        assert ht.add("pool1", batch, NOW + 45) == 0        # full overlap
        assert ht.add("pool1", batch + [self._trade(99)], NOW + 90) == 1
        assert len(ht.get("pool1")) == 31

    def test_dedupe_key_is_ts_maker_volume(self):
        ht = HoldTape()
        a = {"kind": "buy", "volume_usd": 10.0, "ts": NOW, "maker": "m1"}
        b = dict(a, maker="m2")             # different maker -> distinct
        c = dict(a, volume_usd=11.0)        # different volume -> distinct
        d = dict(a, ts=NOW + 1)             # different ts -> distinct
        assert ht.add("p", [a, dict(a), b, c, d], NOW) == 4

    def test_bounding_drops_oldest(self):
        ht = HoldTape(cap_rows=100)
        ht.add("p", [self._trade(i) for i in range(150)], NOW)
        buf = ht.get("p")
        assert len(buf) == 100
        assert buf[0]["ts"] == NOW + 50     # oldest 50 dropped
        # dedupe set rebuilt: re-adding a DROPPED row re-enters (harmless),
        # re-adding a KEPT row does not duplicate
        assert ht.add("p", [self._trade(149)], NOW) == 0

    def test_retention_window_after_close(self):
        ht = HoldTape(retain_secs=1800.0)
        ht.add("p", [self._trade(i) for i in range(5)], NOW)
        ht.sync_open({"p"}, NOW)                    # open -> kept
        assert ht.get("p")
        ht.sync_open(set(), NOW + 10)               # closed at NOW+10
        ht.sync_open(set(), NOW + 10 + 1799)        # inside retention
        assert ht.get("p")
        ht.sync_open(set(), NOW + 10 + 1801)        # past retention
        assert ht.get("p") == []
        assert ht.keys() == []

    def test_reopen_clears_close_countdown(self):
        ht = HoldTape(retain_secs=1800.0)
        ht.add("p", [self._trade(0)], NOW)
        ht.sync_open(set(), NOW)                    # closed
        ht.sync_open({"p"}, NOW + 1000)             # re-opened -> countdown off
        ht.sync_open({"p"}, NOW + 5000)
        assert ht.get("p")                          # survived way past 1800
        ht.sync_open(set(), NOW + 5000)             # closes again from here
        ht.sync_open(set(), NOW + 5000 + 1801)
        assert ht.get("p") == []

    def test_garbage_rows_ignored(self):
        ht = HoldTape()
        assert ht.add("p", [None, "x", 42, {"kind": "buy", "volume_usd": 5.0,
                                            "ts": NOW, "maker": "m"}], NOW) == 1

    def test_keys_isolated_per_pool(self):
        ht = HoldTape()
        ht.add("a", [self._trade(1)], NOW)
        ht.add("b", [self._trade(1)], NOW)          # same trade, other pool
        assert len(ht.get("a")) == 1 and len(ht.get("b")) == 1
        ht.sync_open({"a"}, NOW + 10_000)           # b closed long ago? no —
        # b's countdown STARTS at this sync (first time seen closed)
        assert ht.get("b")
        ht.sync_open({"a"}, NOW + 10_000 + 1801)
        assert ht.get("b") == [] and ht.get("a")
