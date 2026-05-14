"""Use playwright with Edge (msedge channel) + user's existing profile to
load axiom.trade (already-logged-in session), navigate to TOP tab, and
capture the API URL.

NOTE: Edge must be CLOSED before running — playwright can't share an
active profile. Will warn if Edge is detected as running.
"""
import asyncio
import json
import os
import sys
import shutil
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright


EDGE_PROFILE_SRC = (
    Path(os.environ.get("USERPROFILE", "")) / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data"
)


async def main():
    if not EDGE_PROFILE_SRC.exists():
        print(f"Edge profile not found at {EDGE_PROFILE_SRC}")
        sys.exit(1)

    # Copy ONLY the essentials (cookies, login state) to a temp dir.
    # Avoids requiring Edge to be closed.
    temp_dir = Path(tempfile.mkdtemp(prefix="edge-axiom-"))
    print(f"Temp profile: {temp_dir}")
    src_default = EDGE_PROFILE_SRC / "Default"
    dst_default = temp_dir / "Default"
    dst_default.mkdir(parents=True, exist_ok=True)

    # Copy login-related files only
    for fname in ["Cookies", "Cookies-journal", "Local State", "Login Data", "Preferences"]:
        src = src_default / fname
        if src.exists():
            try:
                shutil.copy2(src, dst_default / fname)
                print(f"  copied {fname}")
            except Exception as e:
                print(f"  skip {fname}: {e}")

    # Also copy top-level Local State (needed for cookie decryption keys)
    top_local_state = EDGE_PROFILE_SRC / "Local State"
    if top_local_state.exists():
        try:
            shutil.copy2(top_local_state, temp_dir / "Local State")
            print(f"  copied top Local State")
        except Exception as e:
            print(f"  skip Local State: {e}")

    api_requests = []

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(temp_dir),
            channel="msedge",
            headless=False,
            viewport={"width": 1400, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_request(request):
            url = request.url
            if "axiom.trade" in url and "api" in url:
                api_requests.append({"method": request.method, "url": url})

        page.on("request", on_request)

        print("\nNavigating to axiom.trade/discover...")
        await page.goto("https://axiom.trade/discover?chain=sol", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(5)

        # Take screenshot to see current state
        await page.screenshot(path=".axiom_discover.png")
        print("Screenshot saved: .axiom_discover.png")

        # Look for tabs / find TOP
        await asyncio.sleep(3)

        # Try clicking various tabs
        tabs_to_try = ["Top", "TOP", "Trending", "New", "Migrating"]
        for tab_name in tabs_to_try:
            try:
                tab = page.locator(f"text=/^{tab_name}$/i").first
                if await tab.count() > 0:
                    print(f"\nClicking '{tab_name}' tab...")
                    api_requests.clear()
                    await tab.click(timeout=5000)
                    await asyncio.sleep(4)
                    print(f"  → captured {len(api_requests)} api requests:")
                    for r in api_requests[:20]:
                        print(f"    {r['method']} {r['url']}")
            except Exception as e:
                print(f"  {tab_name}: {e}")

        await asyncio.sleep(2)
        # Final screenshot
        await page.screenshot(path=".axiom_after_clicks.png")

        print("\n=== ALL API REQUESTS (entire session) ===")
        for r in api_requests:
            print(f"  {r['method']} {r['url']}")

        # Save for analysis
        with open(".axiom_api_requests.json", "w") as f:
            json.dump(api_requests, f, indent=2)

        await asyncio.sleep(2)
        await context.close()

    print("\nDone — review .axiom_api_requests.json")
    shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
