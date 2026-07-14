"""Robinhood Chain paper-lane dashboard endpoint — pure parts.

/api/rh-paper aggregation (compute_rh_paper_summary) and the ingest merge
(merge_rh_paper_rows: de-dup on (ts, ev, pool) + oldest-first cap).
Pure-function style (no server), same as the compute_race tests.
"""
from datetime import datetime, timedelta, timezone

from dashboard.web_dashboard import (
    RH_PAPER_MAX_LINES,
    compute_rh_paper_summary,
    merge_rh_paper_rows,
    rh_paper_dedup_key,
    rh_wallet_truth_view,
)


def _day(offset=0):
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")


def _buy(pool="P1", sym="TOK", lat=1.5, day_off=0, hhmm="12:00", **kw):
    r = {"ev": "buy", "ts": f"{_day(day_off)}T{hhmm}:00+00:00", "pool": pool,
         "sym": sym, "usd": 25.0, "lat_total_s": lat}
    r.update(kw)
    return r


def _sell(pool="P1", sym="TOK", pnl=1.0, pct=4.0, day_off=0, hhmm="13:00", **kw):
    r = {"ev": "sell", "ts": f"{_day(day_off)}T{hhmm}:00+00:00", "pool": pool,
         "sym": sym, "pnl_usd": pnl, "pnl_pct": pct}
    r.update(kw)
    return r


# ── compute_rh_paper_summary ─────────────────────────────────────────────────

class TestSummary:
    def test_counts_day_pnl_and_lag_median(self):
        rows = [
            _buy(pool="A", lat=1.0, hhmm="10:00"),
            _buy(pool="B", lat=3.0, hhmm="10:05"),
            _buy(pool="C", lat=2.0, hhmm="10:10"),
            _sell(pool="A", pnl=2.5, hhmm="10:30"),
            _sell(pool="B", pnl=-1.0, hhmm="10:40"),
            _sell(pool="C", pnl=4.0, day_off=1),  # yesterday: not in day pnl
        ]
        out = compute_rh_paper_summary(rows)
        assert out["available"] is True
        assert out["entries"] == 3
        assert out["exits"] == 3
        assert out["day_utc"] == _day(0)
        assert out["day_pnl_usd"] == 1.5           # 2.5 - 1.0 (today only)
        assert out["lag"] == {"median_lat_total_s": 2.0, "n": 3}

    def test_day_pnl_pinned_via_today_utc(self):
        rows = [_sell(pnl=7.0, day_off=1)]
        out = compute_rh_paper_summary(rows, today_utc=_day(1))
        assert out["day_pnl_usd"] == 7.0

    def test_trades_are_last_n_in_order(self):
        rows = [_buy(pool=f"P{i}", hhmm=f"{10 + i // 60:02d}:{i % 60:02d}")
                for i in range(30)]
        out = compute_rh_paper_summary(rows, last_n=20)
        assert len(out["trades"]) == 20
        assert out["trades"][0]["pool"] == "P10"   # oldest kept
        assert out["trades"][-1]["pool"] == "P29"  # newest last
        assert out["entries"] == 30                # counts cover the full ledger

    def test_malformed_and_unknown_events_skipped(self):
        rows = [
            "not a dict", None, 42,
            {"ev": "heartbeat", "ts": f"{_day(0)}T12:00:00"},   # unknown ev
            _buy(lat=None),                                     # lat missing -> no lag row
            _sell(pnl="bad"),                                   # unparseable pnl ignored
            _sell(pnl=3.0, pool="Q"),
        ]
        out = compute_rh_paper_summary(rows)
        assert out["entries"] == 1
        assert out["exits"] == 2
        assert out["day_pnl_usd"] == 3.0
        assert out["lag"] == {"median_lat_total_s": None, "n": 0}
        # unknown/malformed rows never reach the trades table
        assert all(r["ev"] in ("buy", "sell") for r in out["trades"])

    def test_empty_ledger(self):
        out = compute_rh_paper_summary([])
        assert out["available"] is True
        assert out["entries"] == 0 and out["exits"] == 0
        assert out["day_pnl_usd"] == 0.0
        assert out["trades"] == []
        assert out["lag"]["median_lat_total_s"] is None


# ── merge_rh_paper_rows (ingest de-dup + cap) ───────────────────────────────

