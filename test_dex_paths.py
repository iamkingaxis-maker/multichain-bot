import asyncio
from curl_cffi.requests import AsyncSession

HEADERS = {
    "Referer": "https://dexscreener.com/",
    "Accept": "application/json",
}

URLS = [
    "https://io.dexscreener.com/dex/log/recent/v2/pairs?chainIds=solana&rankBy=trendingScoreH6&order=desc",
    "https://io.dexscreener.com/dex/search/v3?rankBy=trendingScoreH6&chain=solana&order=desc",
    "https://io.dexscreener.com/dex/trending?chainIds=solana",
    "https://io.dexscreener.com/dex/pairs/trending?chainId=solana",
    "https://io.dexscreener.com/dex/log/v2/trending?chainIds=solana",
    "https://io.dexscreener.com/dex/search/v2/pairs?rankBy=trendingScoreH6&chainIds=solana&order=desc",
    "https://io.dexscreener.com/u/log/recent/pairs?chainIds=solana&rankBy=trendingScoreH6&order=desc",
    "https://io.dexscreener.com/dex/log/recent/v3/pairs?chainIds=solana&rankBy=trendingScoreH6&order=desc",
    "https://io.dexscreener.com/dex/log/recent/v1/pairs?chainIds=solana&rankBy=trendingScoreH6&order=desc",
    "https://io.dexscreener.com/dex/log/recent/v2/tokens?chainIds=solana&rankBy=trendingScoreH6&order=desc",
]

async def fetch(session, idx, url):
    try:
        r = await session.get(url, headers=HEADERS, timeout=15)
        snippet = r.text[:300].replace("\n", " ")
        print(f"\n[{idx}] Status: {r.status_code}")
        print(f"    URL: {url}")
        print(f"    Body: {snippet}")
    except Exception as e:
        print(f"\n[{idx}] ERROR: {e}")
        print(f"    URL: {url}")

async def main():
    async with AsyncSession(impersonate="chrome110") as session:
        tasks = [fetch(session, i+1, url) for i, url in enumerate(URLS)]
        await asyncio.gather(*tasks)

asyncio.run(main())
