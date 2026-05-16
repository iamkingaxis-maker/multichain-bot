"""Reusable backtest harness for entry-signal mining.

Replaces the proliferation of one-off mining scripts (mine_*.py,
deep_mine_*.py, etc.) with a single DSL-driven harness.

USAGE:
  from scripts.backtest_harness import BacktestHarness, Predicate as P

  bt = BacktestHarness.from_universe("universe_fresh.json")

  compounds = {
      "late_night_fresh": [
          P.in_set("_hour_ct", {22, 23, 0, 1, 2}),
          P.lt("age_hours", 6),
          P.lt("pc_m5", -10),
      ],
      "premium_signature": [
          P.gte("avg_trade_size_h1_usd", 116),
          P.gte("liq_velocity_h1_usd_per_txn", 135),
          P.gte("p90_buy_size_usd", 153),
      ],
  }

  for label, stats in bt.run(compounds).items():
      print(f"{label}: n={stats.n} wr={stats.wr*100:.0f}% "
            f"avg_exit={stats.avg_exit*100:+.1f}%")

  # Pareto-frontier across throughput vs WR
  bt.pareto_report(compounds, out_path="pareto.json")

Output stats per compound:
  n              — events matched
  wins / losses  — counts (won_10pct == True)
  wr             — win rate (0..1)
  avg_exit       — mean exit_pct (proxy for realized P&L)
  total_pnl_pct  — sum of exit_pct (the equity-curve-like metric)
  median_exit    — robust central tendency
  hit_p20_count  — count where peak_pct >= 20% (big winner share)
  sharpe_like    — avg_exit / stdev_exit (unitless)
  max_drawdown   — running-min of cumulative exit_pct
"""
from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


# ─── Predicate DSL ─────────────────────────────────────────────────────


class Predicate:
    """Factory for common numeric predicates. Each returns a callable
    ``(event_dict) -> bool``. All comparisons fail-CLOSED on missing keys
    (returns False) — match the bot's "evidence required" stance.
    """

    @staticmethod
    def lt(key: str, threshold: float) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            v = e.get(key)
            return isinstance(v, (int, float)) and v < threshold
        return _p

    @staticmethod
    def lte(key: str, threshold: float) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            v = e.get(key)
            return isinstance(v, (int, float)) and v <= threshold
        return _p

    @staticmethod
    def gt(key: str, threshold: float) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            v = e.get(key)
            return isinstance(v, (int, float)) and v > threshold
        return _p

    @staticmethod
    def gte(key: str, threshold: float) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            v = e.get(key)
            return isinstance(v, (int, float)) and v >= threshold
        return _p

    @staticmethod
    def between(key: str, lo: float, hi: float) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            v = e.get(key)
            return isinstance(v, (int, float)) and lo <= v < hi
        return _p

    @staticmethod
    def in_set(key: str, allowed: Iterable) -> Callable[[dict], bool]:
        allowed_set = set(allowed)
        def _p(e: dict) -> bool:
            return e.get(key) in allowed_set
        return _p

    @staticmethod
    def eq(key: str, target) -> Callable[[dict], bool]:
        def _p(e: dict) -> bool:
            return e.get(key) == target
        return _p

    @staticmethod
    def true(key: str) -> Callable[[dict], bool]:
        return lambda e: e.get(key) is True

    @staticmethod
    def NOT(p: Callable[[dict], bool]) -> Callable[[dict], bool]:
        return lambda e: not p(e)

    @staticmethod
    def OR(*ps: Callable[[dict], bool]) -> Callable[[dict], bool]:
        return lambda e: any(p(e) for p in ps)

    @staticmethod
    def AND(*ps: Callable[[dict], bool]) -> Callable[[dict], bool]:
        return lambda e: all(p(e) for p in ps)


# ─── Stats container ───────────────────────────────────────────────────


@dataclass
class CompoundStats:
    label: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    wr: float = 0.0
    avg_exit: float = 0.0       # decimal (0.10 = +10%)
    total_pnl_pct: float = 0.0  # sum of exit_pct (decimal)
    median_exit: float = 0.0
    hit_p20_count: int = 0      # events with peak_pct >= 20
    sharpe_like: float = 0.0
    max_drawdown: float = 0.0   # most negative point on cumulative curve
    sample_symbols: list = field(default_factory=list)


# ─── Harness ──────────────────────────────────────────────────────────


