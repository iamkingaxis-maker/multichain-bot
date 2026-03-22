"""
Cloudflare Bypass via Playwright — In-Browser Request Mode

Makes API requests directly from inside the authenticated Playwright
browser context, bypassing Cloudflare's JS challenge entirely.

The browser navigates to io.dexscreener.com once to solve the challenge,
then all subsequent API calls go through context.request.get() which
shares the same authenticated session (cookies + TLS fingerprint).

No cookie extraction — no separate HTTP client — no 403.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Warm-up URL — any page on the domain triggers the CF challenge
_WARMUP_URL = "https://io.dexscreener.com/"

# How long to wait for the JS challenge to resolve
_CHALLENGE_TIMEOUT_S = 35

# How often to refresh the browser session (seconds)
# Playwright contexts don't expire, but we restart every 6h to be safe
_SESSION_TTL = 6 * 3600

_pw_warned = False


class CloudflareBypass:
    """Headless Chromium that solves CF challenges and makes in-browser API calls."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._context = None
        self._lock = asyncio.Lock()
        self._ready = False
        self._session_started = 0.0
        self._available: Optional[bool] = None

    # ── Public API ─────────────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Launch browser and solve the CF challenge. Returns True if ready."""
        async with self._lock:
            return await self._ensure_session()

    async def fetch(self, url: str, params: Dict[str, str] = None) -> Optional[Dict]:
        """Make an authenticated GET request from inside the browser.

        Returns parsed JSON dict, or None on failure.
        """
        if not self._ready:
            async with self._lock:
                if not await self._ensure_session():
                    return None

        # Refresh session if too old
        if time.monotonic() - self._session_started > _SESSION_TTL:
            async with self._lock:
                await self._close_session()
                if not await self._ensure_session():
                    return None

        try:
            full_url = url
            if params:
                qs = "&".join(f"{k}={v}" for k, v in params.items())
                full_url = f"{url}?{qs}"

            response = await self._context.request.get(
                full_url,
                headers={
                    "Accept": "application/json",
                    "Referer": "https://dexscreener.com/",
                    "Origin": "https://dexscreener.com",
                },
                timeout=15_000,
            )

            if response.status == 200:
                text = await response.text()
                try:
                    data = json.loads(text)
                    return data
                except json.JSONDecodeError:
                    # Got HTML (challenge page) — session needs refresh
                    logger.info("CF bypass: got HTML instead of JSON — refreshing session")
                    async with self._lock:
                        await self._close_session()
                        self._ready = False
                    return None
            else:
                logger.info(f"CF bypass fetch: HTTP {response.status} for {full_url[:80]}")
                if response.status in (403, 503):
                    async with self._lock:
                        await self._close_session()
                        self._ready = False
                return None

        except Exception as e:
            logger.info(f"CF bypass fetch error: {e}")
            return None

    async def close(self):
        """Shut down the browser."""
        async with self._lock:
            await self._close_session()

    # ── Internal ────────────────────────────────────────────────────────

    async def _ensure_session(self) -> bool:
        """Launch browser + context and solve the CF challenge. Must be called under lock."""
        if self._ready:
            return True

        try:
            await self._launch_browser()
            solved = await self._solve_challenge()
            if solved:
                self._ready = True
                self._session_started = time.monotonic()
                logger.info("CF bypass: session ready — io.dexscreener.com requests will use in-browser context")
            else:
                logger.warning("CF bypass: challenge not solved within timeout")
            return solved
        except Exception as e:
            logger.warning(f"CF bypass: session init failed: {e}")
            return False

    async def _launch_browser(self):
        """Start Playwright + Chromium if not already running."""
        if self._browser is not None:
            return

        if self._available is False:
            raise RuntimeError("Playwright not available")

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self._available = False
            global _pw_warned
            if not _pw_warned:
                logger.warning("CF bypass: playwright not installed — io.dexscreener.com disabled")
                _pw_warned = True
            raise RuntimeError("playwright not installed")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
            ],
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        self._available = True
        logger.info("CF bypass: Chromium launched")

    async def _solve_challenge(self) -> bool:
        """Navigate to io.dexscreener.com and wait for the CF challenge to pass."""
        page = await self._context.new_page()
        try:
            logger.info("CF bypass: navigating to io.dexscreener.com...")
            await page.goto(_WARMUP_URL, wait_until="domcontentloaded", timeout=30_000)

            # Wait until the page is no longer a Cloudflare challenge
            # CF challenge pages have title "Just a moment..." or similar
            deadline = time.monotonic() + _CHALLENGE_TIMEOUT_S
            while time.monotonic() < deadline:
                title = await page.title()
                url = page.url
                title_lower = title.lower()
                # Cloudflare challenge pages: "Just a moment..." or "Attention Required!"
                if ("just a moment" not in title_lower
                        and "attention required" not in title_lower
                        and "challenge" not in url.lower()):
                    logger.info(f"CF bypass: challenge passed — page title: '{title}'")
                    return True
                await asyncio.sleep(1.5)

            title = await page.title()
            logger.warning(f"CF bypass: timed out or IP-blocked — title: '{title}'")
            return False
        except Exception as e:
            logger.warning(f"CF bypass: challenge navigation error: {e}")
            return False
        finally:
            await page.close()

    async def _close_session(self):
        """Close browser context and browser."""
        self._ready = False
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


# ── Singleton ───────────────────────────────────────────────────────────────

_bypass: Optional[CloudflareBypass] = None
_bypass_lock = asyncio.Lock()


async def get_bypass() -> Optional[CloudflareBypass]:
    """Return the singleton CloudflareBypass, or None if playwright unavailable."""
    global _bypass
    if _bypass is not None:
        return _bypass
    try:
        import playwright  # noqa: F401
    except ImportError:
        global _pw_warned
        if not _pw_warned:
            logger.warning("CF bypass: playwright not installed")
            _pw_warned = True
        return None
    async with _bypass_lock:
        if _bypass is None:
            _bypass = CloudflareBypass()
    return _bypass


async def initialize_bypass() -> bool:
    """Call from main.py before scan cycles start. Returns True if ready."""
    bypass = await get_bypass()
    if bypass is None:
        return False
    return await bypass.initialize()
