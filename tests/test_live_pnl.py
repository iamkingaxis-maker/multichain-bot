"""Real live-P&L computation from actual fills (core/live_pnl.py).

Guards the honest-money view that replaces the simulated bot_state ledger:
real SOL paid on buys vs real SOL received on sells, unsold-corpse detection,
and the gap-vs-simulated reconciliation."""
from core.live_pnl import (
    buy_sol_spent, sell_sol_received, realized_by_token, summarize_real_pnl,
    realized_by_bot,
)

L = 1_000_000_000.0


def _buy(tok, *, size_sol=None, in_amount=None, out_amount=None,
         real_fill_price=None, success=True):
    return {"side": "buy", "token_address": tok, "success": success,
            "size_sol": size_sol, "in_amount": in_amount, "out_amount": out_amount,
            "real_fill_price": real_fill_price}


def _sell(tok, *, out_amount=None, in_amount=None, real_fill_price=None,
          decimals=None, success=True):
    return {"side": "sell", "token_address": tok, "success": success,
            "out_amount": out_amount, "in_amount": in_amount,
            "real_fill_price": real_fill_price, "decimals": decimals}


# ── field extraction ──────────────────────────────────────────────────────────
def test_buy_sol_prefers_size_sol():
    assert buy_sol_spent(_buy("A", size_sol=0.07, in_amount=999 * L)) == 0.07


def test_buy_sol_falls_back_to_in_amount_lamports():
    assert buy_sol_spent(_buy("A", in_amount=int(0.05 * L))) == 0.05


def test_buy_sol_none_when_unavailable():
    assert buy_sol_spent(_buy("A")) is None


def test_sell_sol_from_out_amount_lamports():
    assert sell_sol_received(_sell("A", out_amount=int(0.0559 * L))) == 0.0559


def test_sell_sol_fallback_price_times_tokens():
    # no out_amount: 1000 tokens (decimals 0) * 0.0004 SOL/token = 0.4 SOL
    v = sell_sol_received(_sell("A", in_amount=1000, real_fill_price=0.0004, decimals=0))
    assert abs(v - 0.4) < 1e-9


def test_sell_sol_none_when_unavailable():
    assert sell_sol_received(_sell("A")) is None


# ── per-token pairing ─────────────────────────────────────────────────────────
def test_realized_by_token_nets_buy_and_sell():
    recs = [
        _buy("A", size_sol=0.10),
        _sell("A", out_amount=int(0.13 * L)),  # +0.03 winner
    ]
    d = realized_by_token(recs)["A"]
    assert d["n_buys"] == 1 and d["n_sells"] == 1
    assert abs(d["net_sol"] - 0.03) < 1e-9
    assert d["recovered"] is True


def test_realized_skips_failed_swaps():
    recs = [_buy("A", size_sol=0.10, success=False),
            _sell("A", out_amount=int(0.05 * L), success=False)]
    assert realized_by_token(recs) == {}


def test_unsold_corpse_has_buy_no_sell():
    recs = [_buy("RUG", size_sol=0.07)]
    d = realized_by_token(recs)["RUG"]
    assert d["n_sells"] == 0
    assert abs(d["net_sol"] + 0.07) < 1e-9  # net = -0.07 (money gone)
    assert d["recovered"] is False


# ── summary + reconciliation ──────────────────────────────────────────────────
def test_summary_aggregates_and_reconciles_gap():
    recs = [
        _buy("A", size_sol=0.10), _sell("A", out_amount=int(0.06 * L)),  # -0.04
        _buy("RUG", size_sol=0.05),                                      # corpse -0.05
    ]
    s = summarize_real_pnl(recs, sol_price_usd=100.0, simulated_ledger_usd=12.0)
    assert s["n_tokens"] == 2
    assert abs(s["real_realized_sol"] + 0.09) < 1e-9      # -0.04 + -0.05
    assert s["real_realized_usd"] == -9.0                 # -0.09 * 100
    assert s["unsold_corpse_count"] == 1
    assert s["unsold_corpse_usd"] == -5.0 or s["unsold_corpse_sol"] == 0.05
    # simulated says +12, reality is -9 -> ledger overstates by 21
    assert s["gap_vs_simulated_usd"] == 21.0


def test_summary_empty_is_zeroed_not_raising():
    s = summarize_real_pnl([])
    assert s["n_swaps"] == 0 and s["real_realized_sol"] == 0.0
    assert s["real_realized_usd"] is None  # no price


def test_summary_no_price_leaves_usd_none():
    recs = [_buy("A", size_sol=0.1), _sell("A", out_amount=int(0.2 * L))]
    s = summarize_real_pnl(recs)
    assert s["real_realized_sol"] > 0
    assert s["real_realized_usd"] is None and s["gap_vs_simulated_usd"] is None


# ── per-bot breakdown ─────────────────────────────────────────────────────────
def _b(rec, bot):
    rec["bot_id"] = bot
    return rec


def test_by_bot_splits_and_sorts_worst_first():
    recs = [
        _b(_buy("A", size_sol=0.10), "winner"),
        _b(_sell("A", out_amount=int(0.20 * L)), "winner"),   # +0.10
        _b(_buy("B", size_sol=0.10), "loser"),
        _b(_sell("B", out_amount=int(0.02 * L)), "loser"),    # -0.08
    ]
    rows = realized_by_bot(recs, sol_price_usd=100.0)
    assert [r["bot_id"] for r in rows] == ["loser", "winner"]  # worst first
    assert rows[0]["real_realized_usd"] == -8.0
    assert rows[1]["real_realized_usd"] == 10.0


def test_by_bot_win_rate_and_corpses_are_per_bot():
    recs = [
        _b(_buy("A", size_sol=0.10), "b1"),
        _b(_sell("A", out_amount=int(0.20 * L)), "b1"),  # win
        _b(_buy("RUG", size_sol=0.05), "b1"),            # corpse (no sell)
    ]
    rows = realized_by_bot(recs)
    r = rows[0]
    assert r["bot_id"] == "b1"
    assert r["n_closed_tokens"] == 1 and r["win_rate_pct"] == 100.0
    assert r["unsold_corpse_count"] == 1
    assert abs(r["unsold_corpse_sol"] - 0.05) < 1e-9


def test_by_bot_attributes_untagged_sell_to_buying_bot():
    # Live log: buys carry bot_id, sells DON'T. The sell must be credited to the
    # bot that bought the token (not dumped in a phantom "?" 100%-WR bucket).
    recs = [
        _b(_buy("A", size_sol=0.10), "conviction"),
        {"side": "sell", "token_address": "A", "success": True,
         "out_amount": int(0.06 * L), "bot_id": None},  # -0.04, untagged
    ]
    rows = realized_by_bot(recs, sol_price_usd=100.0)
    assert len(rows) == 1
    assert rows[0]["bot_id"] == "conviction"
    assert rows[0]["n_closed_tokens"] == 1          # paired, not a corpse
    assert rows[0]["unsold_corpse_count"] == 0
    assert rows[0]["real_realized_usd"] == -4.0


def test_by_bot_skips_failed_and_handles_missing_bot_id():
    recs = [
        _buy("A", size_sol=0.1),  # no bot_id -> "?"
        _b(_buy("B", size_sol=0.1, success=False), "dead"),  # failed -> skipped
    ]
    rows = realized_by_bot(recs)
    assert len(rows) == 1 and rows[0]["bot_id"] == "?"
