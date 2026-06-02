"""Egress-control gzip middleware (2026-06-02) — proves it compresses large JSON for
gzip-accepting clients, decompresses correctly, and is TRANSPARENT to clients that
don't accept gzip (no production behavior change)."""
import asyncio, json, gzip
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from dashboard.web_dashboard import gzip_middleware


async def _run():
    big = [{"i": i, "blob": "abcdefghij" * 20} for i in range(2000)]  # big, compressible
    app = web.Application(middlewares=[gzip_middleware])
    app.router.add_get("/big", lambda r: web.json_response(big))
    app.router.add_get("/small", lambda r: web.json_response({"ok": True}))
    server = TestServer(app)
    await server.start_server()
    base = f"http://{server.host}:{server.port}"
    out = {}
    async with aiohttp.ClientSession(auto_decompress=False) as s:
        # 1) large + accepts gzip -> compressed, decompresses to correct data, much smaller
        async with s.get(base + "/big", headers={"Accept-Encoding": "gzip"}) as r:
            raw = await r.read()
            out["big_enc"] = r.headers.get("Content-Encoding")
            out["big_ok"] = len(json.loads(gzip.decompress(raw))) == 2000
            out["big_bytes"] = len(raw)
        # uncompressed size for ratio
        async with s.get(base + "/big", headers={"Accept-Encoding": "identity"}) as r:
            out["big_raw_bytes"] = len(await r.read())
            out["big_identity_enc"] = r.headers.get("Content-Encoding")
        # 2) tiny -> not compressed (overhead not worth it)
        async with s.get(base + "/small", headers={"Accept-Encoding": "gzip"}) as r:
            out["small_enc"] = r.headers.get("Content-Encoding")
    await server.close()
    return out


def test_gzip_compresses_large_json_and_is_transparent():
    o = asyncio.run(_run())
    assert o["big_enc"] == "gzip"                  # compressed when accepted
    assert o["big_ok"] is True                     # decompresses to correct payload
    assert o["big_bytes"] < o["big_raw_bytes"] * 0.5  # >2x smaller (JSON ~5-10x in practice)
    assert o["big_identity_enc"] is None           # TRANSPARENT: non-accepting client uncompressed
    assert o["small_enc"] is None                  # tiny response skipped
    # report the ratio for the record
    print(f"gzip egress: {o['big_raw_bytes']} -> {o['big_bytes']} bytes "
          f"({o['big_bytes']/o['big_raw_bytes']*100:.0f}%)")
