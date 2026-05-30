"""Live Universe Dip-Recorder.

Continuously polls the Solana token universe (DexScreener boosts/profiles +
GeckoTerminal trending/top pools, paginated), detects dip events on each
token's 1m candles, and records entry-time features + scheduled forward
outcome (peak / +25min realized).

Why: rounds 4-7 mining was bottlenecked at 34 tokens × 4h history = 168
events with effective n≈4 distinct hot runners. To do proper compound
mining we need 1000+ independent dip events. This recorder gathers that
over hours/days of live operation across the broader universe (not just
bot trades).

Persistence: appends one JSON line per RESOLVED event to
.universe_recorder/events.jsonl. Resume-safe — on startup loads pending
events from .universe_recorder/pending.json and resumes outcome checks.

Usage:
    python scripts/universe_dip_recorder.py
    python scripts/universe_dip_recorder.py --cycle-s 180 --outcome-min 30

Stop with Ctrl+C — pending events flush to disk.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from feeds.dexscreener_client import DexScreenerClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("universe_recorder")

# Persistent state files. On Railway, set RECORDER_DATA_DIR=/data/universe_recorder
# so events persist across deploys via the Railway volume mount.
DATA_DIR = Path(os.environ.get("RECORDER_DATA_DIR", ".universe_recorder"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
EVENTS_FILE = DATA_DIR / "events.jsonl"
PENDING_FILE = DATA_DIR / "pending.json"
SEEN_FILE = DATA_DIR / "seen_events.txt"
# Dedicated fresh-launch (<2h) outcome sink (2026-05-30). Fresh launches are the
# one token-class the trade-history mining is blind to (aged-skewed), and the
# divergence scan showed they behave differently (pop-then-fade, exit-timing
# lever, freshness-critical). They're rare/small, so this gets a far higher cap
# than events.jsonl — it accumulates a long fresh-launch corpus across regimes
# while the main log keeps rotating. Read with scripts/fresh_launch_recorder.py.
FRESH_FILE = DATA_DIR / "fresh_launches.jsonl"
FRESH_MAX_AGE_H = 2.0
RECORDER_FRESH_MAX_MB = float(os.environ.get("RECORDER_FRESH_MAX_MB", "200"))

# Retention cap for the append-only events log. Without this the recorder grows
# the Railway volume without bound (it hit 80% on 2026-05-27, accelerated by the
# 05-27 coverage widening). When events.jsonl exceeds the cap we drop the OLDEST
# half — oldest events are the least useful for forward mining and the long-range
# historical corpus is captured separately (.mining_7hr). Env-tunable.
RECORDER_MAX_EVENTS_MB = float(os.environ.get("RECORDER_MAX_EVENTS_MB", "50"))


def rotate_events_if_needed(events_file: Path = EVENTS_FILE,
                            max_mb: float = RECORDER_MAX_EVENTS_MB) -> bool:
    """Bound events_file to max_mb by keeping only the most recent ~half when it
    exceeds the cap. Atomic (temp + os.replace) so a crash mid-rotate cannot
    corrupt the log. Returns True if it rotated. Never raises (logs and returns
    False) — a housekeeping failure must not kill the recorder loop."""
    try:
        if not events_file.exists():
            return False
        size_mb = events_file.stat().st_size / (1024 * 1024)
        if size_mb <= max_mb:
            return False
        with events_file.open("r") as f:
            lines = f.readlines()
        keep = lines[len(lines) // 2:]  # most-recent half
        tmp = events_file.with_name(events_file.name + ".tmp")
        with tmp.open("w") as f:
            f.writelines(keep)
        tmp.replace(events_file)
        logger.info(
            f"Rotated {events_file.name}: {size_mb:.1f}MB > {max_mb}MB cap — "
            f"dropped oldest {len(lines) - len(keep)} of {len(lines)} events"
        )
        return True
    except Exception as e:
        logger.error(f"{events_file.name} rotation failed (non-fatal): {e}")
        return False
logger.info(f"Data dir: {DATA_DIR.absolute()}")


# ── Universe discovery ──────────────────────────────────────────────────────

UNIVERSE_REFRESH_S = 300  # 5 min — refresh universe list


def fetch_universe() -> list[tuple[str, str]]:
    """Return list of (kind, address) tuples. kind='token' or 'pair'."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    # 1) DexScreener token endpoints
    sources = [
        "https://api.dexscreener.com/token-boosts/latest/v1",
        "https://api.dexscreener.com/token-boosts/top/v1",
        "https://api.dexscreener.com/token-profiles/latest/v1",
    ]
    for url in sources:
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            for entry in r.json() or []:
                if entry.get("chainId") != "solana":
                    continue
                addr = entry.get("tokenAddress")
                if addr and addr not in seen:
                    seen.add(addr)
                    out.append(("token", addr))
        except Exception as e:
            logger.debug(f"universe src err {url}: {e}")

    # 2) GeckoTerminal trending + top pools (paginated, slow due to rate limit)
    for endpoint in ("trending_pools", "pools"):
        for page in (1, 2, 3, 4, 5):
            try:
                r = requests.get(
                    f"https://api.geckoterminal.com/api/v2/networks/solana/{endpoint}?page={page}",
                    timeout=15, headers={"Accept": "application/json"},
                )
                for p in r.json().get("data", []) or []:
                    attrs = p.get("attributes", {})
                    pair_addr = attrs.get("address")
                    if pair_addr and pair_addr not in seen:
                        seen.add(pair_addr)
                        out.append(("pair", pair_addr))
                time.sleep(2.1)  # GT free 30/min
            except Exception as e:
                logger.debug(f"gt err {endpoint} p{page}: {e}")
                break  # don't keep hammering on error

    logger.info(f"Universe: {len(out)} unique addresses ({sum(1 for k,_ in out if k=='token')} tokens, {sum(1 for k,_ in out if k=='pair')} pairs)")
    return out


