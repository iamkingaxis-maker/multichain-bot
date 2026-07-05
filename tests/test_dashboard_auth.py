"""Tests for dashboard/auth.py — Basic Auth middleware.

Tests the middleware directly via mock Request objects (no live HTTP).
Verifies:
- GET requests bypass auth entirely (read-only is public)
- POST without auth returns 401 + WWW-Authenticate
- POST with valid Basic Auth passes
- POST with invalid credentials returns 401
- Missing env vars fail-open with warning (rollout safety)
- Malformed Authorization header returns 401
"""
import asyncio
import base64
import logging
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from dashboard.auth import basic_auth_middleware


def _basic_header(user: str, password: str) -> str:
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {creds}"


def _make_request(method: str, path: str = "/write", auth_header: str | None = None):
    """Build a minimal mock request that satisfies the middleware's reads."""
    req = MagicMock()
    req.method = method
    req.path = path
    headers = {}
    if auth_header is not None:
        headers["Authorization"] = auth_header
    req.headers = headers
    return req


async def _ok_handler(request):
    return web.Response(text="ok")


def _run(coro):
    # asyncio.run, NOT get_event_loop(): on Python 3.12 any earlier test that
    # used asyncio.run() leaves the main-thread loop unset, so get_event_loop()
    # raises RuntimeError when this file runs mid-suite (the chronic
    # "passes alone, fails in the full run" contamination, fixed 2026-07-05).
    return asyncio.run(coro)


def test_get_request_passes_without_auth(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("GET", "/read")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200


def test_post_without_auth_returns_401(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 401
    assert "WWW-Authenticate" in resp.headers


def test_post_with_correct_basic_auth_passes(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST", auth_header=_basic_header("u", "p"))
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200


def test_post_with_wrong_password_returns_401(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST", auth_header=_basic_header("u", "bad"))
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 401


def test_post_with_wrong_user_returns_401(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST", auth_header=_basic_header("bad", "p"))
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 401


def test_post_without_env_fails_open_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    caplog.set_level(logging.WARNING, logger="dashboard.auth")
    req = _make_request("POST")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200  # fail-open preserves rollout safety
    assert any("UNPROTECTED" in r.message for r in caplog.records)


def test_malformed_authorization_header_returns_401(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST", auth_header="Basic not_base64!")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 401


def test_non_basic_scheme_returns_401(monkeypatch):
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("POST", auth_header="Bearer some_token")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 401


def test_options_request_passes_without_auth(monkeypatch):
    """CORS preflight (OPTIONS) must pass — browsers send it before any POST."""
    monkeypatch.setenv("DASHBOARD_USER", "u")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "p")
    req = _make_request("OPTIONS")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200


# ── E2 (2026-06-02 security audit): fail-CLOSED in LIVE mode when creds unset ──
def test_live_mode_creds_unset_fails_closed(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "false")            # LIVE
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    req = _make_request("POST")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 503                            # write REFUSED, not passed through


def test_paper_mode_creds_unset_still_fails_open(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "true")             # paper
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    req = _make_request("POST")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200                            # don't lock out paper dev


def test_live_mode_get_still_public(monkeypatch):
    monkeypatch.setenv("PAPER_MODE", "false")
    monkeypatch.delenv("DASHBOARD_USER", raising=False)
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    req = _make_request("GET", "/read")
    resp = _run(basic_auth_middleware(req, _ok_handler))
    assert resp.status == 200                            # reads stay public even live
