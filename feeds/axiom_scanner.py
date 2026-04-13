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
        self.chain_id           = "solana"
        self._raw               = raw  # keep for debugging
        # Fresh-pair fields — present in Axiom WS new_pairs events
        self.snipers_hold_pct   = float(raw.get("snipers_hold_percent") or 0)
        self.dev_holds_pct      = float(raw.get("dev_holds_percent") or 0)
        # lp_burned is a percentage (0–100); treat > 0 as burned
        self.lp_burned          = float(raw.get("lp_burned") or 0) > 0
        # mint/freeze authority — None means revoked (safe); any address means active (risky)
        self.mint_authority     = raw.get("mint_authority")   # None = revoked
        self.freeze_authority   = raw.get("freeze_authority") # None = revoked

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
    # "pump swap" / "pumpswap" = PumpSwap, pump.fun's native AMM DEX (launched Mar 2025),
    # the new graduation destination replacing Raydium for most pump.fun tokens.
    _DATA_READY_PROTOCOLS = ("pump amm", "pump swap", "pumpswap", "raydium", "meteora", "orca", "launchlab")

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
            "pairCreatedAt": None,
            "_axiom_ws_fallback": True,  # signals that real DexScreener data wasn't available yet
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

        Tries two strategies in order:
          1. Direct login via proxy+curl_cffi (residential IP + Chrome fingerprint)
          2. Worker relay fallback (if AXIOM_REFRESH_RELAY_URL is configured)

        Requires:
          AXIOM_EMAIL    — your Axiom account email
          AXIOM_PASSWORD — your Axiom account password
          GMAIL_APP_PASSWORD — Gmail App Password for IMAP access
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

        logger.info(f"[AxiomAuth] Attempting fresh login for {email_addr}...")

        # Hash password using Axiom's PBKDF2 method
        SALT = bytes([
            217, 3, 161, 123, 53, 200, 206, 36, 143, 2, 220, 252, 240, 109, 204, 23,
            217, 174, 79, 158, 18, 76, 149, 117, 73, 40, 207, 77, 34, 194, 196, 163
        ])
        derived_key = hashlib.pbkdf2_hmac('sha256', axiom_pass.encode('utf-8'), SALT, 600_000, dklen=32)
        b64_password = _b64.b64encode(derived_key).decode('ascii')

        _login_base_urls = [
            "https://api6.axiom.trade",
            "https://api.axiom.trade",
            "https://api3.axiom.trade",
        ]
        _login_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://axiom.trade",
            "referer": "https://axiom.trade/",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
        }

        # ── Strategy 1: proxy + curl_cffi (residential IP + Chrome TLS fingerprint) ──
        proxy_url = os.environ.get("AXIOM_PROXY_URL", "").strip()
        try:
            from curl_cffi import requests as cffi_requests

            proxies = {"https": proxy_url, "http": proxy_url} if proxy_url else None

            otp_jwt = None
            login_base_used = None

            for base in _login_base_urls:
                try:
                    step1_headers = dict(_login_headers)
                    step1_headers["Cookie"] = "auth-otp-login-token="
                    resp1 = cffi_requests.post(
                        f"{base}/login-password-v2",
                        headers=step1_headers,
                        json={"email": email_addr, "b64Password": b64_password},
                        impersonate="chrome110",
                        proxies=proxies,
                        timeout=15,
                    )
                    if resp1.status_code == 200:
                        otp_jwt = resp1.cookies.get("auth-otp-login-token")
                        if otp_jwt:
                            login_base_used = base
                            logger.info(f"[AxiomAuth] Login step1 OK via curl_cffi ({base}) — reading OTP from Gmail...")
                            break
                    logger.info(f"[AxiomAuth] Login step1 curl_cffi {base} → {resp1.status_code} (no otp_jwt)")
                except Exception as e:
                    logger.info(f"[AxiomAuth] Login step1 curl_cffi {base} error: {e}")
                    continue

            if otp_jwt and login_base_used:
                otp_handler = EmailOTPHandler(
                    email_address=email_addr,
                    email_password=gmail_pass,
                    imap_server="imap.gmail.com",
                    timeout=60.0,
                )
                otp_code = otp_handler.get_otp()
                if not otp_code:
                    logger.warning("[AxiomAuth] Could not read OTP from Gmail — check GMAIL_APP_PASSWORD")
                else:
                    try:
                        step2_headers = dict(_login_headers)
                        step2_headers["Cookie"] = f"auth-otp-login-token={otp_jwt}"
                        resp2 = cffi_requests.post(
                            f"{login_base_used}/login-otp",
                            headers=step2_headers,
                            json={"email": email_addr, "code": otp_code},
                            impersonate="chrome110",
                            proxies=proxies,
                            timeout=15,
                        )
                        if resp2.status_code == 200:
                            new_token = resp2.cookies.get("auth-access-token")
                            new_rt    = resp2.cookies.get("auth-refresh-token") or self.refresh_token
                            if new_token:
                                logger.info("[AxiomAuth] Fresh login successful via curl_cffi!")
                                return self._apply_new_tokens(new_token, new_rt, "email-otp-login-curlffi")
                        logger.info(f"[AxiomAuth] Login step2 curl_cffi → {resp2.status_code}")
                    except Exception as e:
                        logger.info(f"[AxiomAuth] Login step2 curl_cffi error: {e}")
        except ImportError:
            logger.debug("[AxiomAuth] curl_cffi not available for direct login")
        except Exception as e:
            logger.warning(f"[AxiomAuth] Direct login error: {e}")

        # ── Strategy 2: Worker relay fallback ─────────────────────────────────────
        try:
            import urllib3 as _u3, json as _json

            relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "").strip()
            relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "").strip()

            if not relay_url or not relay_secret:
                logger.warning("[AxiomAuth] No Worker relay — fresh login failed")
                return False

            relay_base = relay_url.rstrip("/").removesuffix("/refresh") if relay_url.endswith("/refresh") else relay_url.rstrip("/")
            step1_url  = f"{relay_base}/login/step1"
            step2_url  = f"{relay_base}/login/step2"
            relay_headers = {"Content-Type": "application/json"}

            http = _u3.PoolManager(timeout=_u3.Timeout(connect=10, read=30))

            step1_body = _json.dumps({
                "secret": relay_secret, "email": email_addr, "b64_password": b64_password,
            }).encode()
            resp1 = http.request("POST", step1_url, body=step1_body, headers=relay_headers)
            data1 = _json.loads(resp1.data)
            if not data1.get("ok"):
                logger.warning(f"[AxiomAuth] Login step1 via Worker failed: {data1}")
                return False

            otp_jwt = data1.get("otp_jwt") or ""
            logger.info(f"[AxiomAuth] OTP email sent via Worker — reading from Gmail IMAP...")

            otp_handler = EmailOTPHandler(
                email_address=email_addr, email_password=gmail_pass,
                imap_server="imap.gmail.com", timeout=60.0,
            )
            otp_code = otp_handler.get_otp()
            if not otp_code:
                logger.warning("[AxiomAuth] Could not read OTP from Gmail")
                return False

            step2_body = _json.dumps({
                "secret": relay_secret, "email": email_addr,
                "otp": otp_code, "otp_jwt": otp_jwt, "b64_password": b64_password,
            }).encode()
            resp2 = http.request("POST", step2_url, body=step2_body, headers=relay_headers)
            data2 = _json.loads(resp2.data)

            if data2.get("ok") and data2.get("access_token"):
                new_token = data2["access_token"]
                new_rt    = data2.get("refresh_token") or self.refresh_token
                logger.info(f"[AxiomAuth] Fresh login successful via Worker!")
                return self._apply_new_tokens(new_token, new_rt, "email-otp-login-worker")
            else:
                logger.warning(f"[AxiomAuth] Login step2 via Worker failed: {data2}")
                return False

        except Exception as e:
            logger.warning(f"[AxiomAuth] Worker login error: {e}")
            return False

    async def refresh(self) -> bool:
        """Async wrapper for token refresh."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._try_refresh_sync)

    async def full_relogin(self) -> bool:
        """
        Force a full email+OTP re-login, bypassing the proxy/JWT refresh.
        Used when the JWT is technically valid but the WebSocket session has
        expired server-side (manifests as persistent HTTP 404 on WS connect
        despite successful proxy token refresh).
        """
        logger.info("[AxiomAuth] Forcing full re-login via Worker relay (WS session expired)...")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._try_fresh_login)

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
                    if ttl < 0:
                        # Token already expired — this is an active outage.
                        # Retry every 60s regardless of fail count; do not back off.
                        _check_interval = 60
                        logger.warning(
                            f"[AxiomAuth] Token EXPIRED and refresh failing "
                            f"(attempt {_fail_count}) — retrying in 60s"
                        )
                    else:
                        # Token not yet expired — exponential backoff: 2min, 4min, 8min … cap at 10min
                        _check_interval = min(120 * (2 ** (_fail_count - 1)), 600)
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
      → scanner.process_external_signal() → chart analysis → trader.buy()

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

                 # Micro-cap mode
                 micro_cap_enabled: bool = False,
                 micro_cap_min_usd: float = 10_000,
                 micro_cap_max_usd: float = 50_000,
                 micro_cap_position_usd: float = 80.0,
                 micro_cap_max_snipers_pct: float = 30.0,
                 micro_cap_max_dev_pct: float = 15.0,

                 # Behavior
                 reconnect_delay_seconds: int = 10,
                 fallback_to_dexscreener: bool = True,

                 # Optional dip-watcher (intercepts micro-cap buys)
                 dip_watcher=None):

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

        self.micro_cap_enabled        = micro_cap_enabled
        self.micro_cap_min            = micro_cap_min_usd
        self.micro_cap_max            = micro_cap_max_usd
        self.micro_cap_position_usd   = micro_cap_position_usd
        self.micro_cap_max_snipers    = micro_cap_max_snipers_pct
        self.micro_cap_max_dev        = micro_cap_max_dev_pct

        # Micro-cap candidates seen but not bought — for dashboard recommendations
        from collections import deque as _deque
        import datetime as _dt
        self._dt = _dt
        self.mc_candidates: _deque = _deque(maxlen=40)

        # Optional dip-watcher — intercepts micro-cap buys to wait for dip+recovery
        self.dip_watcher = dip_watcher

        # Set by connect_to_bot() — routes buys through chart analysis gate
        self.scanner = None

        # Set externally to share spike data from AxiomPriceFeed
        self.price_feed = None

        # Raw Axiom API response log (first N tokens for debugging)
        self._axiom_api_logged = 0

        # State
        self._client: Optional[AxiomTradeClient] = None
        self._seen_tokens: dict = {}
        self._running = False
        self._heartbeat_task = None

        # Stats
        self.tokens_received    = 0
        self.tokens_passed_filter = 0
        self.tokens_evaluated   = 0
        self.signals_fired      = 0
        self.reconnect_count    = 0
        self._ws_down_since: Optional[float] = None   # set when scanner drops, cleared on recovery
        self._ws_down_alerted: float = 0.0            # timestamp of last "still down" alert

        # Deferred retry queue: token_address → (retry_at_monotonic, queued_at_monotonic, event)
        # Tokens with no DexScreener data at event time are queued here and retried 60s later.
        # Security + enrich checks were already passed before a token is queued here.
        # Hard cap: 300 entries — oldest evicted on overflow to prevent OOM during mania periods.
        self._deferred: dict = {}
        self._DEFERRED_MAX = 300

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

        if os.environ.get("AXIOM_RELAY_MODE", "").lower() in ("true", "1"):
            logger.info(
                "[AxiomScanner] Relay mode active — "
                "inbound tokens via /api/axiom-relay | DexScreener fallback running"
            )
            self._running = True
            asyncio.ensure_future(self.auth.keep_alive())
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
        _ws_404_count = 0       # consecutive WS 404s despite valid JWT → session expired
        _backoff = self.reconnect_delay

        while self._running:
            try:
                await self._connect_and_stream()
                _auth_failures = 0  # reset on success
                _ws_404_count = 0
                _backoff = self.reconnect_delay
            except AuthenticationError as e:
                _auth_failures += 1
                if _auth_failures >= _max_auth_failures:
                    logger.error(
                        f"[AxiomScanner] Auth failed {_auth_failures} times — "
                        f"check AXIOM_AUTH_TOKEN/AXIOM_REFRESH_TOKEN. Falling back to DexScreener."
                    )
                    await self._alert_scanner_down(f"Auth failed {_auth_failures}× — falling back to DexScreener polling. Check credentials.")
                    if self.fallback:
                        await self._run_dexscreener_fallback()
                    return
                logger.warning(f"[AxiomScanner] Auth failed ({_auth_failures}/{_max_auth_failures}): {e} — retrying in 60s")
                await self._alert_scanner_down(str(e))
                await asyncio.sleep(60)
            except Exception as e:
                self.reconnect_count += 1
                err_str = str(e).lower()
                # Detect persistent WS 404 — JWT is valid but server session has expired.
                # Proxy refresh renews the JWT but not the session; only full re-login fixes it.
                if "404" in err_str or "websocket connection closed" in err_str:
                    _ws_404_count += 1
                    if _ws_404_count >= 3:
                        logger.warning(
                            f"[AxiomScanner] {_ws_404_count} consecutive WS 404s — "
                            "session expired server-side. Triggering full re-login..."
                        )
                        relogin_ok = await self.auth.full_relogin()
                        if relogin_ok:
                            logger.info("[AxiomScanner] Re-login succeeded — reconnecting immediately")
                            _ws_404_count = 0
                            _backoff = self.reconnect_delay
                            continue
                        else:
                            logger.warning("[AxiomScanner] Re-login failed — will retry next cycle")
                            _ws_404_count = 0  # reset to avoid hammering login
                else:
                    _ws_404_count = 0
                logger.warning(f"[AxiomScanner] Disconnected — reconnecting in {_backoff}s: {e}")
                await self._alert_scanner_down(str(e))
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, 300)  # exponential backoff, cap at 5min

    async def _connect_and_stream(self):
        """Establish connection and stream tokens until disconnected."""
        token_valid = await self.auth.ensure_valid_token()
        if not token_valid:
            raise AuthenticationError("Could not obtain valid token")

        logger.info("[AxiomScanner] Connecting to Axiom WebSocket feed")
        if self._ws_down_since is not None:
            import time as _time
            down_mins = int((_time.time() - self._ws_down_since) / 60)
            self._ws_down_since = None
            self._ws_down_alerted = 0.0
            await self.telegram.send(
                f"✅ *Axiom Scanner Recovered*\n"
                f"Was down for {down_mins} min — real-time feed restored"
            )
        else:
            await self.telegram.send(
                "🔌 *Axiom Scanner Connected*\n"
                "Real-time token feed active — no more polling delays"
            )

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

        # Heartbeat task — logs every 60s and raises if recv is frozen 5+ min
        _hb_last_recv = self.tokens_received
        _hb_frozen_ticks = 0
        _HB_STALE_TICKS = 5  # 5 × 60s = 5 min with no new tokens → force reconnect

        async def _heartbeat():
            nonlocal _hb_last_recv, _hb_frozen_ticks
            while True:
                await asyncio.sleep(60)
                logger.info(
                    f"[AxiomScanner] Heartbeat — "
                    f"recv={self.tokens_received} | "
                    f"filtered={self.tokens_passed_filter} | "
                    f"evaluated={self.tokens_evaluated} | "
                    f"signals={self.signals_fired} | "
                    f"deferred={len(self._deferred)}"
                )
                if self.tokens_received == _hb_last_recv:
                    _hb_frozen_ticks += 1
                    if _hb_frozen_ticks >= _HB_STALE_TICKS:
                        logger.warning(
                            f"[AxiomScanner] recv frozen at {self.tokens_received} for "
                            f"{_hb_frozen_ticks} min — WS appears stale, forcing reconnect"
                        )
                        raise RuntimeError("AxiomScanner WS stale — no tokens received for 5+ min")
                else:
                    _hb_last_recv = self.tokens_received
                    _hb_frozen_ticks = 0

        # Deferred retry task — runs every 15s so tokens are retried promptly after their 60s wait
        async def _deferred_retry_loop():
            while True:
                await asyncio.sleep(15)
                await self._retry_deferred()

        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.ensure_future(_heartbeat())
        asyncio.ensure_future(_deferred_retry_loop())

        # Primary: connect via Cloudflare Worker proxy.
        # The Worker runs on Cloudflare's own network so cluster9 never sees a
        # datacenter IP — Railway can reach workers.dev without being blocked.
        relay_url    = os.environ.get("AXIOM_REFRESH_RELAY_URL", "")
        relay_secret = os.environ.get("AXIOM_REFRESH_RELAY_SECRET", "")
        worker_base  = relay_url.replace("/refresh", "").rstrip("/") if relay_url else ""
        if worker_base and relay_secret:
            await self._worker_proxy_ws_stream(worker_base, relay_secret, _on_token_batch)
            return  # raises NetworkError on disconnect — caught by caller

        # Fallback: curl_cffi (Chrome TLS fingerprint) — works on residential IPs
        logger.warning("[AxiomScanner] AXIOM_REFRESH_RELAY_URL not set — trying curl_cffi direct")
        try:
            from curl_cffi import requests as _cffi_req
            await self._curl_cffi_ws_stream(_cffi_req, _on_token_batch)
        except ImportError:
            logger.warning("[AxiomScanner] curl_cffi unavailable — using standard WebSocket")
            client = self.auth.get_client()
            if not client:
                raise AuthenticationError("Could not create AxiomTradeClient")
            ws = client.get_websocket_client()
            await ws.subscribe_new_tokens(_on_token_batch)
            try:
                await ws.start()
            except Exception as e:
                err = str(e).lower()
                if any(k in err for k in ("auth", "login", "password", "404", "token")):
                    raise AuthenticationError(f"WebSocket auth failed: {e}")
                raise
            raise NetworkError("WebSocket connection closed")

    async def _worker_proxy_ws_stream(self, worker_base: str, secret: str, callback):
        """Connect to Axiom new_pairs feed via the Cloudflare Worker WebSocket proxy.

        The Worker (at workers.dev) runs inside Cloudflare's own network, so it can
        reach cluster9.axiom.trade without the datacenter-IP block that Railway gets.
        Auth tokens are passed as query params so no special header tricks are needed.
        """
        import urllib.parse as _up
        import websockets as _ws

        access  = self.auth.auth_token    or ""
        refresh = self.auth.refresh_token or ""
        qs = _up.urlencode({
            "s":             secret,
            "access_token":  access,
            "refresh_token": refresh,
        })
        # Convert https:// base URL to wss://
        ws_base = worker_base.replace("https://", "wss://").replace("http://", "ws://")
        proxy_url = f"{ws_base}/ws-proxy?{qs}"

        logger.info("[AxiomScanner] Connecting via Cloudflare Worker proxy")
        try:
            async with _ws.connect(proxy_url) as ws:
                await ws.send(json.dumps({"action": "join", "room": "new_pairs"}))
                logger.info("[AxiomScanner] Worker proxy connected — subscribed to new_pairs")
                async for message in ws:
                    try:
                        data = json.loads(message)
                        if data.get("room") == "new_pairs" and data.get("content"):
                            await callback([data["content"]])
                    except Exception as parse_err:
                        logger.debug(f"[AxiomScanner] WS parse error: {parse_err}")
        except Exception as e:
            err = str(e).lower()
            if "401" in err or "unauthorized" in err:
                raise AuthenticationError(f"Worker proxy auth failed: {e}")
            raise NetworkError(f"Worker proxy WebSocket error: {e}")
        raise NetworkError("Worker proxy WebSocket closed")

    async def _curl_cffi_ws_stream(self, cffi_req, callback):
        """Connect via curl_cffi WebSocket with Chrome TLS fingerprint to bypass Cloudflare."""
        import json as _json
        import threading as _threading

        loop = asyncio.get_event_loop()
        cf_clearance = os.environ.get("AXIOM_CF_CLEARANCE", "")
        cookie = (
            f"auth-access-token={self.auth.auth_token}; "
            f"auth-refresh-token={self.auth.refresh_token}"
            + (f"; cf_clearance={cf_clearance}" if cf_clearance else "")
        )
        headers = {
            "Origin": "https://axiom.trade",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie,
        }

        _error = [None]

        def _run_sync():
            ws = cffi_req.WebSocket()
            try:
                ws.connect(
                    "wss://cluster9.axiom.trade/",
                    headers=headers,
                    impersonate="chrome131",
                )
                logger.info("[AxiomScanner] curl_cffi WebSocket connected to cluster9")
                ws.send_str(_json.dumps({"action": "join", "room": "new_pairs"}))
                while True:
                    msg = ws.recv_str()
                    if msg is None or msg == "":
                        break
                    try:
                        data = _json.loads(msg)
                        if data.get("room") == "new_pairs" and data.get("content"):
                            asyncio.run_coroutine_threadsafe(
                                callback([data["content"]]), loop
                            )
                    except Exception as parse_err:
                        logger.debug(f"[AxiomScanner] WS parse error: {parse_err}")
            except Exception as e:
                _error[0] = e
            finally:
                try:
                    ws.close()
                except Exception:
                    pass

        await loop.run_in_executor(None, _run_sync)

        if _error[0]:
            raise NetworkError(f"WebSocket error: {_error[0]}")
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
            self._seen_tokens[event.token_address] = None

            # Keep seen dict bounded (preserves insertion order — evicts oldest)
            if len(self._seen_tokens) > 10_000:
                keys = list(self._seen_tokens.keys())
                self._seen_tokens = {k: None for k in keys[-5_000:]}

            # Basic filter — quick and cheap
            # Micro-cap mode lowers the effective floor to allow $10k-$50k tokens through
            effective_min_mcap = (
                self.micro_cap_min if self.micro_cap_enabled else self.min_mcap
            )
            if not event.passes_basic_filters(
                effective_min_mcap, self.max_mcap, self.min_liquidity
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

            # Security gate — detect pool type to apply correct LP lock rules.
            # "pump amm" = pump.fun bonding curve (pre-graduation): no LP to lock.
            # "pumpswap", "raydium", "meteora", etc. = graduated pools: LP lock required.
            if self.security:
                _proto_lower = (event.protocol or "").lower()
                # pump.fun bonding curve protocols start with "pump amm" but are NOT
                # PumpSwap (graduated). Using startswith avoids false negatives from
                # future protocol variants like "pump amm v2" or "pump amm (swapper)".
                _is_bc = _proto_lower.startswith("pump amm") and "swap" not in _proto_lower
                sec_result = await self.security.check(
                    event.token_address, "solana", event.token_symbol,
                    micro_cap=True,           # keep for holder concentration relaxation
                    bonding_curve=_is_bc,     # LP lock exempt only for bonding curve
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
                enrich_passed, enrich_reason, tracked_count = await axiom_enrich_check(
                    self.auth, event.pair_address, deployer_address
                )
                if not enrich_passed:
                    logger.info(
                        f"[AxiomScanner] Enrich blocked: "
                        f"{event.token_symbol} — {enrich_reason}"
                    )
                    return
            else:
                tracked_count = 0

            # Fetch token data — Axiom API first (available immediately), DexScreener fallback
            pair_data = await self._fetch_axiom_data(event)
            if pair_data is None:
                logger.debug(
                    f"[AxiomScanner] No token data for {event.token_symbol} — skipping"
                )
                return

            # If only WebSocket event data is available (no DexScreener/Axiom API yet), defer
            # for 60s so DexScreener has time to index the token and return real price/txn data.
            # Security + enrich checks have already passed above — safe to skip on retry.
            if pair_data.get("_axiom_ws_fallback") and event.token_address not in self._deferred:
                import time as _mt
                # Evict oldest entry if at cap to prevent unbounded memory growth
                if len(self._deferred) >= self._DEFERRED_MAX:
                    oldest = next(iter(self._deferred))
                    self._deferred.pop(oldest)
                now_mt = _mt.monotonic()
                _initial_price = float(pair_data.get("priceUsd") or 0)
                self._deferred[event.token_address] = (now_mt + 60.0, now_mt, event, _initial_price)
                # Pre-subscribe to Axiom WS immediately so we collect 60s of tick data
                # before the deferred retry fires — gives us real 1-min price trend.
                if self.price_feed:
                    self.price_feed.subscribe_token(event.token_address)
                logger.debug(
                    f"[AxiomScanner] Deferred {event.token_symbol} — "
                    f"awaiting DexScreener index in 60s "
                    f"(queue={len(self._deferred)}/{self._DEFERRED_MAX})"
                )
                return

            # Hard MCap check using real DexScreener data (Axiom WebSocket tokens often lack marketCap)
            actual_mcap = float(pair_data.get("marketCap") or 0)

            # ── Micro-cap path ($10k-$50k) ──────────────────────────────────
            if (
                self.micro_cap_enabled
                and actual_mcap > 0
                and self.micro_cap_min <= actual_mcap <= self.micro_cap_max
            ):
                snipers_pct = event.snipers_hold_pct
                dev_pct     = event.dev_holds_pct
                liq         = (pair_data.get("liquidity") or {}).get("usd") or 0
                _dex_url    = f"https://dexscreener.com/solana/{event.token_address}"

                def _log_mc_candidate(reject_reason):
                    self.mc_candidates.appendleft({
                        "time":         self._dt.datetime.now(self._dt.timezone.utc).isoformat(),
                        "symbol":       event.token_symbol,
                        "name":         getattr(event, "token_name", event.token_symbol),
                        "address":      event.token_address,
                        "mcap":         round(actual_mcap),
                        "liquidity":    round(liq),
                        "dev_pct":      round(dev_pct, 1),
                        "snipers_pct":  round(snipers_pct, 1),
                        "lp_burned":    bool(event.lp_burned),
                        "protocol":     event.protocol,
                        "reject_reason": reject_reason,
                        "dex_url":      _dex_url,
                    })

                # Require token to be at least 10 minutes old — the 5-10 min window
                # has 0% win rate empirically (dead zone after initial excitement fades).
                # Win rate stabilises to ~50% only after 10 minutes of price history.
                _MIN_AGE_SECONDS = 600
                _age_seconds: float = -1  # sentinel — unresolved

                # Strategy 1: parse event.created_at (ISO string from Axiom WS)
                _created_at_str = event.created_at
                if _created_at_str:
                    try:
                        import dateutil.parser as _dp
                        _created_dt = _dp.parse(_created_at_str)
                        if _created_dt.tzinfo is None:
                            from datetime import timezone as _tz
                            _created_dt = _created_dt.replace(tzinfo=_tz.utc)
                        _age_seconds = (self._dt.datetime.now(self._dt.timezone.utc) - _created_dt).total_seconds()
                    except Exception:
                        pass  # fall through to strategy 2

                # Strategy 2: use pairCreatedAt from DexScreener (Unix ms)
                if _age_seconds < 0:
                    _pair_created_ms = float(pair_data.get("pairCreatedAt") or 0)
                    if _pair_created_ms > 0:
                        import time as _time
                        _age_seconds = _time.time() - _pair_created_ms / 1000
                        logger.debug(
                            f"[AxiomScanner] {event.token_symbol} — used pairCreatedAt fallback "
                            f"(event.created_at unparseable): age={_age_seconds:.0f}s"
                        )

                if _age_seconds < 0:
                    # No usable timestamp from any source — block
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"no parseable creation timestamp (cannot verify {_MIN_AGE_SECONDS//60}min minimum age)"
                    )
                    _log_mc_candidate("No parseable timestamp — age unverifiable")
                    return

                if _age_seconds < _MIN_AGE_SECONDS:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"token only {_age_seconds:.0f}s old (need {_MIN_AGE_SECONDS}s)"
                    )
                    _log_mc_candidate(f"Too new: {_age_seconds/60:.1f}min < {_MIN_AGE_SECONDS//60}min")
                    return

                if snipers_pct > self.micro_cap_max_snipers:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"snipers hold {snipers_pct:.0f}% (max {self.micro_cap_max_snipers:.0f}%)"
                    )
                    _log_mc_candidate(f"Snipers {snipers_pct:.0f}% > {self.micro_cap_max_snipers:.0f}% max")
                    return

                if dev_pct > self.micro_cap_max_dev:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"dev holds {dev_pct:.0f}% (max {self.micro_cap_max_dev:.0f}%)"
                    )
                    _log_mc_candidate(f"Dev holds {dev_pct:.0f}% > {self.micro_cap_max_dev:.0f}% max")
                    return

                lp_locked = sec_result and sec_result.liquidity_locked
                # Bonding curve tokens (pump.fun pre-graduation) have no real LP pool —
                # liquidity is secured by the bonding curve contract itself.
                # Skip the LP burned/locked requirement for these.
                _proto_lower = (event.protocol or "").lower()
                _is_bc_proto = _proto_lower.startswith("pump amm") and "swap" not in _proto_lower
                if not event.lp_burned and not lp_locked and not _is_bc_proto:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"LP not burned and not locked (rug risk)"
                    )
                    _log_mc_candidate("LP not burned & not locked")
                    return

                # Minimum active buyers in last 5m — dead tokens have zero
                _txns_m5 = (pair_data.get("txns") or {}).get("m5") or {}
                _m5_buys = int(_txns_m5.get("buys") or 0)
                _m5_sells = int(_txns_m5.get("sells") or 0)
                if _m5_buys < 50:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"only {_m5_buys} buyers in last 5m (need 50+)"
                    )
                    _log_mc_candidate(f"Dead: {_m5_buys} m5 buys < 50")
                    return

                # Buy pressure check — more sellers than buyers = distribution phase.
                # Buying into heavy sell pressure is the primary cause of <2min stop losses.
                if _m5_sells > _m5_buys:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"sell pressure: {_m5_sells} sells > {_m5_buys} buys in m5"
                    )
                    _log_mc_candidate(f"Sell pressure: {_m5_sells} sells > {_m5_buys} buys")
                    return

                # Minimum 1h volume — only meaningful once the token has been
                # trading for a full hour. Tokens under 60min old will always
                # show near-zero h1 volume on DexScreener since the window
                # isn't filled yet; the m5 buys check already covers freshness.
                _vol_h1 = float((pair_data.get("volume") or {}).get("h1") or 0)
                if _age_seconds >= 3600 and _vol_h1 < 1000:
                    logger.info(
                        f"[AxiomScanner] Micro-cap blocked: {event.token_symbol} — "
                        f"1h volume ${_vol_h1:,.0f} < $1,000 (token is {_age_seconds/3600:.1f}h old)"
                    )
                    _log_mc_candidate(f"Low vol: ${_vol_h1:,.0f} < $1k")
                    return

                # 1-minute chart check for micro-caps — even 10-min-old tokens can be
                # in a dead-cat bounce or active dump when our signal fires.
                if event.pair_address:
                    _mc_candles = await self._fetch_1min_candles(event.pair_address)
                    _mc_ok, _mc_reason = self._check_1min_momentum(_mc_candles)
                    if not _mc_ok:
                        logger.info(
                            f"[AxiomScanner] Micro-cap 1m chart blocked: {event.token_symbol} "
                            f"— {_mc_reason}"
                        )
                        _log_mc_candidate(f"1m chart: {_mc_reason}")
                        return

                logger.info(
                    f"[AxiomScanner] 🌱 MICRO-CAP SIGNAL: {event.token_symbol} | "
                    f"MCap: ${actual_mcap:,.0f} | "
                    f"Dev: {dev_pct:.0f}% | Snipers: {snipers_pct:.0f}% | "
                    f"LP burned: {event.lp_burned} | LP locked: {bool(lp_locked)}"
                )
                self.signals_fired += 1
                self.tokens_evaluated += 1

                # Micro-cap fresh launches skip chart analysis (no history yet)
                # Route through DipWatcher if configured, otherwise buy direct
                _mc_reason = (
                    f"Micro-cap | ${actual_mcap:,.0f} mcap | "
                    f"dev {dev_pct:.0f}% | snipers {snipers_pct:.0f}%"
                )
                _m5 = float((pair_data.get("priceChange") or {}).get("m5") or 0)
                _in_dip_window = -20 <= _m5 <= -5

                if _in_dip_window:
                    # m5 is already in the dip zone — buy immediately, no waiting
                    logger.info(
                        f"[AxiomScanner] 🎯 Dip entry: {event.token_symbol} "
                        f"m5={_m5:+.1f}% — buying now"
                    )
                    await self.trader.buy(
                        token_address=event.token_address,
                        token_symbol=event.token_symbol,
                        reason=_mc_reason + f" | dip entry m5={_m5:+.1f}%",
                        signal_score=50,
                        override_usd=self.micro_cap_position_usd,
                        pair_address=event.pair_address or "",
                    )
                elif self.dip_watcher:
                    # m5 not in dip zone yet — watch for the dip to develop
                    import time as _t
                    _signal_price = float(pair_data.get("priceUsd") or 0)
                    _h6 = float((pair_data.get("priceChange") or {}).get("h6") or 0)
                    _created_ms = pair_data.get("pairCreatedAt") or 0
                    _age_h = (_t.time() - _created_ms / 1000) / 3600 if _created_ms > 0 else 999.0
                    await self.dip_watcher.watch(
                        token_address=event.token_address,
                        token_symbol=event.token_symbol,
                        reason=_mc_reason,
                        override_usd=self.micro_cap_position_usd,
                        signal_price=_signal_price,
                        h6_pct=_h6,
                        token_age_hours=_age_h,
                    )
                else:
                    await self.trader.buy(
                        token_address=event.token_address,
                        token_symbol=event.token_symbol,
                        reason=_mc_reason,
                        signal_score=50,
                        override_usd=self.micro_cap_position_usd,
                        pair_address=event.pair_address or "",
                    )
                return
            # ── End micro-cap path ──────────────────────────────────────────

            if actual_mcap > 0 and actual_mcap < self.min_mcap:
                logger.info(
                    f"[AxiomScanner] MCap filter drop (real): {event.token_symbol} — ${actual_mcap:,.0f}"
                )
                return

            # m5 price direction filter — if price is falling in the last 5 minutes,
            # the pump has already reversed. Buying into a dump is the primary cause
            # of <2-minute stop losses. Only apply when m5 data is available (not 0).
            _m5_chg = float((pair_data.get("priceChange") or {}).get("m5") or 0)
            if _m5_chg < -3.0:
                logger.info(
                    f"[AxiomScanner] Standard blocked: {event.token_symbol} — "
                    f"m5 price change {_m5_chg:.1f}% (currently dumping)"
                )
                return

            # Full signal evaluation with real mcap/volume/liquidity
            self.tokens_evaluated += 1

            if self.evaluator:
                evaluation = await self.evaluator.evaluate(pair_data)
                if evaluation.hard_skip:
                    logger.info(
                        f"[AxiomScanner] Hard skip: {event.token_symbol} — "
                        f"{', '.join(evaluation.skip_reasons)}"
                    )
                    return
                score = evaluation.total_score
                effective_min = self.min_score
                if self.market_monitor and self.market_monitor.market_restricted:
                    effective_min = self.market_monitor.restricted_threshold
                if score < effective_min:
                    logger.info(
                        f"[AxiomScanner] Score too low: {event.token_symbol} — "
                        f"{score:.0f} < {effective_min:.0f}"
                    )
                    return
            else:
                if not event.has_twitter and not event.has_telegram:
                    return
                score = 70

            # User spike bonus
            user_spike = (
                self.price_feed is not None
                and event.token_address in self.price_feed._user_count_spikes
            )
            if user_spike:
                score += 6
                logger.info(
                    f"[AxiomScanner] User spike bonus: {event.token_symbol} +6"
                )

            # Tracked wallet bonus
            if tracked_count >= 3:
                score += 8
                logger.info(
                    f"[AxiomScanner] Tracked wallet bonus: {event.token_symbol} "
                    f"+8 ({tracked_count} smart wallets holding)"
                )
            elif tracked_count >= 1:
                score += 4
                logger.info(
                    f"[AxiomScanner] Tracked wallet bonus: {event.token_symbol} "
                    f"+4 ({tracked_count} smart wallet holding)"
                )

            # 1-minute chart check — verify current momentum before committing capital.
            # Fetches the last 5 one-minute candles from GeckoTerminal. If the current
            # candle is red or 3 consecutive candles are all falling, the pump is over.
            # GeckoTerminal failures return [] so this never hard-blocks on API errors.
            if event.pair_address:
                _std_candles = await self._fetch_1min_candles(event.pair_address)
                _std_ok, _std_reason = self._check_1min_momentum(_std_candles)
                if not _std_ok:
                    logger.info(
                        f"[AxiomScanner] Standard 1m chart blocked: {event.token_symbol} "
                        f"— {_std_reason}"
                    )
                    return

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

            if self.scanner:
                # Route through scanner's chart analysis — no buy on score alone
                await self.scanner.process_external_signal(
                    token_address=event.token_address,
                    token_symbol=event.token_symbol,
                    reason=f"Axiom signal | score {score:.0f} | {event.protocol}",
                    signal_score=int(score),
                    strategy_tag="AxiomScanner",
                    skip_security=True,
                    price_usd=float(pair_data.get("priceUsd") or 0),
                    liquidity_usd=liq,
                    volume_h1=vol_h1,
                    mcap=mcap,
                )
            else:
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
                    if self.evaluator else False,
                    pair_address=event.pair_address or "",
                )

        except Exception as e:
            logger.error(f"[AxiomScanner] Evaluate/trade error for "
                         f"{event.token_symbol}: {e}")

    async def _retry_deferred(self):
        """
        Re-evaluate tokens that had no DexScreener data when first seen.
        Runs every 15s (independent task). Tokens are retried once after their 60s delay;
        if DexScreener still has no data they are dropped (too new / dead pool).
        Security + enrich checks were already passed before queuing — not re-run here.
        """
        import time as _mt
        now = _mt.monotonic()
        ready = [addr for addr, (t, _qt, _ev, *_) in list(self._deferred.items()) if t <= now]
        if not ready:
            return
        logger.info(f"[AxiomScanner] Retrying {len(ready)} deferred tokens")
        for addr in ready:
            _, _queued_at, event, _initial_price = self._deferred.pop(addr)
            try:
                pair_data = await self._fetch_dexscreener_pair(event)
                if pair_data is None:
                    logger.info(
                        f"[AxiomScanner] Deferred drop: {event.token_symbol} "
                        f"— no DexScreener data after 60s (dead pool or too new)"
                    )
                    if self.price_feed:
                        self.price_feed.unsubscribe_token(event.token_address)
                    continue

                actual_mcap = float(pair_data.get("marketCap") or 0)
                if actual_mcap > 0 and actual_mcap < self.min_mcap:
                    logger.debug(
                        f"[AxiomScanner] Deferred MCap drop: {event.token_symbol} "
                        f"${actual_mcap:,.0f}"
                    )
                    if self.price_feed:
                        self.price_feed.unsubscribe_token(event.token_address)
                    continue

                # ── Sub-minute chart checks using buffered tick data ────────────
                # We pre-subscribed to the Axiom WS at enqueue time (60s ago), so
                # the tick buffer now has ~60s of real price data to analyze.

                # 1. Tick trend — if price fell >5% over the last 60s, we're buying a dump
                if self.price_feed:
                    _trend_60s = self.price_feed.get_tick_trend(event.token_address, 60)
                    _tick_count = self.price_feed.get_tick_count(event.token_address, 60)
                    if _trend_60s is not None and _trend_60s < -5.0:
                        logger.info(
                            f"[AxiomScanner] Deferred tick blocked: {event.token_symbol} — "
                            f"60s trend {_trend_60s:.1f}% ({_tick_count} ticks) — dumping"
                        )
                        self.price_feed.unsubscribe_token(event.token_address)
                        continue
                    if _tick_count >= 3:
                        logger.debug(
                            f"[AxiomScanner] {event.token_symbol} 60s tick trend: "
                            f"{_trend_60s:.1f}% ({_tick_count} ticks)"
                        )

                # 2. Late-entry guard — if price already pumped >30% during our 60s wait, skip
                _current_price = float(pair_data.get("priceUsd") or 0)
                if _initial_price > 0 and _current_price > 0:
                    _price_change_60s = (_current_price / _initial_price - 1) * 100
                    if _price_change_60s > 30.0:
                        logger.info(
                            f"[AxiomScanner] Deferred late-entry: {event.token_symbol} — "
                            f"pumped {_price_change_60s:.0f}% during 60s wait — skipping"
                        )
                        if self.price_feed:
                            self.price_feed.unsubscribe_token(event.token_address)
                        continue

                # 3. 1-minute GeckoTerminal candle check
                if event.pair_address:
                    _candles = await self._fetch_1min_candles(event.pair_address)
                    _ok, _reason = self._check_1min_momentum(_candles)
                    if not _ok:
                        logger.info(
                            f"[AxiomScanner] Deferred 1m chart blocked: {event.token_symbol} "
                            f"— {_reason}"
                        )
                        if self.price_feed:
                            self.price_feed.unsubscribe_token(event.token_address)
                        continue

                self.tokens_evaluated += 1

                if self.evaluator:
                    evaluation = await self.evaluator.evaluate(pair_data)
                    if evaluation.hard_skip:
                        if self.price_feed:
                            self.price_feed.unsubscribe_token(event.token_address)
                        continue
                    score = evaluation.total_score
                    effective_min = self.min_score
                    if self.market_monitor and self.market_monitor.market_restricted:
                        effective_min = self.market_monitor.restricted_threshold
                    if score < effective_min:
                        logger.info(
                            f"[AxiomScanner] Deferred score low: {event.token_symbol} "
                            f"— {score:.0f} < {effective_min:.0f}"
                        )
                        if self.price_feed:
                            self.price_feed.unsubscribe_token(event.token_address)
                        continue
                else:
                    if not event.has_twitter and not event.has_telegram:
                        if self.price_feed:
                            self.price_feed.unsubscribe_token(event.token_address)
                        continue
                    score = 70

                self.signals_fired += 1
                mcap   = pair_data.get("marketCap") or 0
                liq    = (pair_data.get("liquidity") or {}).get("usd") or 0
                vol_h1 = (pair_data.get("volume") or {}).get("h1") or 0
                logger.info(
                    f"[AxiomScanner] 🚀 DEFERRED SIGNAL: {event.token_symbol} | "
                    f"MCap: ${mcap:,.0f} | Score: {score:.0f} | "
                    f"Protocol: {event.protocol}"
                )
                if self.scanner:
                    await self.scanner.process_external_signal(
                        token_address=event.token_address,
                        token_symbol=event.token_symbol,
                        reason=f"Axiom signal | score {score:.0f} | {event.protocol}",
                        signal_score=int(score),
                        strategy_tag="AxiomScanner",
                        skip_security=True,
                        price_usd=float(pair_data.get("priceUsd") or 0),
                        liquidity_usd=liq,
                        volume_h1=vol_h1,
                        mcap=mcap,
                    )
                else:
                    await self.trader.buy(
                        token_address=event.token_address,
                        token_symbol=event.token_symbol,
                        reason=f"Axiom signal | score {score:.0f} | {event.protocol}",
                        signal_score=int(score),
                    )
            except Exception as e:
                logger.debug(
                    f"[AxiomScanner] Deferred retry error for {event.token_symbol}: {e}"
                )
                # Always unsubscribe pre-subscribed token — subscription was started at
                # enqueue time and must be cleaned up on any error path.
                if self.price_feed:
                    self.price_feed.unsubscribe_token(event.token_address)

    async def _fetch_axiom_data(self, event: "AxiomTokenEvent") -> Optional[dict]:
        """
        Fetch token metrics from Axiom API and convert to DexScreener format
        so the existing signal evaluator can score it without changes.

        Fallback chain:
          1. api10 token-info by pair (skip for LaunchLab — always 404)
          2. api6 token by address  (skip for LaunchLab — always 404)
          3. api3 token-info by pair (supports LaunchLab + newer protocols)
          4. DexScreener
          5. WS event data (deferred for real scoring later)
        """
        import aiohttp as _aio
        loop = asyncio.get_event_loop()
        raw_info = None

        proto_lower = event.protocol.lower()
        _skip_legacy_api = "launchlab" in proto_lower  # api10/api6 always 404 for LaunchLab

        # Try pair-specific endpoint first (more accurate for new tokens)
        if not _skip_legacy_api and event.pair_address and AXIOM_AVAILABLE:
            try:
                client = self.auth.get_client()
                if client:
                    raw_info = await loop.run_in_executor(
                        None, client.get_token_info_by_pair, event.pair_address
                    )
            except Exception as e:
                logger.debug(f"[AxiomScanner] get_token_info_by_pair failed "
                             f"for {event.token_symbol}: {e}")

        # Fallback to token-address endpoint
        if not raw_info and not _skip_legacy_api and AXIOM_AVAILABLE:
            try:
                client = self.auth.get_client()
                if client:
                    raw_info = await loop.run_in_executor(
                        None, client.get_token_info, event.token_address
                    )
            except Exception as e:
                logger.debug(f"[AxiomScanner] get_token_info failed "
                             f"for {event.token_symbol}: {e}")

        # api3 supports LaunchLab and newer protocols that api10/api6 don't
        if not raw_info and event.pair_address:
            try:
                import time as _t
                url = (
                    f"https://api3.axiom.trade/token-info"
                    f"?pairAddress={event.pair_address}&v={int(_t.time() * 1000)}"
                )
                cookie = f"auth-access-token={self.auth.auth_token or ''}"
                headers = {
                    "Cookie": cookie,
                    "Accept": "application/json",
                    "Origin": "https://axiom.trade",
                    "Referer": "https://axiom.trade/",
                }
                async with _aio.ClientSession() as session:
                    async with session.get(
                        url, headers=headers, timeout=_aio.ClientTimeout(total=5)
                    ) as resp:
                        if resp.status == 200:
                            raw_info = await resp.json(content_type=None)
                            logger.debug(
                                f"[AxiomScanner] api3 data for {event.token_symbol}"
                            )
            except Exception as e:
                logger.debug(f"[AxiomScanner] api3 failed for {event.token_symbol}: {e}")

        # If Axiom returned data, convert to DexScreener-compatible format
        if raw_info:
            return self._axiom_raw_to_dex(event, raw_info)

        # Try DexScreener (token may be indexed by now)
        ds_data = await self._fetch_dexscreener_pair(event)
        if ds_data:
            return ds_data
        # Last resort: use the data Axiom already sent in the WebSocket event.
        # New tokens are never in external APIs immediately — the WebSocket event
        # itself carries mcap/liquidity/volume from Axiom, which is enough to score.
        return event.to_dexscreener_format()

    def _axiom_raw_to_dex(self, event: "AxiomTokenEvent", raw: dict) -> dict:
        """Convert Axiom API token-info response to DexScreener pair format."""
        mcap    = float(raw.get("marketCap") or raw.get("market_cap") or
                        raw.get("marketCapUsd") or event.mcap_usd or 0)
        liq     = float(raw.get("liquidity") or raw.get("liquidityUsd") or
                        raw.get("liquidity_usd") or event.liquidity_usd or 0)
        vol_h1  = float(raw.get("volume1h") or raw.get("volumeH1") or
                        raw.get("volume_1h") or 0)
        vol_h6  = float(raw.get("volume6h") or raw.get("volumeH6") or 0)
        vol_h24 = float(raw.get("volume24h") or raw.get("volumeH24") or 0)
        price   = float(raw.get("price") or raw.get("priceUsd") or
                        raw.get("price_usd") or 0)
        ch_h1   = float(raw.get("priceChange1h") or raw.get("change1h") or 0)
        ch_h6   = float(raw.get("priceChange6h") or raw.get("change6h") or 0)
        ch_h24  = float(raw.get("priceChange24h") or raw.get("change24h") or 0)
        buys_h1 = int(raw.get("buys1h") or raw.get("buysH1") or 0)
        sells_h1= int(raw.get("sells1h") or raw.get("sellsH1") or 0)
        return {
            "chainId":    "solana",
            "pairAddress": event.pair_address,
            "baseToken":  {
                "address": event.token_address,
                "symbol":  event.token_symbol,
                "name":    event.token_name,
            },
            "priceUsd":   str(price) if price else "0",
            "marketCap":  mcap,
            "liquidity":  {"usd": liq},
            "volume":     {"h1": vol_h1, "h6": vol_h6, "h24": vol_h24, "m5": 0},
            "priceChange":{"h1": ch_h1, "h6": ch_h6, "h24": ch_h24, "m5": 0},
            "txns": {
                "h1": {"buys": buys_h1, "sells": sells_h1},
                "m5": {"buys": 0, "sells": 0},
            },
            "pairCreatedAt": 0,
            "_axiom_source": True,
        }

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

    async def _fetch_1min_candles(self, pool_address: str) -> list:
        """
        Fetch the last 5 one-minute OHLCV candles from GeckoTerminal.
        Returns list of [timestamp, open, high, low, close, volume] arrays.
        Returns [] on any failure — callers must treat empty as "no data, don't block".
        """
        import aiohttp as _aio
        if not pool_address:
            return []
        url = (
            f"https://api.geckoterminal.com/api/v2/networks/solana/pools/"
            f"{pool_address}/ohlcv/minute?aggregate=1&limit=5&currency=usd"
        )
        try:
            async with _aio.ClientSession() as sess:
                async with sess.get(url, timeout=_aio.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        return []
                    raw = await resp.json(content_type=None)
            return (
                raw.get("data", {})
                   .get("attributes", {})
                   .get("ohlcv_list", [])
            )
        except Exception:
            return []

    @staticmethod
    def _check_1min_momentum(candles: list) -> tuple:
        """
        Returns (ok: bool, reason: str).
        ok=False means the 1-minute chart shows clear bearish momentum — skip entry.
        ok=True means momentum is neutral or positive — proceed.

        Rules (only block when evidence is clear — don't block on data gaps):
          - Last 1-min candle down >3% → dumping right now
          - All 3 most-recent candles red AND combined drop >5% → sustained sell-off
          - Zero volume in last candle → dead

        Passes through on empty/insufficient data so we never miss a buy just because
        GeckoTerminal is slow.
        """
        if not candles or len(candles) < 2:
            return True, ""

        # Candles sorted oldest-first: [ts, open, high, low, close, volume]
        last = candles[-1]
        try:
            o, h, l, c, vol = float(last[1]), float(last[2]), float(last[3]), float(last[4]), float(last[5])
        except (IndexError, TypeError, ValueError):
            return True, ""

        if vol <= 0:
            return False, "zero volume in last 1m candle"

        if o > 0 and c < o * 0.97:
            drop = (c / o - 1) * 100
            return False, f"1m candle red {drop:.1f}% — dumping now"

        # Check if last 3 candles are all red and combined trend is bad
        if len(candles) >= 3:
            recent = candles[-3:]
            try:
                closes = [float(c[4]) for c in recent]
                opens  = [float(c[1]) for c in recent]
                all_red = all(closes[i] < opens[i] for i in range(3))
                if all_red and closes[0] > 0:
                    combined_drop = (closes[-1] / closes[0] - 1) * 100
                    if combined_drop < -5.0:
                        return False, f"3 consecutive red candles, {combined_drop:.1f}% drop"
            except (IndexError, TypeError, ValueError):
                pass

        return True, ""

    async def _alert_scanner_down(self, reason: str):
        """Send a Telegram alert when the scanner drops, then hourly reminders if still down."""
        import time as _time
        now = _time.time()
        if self._ws_down_since is None:
            # First failure — alert immediately
            self._ws_down_since = now
            self._ws_down_alerted = now
            short_reason = reason[:200] if reason else "unknown"
            await self.telegram.send(
                f"⚠️ *Axiom Scanner Disconnected*\n"
                f"Reason: {short_reason}\n"
                f"Reconnecting automatically..."
            )
        elif now - self._ws_down_alerted >= 3600:
            # Hourly reminder while still down
            self._ws_down_alerted = now
            down_mins = int((now - self._ws_down_since) / 60)
            await self.telegram.send(
                f"⚠️ *Axiom Scanner Still Down* ({down_mins} min)\n"
                f"Still reconnecting — check Railway logs if this persists"
            )

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
                                    self._seen_tokens[token_addr] = None
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
    Returns (passed: bool, reason: str, tracked_count: int).

    Fails open on any API error — never blocks a trade due to enrichment failure.

    Checks:
      1. Holder concentration — top holder > 20% → reject
      2. Dev history — serial rugger (3+ dead tokens from same dev) → reject
    """
    loop = asyncio.get_event_loop()
    tracked_count = 0

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
                            f"Top holder concentration too high ({top_pct:.1f}%)",
                            0
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
                        return (False, f"Serial rugger — dev has {dead_count} dead tokens", 0)

                    if recent_launches >= 10:
                        return (False, f"High-frequency deployer — {recent_launches} tokens in 30 days", 0)

                    if len(lifetimes_days) >= 3:
                        avg_lifetime = sum(lifetimes_days) / len(lifetimes_days)
                        if avg_lifetime < 2.0:
                            return (False, f"Rug pattern — avg token lifetime {avg_lifetime:.1f} days", 0)
        except Exception as e:
            logger.debug(
                f"[AxiomEnrich] Dev check failed for {deployer_address[:8]}: {e}"
            )
            # Fail open — don't block on API errors

    return (True, "", tracked_count)
