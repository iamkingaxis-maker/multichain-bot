"""REAL live P&L — measured from actual on-chain fills, NOT the simulated ledger.

WHY THIS EXISTS (2026-06-28): the dashboard's per-bot `realized_pnl_total_usd`
(bot_state ledger) is a SIMULATED number — it books every sell at the strategy's
snapshot/decision price and is dominated by paper-mode trades. During the live
probe it reported +$185 "profit" while the real wallet drained ~$48. The only
honest live P&L comes from (a) the on-chain wallet SOL delta and (b) the real
fill amounts captured in live_swaps.jsonl. This module computes (b) — the real
realized P&L from actual fills — and a reconciliation against the simulated
ledger so the gap (drift + slippage + unsold corpses) is visible.

PURE + DEFENSIVE: every function tolerates missing/None fields, never raises,
and ignores unsuccessful swaps. Amounts follow Jupiter swap conventions:
  * BUY  (SOL -> token): in_amount = SOL lamports spent, out_amount = token raw.
  * SELL (token -> SOL): in_amount = token raw sold,  out_amount = SOL lamports.
So real SOL paid on a buy = in_amount/1e9 (or size_sol), and real SOL received
on a sell = out_amount/1e9. We deliberately do NOT use sol_before/sol_after —
that capture is degenerate (always equal) in the live_swaps log.
"""
from __future__ import annotations

LAMPORTS_PER_SOL = 1_000_000_000.0


