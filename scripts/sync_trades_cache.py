"""Incremental local trade cache (2026-06-10, hybrid cost model) — the egress fix.

Analysis used to re-pull the full trade history from Railway (all=1&full=1,
~20MB gzipped per pull, several times a day = the documented bill driver).
This keeps a LOCAL cache (_trades_cache.json) and tops it up incrementally:
newest-first trimmed pages until we overlap what we already have. Typical
sync cost: one ~100-400KB response instead of ~20MB.

Usage:
  python scripts/sync_trades_cache.py            # incremental top-up (trimmed)
  python scripts/sync_trades_cache.py --full     # top-up WITH entry_meta (for mines)
  python scripts/sync_trades_cache.py --rebuild  # one full re-pull (use sparingly)

Analysis scripts: json.load("_trades_cache.json") -> list[dict], newest LAST
(chronological), deduped on (time, type, address, bot_id).
"""
from __future__ import annotations
import gzip
import io
import json
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "https://gracious-inspiration-production.up.railway.app/api/trades"
CACHE = "_trades_cache.json"
PAGE = 2000  # records per incremental page (server caps limit at 5000)


def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    d = json.loads(raw)
    if isinstance(d, dict) and d.get("egress_throttled"):
        sys.exit("ABORT: egress-throttled — wait ~60s and rerun (do not analyze stubs).")
    return d if isinstance(d, list) else d.get("trades", [])


def _key(t):
    # NO bot_id in the key: tracker records flip bot_id None <-> 'baseline_v1'
    # across server restarts, which double-inserted the same trade (caught
    # 2026-06-10: 23 phantom 06-09 sells). time has microsecond precision and
    # pnl disambiguates genuine same-second fan-out sells.
    pnl = t.get("pnl")
    return (t.get("time") or "", t.get("type") or "",
            t.get("address") or t.get("token") or "",
            round(float(pnl), 6) if isinstance(pnl, (int, float)) else None)


def main():
    full = "--full" in sys.argv
    rebuild = "--rebuild" in sys.argv
    try:
        cache = json.load(open(CACHE))
        assert isinstance(cache, list)
    except Exception:
        cache = []
        rebuild = True

    if rebuild:
        print("full rebuild (all=1) — the ONE heavy pull; subsequent runs are incremental")
        trades = _get(f"{BASE}?all=1" + ("&full=1" if full else ""))
        trades.sort(key=lambda t: t.get("time") or "")
        json.dump(trades, open(CACHE, "w"))
        print(f"cache rebuilt: {len(trades)} records -> {CACHE}")
        return

    known = {_key(t) for t in cache}
    newest_known = max((t.get("time") or "" for t in cache), default="")
    fresh = []
    # newest-first trimmed page; one page covers a normal sync gap. If the gap
    # exceeds PAGE records, advise a rebuild rather than paging the server.
    page = _get(f"{BASE}?limit={PAGE}" + ("&full=1" if full else ""))
    page.sort(key=lambda t: t.get("time") or "")
    overlap = any(_key(t) in known for t in page[:50]) or not cache
    for t in page:
        if _key(t) not in known:
            fresh.append(t)
    if not overlap and cache:
        print(f"WARNING: no overlap with cache (gap > {PAGE} records since "
              f"{newest_known[:16]}) — run --rebuild for a complete cache.")
    cache.extend(fresh)
    cache.sort(key=lambda t: t.get("time") or "")
    json.dump(cache, open(CACHE, "w"))
    print(f"synced: +{len(fresh)} new records | cache total {len(cache)} | "
          f"newest {max((t.get('time') or '' for t in cache), default='')[:19]}")


if __name__ == "__main__":
    main()
