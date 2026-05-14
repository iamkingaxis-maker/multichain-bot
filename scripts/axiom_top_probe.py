"""Probe Axiom endpoints via the Worker proxy (bypasses Cloudflare).

User confirmed TOP feed has 1m/5m/1h/24h filters — same shape as
users-trending-v2. So endpoint candidates use timePeriod parameter.
"""
import asyncio
import json
import os
import sys
import aiohttp


# Candidate paths that take timePeriod parameter (per user hint about TOP filters)
PATHS = [
    "/top?timePeriod=1h",
    "/top-v2?timePeriod=1h",
    "/top-v3?timePeriod=1h",
    "/top-tokens?timePeriod=1h",
    "/top-tokens-v2?timePeriod=1h",
    "/top-pairs?timePeriod=1h",
    "/top-pairs-v2?timePeriod=1h",
    "/top-meme?timePeriod=1h",
    "/top-meme-v2?timePeriod=1h",
    "/meme-top?timePeriod=1h",
    "/meme-top-v2?timePeriod=1h",
    "/top-tokens-by-volume?timePeriod=1h",
    "/top-by-volume?timePeriod=1h",
    "/top-gainers?timePeriod=1h",
    "/gainers?timePeriod=1h",
    "/leaderboard?timePeriod=1h",
    "/leaderboard-v2?timePeriod=1h",
    "/trending-top?timePeriod=1h",
    "/users-top?timePeriod=1h",
    "/users-top-v2?timePeriod=1h",
    "/tokens-top?timePeriod=1h",
    "/tokens-top-v2?timePeriod=1h",
    "/discover/top?timePeriod=1h",
    "/explore/top?timePeriod=1h",
    "/explore-top?timePeriod=1h",
    "/feed/top?timePeriod=1h",
    "/popular?timePeriod=1h",
    "/popular-v2?timePeriod=1h",
    "/hot?timePeriod=1h",
    "/hot-v2?timePeriod=1h",
    # surge / pulse variants that user might call TOP
    "/surge?timePeriod=1h",
    "/pulse?timePeriod=1h",
    "/pulse-v2?timePeriod=1h",
    "/users-pulse-v2?timePeriod=1h",
    # also try /users-trending-v2 alternative time periods to confirm worker works
    "/users-trending-v2?timePeriod=24h",
    "/users-trending-v2?timePeriod=5m",
]


async def probe_via_worker(session, path, relay_url, relay_secret, token, cf_cookie):
    """POST to worker proxy with the path; returns (status, n_results, sample)."""
    from urllib.parse import urlparse
    parsed = urlparse(relay_url)
    worker_origin = f"{parsed.scheme}://{parsed.netloc}"
    proxy_url = f"{worker_origin}/rest-proxy"

    cookie_parts = [f"auth-access-token={token}"]
    if cf_cookie:
        cookie_parts.append(f"cf_clearance={cf_cookie}")
    cookie = "; ".join(cookie_parts)

    payload = {
        "secret": relay_secret,
        "path": path,
        "cookie": cookie,
        "server": "https://api3.axiom.trade",
    }
    try:
        async with session.post(
            proxy_url, json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            status = resp.status
            if status != 200:
                return (status, 0, None)
            data = await resp.json(content_type=None)
            # Worker wraps: {status: 200, body: <axiom response>}
            inner_status = data.get("status") if isinstance(data, dict) else None
            body = data.get("body") if isinstance(data, dict) else None
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    pass
            n = 0
            sample = None
            if isinstance(body, list):
                n = len(body)
                sample = body[0] if body else None
            elif isinstance(body, dict):
                if "pairs" in body and isinstance(body["pairs"], list):
                    n = len(body["pairs"])
                    sample = body["pairs"][0] if body["pairs"] else None
                else:
                    n = -1
                    sample = body
            return (inner_status or status, n, sample)
    except Exception as e:
        return (None, 0, str(e))


async def main():
    relay_url = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
    relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()
    token = os.environ.get("AXIOM_AUTH_TOKEN", "").strip()
    cf_cookie = os.environ.get("AXIOM_CF_CLEARANCE", "").strip()

    if not relay_url or not relay_secret or not token:
        print("Missing required env vars: AXIOM_REFRESH_RELAY_URL, _SECRET, AXIOM_AUTH_TOKEN")
        sys.exit(1)

    print(f"Probing via Worker proxy → api3.axiom.trade")
    print(f"Paths to test: {len(PATHS)}")
    print(f"Token: ...{token[-20:]}")
    print()

    hits = []
    async with aiohttp.ClientSession() as session:
        for path in PATHS:
            status, n, sample = await probe_via_worker(
                session, path, relay_url, relay_secret, token, cf_cookie
            )
            marker = "++" if status == 200 and n > 0 else ("..." if status == 200 else "  ")
            print(f"  [{str(status):>5}] {path:55s} {marker} n={n}")
            if status == 200 and n > 0:
                hits.append((path, n, sample))

    print()
    print(f"=== SUMMARY: {len(hits)} hits ===")
    for path, n, sample in hits:
        print(f"\n{path}: n={n}")
        if isinstance(sample, dict):
            print(f"  keys: {list(sample.keys())[:15]}")
            print(f"  sample[:200]: {json.dumps(sample, default=str)[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
