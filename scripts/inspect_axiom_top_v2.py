"""Navigate axiom.trade and click tabs to find the TOP feed URL."""
import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright


EDGE_PROFILE_SRC = (
    Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
)


async def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="edge-axiom-"))
    src_default = EDGE_PROFILE_SRC / "Default"
    dst_default = temp_dir / "Default"
    dst_default.mkdir(parents=True, exist_ok=True)

    for fname in ["Cookies", "Cookies-journal", "Local State", "Login Data", "Preferences"]:
        src = src_default / fname
        if src.exists():
            try:
                shutil.copy2(src, dst_default / fname)
            except Exception:
                pass

    top_local_state = EDGE_PROFILE_SRC / "Local State"
    if top_local_state.exists():
        try:
            shutil.copy2(top_local_state, temp_dir / "Local State")
        except Exception:
            pass

    api_requests = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(temp_dir),
            channel="msedge",
            headless=False,
            viewport={"width": 1600, "height": 1000},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(request):
            url = request.url
            if "axiom.trade" in url and ("api" in url.split("//")[1].split("/")[0]):
                api_requests.append({"method": request.method, "url": url, "phase": "?"})

        page.on("request", on_request)

        # Try directly hitting different routes that might have TOP
        for url in [
            "https://axiom.trade/discover?chain=sol",
            "https://axiom.trade/discover",
            "https://axiom.trade/pulse",
            "https://axiom.trade/top",
        ]:
            print(f"\n=== Navigating {url} ===")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(4)
            except Exception as e:
                print(f"  navigation error: {e}")
                continue
            # Take screenshot
            try:
                await page.screenshot(path=f".axiom_{url.split('/')[-1].replace('?','_')}.png")
            except Exception:
                pass
            # Snapshot text
            try:
                text = await page.evaluate(
                    "() => document.body.innerText.substring(0, 1000)"
                )
                print(f"  Body text preview: {text[:400]}")
            except Exception:
                pass

        # On the final page, find all buttons/tabs and click them
        await asyncio.sleep(2)
        print("\n=== Listing all buttons/tabs ===")
        buttons = await page.evaluate(
            """() => {
                const els = [...document.querySelectorAll('button, [role="tab"], [role="button"], a')];
                return els
                    .filter(el => el.offsetParent !== null && el.textContent.trim())
                    .map(el => ({
                        tag: el.tagName.toLowerCase(),
                        text: el.textContent.trim().substring(0, 50),
                        cls: (el.className || '').toString().substring(0, 80),
                    }))
                    .filter(el => el.text.length > 0 && el.text.length < 30)
                    .slice(0, 60);
            }"""
        )
        for b in buttons:
            print(f"  {b['tag']}: '{b['text']}'  cls={b['cls'][:50]}")

        # Try clicking anything that looks like Top/Pulse/Trending
        for target_text in ["Top", "Pulse", "Discover", "Trending", "Migrating", "New Pairs", "All", "Hot"]:
            try:
                el = page.locator(f"text=/^{target_text}$/").first
                if await el.count() > 0:
                    print(f"\n=== Clicking '{target_text}' ===")
                    before_n = len(api_requests)
                    await el.click(timeout=5000)
                    await asyncio.sleep(4)
                    after_n = len(api_requests)
                    new_reqs = api_requests[before_n:after_n]
                    print(f"  → {len(new_reqs)} new requests:")
                    seen_paths = set()
                    for r in new_reqs:
                        from urllib.parse import urlparse
                        path = urlparse(r['url']).path
                        if path not in seen_paths:
                            seen_paths.add(path)
                            url_stripped = r['url'].split('&v=')[0]
                            print(f"    {r['method']} {url_stripped}")
            except Exception as e:
                pass

        print("\n=== FINAL: all unique paths captured ===")
        seen = set()
        for r in api_requests:
            from urllib.parse import urlparse
            path = urlparse(r['url']).path
            qs = urlparse(r['url']).query.split('&v=')[0] if 'v=' in urlparse(r['url']).query else urlparse(r['url']).query
            key = f"{path}?{qs}" if qs else path
            if key not in seen:
                seen.add(key)
                print(f"  {r['method']} {key}")

        with open(".axiom_api_full.json", "w") as f:
            json.dump(api_requests, f, indent=2)

        await asyncio.sleep(2)
        await context.close()

    shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
