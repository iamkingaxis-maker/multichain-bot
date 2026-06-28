#!/usr/bin/env python3
"""
core/live_faithful_pnl.py — live-vs-paper FIDELITY GAP, importable + side-effect-free.

Quantifies the P&L gap created by two real-money capital guards that ENFORCE only on
live_probe bots but merely SHADOW-log on paper twins:

  1. per-bot daily-loss halt        (entry_meta key: daily_halt_would_block)
  2. per-day per-token re-entry cap  (entry_meta key: reentry_cap_would_block)

See feeds/dip_scanner.py: paper twins reach the same code but DON'T return, so paper
books buys a funded live bot would NEVER take. Those buys are stamped
daily_halt_would_block / reentry_cap_would_block = True. Removing them reconstructs the
P&L a live-faithful bot would have realized.

Conventions (EXACT logic the analyzer script already validated):
  * Realized $ P&L uses the per-sell-leg `pnl` field (dollars), summed across legs per buy.
  * Per-trade % uses `pnl_pct` per leg, fraction-weighted by `sell_fraction`. Win-rate is
    on the fraction-weighted realized pnl_pct (> 0 == win).
  * Positions keyed by (bot_id, ADDRESS) — NEVER symbol (cross-ticker poisoning).
  * Buys -> sells paired FIFO chronologically within each (bot_id, address) group; each
    sell leg is attributed WHOLLY to the oldest open lot and decrements its remaining frac.

`compute_live_faithful(records)` is pure: no I/O, no globals, no mutation of inputs.
"""
from collections import defaultdict, deque
import statistics

DAILY_KEY = "daily_halt_would_block"
REENTRY_KEY = "reentry_cap_would_block"
EPS = 0.01


def _med(xs):
    return statistics.median(xs) if xs else None


def _mean(xs):
    return (sum(xs) / len(xs)) if xs else None


def _agg(rows):
    """Aggregate a list of per-buy rows into n / usd / mean_pct / med_pct / wr."""
    usd = sum(r["usd"] for r in rows)
    pcts = [r["pct"] for r in rows if r["pct"] is not None]
    wins = sum(1 for p in pcts if p > 0)
    wr = (wins / len(pcts)) if pcts else None
    return {
        "n": len(rows),
        "usd": usd,
        "mean_pct": _mean(pcts),
        "med_pct": _med(pcts),
        "wr": wr,
    }


def _pair_per_buy(records):
    """FIFO-pair buys->sell legs within (bot_id, ADDRESS); roll up per CLOSED buy.

    Returns (per_buy_rows, stats) where stats carries window + diagnostic counts.
    Each per_buy row: {bot_id, blocked, daily_blocked, reentry_blocked, usd, pct}.
    Only buys with >=1 realized sell leg are included (open buys excluded)."""
    data = list(records or [])
    buys = [r for r in data if r.get("type") == "buy"]
    sells = [r for r in data if r.get("type") == "sell"]

    groups = defaultdict(list)
    for r in data:
        groups[(r.get("bot_id"), r.get("address"))].append(r)

    lots_for_buy = {}          # id(buyrec) -> lot
    orphan_sells = 0
    orphan_sell_pnl = 0.0

    for _key, recs in groups.items():
        recs.sort(key=lambda r: r.get("time", ""))
        open_lots = deque()
        for r in recs:
            if r.get("type") == "buy":
                lot = {"buy": r, "remaining": 1.0, "legs": []}
                lots_for_buy[id(r)] = lot
                open_lots.append(lot)
            else:  # sell
                if not open_lots:
                    orphan_sells += 1
                    orphan_sell_pnl += float(r.get("pnl") or 0.0)
                    continue
                lot = open_lots[0]
                lot["legs"].append(r)
                frac = r.get("sell_fraction")
                frac = float(frac) if frac is not None else lot["remaining"]
                lot["remaining"] -= frac
                if lot["remaining"] <= EPS:
                    open_lots.popleft()

    per_buy = []
    open_unsold = 0
    none_flag_buys = 0
    for b in buys:
        lot = lots_for_buy.get(id(b))
        legs = lot["legs"] if lot else []
        em = b.get("entry_meta") or {}
        dhalt = em.get(DAILY_KEY)
        rcap = em.get(REENTRY_KEY)
        if dhalt is None and rcap is None:
            none_flag_buys += 1
        blocked = (dhalt is True) or (rcap is True)
        if not legs:
            open_unsold += 1
            continue
        usd = sum(float(s.get("pnl") or 0.0) for s in legs)
        wsum = 0.0
        psum = 0.0
        for s in legs:
            fr = s.get("sell_fraction")
            fr = float(fr) if fr is not None else 1.0
            pp = s.get("pnl_pct")
            if pp is None:
                continue
            wsum += fr
            psum += float(pp) * fr
        pct = (psum / wsum) if wsum > 0 else None
        per_buy.append({
            "bot_id": b.get("bot_id"),
            "blocked": blocked,
            "daily_blocked": dhalt is True,
            "reentry_blocked": rcap is True,
            "usd": usd,
            "pct": pct,
        })

    times = [r.get("time") for r in data if r.get("time")]
    stats = {
        "n_buys": len(buys),
        "n_sells": len(sells),
        "n_closed": len(per_buy),
        "open_unsold": open_unsold,
        "orphan_sells": orphan_sells,
        "orphan_sell_pnl_usd": round(orphan_sell_pnl, 4),
        "none_flag_buys": none_flag_buys,
        "window_start": min(times) if times else None,
        "window_end": max(times) if times else None,
    }
    return per_buy, stats