# ── Feature extraction ──────────────────────────────────────────────────────

def fetch_pair_data(addr: str, kind: str) -> Optional[dict]:
    """Fetch DexScreener pair details. Returns the pair dict or None."""
    try:
        if kind == "token":
            url = f"https://api.dexscreener.com/latest/dex/tokens/{addr}"
        else:
            url = f"https://api.dexscreener.com/latest/dex/pairs/solana/{addr}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json() or {}
        if kind == "token":
            pairs = data.get("pairs") or []
            sol = [p for p in pairs if p.get("chainId") == "solana"]
            if not sol:
                return None
            return max(sol, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        else:
            pairs = data.get("pairs") or data.get("pair") or []
            if isinstance(pairs, dict):
                return pairs
            return pairs[0] if pairs else None
    except Exception:
        return None


def pair_features(pair: dict) -> dict:
    pc = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    vol = pair.get("volume") or {}
    liq = pair.get("liquidity") or {}

    def bs_ratio(window):
        d = txns.get(window) or {}
        b, s = d.get("buys") or 0, d.get("sells") or 0
        return b / s if s else None

    return {
        "pc_m5": pc.get("m5"),
        "pc_h1": pc.get("h1"),
        "pc_h6": pc.get("h6"),
        "pc_h24": pc.get("h24"),
        "bs_m5": bs_ratio("m5"),
        "bs_h1": bs_ratio("h1"),
        "bs_h6": bs_ratio("h6"),
        "bs_h24": bs_ratio("h24"),
        "buys_h1": (txns.get("h1") or {}).get("buys"),
        "sells_h1": (txns.get("h1") or {}).get("sells"),
        "vol_m5": vol.get("m5"),
        "vol_h1": vol.get("h1"),
        "vol_h6": vol.get("h6"),
        "vol_h24": vol.get("h24"),
        "liq_usd": float(liq.get("usd") or 0),
        "fdv": pair.get("fdv") or 0,
        "mcap": pair.get("marketCap") or 0,
        "age_hours": (
            (time.time() * 1000 - (pair.get("pairCreatedAt") or 0)) / 3_600_000
            if pair.get("pairCreatedAt") else None
        ),
    }


def candle_features_at(candles: list, i: int) -> dict:
    """Features computed from the 1m candle at index i (the dip-end candle)."""
    c = candles[i]
    feats = {
        "body_pct": (c.close - c.open) / c.open * 100 if c.open > 0 else 0,
        "range_pct": (c.high - c.low) / c.low * 100 if c.low > 0 else 0,
        "vol_at_event": c.volume,
        "low_at_event": c.low,
        "high_at_event": c.high,
        "open_at_event": c.open,
        "close_at_event": c.close,
    }
    if i >= 3:
        feats["vol_prev3_avg"] = sum(x.volume for x in candles[i-3:i]) / 3
    if i >= 15:
        feats["vol_prev15_avg"] = sum(x.volume for x in candles[i-15:i]) / 15
    if i >= 6:
        feats["cum_pct_5m"] = (c.close - candles[i-6].close) / candles[i-6].close * 100 if candles[i-6].close > 0 else 0
    return feats


# ── Dip detection ───────────────────────────────────────────────────────────

DIP_PCT_THRESHOLD = -4.0
LOOKBACK_5M = 6


def find_new_dip(candles: list, last_seen_ts: int | None) -> Optional[int]:
    """Return the index of the most-recent dip-end candle, or None.

    Uses the LAST candle (most recent) — we only want to record fresh dips
    as they happen, not historical ones (those are already captured by the
    one-shot miner).
    """
    if len(candles) < LOOKBACK_5M + 1:
        return None
    i = len(candles) - 1
    if last_seen_ts is not None and candles[i].open_time <= last_seen_ts:
        return None
    prev_close = candles[i - LOOKBACK_5M].close
    cur_close = candles[i].close
    if prev_close <= 0:
        return None
    cum_pct = (cur_close - prev_close) / prev_close * 100
    if cum_pct <= DIP_PCT_THRESHOLD:
        return i
    return None


# ── State management ───────────────────────────────────────────────────────

class RecorderState:
    """In-memory state — persisted on shutdown."""

    def __init__(self):
        # pending: list of dicts with {event_id, scheduled_ts, ...}
        self.pending: list[dict] = []
        # seen_events: set of "token_addr|candle_ts" to dedupe
        self.seen: set[str] = set()
        # last_seen_ts per pair (only record fresh candle dips)
        self.last_seen_ts: dict[str, int] = {}

    def load(self):
        if PENDING_FILE.exists():
            try:
                self.pending = json.loads(PENDING_FILE.read_text())
                logger.info(f"Loaded {len(self.pending)} pending events")
            except Exception as e:
                logger.warning(f"Could not load pending: {e}")
        if SEEN_FILE.exists():
            try:
                self.seen = set(SEEN_FILE.read_text().splitlines())
                logger.info(f"Loaded {len(self.seen)} seen-event keys")
            except Exception as e:
                logger.warning(f"Could not load seen: {e}")

    def save(self):
        PENDING_FILE.write_text(json.dumps(self.pending, default=str))
        SEEN_FILE.write_text("\n".join(self.seen))

    def add_seen(self, key: str):
        self.seen.add(key)


# ── Main loop ───────────────────────────────────────────────────────────────

async def cycle_universe(client: DexScreenerClient, state: RecorderState, universe: list, outcome_min: int):
    """One pass through the universe — detect new dip events."""
    new_events = 0
    skipped = 0
    for i, (kind, addr) in enumerate(universe):
        pair = fetch_pair_data(addr, kind)
        if not pair:
            skipped += 1
            continue
        pair_addr = pair.get("pairAddress") if kind == "token" else addr
        token_addr = (pair.get("baseToken") or {}).get("address") if kind == "token" else None
        if kind == "pair":
            token_addr = (pair.get("baseToken") or {}).get("address")
        if not pair_addr:
            skipped += 1
            continue
        pf = pair_features(pair)
        # Filter by minimum liq/vol
        if (pf["liq_usd"] or 0) < 20_000 or (pf["vol_h24"] or 0) < 50_000:
            skipped += 1
            continue
        # Fetch recent 1m candles (last 30 min — fresh data)
        try:
            candles = await client.fetch_1m(pair_addr, limit=30)
        except Exception:
            skipped += 1
            continue
        if not candles or len(candles) < LOOKBACK_5M + 1:
            skipped += 1
            continue
        last_seen = state.last_seen_ts.get(pair_addr)
        dip_idx = find_new_dip(candles, last_seen)
        if dip_idx is None:
            # Update last_seen anyway so we don't keep re-checking
            state.last_seen_ts[pair_addr] = candles[-1].open_time
            continue
        # New dip detected!
        event_ts = candles[dip_idx].open_time
        event_key = f"{pair_addr}|{event_ts}"
        if event_key in state.seen:
            continue
        state.add_seen(event_key)
        state.last_seen_ts[pair_addr] = candles[-1].open_time
        # Build event record
        ev = {
            "event_id": event_key,
            "token_address": token_addr,
            "pair_address": pair_addr,
            "symbol": (pair.get("baseToken") or {}).get("symbol"),
            "detected_at_iso": datetime.now(timezone.utc).isoformat(),
            "event_ts": event_ts,
            "entry_price": candles[dip_idx].close,
            # Forward outcome scheduled at outcome_min from now
            "outcome_at_iso": (
                datetime.fromtimestamp(time.time() + outcome_min * 60, tz=timezone.utc).isoformat()
            ),
            "outcome_at_ts": int(time.time()) + outcome_min * 60,
            # Features
            **pf,
            **candle_features_at(candles, dip_idx),
        }
        state.pending.append(ev)
        new_events += 1
        logger.info(
            f"DIP {ev['symbol']:<12} pair={pair_addr[:8]} "
            f"body={ev.get('body_pct',0):+.1f}% cum5m={ev.get('cum_pct_5m',0):+.1f}% "
            f"pc_h6={pf.get('pc_h6')} liq=${pf.get('liq_usd'):.0f} "
            f"age={pf.get('age_hours',0):.1f}h"
        )
        # tiny pause to be polite
        await asyncio.sleep(0.03)
    logger.info(f"Cycle done: {new_events} new dips detected, {skipped} skipped, {len(state.pending)} pending outcomes")


async def resolve_outcomes(client: DexScreenerClient, state: RecorderState):
    """Check pending events whose outcome time has arrived."""
    now = time.time()
    still_pending = []
    resolved = 0
    for ev in state.pending:
        if now < ev.get("outcome_at_ts", 0):
            still_pending.append(ev)
            continue
        # Time to resolve — fetch latest candles
        try:
            candles = await client.fetch_1m(ev["pair_address"], limit=35)
        except Exception:
            still_pending.append(ev)
            continue
        if not candles:
            still_pending.append(ev)
            continue
        # Find candles after the event_ts
        post_event = [c for c in candles if c.open_time > ev["event_ts"]]
        if not post_event:
            # Outcome not yet available; defer
            still_pending.append(ev)
            continue
        entry = ev["entry_price"]
        peak_close = max(c.close for c in post_event)
        last_close = post_event[-1].close
        ev["peak_pct"] = (peak_close - entry) / entry * 100 if entry > 0 else 0
        ev["exit_pct"] = (last_close - entry) / entry * 100 if entry > 0 else 0
        ev["won"] = ev["exit_pct"] > 0
        ev["won_5pct"] = ev["peak_pct"] >= 5
        ev["won_10pct"] = ev["peak_pct"] >= 10
        ev["n_post_candles"] = len(post_event)
        # Append to events file
        line = json.dumps(ev, default=str) + "\n"
        with EVENTS_FILE.open("a") as f:
            f.write(line)
        # Also persist to the dedicated fresh-launch sink if it was <2h at entry,
        # so fresh launches survive the events.jsonl rotation (2026-05-30).
        _age = ev.get("age_hours")
        if isinstance(_age, (int, float)) and not isinstance(_age, bool) and _age < FRESH_MAX_AGE_H:
            try:
                with FRESH_FILE.open("a") as ff:
                    ff.write(line)
            except Exception as _e:
                logger.debug(f"fresh-launch sink write failed: {_e}")
        resolved += 1
    state.pending = still_pending
    if resolved:
        logger.info(f"Resolved {resolved} outcomes; {len(state.pending)} still pending")


async def main(args):
    state = RecorderState()
    state.load()
    client = DexScreenerClient(rate_per_min=120)

    universe: list = []
    last_universe_refresh: float = 0

    stop = False

    def handle_sigint(*_):
        nonlocal stop
        stop = True
        logger.info("Got SIGINT — will exit after this cycle")

    # Signal handlers only attach when running as the main thread (Python
    # rejects signal.signal from worker threads). When bundled inside main.py
    # as a background thread, the bot's own signal handlers will trigger
    # process exit, and the daemon flag tears us down with it.
    import threading
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handle_sigint)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, handle_sigint)

    cycle_num = 0
    try:
        while not stop:
            cycle_num += 1
            now = time.time()

            # Refresh universe periodically
            if not universe or (now - last_universe_refresh) > UNIVERSE_REFRESH_S:
                logger.info(f"[Cycle {cycle_num}] Refreshing universe...")
                universe = fetch_universe()
                last_universe_refresh = time.time()

            # Resolve any due outcomes
            await resolve_outcomes(client, state)

            # Detect new dips
            await cycle_universe(client, state, universe, args.outcome_min)

            # Persist state
            state.save()

            # Bound events.jsonl so the Railway volume can't fill (2026-05-27).
            rotate_events_if_needed()
            # Fresh-launch sink has its own (far higher) cap so it accumulates a
            # long cross-regime corpus while the main log rotates (2026-05-30).
            rotate_events_if_needed(FRESH_FILE, RECORDER_FRESH_MAX_MB)

            # Stats
            try:
                if EVENTS_FILE.exists():
                    n_resolved = sum(1 for _ in EVENTS_FILE.open())
                else:
                    n_resolved = 0
            except Exception:
                n_resolved = 0
            logger.info(f"[Cycle {cycle_num}] Total resolved events on disk: {n_resolved}")

            # Sleep until next cycle
            if stop:
                break
            await asyncio.sleep(args.cycle_s)
    finally:
        state.save()
        logger.info("Shutdown — pending state saved.")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cycle-s", type=int, default=120,
                   help="Cycle interval in seconds (default 120)")
    p.add_argument("--outcome-min", type=int, default=30,
                   help="Forward outcome window in minutes (default 30)")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
