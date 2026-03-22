import sys

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("curl_cffi not installed")
    sys.exit(1)

URL = "https://io.dexscreener.com/dex/search/v2/pairs?rankBy=trendingScoreH6&chainIds=solana&order=desc&minLiq=20000"

headers = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://dexscreener.com/",
    "Origin": "https://dexscreener.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}

profiles = [
    "chrome124",
    "chrome131",
    "chrome120",
    "chrome107",
    "chrome101",
    "safari17_0",
    "safari15_5",
]

print(f"Testing URL: {URL}\n")
print("=" * 70)

successful_profile = None

for profile in profiles:
    print(f"\nProfile: {profile}")
    print("-" * 40)
    try:
        resp = cffi_requests.get(URL, headers=headers, impersonate=profile, timeout=15)
        status = resp.status_code
        body = resp.text
        print(f"Status: {status}")
        print(f"Response (first 400 chars): {body[:400]}")

        if status == 200:
            successful_profile = profile
            print("\n*** SUCCESS: 200 OK ***")
            print("\nFull response structure:")
            try:
                import json
                data = resp.json()
                print(f"Top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data)}")
                if isinstance(data, dict):
                    for k, v in data.items():
                        if isinstance(v, list):
                            print(f"  '{k}': list of {len(v)} items")
                            if v:
                                print(f"    First item keys: {list(v[0].keys()) if isinstance(v[0], dict) else v[0]}")
                        elif isinstance(v, dict):
                            print(f"  '{k}': dict with keys {list(v.keys())}")
                        else:
                            print(f"  '{k}': {v}")
                print(f"\nFull JSON (truncated to 2000 chars):\n{json.dumps(data, indent=2)[:2000]}")
            except Exception as e:
                print(f"Could not parse JSON: {e}")
                print(f"Raw body: {body[:2000]}")

    except Exception as e:
        print(f"Error: {e}")

print("\n" + "=" * 70)
if successful_profile:
    print(f"\nBest profile: {successful_profile}")
else:
    print("\nNo profile returned 200. All profiles tested.")