class TestIngestMerge:
    def test_appends_new_rows(self):
        existing = [_buy(pool="A")]
        merged, added = merge_rh_paper_rows(existing, [_sell(pool="A")])
        assert added == 1
        assert len(merged) == 2
        assert merged[-1]["ev"] == "sell"

    def test_dedup_on_ts_ev_pool(self):
        row = _buy(pool="A")
        merged, added = merge_rh_paper_rows([row], [dict(row), _buy(pool="B")])
        assert added == 1
        assert [r["pool"] for r in merged] == ["A", "B"]

    def test_repush_whole_session_is_idempotent(self):
        session = [_buy(pool="A"), _sell(pool="A"), _buy(pool="B")]
        merged, added = merge_rh_paper_rows([], session)
        assert added == 3
        merged2, added2 = merge_rh_paper_rows(merged, session)
        assert added2 == 0
        assert merged2 == merged

    def test_same_pool_different_ts_or_ev_kept(self):
        rows = [
            _buy(pool="A", hhmm="10:00"),
            _buy(pool="A", hhmm="10:05"),   # same pool, later ts -> kept
            _sell(pool="A", hhmm="10:00"),  # same ts+pool, other ev -> kept
        ]
        merged, added = merge_rh_paper_rows([], rows)
        assert added == 3

    def test_malformed_incoming_skipped(self):
        merged, added = merge_rh_paper_rows(
            [], ["junk", None, {"ev": "buy"}, {"ts": "2026-07-10"},
                 _buy(pool="OK")])
        assert added == 1
        assert merged[0]["pool"] == "OK"

    def test_cap_truncates_oldest(self):
        existing = [{"ev": "buy", "ts": f"t{i}", "pool": "P"} for i in range(5)]
        merged, added = merge_rh_paper_rows(
            existing, [{"ev": "buy", "ts": "t5", "pool": "P"}], max_lines=4)
        assert added == 1
        assert len(merged) == 4
        assert merged[0]["ts"] == "t2"   # oldest dropped
        assert merged[-1]["ts"] == "t5"

    def test_default_cap_is_50k(self):
        assert RH_PAPER_MAX_LINES == 50_000

    def test_dedup_key_shape(self):
        r = _buy(pool="A")
        assert rh_paper_dedup_key(r) == (r["ts"], "buy", "A")


class TestReplaceSemantics:
    """?replace=1 full-sync: merge from empty — corrections with the same
    (ts, ev, pool) key overwrite instead of being dedupe-skipped (the BILLY
    slice-cost phantom could never be fixed in append mode, 2026-07-10)."""

    def test_replace_is_merge_from_empty_with_corrected_row(self):
        bad = _sell(pool="A")
        bad["pnl_usd"] = -18.8
        merged, _ = merge_rh_paper_rows([], [bad])
        fixed = dict(bad)
        fixed["pnl_usd"] = -0.04
        fixed["corrected"] = "slice-cost bug"
        # append mode: correction is SKIPPED (same dedupe key)
        appended, added = merge_rh_paper_rows(merged, [fixed])
        assert added == 0 and appended[0]["pnl_usd"] == -18.8
        # replace mode = merge from empty: correction wins
        replaced, _ = merge_rh_paper_rows([], [fixed])
        assert replaced[0]["pnl_usd"] == -0.04


# ── rh_wallet_truth_view (GET /api/rh-wallet-truth shaping) ──────────────────

class TestWalletTruthView:
    """The RH hot-wallet on-chain truth payload — the Solana /api/wallet-truth
    analog: flags available and derives total_usd from the ETH price captured
    with the reading. Pure; never raises."""

    def _snap(self, **kw):
        # a realistic keyless snapshot as rh_wallet_truth() writes it
        s = {"ok": True, "chain": "robinhood", "wallet": "0x1234…cdef",
             "eth_now": 0.02, "weth_now": 0.005, "total_eth": 0.025,
             "baseline_eth": 0.03, "delta_eth": -0.005,
             "eth_price_usd": 1600.0, "delta_usd": -8.0}
        s.update(kw)
        return s

    def test_flags_available_and_derives_total_usd(self):
        out = rh_wallet_truth_view(self._snap())
        assert out["available"] is True
        assert out["total_usd"] == 40.0            # 0.025 * 1600
        assert out["delta_usd"] == -8.0            # passed through untouched
        assert out["eth_now"] == 0.02 and out["weth_now"] == 0.005

    def test_no_price_means_no_total_usd(self):
        out = rh_wallet_truth_view(self._snap(eth_price_usd=None, delta_usd=None))
        assert out["available"] is True
        assert "total_usd" not in out             # cannot fabricate a USD number

    def test_zero_or_bad_price_skipped(self):
        assert "total_usd" not in rh_wallet_truth_view(self._snap(eth_price_usd=0))
        assert "total_usd" not in rh_wallet_truth_view(
            self._snap(eth_price_usd="oops"))

    def test_error_snapshot_still_available_no_total(self):
        # a read error writes ok:False + error and NO balances — the card shows
        # the error, never a stale/zero number (2026-07-10 incident class)
        out = rh_wallet_truth_view(
            {"ok": False, "error": "RpcError: boom", "wallet": "0x1234…cdef"})
        assert out["available"] is True
        assert out["error"] == "RpcError: boom"
        assert "total_usd" not in out

    def test_malformed_snapshot_unavailable(self):
        assert rh_wallet_truth_view(None)["available"] is False
        assert rh_wallet_truth_view("not a dict")["available"] is False

    def test_does_not_mutate_input(self):
        snap = self._snap()
        rh_wallet_truth_view(snap)
        assert "available" not in snap and "total_usd" not in snap