def _f(v):
    """Coerce to float, treating bool/None/garbage as None."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def buy_sol_spent(rec: dict) -> float | None:
    """Real SOL paid on a BUY. Prefer size_sol; fall back to in_amount/1e9.

    Returns None if neither is available (so callers can skip incomplete recs)."""
    s = _f(rec.get("size_sol"))
    if s is not None and s > 0:
        return s
    ia = _f(rec.get("in_amount"))
    if ia is not None and ia > 0:
        return ia / LAMPORTS_PER_SOL
    return None


def sell_sol_received(rec: dict) -> float | None:
    """Real SOL received on a SELL. out_amount is SOL lamports out.

    Falls back to in_amount(tokens) * real_fill_price when out_amount is absent.
    Returns None when neither is available."""
    oa = _f(rec.get("out_amount"))
    if oa is not None and oa > 0:
        return oa / LAMPORTS_PER_SOL
    ia = _f(rec.get("in_amount"))
    px = _f(rec.get("real_fill_price"))
    if ia is not None and px is not None and ia > 0 and px > 0:
        dec = _f(rec.get("decimals"))
        toks = ia / (10 ** int(dec)) if dec is not None else ia
        return toks * px
    return None


def realized_by_token(recs: list) -> dict:
    """Pair successful live swaps by token_address into real-fill SOL flows.

    Returns {token_address: {buy_sol, sell_sol, net_sol, n_buys, n_sells,
    recovered}} where net_sol = sell_sol - buy_sol (the real realized SOL on
    that token so far) and recovered = whether sells have returned >= the SOL
    put in. A token with n_sells == 0 (or net_sol strongly negative while held)
    is an UNSOLD CORPSE — real money spent that the simulated ledger never books
    as a loss. Pure + defensive."""
    out: dict = {}
    for r in recs or []:
        if not bool(r.get("success")):
            continue
        tok = r.get("token_address") or ""
        side = (r.get("side") or "").strip().lower()
        d = out.setdefault(tok, {"buy_sol": 0.0, "sell_sol": 0.0,
                                 "n_buys": 0, "n_sells": 0})
        if side == "buy":
            v = buy_sol_spent(r)
            if v is not None:
                d["buy_sol"] += v
                d["n_buys"] += 1
        elif side == "sell":
            v = sell_sol_received(r)
            if v is not None:
                d["sell_sol"] += v
                d["n_sells"] += 1
    for tok, d in out.items():
        d["net_sol"] = round(d["sell_sol"] - d["buy_sol"], 9)
        d["buy_sol"] = round(d["buy_sol"], 9)
        d["sell_sol"] = round(d["sell_sol"], 9)
        d["recovered"] = d["sell_sol"] >= d["buy_sol"] and d["n_buys"] > 0
    return out


def realized_by_bot(recs: list, sol_price_usd: float | None = None) -> list:
    """Per-bot real-fill realized P&L, so we can see which config's REAL edge is
    least-negative (vs the simulated ledger which credits them all).

    Groups successful swaps by bot_id, pairs buys/sells per token WITHIN each bot,
    and returns a list (worst real_realized_sol first) of:
      bot_id, n_swaps, n_tokens, n_closed_tokens, buy_sol, sell_sol,
      real_realized_sol, real_realized_usd, unsold_corpse_count,
      unsold_corpse_sol, win_rate_pct (per closed token, real outcome).
    Pure + defensive."""
    px = _f(sol_price_usd)
    # Sells in the live-swap log do NOT carry bot_id (only buys do), so attribute
    # each sell to the bot that BOUGHT that token. Without this, every named bot
    # looks like all-corpses (buys only) and a phantom "?" bucket holds all sells
    # at 100% WR. Build token -> bot from buys first.
    tok_bot: dict = {}
    for r in recs or []:
        if bool(r.get("success")) and (r.get("side") or "").strip().lower() == "buy":
            b = r.get("bot_id")
            if b:
                tok_bot.setdefault(r.get("token_address") or "", b)
    grouped: dict = {}
    for r in recs or []:
        if not bool(r.get("success")):
            continue
        bid = r.get("bot_id")
        if not bid:  # sell (or untagged) -> the bot that bought this token
            bid = tok_bot.get(r.get("token_address") or "", "?")
        grouped.setdefault(bid, []).append(r)
    out = []
    for bid, brecs in grouped.items():
        bt = realized_by_token(brecs)
        real_sol = round(sum(d["net_sol"] for d in bt.values()), 9)
        buy_sol = round(sum(d["buy_sol"] for d in bt.values()), 9)
        sell_sol = round(sum(d["sell_sol"] for d in bt.values()), 9)
        corpses = {t: d for t, d in bt.items()
                   if d["n_buys"] > 0 and d["n_sells"] == 0}
        closed = {t: d for t, d in bt.items() if d["n_sells"] > 0}
        wins = sum(1 for d in closed.values() if d["net_sol"] > 0)
        out.append({
            "bot_id": bid,
            "n_swaps": len(brecs),
            "n_tokens": len(bt),
            "n_closed_tokens": len(closed),
            "buy_sol": buy_sol,
            "sell_sol": sell_sol,
            "real_realized_sol": real_sol,
            "real_realized_usd": (round(real_sol * px, 2) if px is not None else None),
            "unsold_corpse_count": len(corpses),
            "unsold_corpse_sol": round(sum(d["buy_sol"] for d in corpses.values()), 9),
            "win_rate_pct": (round(100.0 * wins / len(closed), 1) if closed else None),
        })
    out.sort(key=lambda d: d["real_realized_sol"])
    return out


def summarize_real_pnl(recs: list, sol_price_usd: float | None = None,
                       simulated_ledger_usd: float | None = None) -> dict:
    """Aggregate real-fill realized P&L and reconcile against the simulated ledger.

    real_realized_sol = sum of per-token net_sol (real SOL out of trading).
    unsold_corpses = tokens with buys but zero sells (money spent, never booked).
    gap_vs_simulated_usd = simulated_ledger_usd - real_realized_usd (how much the
    dashboard ledger overstates reality). All USD fields None when no price.
    Pure + defensive; empty input -> zeroed summary."""
    by_tok = realized_by_token(recs)
    real_sol = round(sum(d["net_sol"] for d in by_tok.values()), 9)
    buy_sol = round(sum(d["buy_sol"] for d in by_tok.values()), 9)
    sell_sol = round(sum(d["sell_sol"] for d in by_tok.values()), 9)
    corpses = {t: d for t, d in by_tok.items()
               if d["n_buys"] > 0 and d["n_sells"] == 0}
    corpse_sol = round(sum(d["buy_sol"] for d in corpses.values()), 9)

    px = _f(sol_price_usd)
    real_usd = round(real_sol * px, 2) if px is not None else None
    corpse_usd = round(corpse_sol * px, 2) if px is not None else None
    sim = _f(simulated_ledger_usd)
    gap_usd = (round(sim - real_usd, 2)
               if (sim is not None and real_usd is not None) else None)

    return {
        "n_swaps": len([r for r in (recs or []) if bool(r.get("success"))]),
        "n_tokens": len(by_tok),
        "total_buy_sol": buy_sol,
        "total_sell_sol": sell_sol,
        "real_realized_sol": real_sol,
        "real_realized_usd": real_usd,
        "unsold_corpse_count": len(corpses),
        "unsold_corpse_sol": corpse_sol,
        "unsold_corpse_usd": corpse_usd,
        "simulated_ledger_usd": (round(sim, 2) if sim is not None else None),
        "gap_vs_simulated_usd": gap_usd,
        "sol_price_usd": (round(px, 2) if px is not None else None),
    }