class BacktestHarness:
    """Compounds over a list of event dicts. Stateless — load once,
    evaluate many compound bundles."""

    def __init__(self, events: list[dict]):
        self.events = events
        self._enrich_hour_ct()

    @classmethod
    def from_universe(cls, path: str) -> "BacktestHarness":
        data = json.loads(Path(path).read_text())
        if isinstance(data, dict):
            data = data.get("events") or data.get("rows") or []
        return cls(list(data))

    def _enrich_hour_ct(self) -> None:
        """Add ``_hour_ct`` (Central Time hour) if ``detected_at_iso``
        is present. Bot trades on CT-localized hour-of-day signals."""
        for e in self.events:
            iso = e.get("detected_at_iso") or e.get("detected_at")
            if not iso or "_hour_ct" in e:
                continue
            try:
                s = iso.replace("Z", "+00:00") if "Z" in iso else iso
                pdt = dt.datetime.fromisoformat(s)
                # UTC → CT (no DST handling — close enough for mining)
                ct = pdt - dt.timedelta(hours=5)
                e["_hour_ct"] = ct.hour
                e["_date_ct"] = ct.date().isoformat()
            except Exception:
                continue

    def match(self, predicates: list[Callable[[dict], bool]]) -> list[dict]:
        return [e for e in self.events
                if all(p(e) for p in predicates)]

    def stats(self, label: str,
              predicates: list[Callable[[dict], bool]]) -> CompoundStats:
        matched = self.match(predicates)
        cs = CompoundStats(label=label, n=len(matched))
        if not matched:
            return cs

        exits = [e.get("exit_pct", 0) for e in matched]
        # exit_pct in universe is in PERCENT (e.g. -10.5 means -10.5%);
        # normalize to decimal (-0.105) for cumulative math.
        exits_d = [(v / 100.0) if isinstance(v, (int, float)) else 0.0
                   for v in exits]
        peaks = [e.get("peak_pct", 0) for e in matched]

        cs.wins = sum(1 for e in matched if e.get("won_10pct"))
        cs.losses = cs.n - cs.wins
        cs.wr = cs.wins / cs.n
        cs.avg_exit = sum(exits_d) / cs.n
        cs.total_pnl_pct = sum(exits_d)
        cs.median_exit = sorted(exits_d)[cs.n // 2]
        cs.hit_p20_count = sum(1 for p in peaks
                               if isinstance(p, (int, float)) and p >= 20)

        # Sharpe-like: mean / stdev (no risk-free rate)
        if cs.n >= 2:
            mu = cs.avg_exit
            var = sum((x - mu) ** 2 for x in exits_d) / (cs.n - 1)
            sd = math.sqrt(var)
            cs.sharpe_like = (mu / sd) if sd > 0 else 0.0

        # Max drawdown on cumulative sum of decimal exits (chronological
        # if events ordered; otherwise just a stat snapshot)
        cum = 0.0
        peak_cum = 0.0
        dd = 0.0
        for x in exits_d:
            cum += x
            if cum > peak_cum:
                peak_cum = cum
            dd = min(dd, cum - peak_cum)
        cs.max_drawdown = dd

        cs.sample_symbols = [e.get("symbol", "?") for e in matched[:8]]
        return cs

    def run(self, compounds: dict[str, list[Callable]]) -> dict[str, CompoundStats]:
        return {label: self.stats(label, preds)
                for label, preds in compounds.items()}

    def pareto_report(self, compounds: dict[str, list[Callable]],
                      out_path: str | None = None) -> list[dict]:
        """Build throughput-vs-WR Pareto frontier across compounds.

        For each compound, compute (n, wr, total_pnl_pct, avg_exit). Sort
        by n descending. A point is on the frontier if no later point
        dominates it (>= wr AND >= total_pnl_pct).
        """
        results = self.run(compounds)
        rows = [
            {
                "label": r.label,
                "n": r.n,
                "wr": round(r.wr, 4),
                "total_pnl_pct": round(r.total_pnl_pct, 4),
                "avg_exit": round(r.avg_exit, 4),
                "sharpe_like": round(r.sharpe_like, 3),
                "max_drawdown": round(r.max_drawdown, 4),
            }
            for r in results.values() if r.n > 0
        ]
        rows.sort(key=lambda r: -r["n"])
        # Pareto: keep rows that aren't strictly dominated on (wr, pnl)
        on_frontier = []
        for r in rows:
            dominated = False
            for q in rows:
                if q is r:
                    continue
                if q["wr"] >= r["wr"] and q["total_pnl_pct"] >= r["total_pnl_pct"] \
                        and (q["wr"] > r["wr"] or q["total_pnl_pct"] > r["total_pnl_pct"]):
                    dominated = True
                    break
            if not dominated:
                on_frontier.append(r)

        if out_path:
            Path(out_path).write_text(json.dumps({
                "all": rows, "pareto_frontier": on_frontier,
            }, indent=2))
        return on_frontier


# ─── CLI smoke test ────────────────────────────────────────────────────


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python scripts/backtest_harness.py <universe_fresh.json>")
        sys.exit(1)

    P = Predicate
    bt = BacktestHarness.from_universe(sys.argv[1])
    print(f"loaded {len(bt.events)} events")

    compounds = {
        "late_night_fresh_dip": [
            P.in_set("_hour_ct", {22, 23, 0, 1, 2}),
            P.lt("age_hours", 6),
            P.lt("pc_m5", -10),
        ],
        "premium_signature": [
            P.gte("avg_trade_size_h1_usd", 116),
            P.gte("liq_velocity_h1_usd_per_txn", 135),
            P.gte("p90_buy_size_usd", 153),
        ],
        "deep_dip_youngmcap": [
            P.lt("pc_h24", -7.48),
            P.gte("peak_h24_6h_pct", 7.2),
        ],
    }

    pareto = bt.pareto_report(compounds, out_path="pareto_report.json")
    print(f"\nPareto frontier ({len(pareto)} compounds):")
    for r in pareto:
        print(f"  {r['label']:<28} n={r['n']:>4} wr={r['wr']*100:>4.0f}% "
              f"avg_exit={r['avg_exit']*100:+5.1f}% sharpe={r['sharpe_like']:>5.2f} "
              f"DD={r['max_drawdown']*100:>+6.1f}%")
