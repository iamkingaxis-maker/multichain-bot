"""Fast-mover retro validator — tests entry triggers on tokens that
ACTUALLY moved fast, not on the bot's existing slow-mover universe.

Why: validate_trigger.py's retro uses bot-traded pairs, which is a biased
sample of the bot's CURRENT (slow-mover) universe. If we're mining for
fast-mover triggers, testing them on slow-mover data is tautological.

This validator:
  1. Loads master bar dataset (.deep_token_bars_master.json)
  2. Filters tokens to "fast-movers" — tokens with >= 1 instance of
     a +10% move within 20 1m bars (i.e. tokens that demonstrably DO
     move fast in their history)
  3. For each candidate trigger module, scans every bar:
       - If trigger.should_enter() returns True, treat as entry
       - Simulate bot lifecycle on the next 60 1m bars:
           - +8% gain → TP1 hit, return +8%
           - -12% drop first → stop, return -12%
           - Otherwise → 60-min close pct
  4. Reports per-trigger stats and per-token breakdown

Usage:
  python scripts/fast_mover_retro.py
"""
import json
import importlib.util
import sys
from collections import defaultdict


def is_fast_mover_token(bars, fast_pct=10.0, fast_window=20, min_events=1):
    """A token is a fast-mover if it had >= min_events instances of
    fast_pct% gains within fast_window 1m bars in its recorded history."""
    events = 0
    for i in range(len(bars) - fast_window):
        entry_p = bars[i]['c']
        if entry_p <= 0:
            continue
        max_gain_pct = max(
            (bars[j]['h'] / entry_p - 1) * 100
            for j in range(i + 1, i + 1 + fast_window)
        )
        if max_gain_pct >= fast_pct:
            events += 1
            if events >= min_events:
                return True
    return False


def simulate_lifecycle(bars, entry_i, tp_pct=8.0, stop_pct=12.0, max_hold=60):
    """Simulate buy at bars[entry_i].close, watch next max_hold bars.
    Returns realized %."""
    entry_p = bars[entry_i].get('c', 0)
    if entry_p <= 0:
        return None
    horizon = bars[entry_i + 1:min(len(bars), entry_i + 1 + max_hold)]
    if len(horizon) < 5:
        return None
    for b in horizon:
        h_pct = (b['h'] / entry_p - 1) * 100
        l_pct = (b['l'] / entry_p - 1) * 100
        # Use conservative simulation: assume the worse path within the bar
        if l_pct <= -stop_pct:
            return -stop_pct
        if h_pct >= tp_pct:
            return tp_pct
    last_close = horizon[-1]['c']
    return (last_close / entry_p - 1) * 100


def load_trigger(path):
    spec = importlib.util.spec_from_file_location('trigger', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    data = json.load(open('.deep_token_bars_master.json', encoding='utf-8'))
    tokens = data.get('tokens', {})
    print(f"Tokens in master: {len(tokens)}")

    # Filter to fast-mover tokens
    fast_tokens = {}
    for addr, info in tokens.items():
        bars = info.get('bars') or []
        if len(bars) < 100:
            continue
        if is_fast_mover_token(bars, fast_pct=10.0, fast_window=20, min_events=1):
            fast_tokens[addr] = info
    print(f"Fast-mover tokens (>=1 event of +10% in 20min): {len(fast_tokens)}")

    # Stricter cohort
    fastest_tokens = {}
    for addr, info in tokens.items():
        bars = info.get('bars') or []
        if len(bars) < 100:
            continue
        if is_fast_mover_token(bars, fast_pct=10.0, fast_window=20, min_events=3):
            fastest_tokens[addr] = info
    print(f"Fastest-mover tokens (>=3 events): {len(fastest_tokens)}")
    print()

    triggers = [
        'scripts/proposals/trigger_momentum_continuation.py',
        'scripts/proposals/trigger_range_expansion.py',
        'scripts/proposals/trigger_5m_vol_burst.py',
        'scripts/proposals/trigger_explosive_break.py',
    ]

    print("=" * 90)
    print("Retro on FAST-MOVER cohort (n_tokens={})".format(len(fast_tokens)))
    print("=" * 90)
    for trig_path in triggers:
        mod = load_trigger(trig_path)
        name = mod.NAME
        results = []
        per_token_stats = defaultdict(list)
        for addr, info in fast_tokens.items():
            bars = info.get('bars') or []
            for i in range(35, len(bars) - 65):
                cur = bars[i]
                recent_bars = bars[max(0, i - 60):i + 1]
                try:
                    fires = mod.should_enter(
                        cur['o'], cur['h'], cur['l'], cur['c'],
                        v=cur.get('v'), em={}, recent_bars=recent_bars,
                    )
                except Exception:
                    fires = False
                if fires:
                    pnl = simulate_lifecycle(bars, i)
                    if pnl is None:
                        continue
                    results.append(pnl)
                    per_token_stats[addr].append(pnl)
        if results:
            n = len(results)
            avg = sum(results) / n
            wins = sum(1 for r in results if r > 0)
            tp_hits = sum(1 for r in results if r >= 7.9)
            stops = sum(1 for r in results if r <= -11.9)
            print(f"\n{name}")
            print(f"  fires:  n={n}  avg={avg:+.2f}%  WR={wins/n*100:.1f}%")
            print(f"  TP1 hits: {tp_hits} ({tp_hits/n*100:.1f}%)  stops: {stops} ({stops/n*100:.1f}%)")
            print(f"  unique tokens fired on: {len(per_token_stats)}")
        else:
            print(f"\n{name}: 0 fires")

    print()
    print("=" * 90)
    print("Retro on FASTEST-MOVER cohort (n_tokens={}, >=3 events)".format(len(fastest_tokens)))
    print("=" * 90)
    for trig_path in triggers:
        mod = load_trigger(trig_path)
        name = mod.NAME
        results = []
        for addr, info in fastest_tokens.items():
            bars = info.get('bars') or []
            for i in range(35, len(bars) - 65):
                cur = bars[i]
                recent_bars = bars[max(0, i - 60):i + 1]
                try:
                    fires = mod.should_enter(
                        cur['o'], cur['h'], cur['l'], cur['c'],
                        v=cur.get('v'), em={}, recent_bars=recent_bars,
                    )
                except Exception:
                    fires = False
                if fires:
                    pnl = simulate_lifecycle(bars, i)
                    if pnl is None:
                        continue
                    results.append(pnl)
        if results:
            n = len(results)
            avg = sum(results) / n
            wins = sum(1 for r in results if r > 0)
            tp_hits = sum(1 for r in results if r >= 7.9)
            stops = sum(1 for r in results if r <= -11.9)
            print(f"\n{name}")
            print(f"  fires:  n={n}  avg={avg:+.2f}%  WR={wins/n*100:.1f}%")
            print(f"  TP1 hits: {tp_hits} ({tp_hits/n*100:.1f}%)  stops: {stops} ({stops/n*100:.1f}%)")
        else:
            print(f"\n{name}: 0 fires")


if __name__ == "__main__":
    main()
