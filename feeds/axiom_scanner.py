"""
Axiom Real-Time Scanner
Replaces DexScreener polling with Axiom's WebSocket push feed.

Instead of asking DexScreener for tokens every 10-30 seconds,
Axiom pushes new tokens to you the moment they appear on-chain.

This is a DROP-IN replacement for multi_source_scanner.py's polling loop.
DexScreener is kept as a fallback if the Axiom connection drops.

Integration into main.py:
    from feeds.axiom_scanner import AxiomScanner

    axiom_scanner = AxiomScanner(
        auth_token=config.axiom_auth_token,
        refresh_token=config.axiom_refresh_token,
        trader=sol_trader,
        signal_evaluator=signal_evaluator,
        security_checker=security,
        telegram=telegram,
        tracker=tracker,
        market_monitor=market_monitor,
        min_mcap=config.min_mcap,
        max_mcap=config.max_mcap,
        min_score=config.min_combined_score
    )
    tasks.append(axiom_scanner.run())
"""

import asyncio
import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Attempt to import axiomtradeapi — graceful fallback if not installed
try:
    from axiomtradeapi import AxiomTradeClient, AxiomAuth, AxiomTradeWebSocketClient
    # Stub exception classes — actual package doesn't define them separately
    class AuthenticationError(Exception): pass
    class NetworkError(Exception): pass
    class APIError(Exception): pass
    AXIOM_AVAILABLE = True
except ImportError:
    AXIOM_AVAILABLE = False
    logger.warning(
        "[AxiomScanner] axiomtradeapi not installed. "
        "Run: pip install axiomtradeapi\n"
        "Falling back to DexScreener polling."
    )


class AxiomTokenEvent:
    """Normalized token event from Axiom WebSocket.

    Axiom sends snake_case field names in new_pairs events:
      token_address, token_name, token_ticker, pair_address, signature, token_image
    Protocol/mcap/liquidity may be absent — we treat 0 as unknown and allow through.
    """
    def __init__(self, raw: dict):
        # Support both snake_case (WS feed) and camelCase (legacy)
        self.token_address  = (raw.get("token_address") or
                               raw.get("tokenAddress") or "")
        self.token_symbol   = (raw.get("token_ticker") or
                               raw.get("tokenTicker") or "?")
        self.token_name     = (raw.get("token_name") or
                               raw.get("tokenName") or "Unknown")
        self.pair_address   = (raw.get("pair_address") or
                               raw.get("pairAddress") or "")
        self.mcap_sol       = float(
            raw.get("marketCapSol") or raw.get("market_cap_sol") or 0
        )
        self.volume_sol     = float(
            raw.get("volumeSol") or raw.get("volume_sol") or 0
        )
        self.liquidity_sol  = float(
            raw.get("liquiditySol") or raw.get("liquidity_sol") or 0
        )
        self.protocol       = (raw.get("protocol") or
                               raw.get("dex") or "unknown")
        self.has_twitter    = bool(raw.get("twitter"))
        self.has_telegram   = bool(raw.get("telegram"))
        self.has_website    = bool(raw.get("website"))
        self.created_at     = (raw.get("createdAt") or
                               raw.get("created_at") or "")
        self.chain_id       = "solana"
        self._raw           = raw  # keep for debugging

    @property
    def mcap_usd(self) -> float:
        """Approximate USD value (SOL price fetched separately)."""
        return self.mcap_sol * 150.0  # Approximation — bot has real SOL price

    @property
    def liquidity_usd(self) -> float:
        return self.liquidity_sol * 150.0

    @property
    def has_socials(self) -> bool:
        return self.has_twitter or self.has_telegram

    # Protocols that fire AFTER indexing (graduated/established pools — have DexScreener data)
    # "Pump V1" and "Virtual Curve" are brand-new launches with zero data — skip them.
    _DATA_READY_PROTOCOLS = ("pump amm", "raydium", "meteora", "orca", "launchlab")

    def passes_basic_filters(self, min_mcap_usd: float,
                              max_mcap_usd: float,
                              min_liquidity_usd: float = 5_000) -> bool:
        """Quick pre-filter before full signal evaluation."""
        if not self.token_address:
            return False

        # Only allow protocols that have indexed data when the WS event fires.
        # Pump V1 / Virtual Curve = just born, no DexScreener data yet.
        proto_lower = self.protocol.lower()
        if not any(p in proto_lower for p in self._DATA_READY_PROTOCOLS):
            return False

        # MCap: must meet minimum (these protocols have real data, mcap won't be 0)
        if self.mcap_usd > 0 and self.mcap_usd < min_mcap_usd:
            return False

        # Liquidity gate
        if self.liquidity_usd > 0 and self.liquidity_usd < min_liquidity_usd:
            return False

        return True

    def to_dexscreener_format(self) -> dict:
        """
        Convert to a format compatible with the existing signal evaluator.
        This lets AxiomScanner feed into the same scoring pipeline
        without changing signal_evaluator.py at all.
        """
        return {
            "chainId": "solana",
            "baseToken": {
                "address": self.token_address,
                "symbol": self.token_symbol,
                "name": self.token_name
            },
            "marketCap": self.mcap_usd,
            "liquidity": {"usd": self.liquidity_usd},
            "volume": {
                "h1": self.volume_sol * 150.0,
                "h6": 0,
                "h24": 0,
                "m5": 0
            },
            "priceChange": {"m5": 0, "h1": 0, "h6": 0, "h24": 0},
            "txns": {
                "m5": {"buys": 0, "sells": 0},
                "h1": {"buys": 0, "sells": 0}
            },
            "info": {
                "socials": (
                    [{"type": "twitter", "url": ""}] if self.has_twitter else []
                ) + (
                    [{"type": "telegram", "url": ""}] if self.has_telegram else []
                )
            },
            "pairCreatedAt": None
        }


