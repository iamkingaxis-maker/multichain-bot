#!/usr/bin/env python
"""
PER-GATE SHADOW WOULD-BLOCK P&L ATTRIBUTION
===========================================

THE QUESTION
------------
Several entry gates run in SHADOW: they log a "would-block" but the trade STILL
EXECUTES, so its realized outcome still lands in the ledger. So for EACH gate we
can answer the only number that justifies (or kills) an ENFORCE decision while
honoring the standing "don't kill winners" constraint:

    If we flipped this gate to ENFORCE, how many WINNERS would it have killed,
    and how much BLEED would it have avoided — net?

THE JOIN
--------
core/shadow_gate_log.py emits one jsonl line per shadow would-block to
DATA_DIR/shadow_gate_events.jsonl (ts, gate, bot, token_address, symbol, ctx).
This joiner matches each would-block to the realized CLOSED trade it would have
prevented: SAME bot_id AND token_address (ADDRESS-keyed — never symbol; symbol
cross-poisons same-ticker mints), entry time nearest the event ts within
--max-skew. One event -> at most one trade; a trade is counted ONCE per gate
(dedupe), so multiple would-blocks of the same gate on one trade don't
double-count.

P&L RULE (hard project constraint)
-----------------------------------
NEVER sum the trades-feed per-trade `pnl` DOLLAR field — it is corrupted/inverted
and produces wrong rankings. Win/loss + edge use `pnl_pct` (PRIMARY). To express
dollars we RECONSTRUCT entry size from usd_received / (1 + pnl_pct/100) and
multiply by pnl_pct/100. Every $ figure is labeled "reconstructed from pct x
size"; the pct-based numbers are always reported as primary.

FORWARD-ONLY: historical would-blocks are gone from the rolling Railway log
buffer — attribution only covers would-blocks captured since deploy.

READ-ONLY: reads the JSONL + a trades JSON dump. Never touches money/state.

USAGE
-----
  # 1) pull the trades feed to a REPO-DIR file (Git-Bash /tmp != Windows-python /tmp)
  curl -s "<railway>/api/trades?all=1" -o ./_sgtrades.json
  # 2) join (events default to $DATA_DIR/shadow_gate_events.jsonl)
  python scripts/shadow_gate_pnl.py --events ./shadow_gate_events.jsonl --trades ./_sgtrades.json
  rm ./_sgtrades.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
from typing import Dict, List, Optional

# Allow running as a bare script: make the repo root importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ──────────────────────────────────────────────────────────────────────────────
# PURE / TESTABLE CORE
# ──────────────────────────────────────────────────────────────────────────────

def _parse_iso(s) -> Optional[dt.datetime]:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def reconstruct_size_usd(trade: dict) -> Optional[float]:
    """Reconstruct the ENTRY notional ($) from the exit proceeds + pnl_pct:
        entry_size = usd_received / (1 + pnl_pct/100)
    Returns None if either field is missing/unusable (NEVER raises). We do NOT
    use the feed `pnl` dollar field (corrupted/inverted per project rule)."""
    ur = trade.get("usd_received")
    pp = trade.get("pnl_pct")
    try:
        ur = float(ur)
        pp = float(pp)
    except (TypeError, ValueError):
        return None
    denom = 1.0 + pp / 100.0
    if denom == 0:
        return None
    return ur / denom


def _trade_entry_ts(trade: dict) -> Optional[float]:
    """Unix-seconds entry time for a sell. Prefer an explicit entry_ts (tests),
    else derive from sell `time` minus `hold_secs`."""
    ets = trade.get("entry_ts")
    if ets is not None:
        try:
            return float(ets)
        except (TypeError, ValueError):
            pass
    sell_ts = _parse_iso(trade.get("time"))
    if sell_ts is None:
        return None
    hold = trade.get("hold_secs")
    try:
        hold = float(hold) if hold is not None else 0.0
    except (TypeError, ValueError):
        hold = 0.0
    return sell_ts.timestamp() - hold


def _trade_size_usd(trade: dict) -> Optional[float]:
    """Entry notional ($): explicit size_usd (tests) else reconstructed from
    usd_received x pnl_pct (NEVER the corrupted feed `pnl`)."""
    s = trade.get("size_usd")
    if s is not None:
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
    return reconstruct_size_usd(trade)


def index_trades(trades: List[dict]) -> Dict[str, List[dict]]:
    """(bot_lower, addr_lower) is the join scope; here we index addr_lower ->
    [closed sell dicts] each annotated with a numeric entry_ts. ADDRESS-keyed.
    Only CLOSED sells with a usable pnl_pct + entry time are indexed."""
    by_addr: Dict[str, List[dict]] = {}
    for t in trades:
        # synthetic test trades omit `type`; treat present-type as a filter.
        if t.get("type") is not None and t.get("type") != "sell":
            continue
        if t.get("pnl_pct") is None:
            continue
        addr = (t.get("address") or t.get("token") or "")
        if not addr:
            continue
        ets = _trade_entry_ts(t)
        if ets is None:
            continue
        rec = dict(t)
        rec["entry_ts"] = ets
        by_addr.setdefault(addr.lower(), []).append(rec)
    return by_addr


def match_block_to_trade(event: dict, trades_by_addr: Dict[str, List[dict]],
                         max_skew: float = 600.0) -> Optional[dict]:
    """Match a shadow would-block event to the realized CLOSED sell it would have
    prevented: SAME bot_id AND token_address (ADDRESS-keyed, lowercased), entry
    time NEAREST the event ts within max_skew. None if no such trade.

    `trades_by_addr` is the output of index_trades (addr_lower -> sell dicts with
    numeric entry_ts)."""
    addr = (event.get("token_address") or "").lower()
    if not addr:
        return None
    bot = (event.get("bot") or "")
    cand = trades_by_addr.get(addr)
    if not cand:
        return None
    ev_ts = _parse_iso(event.get("ts"))
    ev_secs = ev_ts.timestamp() if ev_ts else None
    best = None
    best_skew = None
    for t in cand:
        if str(t.get("bot_id", "")) != str(bot):
            continue
        ets = t.get("entry_ts")
        if ets is None:
            continue
        if ev_secs is None:
            # no event ts -> can't rank by skew; take the first same-bot match.
            return t
        skew = abs(float(ets) - ev_secs)
        if skew <= max_skew and (best_skew is None or skew < best_skew):
            best = t
            best_skew = skew
    return best


def _trade_key(trade: dict) -> tuple:
    """Stable identity for dedupe within a gate (one trade counted once)."""
    return (
        str(trade.get("bot_id", "")),
        str(trade.get("address") or trade.get("token") or "").lower(),
        trade.get("entry_ts"),
    )


def gate_attribution(events: List[dict], trades: List[dict],
                     max_skew: float = 600.0) -> Dict[str, dict]:
    """Per gate, over the matched would-blocked CLOSED trades, compute the
    enforce-decision numbers. Returns {gate: {...}}.

    Per-gate keys:
      n_blocked, n_unmatched_events,
      winners_blocked, winners_blocked_pct, losers_blocked,   <- winner-kill
      wr, median_pnl_pct, sum_pnl_pct,
      bleed_avoided_usd, winners_given_up_usd,                <- reconstructed $
      net_edge_pct (= -sum_pnl_pct), net_edge_usd (reconstructed)
    """
    by_addr = index_trades(trades)
    # gate -> {set_of_trade_keys, list_of_(pnl_pct, size_usd), unmatched_count}
    acc: Dict[str, dict] = {}
    for ev in events:
        gate = str(ev.get("gate", "?"))
        g = acc.setdefault(gate, {"keys": set(), "rows": [], "unmatched": 0})
        t = match_block_to_trade(ev, by_addr, max_skew)
        if t is None:
            g["unmatched"] += 1
            continue
        key = _trade_key(t)
        if key in g["keys"]:
            continue  # dedupe: this trade already attributed to this gate
        g["keys"].add(key)
        try:
            pp = float(t.get("pnl_pct"))
        except (TypeError, ValueError):
            g["unmatched"] += 1
            continue
        g["rows"].append((pp, _trade_size_usd(t)))

    out: Dict[str, dict] = {}
    for gate, g in acc.items():
        rows = g["rows"]
        n = len(rows)
        pcts = [pp for pp, _ in rows]
        winners = [pp for pp in pcts if pp > 0]
        losers = [pp for pp in pcts if pp <= 0]
        # reconstructed $ (only over rows where size is known)
        bleed_avoided = -sum((sz * pp / 100.0)
                             for pp, sz in rows if sz is not None and pp <= 0)
        winners_given_up = sum((sz * pp / 100.0)
                               for pp, sz in rows if sz is not None and pp > 0)
        net_edge_usd = -sum((sz * pp / 100.0)
                            for pp, sz in rows if sz is not None)
        out[gate] = {
            "n_blocked": n,
            "n_unmatched_events": g["unmatched"],
            "winners_blocked": len(winners),
            "winners_blocked_pct": (100.0 * len(winners) / n) if n else None,
            "losers_blocked": len(losers),
            "wr": (100.0 * len(winners) / n) if n else None,
            "median_pnl_pct": statistics.median(pcts) if pcts else None,
            "sum_pnl_pct": sum(pcts),
            "bleed_avoided_usd": bleed_avoided,
            "winners_given_up_usd": winners_given_up,
            "net_edge_pct": -sum(pcts),
            "net_edge_usd": net_edge_usd,
        }
    return out


def compute_gate_pnl(events_path: str, trades: List[dict],
                     max_skew: float = 600.0,
                     out_path: Optional[str] = None) -> Dict[str, dict]:
    """Importable wrapper: load shadow_gate_events.jsonl from `events_path`,
    join to `trades` (already-parsed list of closed sells), and return the
    per-gate attribution dict. If out_path is given, also write the dict to
    disk (atomic temp + replace). FAIL-OPEN: a missing events file returns {}
    (forward-only — nothing captured yet); never raises.

    `trades` is passed in already-parsed so the caller controls the (heavy)
    trades read off-loop — this function does no trades IO."""
    if not events_path or not os.path.exists(events_path):
        return {}
    try:
        events = _load_jsonl(events_path)
    except Exception:
        return {}
    out = gate_attribution(events, trades, max_skew=max_skew)
    if out_path:
        try:
            tmp = out_path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(out, fh)
            os.replace(tmp, out_path)
        except Exception:
            pass
    return out


def verdict_line(gate: str, g: dict) -> str:
    """One-line ENFORCE verdict for a gate. Calls out the winner-kill count and
    warns at low n. $ figures are reconstructed (pct x size)."""
    n = g["n_blocked"]
    wr = g["wr"]
    wr_s = f"{wr:.0f}%" if wr is not None else "--"
    net_usd = g["net_edge_usd"]
    net_pct = g["net_edge_pct"]
    helps = net_pct > 0
    head = "ENFORCE LOOKS +" if helps else "ENFORCE LOOKS - (would cost net P&L)"
    lown = "  [!] LOW-n (<30): provisional" if n < 30 else ""
    return (
        f"{gate}: {head}: fleet delta ~{net_pct:+.0f}pp "
        f"(reconstructed ~${net_usd:+.2f}) "
        f"-- avoided ${g['bleed_avoided_usd']:.2f} bleed, "
        f"gave up ${g['winners_given_up_usd']:.2f} over {g['winners_blocked']} "
        f"WINNERS-KILLED -- n={n}, WR_blocked={wr_s}, "
        f"unmatched={g['n_unmatched_events']}{lown}"
    )


# ──────────────────────────────────────────────────────────────────────────────
# LIVE GLUE  (not unit-tested — file/network IO; exercised by the live run)
# ──────────────────────────────────────────────────────────────────────────────

def _load_jsonl(path: str) -> List[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def _load_trades(path: str) -> List[dict]:
    with open(path) as f:
        d = json.load(f)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        for k in ("trades", "data", "results"):
            if isinstance(d.get(k), list):
                return d[k]
    return []


def run(args) -> int:
    events_path = args.events or os.path.join(
        os.environ.get("DATA_DIR", "/data"), "shadow_gate_events.jsonl")
    if not os.path.exists(events_path):
        print(f"[shadow-gate-pnl] no events file at {events_path} "
              f"(forward-only — nothing captured yet?)")
        return 0
    events = _load_jsonl(events_path)
    trades = _load_trades(args.trades)
    out = gate_attribution(events, trades, max_skew=args.max_skew)

    print(f"\nSHADOW-GATE P&L ATTRIBUTION  (events={len(events)}, "
          f"trades={len(trades)}, max_skew={args.max_skew:.0f}s)")
    print("P&L PRIMARY = pnl_pct; $ = RECONSTRUCTED from usd_received x pnl_pct "
          "(NEVER the feed `pnl` field).")
    print("=" * 88)
    if not out:
        print("(no gate events)")
        return 0
    # sort by where enforcing helps most (net_edge_pct desc)
    for gate, g in sorted(out.items(), key=lambda kv: kv[1]["net_edge_pct"],
                          reverse=True):
        med = g["median_pnl_pct"]
        med_s = f"{med:+.1f}%" if med is not None else "--"
        wr_s = f"{g['wr']:.0f}%" if g["wr"] is not None else "--"
        wkpct = g["winners_blocked_pct"]
        wkpct_s = f"{wkpct:.0f}%" if wkpct is not None else "--"
        print(
            f"\n[{gate}]\n"
            f"  n_blocked={g['n_blocked']}  unmatched_events={g['n_unmatched_events']}\n"
            f"  WINNERS-KILLED={g['winners_blocked']} "
            f"({wkpct_s} of blocked)  "
            f"losers_blocked={g['losers_blocked']}  "
            f"WR_blocked={wr_s}\n"
            f"  median_pnl_pct={med_s}  sum_pnl_pct={g['sum_pnl_pct']:+.1f}pp\n"
            f"  net_edge: {g['net_edge_pct']:+.1f}pp  |  reconstructed ${g['net_edge_usd']:+.2f}\n"
            f"  bleed_avoided=${g['bleed_avoided_usd']:.2f}  "
            f"winners_given_up=${g['winners_given_up_usd']:.2f}\n"
            f"  >> {verdict_line(gate, g)}"
        )
    print("\n" + "=" * 88)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--events", default=None,
                   help="path to shadow_gate_events.jsonl "
                        "(default $DATA_DIR/shadow_gate_events.jsonl)")
    p.add_argument("--trades", required=True,
                   help="path to a JSON dump of <railway>/api/trades?all=1 "
                        "(curl to a REPO-DIR file, rm after)")
    p.add_argument("--max-skew", type=float, default=600.0, dest="max_skew",
                   help="max seconds between event ts and trade entry (default 600)")
    return run(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
