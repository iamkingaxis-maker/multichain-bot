"""SP5 stale-cache reset migration.

The first multi-bot fleet deploy (pre-commit 53eca0d) bought many tokens
on stale-cache prices, then bled out. Those losses sit in trades.json and
pollute SP4 attribution. This migration:

1. Computes each bot's "true" capital state from POST-cutoff trades only.
2. Rewrites bot_state/<id>.json with the recomputed balance/in_flight/realized.
3. Drops a sentinel file so it never runs twice.

The cutoff is sp4_common.MIN_TRADE_TIMESTAMP (currently the 2026-05-23
post-restoration deploy timestamp). The restoration logic in dip_scanner
should also filter open-position restoration by the same cutoff, so
pre-cutoff zombie buys don't get re-opened.

This script does NOT mutate trades.json — that's the canonical record.
Pre-cutoff trades are simply excluded from balance accounting.

Idempotent via /data/sp5_reset_done.json sentinel.
"""
from __future__ import annotations
import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Optional


def _load_bot_configs(config_dir: Path) -> dict[str, dict]:
    """Read all bot config JSONs to map bot_id -> {paper_capital_usd, base_position_usd}."""
    out: dict[str, dict] = {}
    for p in sorted(config_dir.glob("*.json")):
        try:
            cfg = json.loads(p.read_text())
        except Exception:
            continue
        bid = cfg.get("bot_id")
        if not bid:
            continue
        out[bid] = {
            "paper_capital_usd": float(cfg.get("paper_capital_usd", 2000.0)),
            "base_position_usd": float(cfg.get("base_position_usd", 20.0)),
        }
    return out


def _recompute_state(
    bot_id: str,
    paper_capital: float,
    base_position: float,
    trades: list[dict],
    cutoff: str,
) -> dict:
    """Given a bot's filtered trade history, compute fresh capital state.

    Algorithm: start with balance=paper_capital, in_flight=0, realized=0.
    Walk every post-cutoff buy and sell in time order. Pair sells with
    buys via (token, entry_price). Any unmatched buys at the end are
    still-open positions and account for in_flight.
    """
    buys_open: dict[tuple, dict] = {}
    balance = paper_capital
    in_flight = 0.0
    realized = 0.0

    bot_trades = [t for t in trades if t.get("bot_id", "baseline_v1") == bot_id]
    bot_trades = [t for t in bot_trades if (t.get("time") or "") >= cutoff]
    bot_trades.sort(key=lambda t: t.get("time", ""))

    for t in bot_trades:
        token = t.get("token")
        price = t.get("entry_price")
        if t.get("type") == "buy":
            size = float(t.get("amount_usd", base_position))
            balance -= size
            in_flight += size
            buys_open[(token, price)] = t
        elif t.get("type") == "sell":
            key = (token, price)
            buy = buys_open.pop(key, None)
            if buy is None:
                # sell w/o matching buy (cross-cutoff) — ignore for balance
                # purposes; the buy was pre-cutoff so the position never
                # existed in the new accounting.
                continue
            buy_size = float(buy.get("amount_usd", base_position))
            pnl = float(t.get("pnl", 0.0))
            proceeds = buy_size + pnl
            in_flight -= buy_size
            balance += proceeds
            realized += pnl

    return {
        "bot_id": bot_id,
        "balance_usd": balance,
        "in_flight_usd": in_flight,
        "realized_pnl_total_usd": realized,
        "daily_pnl_usd": 0.0,
        # daily_pnl_date intentionally left as "" — PerBotCapital.from_dict
        # will reset it on first read via _check_daily_rollover.
        "daily_pnl_date": "",
    }


def migrate(
    data_dir: Path,
    config_dir: Path,
    cutoff: Optional[str] = None,
    force: bool = False,
) -> int:
    """Run the reset. Returns number of bot_state files rewritten.

    Idempotent via /data/sp5_reset_done.json sentinel. Pass force=True
    to bypass the sentinel (for testing / re-running).
    """
    if cutoff is None:
        # Lazy import so this module doesn't depend on scripts/ being a package
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from sp4_common import MIN_TRADE_TIMESTAMP
        cutoff = MIN_TRADE_TIMESTAMP

    if not cutoff:
        print("[sp5_reset] MIN_TRADE_TIMESTAMP is empty — skipping (no cutoff configured)")
        return 0

    sentinel = data_dir / "sp5_reset_done.json"
    if sentinel.exists() and not force:
        # Re-fire if cutoff has changed since the last run.
        try:
            prev = json.loads(sentinel.read_text())
            if prev.get("cutoff") == cutoff:
                print(f"[sp5_reset] sentinel exists at {sentinel} for same cutoff — skipping")
                return 0
            print(f"[sp5_reset] cutoff changed ({prev.get('cutoff')} -> {cutoff}) — re-running reset")
        except Exception:
            print(f"[sp5_reset] sentinel unreadable — re-running reset")

    trades_path = data_dir / "trades.json"
    if not trades_path.exists():
        print(f"[sp5_reset] no trades.json at {trades_path} — nothing to reset")
        return 0
    trades = json.loads(trades_path.read_text())

    configs = _load_bot_configs(config_dir)
    if not configs:
        print(f"[sp5_reset] no bot configs found in {config_dir} — aborting")
        return 0

    bot_state_dir = data_dir / "bot_state"
    bot_state_dir.mkdir(exist_ok=True)

    # Backup existing bot_state files before overwriting
    backup_dir = data_dir / "bot_state.pre-sp5-reset"
    if not backup_dir.exists():
        backup_dir.mkdir()
        for p in bot_state_dir.glob("*.json"):
            (backup_dir / p.name).write_text(p.read_text())
        print(f"[sp5_reset] backed up existing bot_state to {backup_dir}")

    rewritten = 0
    for bot_id, cfg in configs.items():
        state = _recompute_state(
            bot_id=bot_id,
            paper_capital=cfg["paper_capital_usd"],
            base_position=cfg["base_position_usd"],
            trades=trades,
            cutoff=cutoff,
        )
        out_path = bot_state_dir / f"{bot_id}.json"
        out_path.write_text(json.dumps(state, indent=2))
        rewritten += 1

    sentinel.write_text(json.dumps({
        "cutoff": cutoff,
        "rewritten_bots": rewritten,
    }, indent=2))

    print(f"[sp5_reset] reset {rewritten} bot_state files (cutoff={cutoff})")
    return rewritten


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="/data")
    p.add_argument("--config-dir", default=str(Path(__file__).parent.parent / "config" / "bots"))
    p.add_argument("--cutoff", default=None, help="Override MIN_TRADE_TIMESTAMP")
    p.add_argument("--force", action="store_true", help="Bypass sentinel")
    args = p.parse_args()
    raise SystemExit(0 if migrate(
        data_dir=Path(args.data_dir),
        config_dir=Path(args.config_dir),
        cutoff=args.cutoff,
        force=args.force,
    ) >= 0 else 1)
