"""Axiom Universe Coverage Audit.

Pulls the live Axiom trending feed (multiple timeframes + tabs) and
cross-references against our universe recorder. Reports tokens Axiom
flags as trending that we DID NOT see in the universe — i.e. coverage
gaps in our scanner that may be missed entry opportunities.

Usage:
  AXIOM_AUTH_TOKEN=<token> python scripts/axiom_universe_audit.py [--limit 200] [--out gap_report.json]

OR (if running on Railway box):
  python scripts/axiom_universe_audit.py
  (loads /data/axiom_tokens.json automatically)

Output: per-tab gap report — tokens trending on Axiom but absent from our
last 24h of universe recorder events. Helps validate whether our scanner
is finding most of the active universe or systematically missing slices.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any

import aiohttp

DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://gracious-inspiration-production.up.railway.app",
)

AXIOM_SERVERS = [
    "https://api2.axiom.trade",
    "https://api3.axiom.trade",
    "https://api4.axiom.trade",
    "https://api.axiom.trade",
    "https://api5.axiom.trade",
    "https://api6.axiom.trade",
    "https://api7.axiom.trade",
]

HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://axiom.trade",
    "Referer": "https://axiom.trade/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

TABS = [
    ("top_1h",      "/users-trending-v2?timePeriod=1h"),
    ("top_5m",      "/users-trending-v2?timePeriod=5m"),
    ("top_24h",     "/users-trending-v2?timePeriod=24h"),
    ("new_1h",      "/new-trending-v2?timePeriod=1h"),
    ("new_5m",      "/new-trending-v2?timePeriod=5m"),
]


def load_token() -> str:
    """Prefer DATA_DIR/axiom_tokens.json (Railway path), fall back to env."""
    for p in (
        Path(os.environ.get("DATA_DIR", "/data")) / "axiom_tokens.json",
        Path.home() / ".axiom_tokens.json",
    ):
        if p.exists():
            try:
                data = json.loads(p.read_text())
                tok = data.get("access_token")
                if tok:
                    return tok
            except Exception:
                pass
    return os.environ.get("AXIOM_AUTH_TOKEN", "")


async def fetch_axiom_tab(session: aiohttp.ClientSession, path: str, token: str) -> list[dict]:
    cookie = f"auth-access-token={token}"
    headers = {**HEADERS_BASE, "Cookie": cookie}
    for server in AXIOM_SERVERS:
        url = f"{server}{path}"
        try:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        return data.get("pairs") or data.get("tokens") or []
                if resp.status in (401, 403):
                    print(f"[!] Axiom auth rejected ({resp.status}) at {server}")
                    return []
        except Exception as e:
            print(f"[!] {server}{path}: {e}", file=sys.stderr)
    return []


def fetch_universe_recent(limit: int = 5000) -> list[dict]:
    """Fetch recent universe-recorder events from production dashboard."""
    url = f"{DASHBOARD_URL}/api/universe-recorder?limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
        if isinstance(data, list):
            return data
        return data.get("events") or data.get("rows") or []
    except Exception as e:
        print(f"[!] universe fetch failed: {e}", file=sys.stderr)
        return []


def extract_addrs(rows: list[dict]) -> set[str]:
    """Pull token/pair address fields from heterogeneous shape."""
    out: set[str] = set()
    for r in rows:
        if not isinstance(r, dict):
            continue
        for k in ("token_address", "tokenAddress", "address",
                  "baseTokenAddress", "pair_address", "pairAddress",
                  "mint", "ca"):
            v = r.get(k)
            if isinstance(v, str) and len(v) >= 32:
                out.add(v.lower())
    return out


def extract_symbol(r: dict) -> str:
    for k in ("symbol", "tokenSymbol", "baseTokenSymbol", "name", "tokenName"):
        v = r.get(k)
        if isinstance(v, str) and v:
            return v
    return "?"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=5000,
                    help="universe events to compare against")
    ap.add_argument("--out", default=None, help="optional JSON output path")
    args = ap.parse_args()

    token = load_token()
    if not token:
        print("[!] no AXIOM_AUTH_TOKEN — aborting", file=sys.stderr)
        return 1

    print(f"Fetching {args.limit} universe events from {DASHBOARD_URL} ...")
    universe = fetch_universe_recent(args.limit)
    universe_addrs = extract_addrs(universe)
    print(f"  → {len(universe)} universe events, {len(universe_addrs)} unique tokens")

    report: dict[str, Any] = {
        "universe_events": len(universe),
        "universe_unique_tokens": len(universe_addrs),
        "tabs": {},
    }
    async with aiohttp.ClientSession() as session:
        for tab_name, path in TABS:
            rows = await fetch_axiom_tab(session, path, token)
            axiom_addrs = extract_addrs(rows)
            missing = axiom_addrs - universe_addrs
            gap_rate = len(missing) / max(len(axiom_addrs), 1)
            print(f"\n[{tab_name}] axiom={len(axiom_addrs)} missing_from_universe={len(missing)} ({gap_rate*100:.0f}%)")

            # Sample the missing tokens with their symbols + key metrics
            sample = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                addrs = set()
                for k in ("token_address", "tokenAddress", "address",
                          "baseTokenAddress", "pair_address", "pairAddress",
                          "mint", "ca"):
                    v = r.get(k)
                    if isinstance(v, str) and len(v) >= 32:
                        addrs.add(v.lower())
                if not (addrs & missing):
                    continue
                sample.append({
                    "symbol": extract_symbol(r),
                    "addr": next(iter(addrs & missing)),
                    "mcap": r.get("marketCap") or r.get("mcap"),
                    "vol_h1": r.get("volume_1h") or r.get("vol_h1"),
                    "liq": r.get("liquidity") or r.get("liq_usd"),
                    "pc_h1": r.get("priceChange_1h") or r.get("pc_h1"),
                })
                if len(sample) >= 15:
                    break

            report["tabs"][tab_name] = {
                "axiom_count": len(axiom_addrs),
                "missing_count": len(missing),
                "gap_rate": round(gap_rate, 3),
                "missing_sample": sample,
            }
            for s in sample[:10]:
                print(f"  {s['symbol']:<14} addr={s['addr'][:10]}... "
                      f"mcap={s['mcap']} vol_h1={s['vol_h1']} liq={s['liq']}")

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
        print(f"\n→ wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
