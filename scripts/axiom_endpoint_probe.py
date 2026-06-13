"""
One-off probe for Axiom REST discovery endpoints.
Run on Railway (where auth token is cached at /data/axiom_tokens.json).

Usage: `railway run python scripts/axiom_endpoint_probe.py`
Or add as a one-shot task via `railway shell`.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import aiohttp

SERVERS = [
    "https://api2.axiom.trade",
    "https://api3.axiom.trade",
    "https://api4.axiom.trade",
    "https://api.axiom.trade",
    "https://api5.axiom.trade",
    "https://api6.axiom.trade",
    "https://api7.axiom.trade",
]

# Candidate path guesses (GET endpoints for token discovery / trending)
PATHS = [
    "/users-trending-v2?timePeriod=1h",
    "/users-trending-v3?timePeriod=1h",
    "/users-trending?timePeriod=1h",
    "/meme-trending",
    "/meme-trending-v2",
    "/meme-trending-v3",
    "/trending",
    "/trending-v2",
    "/trending-v3",
    "/pulse",
    "/pulse-v2",
    "/discover/trending",
    "/api/trending",
    "/v1/trending",
    "/v2/trending",
    "/tokens/trending",
    "/tokens",
    "/top-tokens",
    "/hot-tokens",
    "/new-pairs",
    "/pairs",
    "/feed",
    "/feed/trending",
    "/explore/trending",
    "/explore",
    "/surge",
    "/surging",
]


def load_token() -> str:
    path = Path(os.environ.get("DATA_DIR", "/data")) / "axiom_tokens.json"
    if not path.exists():
        # Fallback to env var
        return os.environ.get("AXIOM_AUTH_TOKEN", "")
    try:
        data = json.loads(path.read_text())
        return data.get("access_token") or ""
    except Exception:
        return ""


HEADERS_BASE = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://axiom.trade",
    "Referer": "https://axiom.trade/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


async def probe(session, server, path, headers):
    url = f"{server}{path}"
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            status = resp.status
            body_sample = ""
            if status == 200:
                try:
                    data = await resp.json(content_type=None)
                    if isinstance(data, list):
                        body_sample = f"list(len={len(data)})"
                    elif isinstance(data, dict):
                        body_sample = f"dict(keys={list(data.keys())[:6]})"
                except Exception:
                    body_sample = "non-json"
            return status, body_sample
    except Exception as e:
        return None, f"ERR:{type(e).__name__}"


async def main():
    token = load_token()
    print(f"Auth token: {'SET (' + str(len(token)) + ' chars)' if token else 'NOT SET'}")
    headers = {**HEADERS_BASE}
    if token:
        headers["Cookie"] = f"auth-access-token={token}"

    hits = []
    # Sequential with small delay to avoid triggering Cloudflare protection
    async with aiohttp.ClientSession() as session:
        for server in SERVERS[:3]:  # api2/3/4 only (others known broken)
            print(f"\n--- {server} ---")
            for path in PATHS:
                status, sample = await probe(session, server, path, headers)
                tag = "HIT" if status == 200 else (str(status) if status else "ERR")
                line = f"  [{tag:>5}] {path}  {sample}"
                print(line)
                if status == 200:
                    hits.append(f"{server}{path}  {sample}")
                await asyncio.sleep(0.3)

    print()
    print(f"=== SUMMARY: {len(hits)} 200s ===")
    for h in hits:
        print(h)


if __name__ == "__main__":
    asyncio.run(main())