class AxiomAuthManager:
    """
    Manages Axiom authentication tokens with automatic refresh.

    The axiomtradeapi package's refresh endpoint (/refresh-access-token on
    api.axiom.trade) returns 404, and its login flow requires an OTP email code.
    So we implement our own refresh by probing all known Axiom API servers for
    the working refresh endpoint, then running a background keep-alive task.
    """

    # Every known Axiom API base URL — we probe each one at startup
    _REFRESH_CANDIDATES = [
        "https://api.axiom.trade",
        "https://api3.axiom.trade",
        "https://api6.axiom.trade",
        "https://api9.axiom.trade",
        "https://api10.axiom.trade",
        "https://api2.axiom.trade",
        "https://api4.axiom.trade",
        "https://api5.axiom.trade",
        "https://api7.axiom.trade",
        "https://api8.axiom.trade",
    ]
    # Multiple path candidates — Axiom may have changed the endpoint path
    _REFRESH_PATHS = [
        "/refresh-access-token",
        "/refresh",
        "/auth/refresh",
        "/v2/refresh-access-token",
        "/api/refresh-access-token",
        "/token/refresh",
    ]
    _REFRESH_PATH = "/refresh-access-token"  # kept for backward compat

    def __init__(self,
                 email: Optional[str] = None,
                 password: Optional[str] = None,
                 auth_token: Optional[str] = None,
                 refresh_token: Optional[str] = None):

        # Priority: constructor args → environment variables
        self.email         = email or os.environ.get("AXIOM_EMAIL", "")
        self.password      = password or os.environ.get("AXIOM_PASSWORD", "")
        self.auth_token    = auth_token or os.environ.get("AXIOM_AUTH_TOKEN", "")
        self.refresh_token = refresh_token or os.environ.get("AXIOM_REFRESH_TOKEN", "")
        # Upgrade to disk-cached tokens if they're fresher (avoids 30s gap after redeploy)
        _disk_at, _disk_rt = AxiomAuthManager.load_tokens_from_disk()
        if _disk_at and _disk_rt:
            _disk_exp = AxiomAuthManager._parse_jwt_exp(_disk_at)
            _curr_exp = AxiomAuthManager._parse_jwt_exp(self.auth_token) if self.auth_token else 0
            if _disk_exp > _curr_exp:
                self.auth_token = _disk_at
                self.refresh_token = _disk_rt

        self._client: Optional["AxiomTradeClient"] = None
        self._working_refresh_url: Optional[str] = None  # discovered at runtime

    @property
    def has_credentials(self) -> bool:
        return bool((self.email and self.password) or self.auth_token or self.refresh_token)

    @staticmethod
    def _parse_jwt_exp(token: str) -> float:
        """Decode the exp field from a JWT without verifying the signature."""
        try:
            payload_b64 = token.split('.')[1]
            payload_b64 += '=' * (4 - len(payload_b64) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload_b64))
            return float(data.get('exp', 0))
        except Exception:
            return 0.0

    def _apply_new_tokens(self, new_token: str, new_rt: str, source: str) -> bool:
        """Apply successfully-obtained tokens and log result."""
        import time as _t
        new_rt = new_rt or self.refresh_token
        self.auth_token = new_token
        self.refresh_token = new_rt
        if self._client and self._client.auth_manager.tokens:
            self._client.auth_manager._set_tokens(new_token, new_rt, save_tokens=False)
            real_exp = self._parse_jwt_exp(new_token)
            if real_exp > 0:
                # Push expires_at 24h past real expiry so the library's internal
                # retry loop never fires — our keep_alive() handles refresh via Worker
                self._client.auth_manager.tokens.expires_at = real_exp + 86400
        remaining = self._parse_jwt_exp(new_token) - _t.time()
        logger.info(f"[AxiomAuth] Token refreshed via {source} — valid for {remaining/60:.1f} min")
        # Persist to /data volume so restarts start with a valid token
        self._save_tokens_to_disk(new_token, new_rt)
        return True

    _TOKEN_FILE = os.path.join(os.environ.get("DATA_DIR", "/data"), "axiom_tokens.json")

    def _save_tokens_to_disk(self, access_token: str, refresh_token: str) -> None:
        """Write fresh tokens to /data/axiom_tokens.json for next startup."""
        import json as _j, time as _t
        try:
            with open(self._TOKEN_FILE, "w") as f:
                _j.dump({"access_token": access_token, "refresh_token": refresh_token,
                          "saved_at": _t.time()}, f)
            logger.info(f"[AxiomAuth] Token saved to disk ({self._TOKEN_FILE})")
        except Exception as e:
            logger.debug(f"[AxiomAuth] Token disk save skipped: {e}")

    @classmethod
    def load_tokens_from_disk(cls) -> tuple:
        """Load saved tokens from /data/axiom_tokens.json. Returns (access, refresh) or (None, None)."""
        import json as _j, time as _t
        token_file = os.path.join(os.environ.get("DATA_DIR", "/data"), "axiom_tokens.json")
        try:
            with open(token_file) as f:
                data = _j.load(f)
            at = data.get("access_token", "")
            rt = data.get("refresh_token", "")
            if at and rt:
                logger.info(f"[AxiomAuth] Loaded saved tokens from disk (saved {(_t.time()-data.get('saved_at',0))/60:.0f} min ago)")
                return at, rt
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"[AxiomAuth] Token disk load failed: {e}")
        return None, None

    def _try_refresh_via_curl_cffi(self) -> bool:
        """
        Attempt refresh using curl_cffi which impersonates Chrome's TLS fingerprint.
        Cloudflare bot protection checks both IP reputation AND TLS fingerprint.
        If the block is fingerprint-based (not pure IP), this bypasses it.
        """
        try:
            from curl_cffi import requests as cffi_requests
        except ImportError:
            return False

        cookie_header = (
            f"auth-refresh-token={self.refresh_token}; "
            f"auth-access-token={self.auth_token or ''}"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Length": "0",
            "Origin": "https://axiom.trade",
            "Referer": "https://axiom.trade/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "Cookie": cookie_header,
        }

        if self._working_refresh_url:
            # Already found a working URL — extract base and use only that
            for _p in self._REFRESH_PATHS:
                if self._working_refresh_url.endswith(_p):
                    _working_base = self._working_refresh_url[:-len(_p)]
                    break
            else:
                _working_base = self._working_refresh_url.rsplit("/", 1)[0]
            base_urls = [_working_base]
            paths_to_try = [self._working_refresh_url[len(_working_base):]]
        else:
            base_urls = list(self._REFRESH_CANDIDATES)
            paths_to_try = self._REFRESH_PATHS

        for base in base_urls:
            for path in paths_to_try:
                url = f"{base}{path}"
                try:
                    resp = cffi_requests.post(
                        url, headers=headers, data=b"",
                        impersonate="chrome110", timeout=10
                    )
                    status = resp.status_code
                    new_token = None
                    new_rt = None
                    for sc in resp.headers.get_list("set-cookie") if hasattr(resp.headers, 'get_list') else []:
                        if "auth-access-token=" in sc and new_token is None:
                            new_token = sc.split("auth-access-token=")[1].split(";")[0].strip()
                        if "auth-refresh-token=" in sc and new_rt is None:
                            new_rt = sc.split("auth-refresh-token=")[1].split(";")[0].strip()
                    # curl_cffi may expose cookies via resp.cookies
                    if not new_token:
                        new_token = resp.cookies.get("auth-access-token")
                        new_rt = new_rt or resp.cookies.get("auth-refresh-token")
                    if new_token:
                        logger.info(
                            f"[AxiomAuth] curl_cffi found working path: {path} on {base}"
                        )
                        self._working_refresh_url = url
                        return self._apply_new_tokens(new_token, new_rt, f"curl_cffi:{base}")
                    elif status not in (404, 405):
                        # Non-404 responses (200 without token, 401, 403) are still interesting
                        logger.debug(
                            f"[AxiomAuth] curl_cffi {base}{path} -> {status} (no token)"
                        )
                except Exception as e:
                    logger.debug(f"[AxiomAuth] curl_cffi probe {base}{path}: {e}")
                    continue

        return False

    def _try_refresh_via_proxy(self) -> bool:
        """
        Refresh via a residential HTTP proxy.
        Routes the refresh call through a proxy with a residential IP so Axiom
        doesn't see Railway's datacenter IP.

        Configure via env var:
          AXIOM_PROXY_URL — e.g. http://user:pass@p.webshare.io:80

        Works with any HTTP proxy that provides residential IPs (Webshare, Bright Data, etc.)
        """
        proxy_url = os.environ.get("AXIOM_PROXY_URL", "").strip()
        if not proxy_url:
            return False

        cookie_header = (
            f"auth-refresh-token={self.refresh_token}; "
            f"auth-access-token={self.auth_token or ''}"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Length": "0",
            "Origin": "https://axiom.trade",
            "Referer": "https://axiom.trade/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header,
        }

        base_urls = (
            [self._working_refresh_url.rsplit("/", 1)[0]]
            if self._working_refresh_url else list(self._REFRESH_CANDIDATES)
        )
        paths = (
            [self._working_refresh_url[len(self._working_refresh_url.rsplit("/", 1)[0]):]]
            if self._working_refresh_url else self._REFRESH_PATHS
        )

        # Strategy A: curl_cffi with proxy (Chrome fingerprint + residential IP)
        try:
            from curl_cffi import requests as cffi_requests
            for base in base_urls:
                for path in paths:
                    url = f"{base}{path}"
                    try:
                        resp = cffi_requests.post(
                            url, headers=headers, data=b"",
                            impersonate="chrome110", timeout=15,
                            proxies={"http": proxy_url, "https": proxy_url},
                        )
                        new_token = resp.cookies.get("auth-access-token")
                        new_rt = resp.cookies.get("auth-refresh-token")
                        if not new_token:
                            for sc in (resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []):
                                if "auth-access-token=" in sc and not new_token:
                                    new_token = sc.split("auth-access-token=")[1].split(";")[0].strip()
                                if "auth-refresh-token=" in sc and not new_rt:
                                    new_rt = sc.split("auth-refresh-token=")[1].split(";")[0].strip()
                        if new_token:
                            logger.info(f"[AxiomAuth] Proxy+curl_cffi success: {base}{path}")
                            self._working_refresh_url = url
                            return self._apply_new_tokens(new_token, new_rt, "proxy+curl_cffi")
                    except Exception as e:
                        logger.debug(f"[AxiomAuth] proxy+curl_cffi {url}: {e}")
        except ImportError:
            pass

        # Strategy B: urllib3 with proxy
        try:
            import urllib3
            proxy = urllib3.ProxyManager(proxy_url, timeout=urllib3.Timeout(connect=10, read=15))
            for base in base_urls:
                for path in paths:
                    url = f"{base}{path}"
                    try:
                        resp = proxy.request("POST", url, headers=headers, body=b"")
                        set_cookies = resp.headers.getlist("Set-Cookie")
                        new_token, new_rt = None, None
                        for sc in set_cookies:
                            if "auth-access-token=" in sc and not new_token:
                                new_token = sc.split("auth-access-token=")[1].split(";")[0].strip()
                            if "auth-refresh-token=" in sc and not new_rt:
                                new_rt = sc.split("auth-refresh-token=")[1].split(";")[0].strip()
                        if new_token:
                            logger.info(f"[AxiomAuth] Proxy+urllib3 success: {base}{path}")
                            self._working_refresh_url = url
                            return self._apply_new_tokens(new_token, new_rt, "proxy+urllib3")
                    except Exception as e:
                        logger.debug(f"[AxiomAuth] proxy+urllib3 {url}: {e}")
        except Exception as e:
            logger.debug(f"[AxiomAuth] proxy+urllib3 setup failed: {e}")

        logger.warning("[AxiomAuth] Proxy refresh failed — check AXIOM_PROXY_URL")
        return False

    def _try_refresh_via_worker(self) -> bool:
        """
        Refresh via a Cloudflare Worker relay.
        The Worker calls Axiom from Cloudflare's own network (trusted by Axiom's Cloudflare setup).
        Configure via env vars:
          AXIOM_REFRESH_RELAY_URL    — e.g. https://axiom-refresh.yourname.workers.dev/refresh
          AXIOM_REFRESH_RELAY_SECRET — must match REFRESH_SECRET in the Worker
        """
        import urllib3 as _u3

        relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
        relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()

        if not relay_url or not relay_secret:
            return False

        import json as _json
        payload = _json.dumps({
            "secret":        relay_secret,
            "access_token":  self.auth_token or "",
            "refresh_token": self.refresh_token,
        }).encode()

        http = _u3.PoolManager(timeout=_u3.Timeout(connect=5, read=15))
        try:
            resp = http.request(
                "POST", relay_url,
                body=payload,
                headers={"Content-Type": "application/json"},
            )
            data = _json.loads(resp.data)
            if data.get("ok") and data.get("access_token"):
                logger.info(
                    f"[AxiomAuth] Worker relay success via {data.get('endpoint', relay_url)}"
                )
                return self._apply_new_tokens(
                    data["access_token"],
                    data.get("refresh_token"),
                    "CloudflareWorker",
                )
            else:
                logger.info(f"[AxiomAuth] Worker relay returned: {data}")
        except Exception as e:
            logger.info(f"[AxiomAuth] Worker relay error: {e}")

        return False

    def _try_refresh_sync(self) -> bool:
        """
        Synchronous token refresh. Tries four strategies in order:
        1. Residential proxy (AXIOM_PROXY_URL) — residential IP bypasses Axiom's datacenter block
        2. Cloudflare Worker relay — proxies through Cloudflare's network
        3. curl_cffi Chrome impersonation — bypasses TLS fingerprint checks
        4. urllib3 direct probe — works on residential IPs, blocked on Railway datacenter IPs
        """
        import time as _t
        import urllib3

        if not self.refresh_token:
            logger.warning("[AxiomAuth] No refresh_token available — cannot auto-refresh")
            return False

        # Strategy 1: Residential proxy (best for Railway — bypasses datacenter IP block)
        if self._try_refresh_via_proxy():
            return True

        # Strategy 2: Cloudflare Worker relay
        if self._try_refresh_via_worker():
            return True

        # Strategy 3: curl_cffi Chrome TLS impersonation
        if self._try_refresh_via_curl_cffi():
            return True

        # Strategy 4: urllib3 direct probe (works on residential IP, blocked on Railway)
        if self._working_refresh_url:
            for _p in self._REFRESH_PATHS:
                if self._working_refresh_url.endswith(_p):
                    _working_base = self._working_refresh_url[:-len(_p)]
                    break
            else:
                _working_base = self._working_refresh_url.rsplit("/", 1)[0]
            base_urls_3 = [_working_base]
            paths_3 = [self._working_refresh_url[len(_working_base):]]
        else:
            base_urls_3 = list(self._REFRESH_CANDIDATES)
            paths_3 = self._REFRESH_PATHS

        cookie_header = (
            f"auth-refresh-token={self.refresh_token}; "
            f"auth-access-token={self.auth_token or ''}"
        )
        req_headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Length": "0",
            "Origin": "https://axiom.trade",
            "Referer": "https://axiom.trade/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header,
        }

        http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=10))

        for base in base_urls_3:
            for path in paths_3:
                url = f"{base}{path}"
                try:
                    resp = http.request("POST", url, headers=req_headers, body=b"")
                    status = resp.status

                    set_cookies = resp.headers.getlist("Set-Cookie")
                    new_token = None
                    new_rt = None
                    for sc in set_cookies:
                        if "auth-access-token=" in sc and new_token is None:
                            new_token = sc.split("auth-access-token=")[1].split(";")[0].strip()
                        if "auth-refresh-token=" in sc and new_rt is None:
                            new_rt = sc.split("auth-refresh-token=")[1].split(";")[0].strip()

                    if new_token:
                        logger.info(f"[AxiomAuth] urllib3 found working path: {path} on {base}")
                        self._working_refresh_url = url
                        return self._apply_new_tokens(new_token, new_rt, base)
                    elif status not in (404, 405):
                        logger.info(
                            f"[AxiomAuth] Probe {base}{path} -> {status} | "
                            f"set-cookies={len(set_cookies)}"
                        )

                except Exception as e:
                    logger.debug(f"[AxiomAuth] Probe exception {base}{path}: {e}")
                    continue

        logger.warning("[AxiomAuth] Refresh failed on all endpoints — trying fresh login...")

        # Strategy 4: full email+OTP login flow (only when refresh fails)
        # Reads OTP automatically from Gmail via IMAP
        return self._try_fresh_login()

    def _try_fresh_login(self) -> bool:
        """
        Get fresh tokens by doing a full Axiom login with email+password+OTP.
        Reads the OTP automatically from Gmail using IMAP.

        Requires:
          AXIOM_EMAIL    — your Axiom account email
          AXIOM_PASSWORD — your Axiom account password
          GMAIL_APP_PASSWORD — Gmail App Password for IMAP access
                              (generate at myaccount.google.com/apppasswords)
                              Falls back to AXIOM_PASSWORD if not set.
        """
        import hashlib, base64 as _b64, time as _t
        try:
            from axiomtradeapi.tools.email_otp import EmailOTPHandler
        except ImportError:
            logger.debug("[AxiomAuth] EmailOTPHandler not available")
            return False

        email_addr = self.email or os.environ.get("AXIOM_EMAIL", "")
        axiom_pass = self.password or os.environ.get("AXIOM_PASSWORD", "")
        gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "") or axiom_pass

        if not email_addr or not axiom_pass:
            logger.debug("[AxiomAuth] No email/password for fresh login")
            return False

        if not gmail_pass:
            logger.debug("[AxiomAuth] No Gmail app password for OTP reading")
            return False

        logger.info(f"[AxiomAuth] Attempting fresh login for {email_addr} via email OTP...")

        try:
            import urllib3 as _u3, json as _json
            # Hash password using the library's PBKDF2 method
            SALT = bytes([
                217, 3, 161, 123, 53, 200, 206, 36, 143, 2, 220, 252, 240, 109, 204, 23,
                217, 174, 79, 158, 18, 76, 149, 117, 73, 40, 207, 77, 34, 194, 196, 163
            ])
            derived_key = hashlib.pbkdf2_hmac('sha256', axiom_pass.encode('utf-8'), SALT, 600_000, dklen=32)
            b64_password = _b64.b64encode(derived_key).decode('ascii')

            http = _u3.PoolManager(timeout=_u3.Timeout(connect=10, read=30))

            relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
            relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()

            if not relay_url or not relay_secret:
                logger.warning("[AxiomAuth] No Worker relay configured — fresh login requires AXIOM_REFRESH_RELAY_URL + AXIOM_REFRESH_RELAY_SECRET")
                return False

            # Derive Worker login URLs from relay URL base
            relay_base = relay_url.rstrip("/").removesuffix("/refresh") if relay_url.endswith("/refresh") else relay_url.rstrip("/")
            step1_url  = f"{relay_base}/login/step1"
            step2_url  = f"{relay_base}/login/step2"

            relay_headers = {"Content-Type": "application/json"}

            # Step 1: Worker forwards email+password to Axiom → Axiom emails OTP
            step1_body = _json.dumps({
                "secret":       relay_secret,
                "email":        email_addr,
                "b64_password": b64_password,
            }).encode()
            resp1 = http.request("POST", step1_url, body=step1_body, headers=relay_headers)
            data1 = _json.loads(resp1.data)
            if not data1.get("ok"):
                logger.warning(f"[AxiomAuth] Login step1 via Worker failed: {data1}")
                return False

            otp_jwt = data1.get("otp_jwt") or ""
            logger.info(f"[AxiomAuth] OTP email sent via Worker (endpoint: {data1.get('endpoint')}) — reading from Gmail IMAP...")

            # Step 2: read OTP from Gmail (IMAP works fine from Railway)
            otp_handler = EmailOTPHandler(
                email_address=email_addr,
                email_password=gmail_pass,
                imap_server="imap.gmail.com",
                timeout=60.0,
            )
            otp_code = otp_handler.get_otp()
            if not otp_code:
                logger.warning("[AxiomAuth] Could not read OTP from Gmail — check GMAIL_APP_PASSWORD")
                return False

            logger.info("[AxiomAuth] OTP received — completing login via Worker...")

            # Step 3: Worker forwards OTP to Axiom → returns fresh tokens
            step2_body = _json.dumps({
                "secret":       relay_secret,
                "email":        email_addr,
                "otp":          otp_code,
                "otp_jwt":      otp_jwt,
                "b64_password": b64_password,
            }).encode()
            resp2 = http.request("POST", step2_url, body=step2_body, headers=relay_headers)
            data2 = _json.loads(resp2.data)

            if data2.get("ok") and data2.get("access_token"):
                new_token = data2["access_token"]
                new_rt    = data2.get("refresh_token") or self.refresh_token
                logger.info(f"[AxiomAuth] Fresh login successful via Worker (endpoint: {data2.get('endpoint')})!")
                return self._apply_new_tokens(new_token, new_rt, "email-otp-login-worker")
            else:
                logger.warning(f"[AxiomAuth] Login step2 via Worker failed: {data2}")
                return False

        except Exception as e:
            logger.warning(f"[AxiomAuth] Fresh login error: {e}")
            return False

    async def refresh(self) -> bool:
        """Async wrapper for token refresh."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._try_refresh_sync)

    async def keep_alive(self):
        """
        Background task: refresh the token 2 minutes before it expires.
        Runs forever. Backs off exponentially on repeated failures so it
        does not spam logs when the Axiom refresh endpoint is unreachable.
        """
        import time as _t
        _fail_count = 0
        _check_interval = 30  # seconds between checks
        while True:
            await asyncio.sleep(_check_interval)
            if not self.auth_token:
                continue
            exp = self._parse_jwt_exp(self.auth_token)
            if exp == 0:
                continue
            ttl = exp - _t.time()
            if ttl < 120:  # refresh when < 2 min remaining
                logger.info(
                    f"[AxiomAuth] Token {'expired' if ttl < 0 else f'expiring in {ttl:.0f}s'} — "
                    f"refreshing (attempt {_fail_count + 1})..."
                )
                ok = await self.refresh()
                if ok:
                    _fail_count = 0
                    _check_interval = 30  # reset to normal check frequency
                else:
                    _fail_count += 1
                    # Exponential backoff: 2min, 4min, 8min … cap at 30min
                    _check_interval = min(120 * (2 ** (_fail_count - 1)), 1800)
                    if _fail_count == 1:
                        logger.warning(
                            "[AxiomAuth] Refresh failed — token may be expired or "
                            "Axiom changed their refresh endpoint path. "
                            "Retrying with longer backoff."
                        )
                    elif _fail_count >= 5:
                        logger.error(
                            "[AxiomAuth] Token refresh failed 5+ times. "
                            "Paste fresh AXIOM_AUTH_TOKEN + AXIOM_REFRESH_TOKEN from "
                            "your browser into Railway Variables to restore real-time feed."
                        )

    def get_client(self) -> Optional["AxiomTradeClient"]:
        """Get or create an authenticated AxiomTradeClient with correct expiry."""
        if not AXIOM_AVAILABLE:
            return None
        if not self._client:
            self._client = AxiomTradeClient(
                username=self.email or None,
                password=self.password or None,
                auth_token=self.auth_token or None,
                refresh_token=self.refresh_token or None,
            )
            # Push expires_at 24h past real expiry so the library's internal
            # retry loop never fires — our keep_alive() handles refresh via Worker
            if self.auth_token and self._client.auth_manager.tokens:
                import time as _t
                real_exp = self._parse_jwt_exp(self.auth_token)
                if real_exp > 0:
                    self._client.auth_manager.tokens.expires_at = real_exp + 86400
                    remaining = real_exp - _t.time()
                    logger.info(f"[AxiomAuth] JWT valid for {remaining/60:.1f} min — keep-alive active")
        return self._client

    def authenticated_get_sync(self, url: str) -> Optional[dict]:
        """
        Make an authenticated GET request to an Axiom API endpoint,
        bypassing Cloudflare with curl_cffi TLS impersonation.

        Returns parsed JSON dict on success, None on failure.
        Falls back to urllib3 if curl_cffi is unavailable.
        """
        cookie_header = (
            f"auth-access-token={self.auth_token or ''}; "
            f"auth-refresh-token={self.refresh_token or ''}"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://axiom.trade",
            "Referer": "https://axiom.trade/",
            "Cookie": cookie_header,
        }

        # Try curl_cffi first (bypasses TLS fingerprinting)
        try:
            from curl_cffi import requests as cffi_requests
            resp = cffi_requests.get(
                url, headers=headers,
                impersonate="chrome110", timeout=10
            )
            if resp.status_code == 200:
                return resp.json()
            logger.info(f"[AxiomAuth] curl_cffi GET {url} → {resp.status_code} | body={resp.text[:200]}")
        except ImportError:
            logger.info("[AxiomAuth] curl_cffi not available for GET request")
        except Exception as e:
            logger.info(f"[AxiomAuth] curl_cffi GET error: {e}")

        # Fallback: Cloudflare Worker relay (if configured)
        relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
        relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()
        if relay_url and relay_secret:
            import json as _json, urllib3 as _u3
            payload = _json.dumps({
                "secret": relay_secret,
                "method": "GET",
                "url": url,
                "access_token": self.auth_token or "",
                "refresh_token": self.refresh_token or "",
            }).encode()
            http = _u3.PoolManager(timeout=_u3.Timeout(connect=5, read=15))
            try:
                resp = http.request(
                    "POST", relay_url,
                    body=payload,
                    headers={"Content-Type": "application/json"},
                )
                data = _json.loads(resp.data)
                if data.get("ok") and data.get("body"):
                    return data["body"]
            except Exception as e:
                logger.debug(f"[AxiomAuth] Worker relay GET error: {e}")

        # Final fallback: direct urllib3 (works on residential IP, blocked on Railway)
        import urllib3, json as _json
        http = urllib3.PoolManager(timeout=urllib3.Timeout(connect=5, read=10))
        try:
            resp = http.request("GET", url, headers=headers)
            if resp.status == 200:
                return _json.loads(resp.data)
        except Exception as e:
            logger.debug(f"[AxiomAuth] urllib3 GET error: {e}")

        return None

    async def ensure_valid_token(self) -> bool:
        """
        Ensure we have a non-expired access token before attempting WebSocket connections.
        Returns True only if the token is valid (or was successfully refreshed).
        This prevents the library from attempting connections with expired tokens,
        which causes rapid 401 spam from the library's internal retry loop.
        """
        import time as _t
        if not AXIOM_AVAILABLE:
            return False
        if not self.has_credentials:
            logger.error(
                "[AxiomAuth] No credentials available. "
                "Set AXIOM_AUTH_TOKEN + AXIOM_REFRESH_TOKEN in Railway Variables."
            )
            return False
        if not self.auth_token:
            return False
        exp = self._parse_jwt_exp(self.auth_token)
        if exp > 0:
            ttl = exp - _t.time()
            if ttl < 30:  # token expired or about to expire
                logger.info(
                    f"[AxiomAuth] Token {'expired' if ttl < 0 else f'expiring in {ttl:.0f}s'} — "
                    "refreshing before WebSocket connect..."
                )
                ok = await self.refresh()
                if not ok:
                    logger.warning(
                        "[AxiomAuth] Token expired and refresh failed. "
                        "Paste fresh AXIOM_AUTH_TOKEN + AXIOM_REFRESH_TOKEN from "
                        "your browser into Railway Variables to restore Axiom feed."
                    )
                    return False  # Don't attempt WS — prevents library 401 spam
        return True


class AxiomScanner:
    """
    Real-time token scanner using Axiom's WebSocket feed.

    Replaces the DexScreener polling loop with push-based token discovery.
    New tokens arrive the moment they appear on Raydium/Orca/Pump.fun —
    no waiting for the next poll cycle.

    Architecture:
      Axiom WebSocket → token filter → signal evaluator → security check
      → trader.buy() (same pipeline as DexScreener scanner)

    Falls back to DexScreener polling if Axiom connection is unavailable.
    """

    def __init__(self,
                 auth_manager: AxiomAuthManager,
                 trader,
                 signal_evaluator,
                 security_checker,
                 telegram,
                 tracker,
                 market_monitor=None,

                 # Token filters
                 min_mcap_usd: float = 200_000,
                 max_mcap_usd: float = 1_000_000,
                 min_liquidity_usd: float = 50_000,
                 min_score: float = 65.0,

                 # Behavior
                 reconnect_delay_seconds: int = 10,
                 fallback_to_dexscreener: bool = True):

        self.auth          = auth_manager
        self.trader        = trader
        self.evaluator     = signal_evaluator
        self.security      = security_checker
        self.telegram      = telegram
        self.tracker       = tracker
        self.market_monitor = market_monitor

        self.min_mcap      = min_mcap_usd
        self.max_mcap      = max_mcap_usd
        self.min_liquidity = min_liquidity_usd
        self.min_score     = min_score
        self.reconnect_delay = reconnect_delay_seconds
        self.fallback      = fallback_to_dexscreener

        # Raw Axiom API response log (first N tokens for debugging)
        self._axiom_api_logged = 0

        # State
        self._client: Optional[AxiomTradeClient] = None
        self._seen_tokens: set = set()
        self._running = False

        # Stats
        self.tokens_received    = 0
        self.tokens_passed_filter = 0
        self.tokens_evaluated   = 0
        self.signals_fired      = 0
        self.reconnect_count    = 0

    async def run(self):
        """
        Main scanner loop.
        Connects to Axiom WebSocket and processes real-time token feed.
        Falls back to DexScreener polling if connection fails.
        """
        if not AXIOM_AVAILABLE:
            logger.warning(
                "[AxiomScanner] axiomtradeapi not available — "
                "run: pip install axiomtradeapi"
            )
            if self.fallback:
                await self._run_dexscreener_fallback()
            return

        if not self.auth.has_credentials:
            logger.warning(
                "[AxiomScanner] No Axiom credentials configured. "
                "Set AXIOM_EMAIL and AXIOM_PASSWORD in Railway Variables. "
                "Falling back to DexScreener polling."
            )
            if self.fallback:
                await self._run_dexscreener_fallback()
            return

        self._running = True
        logger.info(
            "[AxiomScanner] Starting real-time token feed | "
            f"MCap: ${self.min_mcap/1000:.0f}k-${self.max_mcap/1000:.0f}k | "
            f"Min score: {self.min_score}"
        )

        # Start background token keep-alive (refreshes 2 min before expiry)
        asyncio.ensure_future(self.auth.keep_alive())

        _auth_failures = 0
        _max_auth_failures = 3  # allow a few failures before falling back
        _backoff = self.reconnect_delay

        while self._running:
            try:
                await self._connect_and_stream()
                _auth_failures = 0  # reset on success
                _backoff = self.reconnect_delay
            except AuthenticationError as e:
                _auth_failures += 1
                if _auth_failures >= _max_auth_failures:
                    logger.error(
                        f"[AxiomScanner] Auth failed {_auth_failures} times — "
                        f"check AXIOM_AUTH_TOKEN/AXIOM_REFRESH_TOKEN. Falling back to DexScreener."
                    )
                    if self.fallback:
                        await self._run_dexscreener_fallback()
                    return
                logger.warning(f"[AxiomScanner] Auth failed ({_auth_failures}/{_max_auth_failures}): {e} — retrying in 60s")
                await asyncio.sleep(60)
            except Exception as e:
                self.reconnect_count += 1
                logger.warning(f"[AxiomScanner] Disconnected — reconnecting in {_backoff}s: {e}")
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, 300)  # exponential backoff, cap at 5min

    async def _connect_and_stream(self):
        """Establish connection and stream tokens until disconnected."""
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise AuthenticationError("Could not obtain valid token")

        client = self.auth.get_client()
        if not client:
            raise AuthenticationError("Could not create AxiomTradeClient")

        # Do NOT call client.login() — it uses the broken login-password-v2 endpoint.
        # The WebSocket client calls auth_manager.ensure_valid_authentication() before
        # connecting, which uses the WORKING refresh endpoint:
        #   https://api.axiom.trade/refresh-access-token
        # As long as expires_at is set correctly (done in get_client), refresh is
        # triggered automatically whenever the token is within 15 min of expiry.
        ws = client.get_websocket_client()

        logger.info("[AxiomScanner] Connected to Axiom WebSocket feed")
        await self.telegram.send(
            "🔌 *Axiom Scanner Connected*\n"
            "Real-time token feed active — no more polling delays"
        )

        # Block until WebSocket disconnects using an event
        _disconnect_event = asyncio.Event()

        _batch_count = 0

        async def _on_token_batch(raw_tokens):
            nonlocal _batch_count
            _batch_count += 1
            if _batch_count <= 3:
                logger.info(
                    f"[AxiomScanner] WS batch #{_batch_count} — "
                    f"keys: {list(raw_tokens[0].keys()) if raw_tokens else '[]'} | "
                    f"sample: {str(raw_tokens[0])[:200] if raw_tokens else ''}"
                )
            await self._handle_token_batch(raw_tokens)

        await ws.subscribe_new_tokens(_on_token_batch)

        # Heartbeat task — logs every 60s
        async def _heartbeat():
            while True:
                await asyncio.sleep(60)
                logger.info(
                    f"[AxiomScanner] Heartbeat — "
                    f"recv={self.tokens_received} | "
                    f"filtered={self.tokens_passed_filter} | "
                    f"evaluated={self.tokens_evaluated} | "
                    f"signals={self.signals_fired}"
                )

        asyncio.ensure_future(_heartbeat())

        try:
            await ws.start()
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("auth", "login", "password", "404", "token")):
                raise AuthenticationError(f"WebSocket auth failed: {e}")
            raise

        # ws.start() returned — connection closed cleanly, raise to trigger reconnect
        raise NetworkError("WebSocket connection closed")

    async def _handle_token_batch(self, raw_tokens: list):
        """Process a batch of tokens from Axiom WebSocket."""
        for raw in raw_tokens:
            await self._process_token(raw)

    async def _process_token(self, raw: dict):
        """Filter and immediately evaluate using Axiom API data."""
        try:
            event = AxiomTokenEvent(raw)
            self.tokens_received += 1

            # Skip if already seen
            if event.token_address in self._seen_tokens:
                return
            self._seen_tokens.add(event.token_address)

            # Keep seen set bounded
            if len(self._seen_tokens) > 10_000:
                self._seen_tokens = set(list(self._seen_tokens)[-5_000:])

            # Basic filter — quick and cheap
            if not event.passes_basic_filters(
                self.min_mcap, self.max_mcap, self.min_liquidity
            ):
                logger.debug(
                    f"[AxiomScanner] Filter drop: "
                    f"{event.token_symbol} | protocol={event.protocol}"
                )
                return

            self.tokens_passed_filter += 1
            logger.info(
                f"[AxiomScanner] Evaluating: {event.token_symbol} | "
                f"protocol={event.protocol} | "
                f"passed={self.tokens_passed_filter}/{self.tokens_received}"
            )

            await self._evaluate_and_trade(event)

        except Exception as e:
            logger.error(f"[AxiomScanner] Token intake error: {e}")

    async def _evaluate_and_trade(self, event: "AxiomTokenEvent"):
        """
        Evaluate immediately — only runs for graduated/established protocols
        that already have DexScreener data at WS event time.
        """
        try:
            # Market condition gate
            if self.market_monitor and self.market_monitor.market_restricted:
                if not self.market_monitor.should_trade(signal_score=0):
                    return

            # Security gate
            if self.security:
                sec_result = await self.security.check(
                    event.token_address, "solana"
                )
                if sec_result and not sec_result.passed:
                    logger.info(
                        f"[AxiomScanner] Security blocked: "
                        f"{event.token_symbol} — {sec_result.risk_level}"
                    )
                    return

            # Enrichment check — holder concentration + dev history
            deployer_address = event._raw.get("deployer_address", "") or \
                               event._raw.get("deployer", "") or ""
            if event.pair_address:
                enrich_passed, enrich_reason = await axiom_enrich_check(
                    self.auth, event.pair_address, deployer_address
                )
                if not enrich_passed:
                    logger.info(
                        f"[AxiomScanner] Enrich blocked: "
                        f"{event.token_symbol} — {enrich_reason}"
                    )
                    return

            # Fetch DexScreener data — should be available for graduated protocols
            pair_data = await self._fetch_dexscreener_pair(event)
            if pair_data is None:
                logger.debug(
                    f"[AxiomScanner] No DexScreener data for {event.token_symbol} — skipping"
                )
                return

            # Hard MCap check using real DexScreener data (Axiom WebSocket tokens often lack marketCap)
            actual_mcap = float(pair_data.get("marketCap") or 0)
            if actual_mcap > 0 and actual_mcap < self.min_mcap:
                logger.debug(
                    f"[AxiomScanner] MCap filter drop (real): {event.token_symbol} — ${actual_mcap:,.0f}"
                )
                return

            # Full signal evaluation with real mcap/volume/liquidity
            self.tokens_evaluated += 1

            if self.evaluator:
                evaluation = await self.evaluator.evaluate(pair_data)
                if evaluation.hard_skip:
                    logger.debug(
                        f"[AxiomScanner] Hard skip: {event.token_symbol} — "
                        f"{', '.join(evaluation.skip_reasons)}"
                    )
                    return
                score = evaluation.total_score
                effective_min = self.min_score
                if self.market_monitor and self.market_monitor.market_restricted:
                    effective_min = self.market_monitor.restricted_threshold
                if score < effective_min:
                    return
            else:
                if not event.has_twitter and not event.has_telegram:
                    return
                score = 70

            # Signal fires
            self.signals_fired += 1
            mcap   = pair_data.get("marketCap") or 0
            liq    = (pair_data.get("liquidity") or {}).get("usd") or 0
            vol_h1 = (pair_data.get("volume") or {}).get("h1") or 0
            logger.info(
                f"[AxiomScanner] 🚀 SIGNAL: {event.token_symbol} | "
                f"MCap: ${mcap:,.0f} | Score: {score:.0f} | "
                f"Protocol: {event.protocol}"
            )

            await self.telegram.send(
                f"🚀 *Axiom Signal* [Solana]\n\n"
                f"🪙 ${event.token_symbol} — {event.token_name}\n"
                f"📊 MCap: ${mcap:,.0f}\n"
                f"💧 Liquidity: ${liq:,.0f}\n"
                f"📈 Volume 1h: ${vol_h1:,.0f}\n"
                f"⭐ Score: {score:.0f}/100\n"
                f"🔗 Protocol: {event.protocol}\n"
                f"⚡ Axiom — graduated pool"
            )

            await self.trader.buy(
                token_address=event.token_address,
                token_symbol=event.token_symbol,
                reason=f"Axiom signal | score {score:.0f} | {event.protocol}",
                signal_score=int(score),
                hh_hl_confirmed=getattr(evaluation, "hh_hl_confirmed", False)
                if self.evaluator else False
            )

        except Exception as e:
            logger.error(f"[AxiomScanner] Evaluate/trade error for "
                         f"{event.token_symbol}: {e}")

    async def _fetch_axiom_data(self, event: "AxiomTokenEvent") -> Optional[dict]:
        """
        Fetch token metrics from Axiom API and convert to DexScreener format
        so the existing signal evaluator can score it without changes.

        Uses get_token_info_by_pair (api10) which returns rich data for the
        specific pair address we received from the WebSocket.
        Falls back to get_token_info (api6) by token address.
        """
        loop = asyncio.get_event_loop()
        raw_info = None

        # Try pair-specific endpoint first (more accurate for new tokens)
        if event.pair_address and AXIOM_AVAILABLE:
            try:
                client = self.auth.get_client()
                if client:
                    raw_info = await loop.run_in_executor(
                        None, client.get_token_info_by_pair, event.pair_address
                    )
            except Exception as e:
                logger.info(f"[AxiomScanner] get_token_info_by_pair failed "
                            f"for {event.token_symbol}: {e}")

        # Fallback to token-address endpoint
        if not raw_info and AXIOM_AVAILABLE:
            try:
                client = self.auth.get_client()
                if client:
                    raw_info = await loop.run_in_executor(
                        None, client.get_token_info, event.token_address
                    )
            except Exception as e:
                logger.info(f"[AxiomScanner] get_token_info failed "
                            f"for {event.token_symbol}: {e}")

        if not raw_info:
            # Try DexScreener as final fallback (token should be indexed by now)
            return await self._fetch_dexscreener_pair(event)

    async def _fetch_dexscreener_pair(self, event: "AxiomTokenEvent") -> Optional[dict]:
        """Fetch DexScreener pair data. Returns DexScreener pair dict or None."""
        import aiohttp
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{event.token_address}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    data = await resp.json(content_type=None)
                    pairs = [
                        p for p in (data.get("pairs") or [])
                        if p.get("chainId") == "solana"
                    ]
                    if not pairs:
                        return None
                    return max(pairs, key=lambda p: (
                        p.get("liquidity", {}).get("usd") or 0
                    ))
        except Exception as e:
            logger.debug(f"[AxiomScanner] DexScreener fallback failed for "
                         f"{event.token_address[:8]}: {e}")
            return None

    async def _run_dexscreener_fallback(self):
        """
        Fallback polling mode using DexScreener.
        Used when Axiom is unavailable.
        Logs a warning so you know it's running in fallback mode.
        """
        import aiohttp
        import socket

        logger.warning(
            "[AxiomScanner] Running in DexScreener fallback mode. "
            "Add AXIOM_EMAIL and AXIOM_PASSWORD to Railway Variables "
            "to enable real-time feed."
        )

        await self.telegram.send(
            "⚠️ *Axiom Scanner — Fallback Mode*\n"
            "Running on DexScreener polling (10s interval).\n"
            "Add AXIOM_EMAIL + AXIOM_PASSWORD to Railway Variables "
            "to enable real-time feed."
        )

        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        while True:
            try:
                async with aiohttp.ClientSession(connector=connector) as session:
                    url = (
                        "https://api.dexscreener.com/latest/dex/search"
                        "?q=solana&order=trending"
                    )
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=8)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            pairs = [
                                p for p in data.get("pairs", [])
                                if p.get("chainId") == "solana"
                            ]
                            for pair in pairs[:20]:
                                token_addr = pair.get(
                                    "baseToken", {}
                                ).get("address", "")
                                if token_addr and token_addr not in self._seen_tokens:
                                    self._seen_tokens.add(token_addr)
                                    raw = {
                                        "tokenAddress": token_addr,
                                        "tokenTicker": pair.get(
                                            "baseToken", {}
                                        ).get("symbol", "?"),
                                        "tokenName": pair.get(
                                            "baseToken", {}
                                        ).get("name", ""),
                                        "marketCapSol": (
                                            pair.get("marketCap", 0) or 0
                                        ) / 150,
                                        "liquiditySol": (
                                            pair.get("liquidity", {}).get("usd", 0)
                                            or 0
                                        ) / 150,
                                        "volumeSol": (
                                            pair.get("volume", {}).get("h1", 0)
                                            or 0
                                        ) / 150,
                                        "protocol": "raydium",
                                        "twitter": any(
                                            s.get("type") == "twitter"
                                            for s in pair.get(
                                                "info", {}
                                            ).get("socials", [])
                                        ),
                                        "telegram": any(
                                            s.get("type") == "telegram"
                                            for s in pair.get(
                                                "info", {}
                                            ).get("socials", [])
                                        ),
                                    }
                                    await self._process_token(raw)
            except Exception as e:
                logger.debug(f"[AxiomScanner] Fallback error: {e}")

            await asyncio.sleep(10)

    def get_stats(self) -> dict:
        return {
            "scanner": "axiom_realtime"
            if (AXIOM_AVAILABLE and self.auth.has_credentials)
            else "dexscreener_fallback",
            "tokens_received":      self.tokens_received,
            "tokens_passed_filter": self.tokens_passed_filter,
            "tokens_evaluated":     self.tokens_evaluated,
            "signals_fired":        self.signals_fired,
            "reconnect_count":      self.reconnect_count,
            "seen_tokens":          len(self._seen_tokens)
        }


# ─── Phase 3: Holder & Dev Enrichment ────────────────────────────────────────

async def axiom_enrich_check(auth_manager,
                              pair_address: str,
                              deployer_address: str = "") -> tuple:
    """
    Enrichment check using Axiom's holder and dev APIs.
    Returns (passed: bool, reason: str).

    Fails open on any API error — never blocks a trade due to enrichment failure.

    Checks:
      1. Holder concentration — top holder > 20% → reject
      2. Dev history — serial rugger (3+ dead tokens from same dev) → reject
    """
    loop = asyncio.get_event_loop()

    # ── 1. Holder concentration ───────────────────────────────────────────────
    if pair_address and AXIOM_AVAILABLE:
        try:
            client = auth_manager.get_client()
            if client:
                holder_data = await loop.run_in_executor(
                    None, client.get_holder_data, pair_address
                )
                # Response may be a list of holder dicts, or a dict with a list
                holders = None
                if isinstance(holder_data, list):
                    holders = holder_data
                elif isinstance(holder_data, dict):
                    holders = (
                        holder_data.get("holders") or
                        holder_data.get("data") or
                        holder_data.get("topHolders") or []
                    )

                if holders:
                    # Find the top holder percentage
                    top_pct = 0.0
                    for h in holders:
                        # Field names vary — try common ones
                        pct = float(
                            h.get("percentage") or
                            h.get("pct") or
                            h.get("holdingPercent") or
                            h.get("percentageHeld") or 0
                        )
                        if pct > top_pct:
                            top_pct = pct

                    # Some APIs return 0–1 range, others 0–100
                    if 0 < top_pct <= 1.0:
                        top_pct *= 100  # normalize to percentage

                    if top_pct > 20.0:
                        return (
                            False,
                            f"Top holder concentration too high ({top_pct:.1f}%)"
                        )
                # Secondary: tracked wallet presence (only_tracked_wallets=True)
                try:
                    tracked_holders = await loop.run_in_executor(
                        None, client.get_holder_data, pair_address, True
                    )
                    tracked_list = []
                    if isinstance(tracked_holders, list):
                        tracked_list = tracked_holders
                    elif isinstance(tracked_holders, dict):
                        tracked_list = (
                            tracked_holders.get("holders") or
                            tracked_holders.get("data") or []
                        )
                    tracked_count = len(tracked_list)
                    if tracked_count >= 3:
                        logger.debug(
                            f"[AxiomEnrich] {pair_address[:8]}: "
                            f"{tracked_count} tracked wallets holding — positive signal"
                        )
                except Exception as e:
                    logger.debug(f"[AxiomEnrich] Tracked holder check failed: {e}")
        except Exception as e:
            logger.debug(f"[AxiomEnrich] Holder check failed for {pair_address[:8]}: {e}")
            # Fail open — don't block on API errors

    # ── 2. Dev token history ──────────────────────────────────────────────────
    if deployer_address and AXIOM_AVAILABLE:
        try:
            client = auth_manager.get_client()
            if client:
                dev_data = await loop.run_in_executor(
                    None, client.get_dev_tokens, deployer_address
                )
                # Response may be list of tokens, or dict with list
                dev_tokens = None
                if isinstance(dev_data, list):
                    dev_tokens = dev_data
                elif isinstance(dev_data, dict):
                    dev_tokens = (
                        dev_data.get("tokens") or
                        dev_data.get("data") or []
                    )

                if dev_tokens:
                    import time as _time
                    now_ms = _time.time() * 1000
                    thirty_days_ms = 30 * 86400 * 1000

                    dead_count = 0
                    recent_launches = 0
                    lifetimes_days = []

                    for t in dev_tokens:
                        liq = float(t.get("liquidity") or t.get("liquidityUsd") or t.get("liquidity_usd") or 0)
                        vol = float(t.get("volume24h") or t.get("volume_24h") or t.get("volumeUsd") or 0)
                        created_at = t.get("createdAt") or t.get("created_at") or 0

                        if liq == 0 and vol == 0:
                            dead_count += 1

                        if created_at and (now_ms - float(created_at)) < thirty_days_ms:
                            recent_launches += 1

                        if liq == 0 and vol == 0 and created_at:
                            age_days = (now_ms - float(created_at)) / (86400 * 1000)
                            lifetimes_days.append(min(age_days, 90))

                    if dead_count >= 3:
                        return (False, f"Serial rugger — dev has {dead_count} dead tokens")

                    if recent_launches >= 10:
                        return (False, f"High-frequency deployer — {recent_launches} tokens in 30 days")

                    if len(lifetimes_days) >= 3:
                        avg_lifetime = sum(lifetimes_days) / len(lifetimes_days)
                        if avg_lifetime < 2.0:
                            return (False, f"Rug pattern — avg token lifetime {avg_lifetime:.1f} days")
        except Exception as e:
            logger.debug(
                f"[AxiomEnrich] Dev check failed for {deployer_address[:8]}: {e}"
            )
            # Fail open — don't block on API errors

    return (True, "")
