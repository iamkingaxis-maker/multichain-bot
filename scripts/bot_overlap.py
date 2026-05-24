#!/usr/bin/env python
"""Cross-bot token-overlap matrix.

Usage:
    python scripts/bot_overlap.py <bot1> <bot2> [bot3] [bot4...]
    python scripts/bot_overlap.py --from-file trades.json <bot1> <bot2>

For each pair of bots, computes:
  - |tokens_a ∩ tokens_b| / |tokens_a ∪ tokens_b|  (Jaccard overlap)
  - n unique tokens each bot bought (post-cutoff, non-synthetic)

High overlap (>70%) means the bots are bidding on the same population —
their realized P&L diff measures sizing/exit/filter differences, NOT
"different alpha." Low overlap (<30%) means the comparison is more about
which population each bot reaches than which strategy works.

This is methodology validation: it tells you whether a paired comparison
is honest or polluted by population mismatch.

No Railway impact — reads /api/trades or pre-dumped JSON.
"""
from __future__ import annotations
import argparse
import json
import sys
import urllib.request
from collections import defaultdict


API_URL = "https://gracious-inspiration-production.up.railway.app/api/trades?full=1&limit={limit}"
CUTOFF = "2026-05-23T15:40:00+00:00"


def fetch_trades(limit: int, from_file: str | None) -> list[dict]:
    if from_file:
        with open(from_file, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("trades", [])
    url = API_URL.format(limit=limit)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = json.loads(resp.read())
    return data if isinstance(data, list) else data.get("trades", [])


def tokens_for(trades: list[dict], bot_id: str) -> set[str]:
    """Set of unique tokens this bot bought, post-cutoff."""
    out: set[str] = set()
    for t in trades:
        if t.get("type") != "buy":
            continue
        if t.get("bot_id") != bot_id:
            continue
        if (t.get("time") or "") < CUTOFF:
            continue
        tok = t.get("token")
        if tok:
            out.add(tok)
    return out


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("bots", nargs="+", help="bot_ids to compare (2 or more)")
    p.add_argument("--limit", type=int, default=10000)
    p.add_argument("--from-file", type=str, default=None,
                   help="Read trades from pre-dumped JSON instead of API")
    args = p.parse_args()

    if len(args.bots) < 2:
        print("Need at least 2 bot_ids", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching trades...")
    trades = fetch_trades(args.limit, args.from_file)
    print(f"Pulled {len(trades)} records.")

    tokens_by_bot = {b: tokens_for(trades, b) for b in args.bots}
    for b in args.bots:
        n = len(tokens_by_bot[b])
        if n == 0:
            print(f"  WARNING: {b} has 0 post-cutoff buys", file=sys.stderr)

    # Header
    print()
    print(f"Token overlap (Jaccard) — N unique tokens per bot in parens:")
    print()
    cell_w = 14
    header = " " * 25 + "".join(f"{b[:cell_w]:>{cell_w+1}s}" for b in args.bots)
    print(header)

    # Matrix
    for ba in args.bots:
        row_label = f"{ba[:24]:24s} ({len(tokens_by_bot[ba]):3d})"
        cells = []
        for bb in args.bots:
            if ba == bb:
                cells.append(f"{'—':>{cell_w}s}")
            else:
                j = jaccard(tokens_by_bot[ba], tokens_by_bot[bb])
                cells.append(f"{j*100:>{cell_w-1}.1f}%")
        print(row_label + " " + " ".join(cells))

    # Interpretation hints
    print()
    print("Interpretation:")
    print("  >70%: bots fire on essentially the same population — diff measures sizing/exit/filter at SAME entry")
    print("  30-70%: partial overlap — comparison is a mix of population and strategy")
    print("  <30%: bots reach different populations — comparison is more about coverage than strategy")


if __name__ == "__main__":
    main()
