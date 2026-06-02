"""Basic-auth middleware for the dashboard.

Gates write endpoints (POST/PUT/DELETE/PATCH) behind HTTP Basic Auth.
Reads (GET) are unprotected — dashboard stays browse-able without login.

Credentials come from env vars DASHBOARD_USER + DASHBOARD_PASSWORD.
If EITHER is unset, the middleware fails-open with a noisy warning
(preserves existing paper-mode behavior; we don't accidentally lock
ourselves out by deploying the middleware before env vars are set).

Designed for single-user dashboards. For multi-user, replace with a
session-cookie auth backend.
"""
from __future__ import annotations
import base64
import hmac
import logging
import os
from aiohttp import web

logger = logging.getLogger(__name__)

# Endpoints that should NEVER require auth even if they happen to be POST.
# Currently empty; reserve for "publicly callable" mutations if any get
# added (e.g. webhook receivers from external services).
PUBLIC_POST_PATHS: set[str] = set()


@web.middleware
async def basic_auth_middleware(request: web.Request, handler):
    """Gate POST/PUT/DELETE/PATCH behind Basic Auth.

    GET/HEAD/OPTIONS pass through unauthenticated — dashboard reads
    remain public so curl / scripts / browser tabs work as before.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await handler(request)
    if request.path in PUBLIC_POST_PATHS:
        return await handler(request)

    expected_user = os.environ.get("DASHBOARD_USER", "")
    expected_pass = os.environ.get("DASHBOARD_PASSWORD", "")
    if not expected_user or not expected_pass:
        # E2 (2026-06-02 security audit): fail-CLOSED when the wallet is LIVE.
        # In paper mode keep failing OPEN (don't lock out local dev during the staged
        # rollout), but if this is a live deploy (PAPER_MODE explicitly false) an unset
        # credential MUST reject writes — otherwise /api/buy,/sell,/reset are exposed to
        # the internet with real capital behind them.
        _live = os.environ.get("PAPER_MODE", "true").strip().lower() in ("false", "0", "no")
        if _live:
            logger.critical(
                "[Dashboard] LIVE mode but DASHBOARD_USER/PASSWORD unset — REJECTING "
                "write (fail-closed). Set credentials in Railway env."
            )
            return web.Response(status=503, text="Auth not configured (live mode) — write refused")
        logger.warning(
            "[Dashboard] DASHBOARD_USER/PASSWORD env vars NOT SET — "
            "POST endpoints UNPROTECTED (paper mode). Set them in Railway env to enable auth."
        )
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="multichain-bot"'},
            text="Authentication required",
        )
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        user, _, password = decoded.partition(":")
    except Exception:
        return web.Response(status=401, text="Malformed Authorization header")

    # Constant-time comparison to prevent timing oracles
    user_ok = hmac.compare_digest(user, expected_user)
    pass_ok = hmac.compare_digest(password, expected_pass)
    if not (user_ok and pass_ok):
        return web.Response(
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="multichain-bot"'},
            text="Invalid credentials",
        )

    return await handler(request)