def compute_live_faithful(records):
    """Compute paper vs live-faithful P&L from a trade ledger (list of dict records).

    Live-faithful EXCLUDES closed buys whose entry_meta daily_halt_would_block or
    reentry_cap_would_block was True (the trades a funded live bot would skip).

    Returns:
      {
        "fleet": {paper_total_usd, live_faithful_total_usd, delta_usd,
                  would_block_n, would_block_pct, would_block_wr,
                  paper_wr, live_faithful_wr, ...},
        "per_bot": {bot_id: {...}, ...},
        "meta": {n_closed, window_start, window_end, ...},
      }
    delta_usd = paper_total_usd - live_faithful_total_usd == sum of would-blocked realized $.
    Pure / fail-open: malformed or missing entry_meta is treated as NOT blocked.
    """
    per_buy, stats = _pair_per_buy(records)

    paper = _agg(per_buy)
    livef_rows = [r for r in per_buy if not r["blocked"]]
    livef = _agg(livef_rows)
    blocked_rows = [r for r in per_buy if r["blocked"]]
    blk = _agg(blocked_rows)

    n_closed = len(per_buy)
    n_blk = len(blocked_rows)
    pct_blk = (100.0 * n_blk / n_closed) if n_closed else 0.0
    delta = paper["usd"] - livef["usd"]

    fleet = {
        "paper_total_usd": round(paper["usd"], 4),
        "live_faithful_total_usd": round(livef["usd"], 4),
        "delta_usd": round(delta, 4),
        "would_block_n": n_blk,
        "would_block_pct": round(pct_blk, 2),
        "would_block_usd": round(blk["usd"], 4),
        "would_block_wr": blk["wr"],
        "paper_wr": paper["wr"],
        "live_faithful_wr": livef["wr"],
        "paper_n": paper["n"],
        "live_faithful_n": livef["n"],
        "paper_mean_pct": paper["mean_pct"],
        "live_faithful_mean_pct": livef["mean_pct"],
        "direction": (
            "no_would_blocked_closed_trades" if n_blk == 0
            else ("paper_OVERSTATES" if blk["usd"] > 0 else "paper_UNDERSTATES")
        ),
    }

    bybot = defaultdict(list)
    for r in per_buy:
        bybot[r["bot_id"]].append(r)
    per_bot = {}
    for bot, rows in bybot.items():
        pa = _agg(rows)
        bl = [r for r in rows if r["blocked"]]
        lf = _agg([r for r in rows if not r["blocked"]])
        bla = _agg(bl)
        per_bot[bot] = {
            "paper_total_usd": round(pa["usd"], 4),
            "live_faithful_total_usd": round(lf["usd"], 4),
            "delta_usd": round(pa["usd"] - lf["usd"], 4),
            "would_block_n": len(bl),
            "would_block_pct": round(100.0 * len(bl) / len(rows), 2) if rows else 0.0,
            "would_block_usd": round(bla["usd"], 4),
            "would_block_wr": bla["wr"],
            "paper_wr": pa["wr"],
            "live_faithful_wr": lf["wr"],
            "paper_n": pa["n"],
            "live_faithful_n": lf["n"],
        }

    meta = {
        "n_closed": n_closed,
        "n_buys": stats["n_buys"],
        "n_sells": stats["n_sells"],
        "open_unsold": stats["open_unsold"],
        "orphan_sells": stats["orphan_sells"],
        "orphan_sell_pnl_usd": stats["orphan_sell_pnl_usd"],
        "none_flag_buys": stats["none_flag_buys"],
        "window_start": stats["window_start"],
        "window_end": stats["window_end"],
        "daily_key": DAILY_KEY,
        "reentry_key": REENTRY_KEY,
    }

    return {"fleet": fleet, "per_bot": per_bot, "meta": meta}
