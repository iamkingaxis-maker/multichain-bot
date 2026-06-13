"""
Web Dashboard
Real-time browser dashboard for monitoring the bot.
Access from any device on your network at http://localhost:8080

Serves a single-page dark-mode dashboard with:
  - Server-Sent Events for real-time push updates (no polling)
  - Cumulative P&L chart (Chart.js)
  - Open positions with hold time and progress bars
  - Strategy & chain breakdowns
  - Full trade history table with search
  - Security gate stats
  - Live event feed
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiohttp import web


@web.middleware
async def gzip_middleware(request, handler):
    """EGRESS CONTROL (2026-06-02): gzip sizeable text/JSON responses for clients that
    accept it. The dashboard /api/* payloads — especially /api/trades full=1&limit=5000
    (multi-MB), pulled by the dashboard auto-refresh, scheduled agents, and analysis —
    dominate Railway egress, and JSON gzips ~5-10x. TRANSPARENT to production: clients
    that don't send Accept-Encoding (e.g. plain urllib) are served uncompressed and
    unchanged; browsers / requests / curl_cffi accept + auto-decompress. SSE
    StreamResponses are skipped (not web.Response); tiny bodies (<500B) skip the overhead.
    """
    resp = await handler(request)
    try:
        if ("gzip" in (request.headers.get("Accept-Encoding") or "")
                and isinstance(resp, web.Response)
                and resp.body is not None
                and len(resp.body) > 500
                and not resp.headers.get("Content-Encoding")):
            resp.enable_compression(web.ContentCoding.gzip)
    except Exception:
        pass
    return resp


# ── EGRESS BURST CONTROL (2026-06-04) ──────────────────────────────────────────
# The HEAVY payloads (/api/trades full=1|all=1 ~69MB raw, /api/universe-recorder
# large limits) dominate Railway egress when hammered — e.g. a fan-out of analysis
# agents each pulling full=1 in a loop (the documented "agents inflate egress"
# failure mode). Normal dashboard polling uses the TRIMMED 200-record path and is
# never affected by this. This is a shared global budget: heavy serves are allowed
# at most once per MIN_INTERVAL and MAX_PER_HOUR total; beyond that, heavy requests
# are DOWNGRADED to the trimmed response (a 99%+ byte cut) rather than served raw.
# A single daily analyzer pull or an occasional manual audit always passes; only a
# runaway/looping consumer gets throttled.
_HEAVY_SERVES = []  # monotonic timestamps of recent heavy serves (rolling 1h)


def _egress_heavy_cfg():
    try:
        mi = float(os.environ.get("EGRESS_HEAVY_MIN_INTERVAL_SECS", "10"))
    except (TypeError, ValueError):
        mi = 10.0
    try:
        mph = int(os.environ.get("EGRESS_HEAVY_MAX_PER_HOUR", "20"))
    except (TypeError, ValueError):
        mph = 20
    return mi, mph


def _egress_allow_heavy():
    """Token-budget gate for heavy payloads. Returns True if a heavy response may be
    served now (and records it); False if the caller should DOWNGRADE to trimmed."""
    mi, mph = _egress_heavy_cfg()
    now = time.monotonic()
    cutoff = now - 3600
    while _HEAVY_SERVES and _HEAVY_SERVES[0] < cutoff:
        _HEAVY_SERVES.pop(0)
    if _HEAVY_SERVES and (now - _HEAVY_SERVES[-1]) < mi:
        return False
    if len(_HEAVY_SERVES) >= mph:
        return False
    _HEAVY_SERVES.append(now)
    return True


logger = logging.getLogger(__name__)

# ── HTML ─────────────────────────────────────────────────────────────────────

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Heisenberg | Memecoin Lab</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  /* ── Breaking Bad palette ──────────────────────────────────────
     Background: methylamine-dark with the slightest green tint.
     Accents drawn from the show's iconic visual language:
       - Heisenberg blue (the meth color) for primary accent
       - Toxic methylamine green for success / wins
       - Hazmat yellow for warnings
       - Blood red for stops / losses
       - Desert sand for muted text
  */
  :root {
    --bg:        #0a0d0c;
    --card:      #13181a;
    --border:    #2a3530;
    --border2:   #1a2120;
    --text:      #e8e3d8;
    --muted:     #8b8470;
    --accent:    #5cdcff;   /* Heisenberg blue */
    --green:     #5fae2c;   /* methylamine */
    --green-lt:  #7eff43;
    --red:       #d4351c;   /* blood */
    --yellow:    #ffcc00;   /* hazmat */
    --sol:       #b366ff;
  }

  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: ui-monospace, 'Cascadia Code', 'Courier New', monospace;
    font-size: 13px;
    min-height: 100vh;
  }

  /* ── Header ── */
  .header {
    background: var(--card);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .header h1 { font-size: 17px; color: var(--accent); letter-spacing: 0.5px; }
  /* Periodic-table-style element badge: tribute to the show's title cards */
  .heis-element {
    display: inline-flex; flex-direction: column; align-items: center;
    background: var(--card); border: 1px solid var(--green);
    border-radius: 4px; padding: 2px 6px; line-height: 1;
    color: var(--green-lt); margin-right: 8px;
    box-shadow: 0 0 8px #5fae2c30;
  }
  .heis-element .num { font-size: 8px; opacity: .7; }
  .heis-element .sym { font-size: 13px; font-weight: 800; letter-spacing: 0; }
  .status-pill {
    display: flex; align-items: center; gap: 6px;
    background: #1c2128; border: 1px solid var(--border2);
    border-radius: 20px; padding: 4px 12px; font-size: 11px; color: var(--muted);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green); flex-shrink: 0;
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1;box-shadow:0 0 0 0 #2ea04360} 50%{opacity:.7;box-shadow:0 0 0 4px #2ea04310} }
  .header-right { display: flex; align-items: center; gap: 16px; color: var(--muted); font-size: 11px; }
  #clock { color: var(--text); font-size: 12px; }
  .mode-badge {
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
    padding: 3px 10px; border-radius: 20px; text-transform: uppercase;
  }
  .mode-badge.paper { background: #1a2a1a; color: var(--green-lt); border: 1px solid #5fae2c50; }
  .mode-badge.live  { background: #2e1414; color: var(--red); border: 1px solid #d4351c60; }
  .sol-gate-badge {
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
    border-radius: 4px;
    letter-spacing: 0.3px;
    cursor: default;
  }
  .sol-gate-badge.pass  { background: #1a2a1a; color: var(--green-lt); border: 1px solid #5fae2c50; }
  .sol-gate-badge.block { background: #2e1414; color: var(--red); border: 1px solid #d4351c60; }
  .sol-gate-badge.stale { background: #2a261a; color: #d4a017; border: 1px solid #d4a01750; }
  .pause-btn {
    font-size: 11px; font-weight: 600; padding: 4px 14px; border-radius: 6px;
    border: 1px solid var(--border2); cursor: pointer; transition: all .15s;
    background: var(--card); color: var(--text);
  }
  .pause-btn:hover { background: var(--border2); }
  .pause-btn.paused { background: #3d1f1f; color: #f85149; border-color: #f8514940; }

  /* ── Layout ── */
  .main { padding: 20px 20px 40px; display: flex; flex-direction: column; gap: 20px; max-width: 1800px; margin: 0 auto; }

  /* ── Cards ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px 20px;
    overflow: hidden;
  }
  .card-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.2px;
    color: var(--muted);
    margin-bottom: 14px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .card-title .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }

  /* ── Stat Cards Row ── */
  .stat-row {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 16px;
  }
  @media (max-width: 900px) { .stat-row { grid-template-columns: repeat(2, 1fr); } }
  @media (max-width: 500px) { .stat-row { grid-template-columns: 1fr; } }

  .stat-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px;
  }
  .stat-card .label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 6px; }
  .stat-card .value { font-size: 26px; font-weight: 700; line-height: 1.1; }
  .stat-card .sub { font-size: 11px; color: var(--muted); margin-top: 4px; }

  /* ── P&L Chart ── */
  .chart-wrap { position: relative; height: 220px; }

  /* ── Two-column layout ── */
  .two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  @media (max-width: 800px) { .two-col { grid-template-columns: 1fr; } }

  /* ── Three-column layout ── */
  .three-col {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 16px;
  }
  @media (max-width: 700px) { .three-col { grid-template-columns: 1fr; } }

  /* ── Tables ── */
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; padding: 8px 10px;
    font-size: 10px; text-transform: uppercase; letter-spacing: 1px;
    color: var(--muted); border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td { padding: 8px 10px; border-bottom: 1px solid var(--border2); vertical-align: middle; font-size: 12px; }
  tr:last-child td { border-bottom: none; }
  tr.row-win td:last-child { color: var(--green-lt); }
  tr.row-loss td:last-child { color: var(--red); }
  tr.row-win { background: #2ea04308; }
  tr.row-loss { background: #f8514908; }

  /* ── Badges ── */
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 10px; font-weight: 700; letter-spacing: 0.5px; white-space: nowrap;
  }
  .badge-sol  { background: #9945ff22; color: var(--sol); }
  .badge-scanner { background: #58a6ff22; color: var(--accent); }
  .badge-copy    { background: #2ea04322; color: var(--green-lt); }
  .badge-scalper { background: #d2992222; color: var(--yellow); }
  .badge-pump    { background: #ff6b3522; color: #ff6b35; }

  /* ── Progress bar ── */
  .progress-wrap { width: 80px; height: 5px; background: var(--border); border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; }
  .progress-fill { height: 100%; border-radius: 3px; background: var(--green); transition: width 0.4s; }

  /* ── Event Feed ── */
  #event-feed {
    height: 280px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 1px;
  }
  #event-feed::-webkit-scrollbar { width: 4px; }
  #event-feed::-webkit-scrollbar-track { background: transparent; }
  #event-feed::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .feed-item {
    padding: 6px 8px; border-radius: 5px; font-size: 11px;
    display: flex; gap: 8px; align-items: flex-start;
    border-left: 2px solid transparent;
  }
  .feed-buy  { border-color: var(--green);  background: #2ea04310; }
  .feed-sell { border-color: var(--red);    background: #f8514910; }
  .feed-sig  { border-color: var(--yellow); background: #d2992210; }
  .feed-info { border-color: var(--accent); background: #58a6ff10; }
  .feed-time { color: var(--muted); white-space: nowrap; font-size: 10px; flex-shrink: 0; }
  .feed-msg  { color: var(--text); flex: 1; word-break: break-word; }

  /* ── Strategy + Chain breakdown cards ── */
  .breakdown-card { padding: 16px 18px; }
  .breakdown-card .card-label { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 2px; }
  .breakdown-card .card-name { font-size: 15px; font-weight: 700; margin-bottom: 12px; }
  .breakdown-card .stat-line { display: flex; justify-content: space-between; padding: 5px 0; border-bottom: 1px solid var(--border2); font-size: 12px; }
  .breakdown-card .stat-line:last-child { border-bottom: none; }
  /* Full-screen tab views: when a tab is open, hide all other top-level content so
     the tab renders alone at the top. (Scrolling can't reach the tab — its content
     is shorter than the viewport, so it can never reach the top of a long page.) */
  body.psweep-open > *:not(#profitsweep-tab):not(script):not(style) { display: none !important; }
  body.attr-open > *:not(#attribution-tab):not(script):not(style) { display: none !important; }
  .breakdown-card .stat-line .k { color: var(--muted); }

  /* ── Active Strategies panel ── */
  .strategies-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 14px;
  }
  .strat-status-card {
    background: #1c2128;
    border: 1px solid var(--border2);
    border-radius: 8px;
    padding: 14px 16px;
  }
  .strat-status-card .strat-header {
    display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
  }
  .strat-status-card .strat-dot {
    width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0;
  }
  .strat-status-card .strat-name {
    font-size: 13px; font-weight: 700; flex: 1;
  }
  .strat-status-card .strat-badge {
    font-size: 10px; font-weight: 700; letter-spacing: 0.5px;
    padding: 2px 7px; border-radius: 8px;
  }
  .strat-status-card .strat-stat {
    display: flex; justify-content: space-between;
    font-size: 11px; padding: 3px 0;
    border-bottom: 1px solid #21262d;
  }
  .strat-status-card .strat-stat:last-child { border-bottom: none; }
  .strat-status-card .strat-stat .sk { color: var(--muted); }
  .badge-running { background: #2ea04322; color: var(--green-lt); }
  .badge-stopped { background: #f8514920; color: var(--red); }

  /* ── Trade History filter ── */
  .filter-row { display: flex; gap: 10px; margin-bottom: 14px; align-items: center; flex-wrap: wrap; }
  .filter-input {
    flex: 1; min-width: 180px;
    background: #1c2128; border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 12px; padding: 6px 12px; outline: none;
    font-family: inherit;
  }
  .filter-input:focus { border-color: var(--accent); }
  .filter-select {
    background: #1c2128; border: 1px solid var(--border); border-radius: 6px;
    color: var(--muted); font-size: 12px; padding: 6px 10px; outline: none;
    font-family: inherit;
  }

  /* ── Security card ── */
  .sec-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .sec-item { padding: 10px; background: #1c2128; border-radius: 6px; }
  .sec-item .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; margin-bottom: 3px; }
  .sec-item .v { font-size: 18px; font-weight: 700; }

  /* ── Utility ── */
  .green  { color: var(--green-lt); }
  .red    { color: var(--red); }
  .yellow { color: var(--yellow); }
  .blue   { color: var(--accent); }
  .muted  { color: var(--muted); }
  .empty  { color: var(--muted); text-align: center; padding: 28px 0; font-size: 12px; }

  /* hold time bar color override */
  .progress-fill.tp1 { background: var(--yellow); }
  .progress-fill.tp2 { background: #f0883e; }
  .progress-fill.tp3 { background: var(--green); }

  /* ── Mobile (≤ 600px) ─────────────────────────────────────────────
     The Breaking Bad header has a lot of items; on phone widths it
     was wrapping awkwardly and pushing content off-screen. This block
     stacks the header, shrinks badges/fonts, tightens card padding,
     and fixes a few grid minmaxes that were wider than phone screens. */
  @media (max-width: 600px) {
    body { font-size: 12px; }
    .header {
      flex-direction: column; align-items: stretch;
      gap: 8px; padding: 10px 12px;
    }
    .header-left { flex-wrap: wrap; gap: 8px; }
    .header h1 { font-size: 14px; line-height: 1.2; }
    .heis-element { padding: 1px 4px; margin-right: 4px; }
    .heis-element .num { font-size: 7px; }
    .heis-element .sym { font-size: 11px; }
    .header-right {
      flex-wrap: wrap; gap: 8px 10px;
      font-size: 10px;
    }
    .header-right > span:first-of-type { /* uptime label can hide */
      display: none;
    }
    #clock { font-size: 11px; }
    .pause-btn { padding: 3px 10px; font-size: 10px; }
    .mode-badge { padding: 2px 8px; font-size: 10px; }

    .main { padding: 12px 10px 28px; gap: 12px; }
    .card { padding: 12px 12px; border-radius: 8px; }
    .card-title { font-size: 10px; margin-bottom: 10px; }

    .stat-card { padding: 10px 12px; }
    .stat-card .label { font-size: 10px; margin-bottom: 4px; }
    .stat-card .value { font-size: 19px; }
    .stat-card .sub { font-size: 10px; }

    .chart-wrap { height: 180px; }

    .strategies-grid { grid-template-columns: 1fr; gap: 10px; }
    .strat-status-card { padding: 10px 12px; }

    .sec-grid { grid-template-columns: 1fr 1fr; gap: 6px; }
    .sec-item { padding: 8px; }
    .sec-item .v { font-size: 15px; }

    /* Tables: keep horizontal scroll, but tighten padding */
    th, td { padding: 6px 6px; font-size: 11px; }
    th { font-size: 9px; }

    .filter-row { gap: 6px; margin-bottom: 10px; }
    .filter-input, .filter-select { font-size: 11px; padding: 5px 8px; }

    #event-feed { height: 240px; }
    .feed-item { font-size: 10px; padding: 5px 6px; }

    .breakdown-card { padding: 12px 14px; }
    .breakdown-card .card-name { font-size: 13px; }
    .breakdown-card .stat-line { font-size: 11px; }
  }

  /* Very narrow (≤ 380px — older/smaller phones) */
  @media (max-width: 380px) {
    .header h1 { font-size: 12px; }
    /* Drop the second periodic-element badge; keep the first as accent */
    .heis-element + .heis-element { display: none; }
    .stat-card .value { font-size: 17px; }
    .sec-grid { grid-template-columns: 1fr; }
  }

  /* ── ATTRIBUTION tab ── */
  .attr-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
  .attr-panel { background: #1a1a1a; padding: 1rem; border-radius: 8px; border: 1px solid var(--border); }
  .attr-panel h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin-bottom: 10px; }
  .attr-panel table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  .attr-panel th, .attr-panel td { padding: 0.3rem 0.5rem; text-align: right; border-bottom: 1px solid var(--border2); }
  .attr-panel th:first-child, .attr-panel td:first-child { text-align: left; }
  .attr-panel th { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
  .attr-panel tbody tr:last-child td { border-bottom: none; }
  .attr-panel tbody tr:nth-child(odd) { background: #222; }
  .attr-panel .pnl-pos { color: #4caf50; }
  .attr-panel .pnl-neg { color: #f44336; }
  #champion-preview {
    background: #111; padding: 0.5rem; max-height: 400px; overflow: auto;
    font-size: 0.75rem; white-space: pre-wrap; border-radius: 4px;
  }
  @media (max-width: 600px) {
    .attr-grid { grid-template-columns: 1fr; }
  }

  /* ── FLEET panel ── */
  .fleet-panel { background: #1a1a1a; padding: 1rem; margin: 1rem 0; border-radius: 8px; border: 1px solid var(--border); }
  .fleet-panel h2 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin-bottom: 10px; }
  .fleet-panel table { width: 100%; border-collapse: collapse; }
  .fleet-panel th, .fleet-panel td { padding: 0.4rem 0.6rem; text-align: right; font-size: 12px; border-bottom: 1px solid var(--border2); }
  .fleet-panel th { font-size: 10px; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); }
  .fleet-panel th:first-child, .fleet-panel td:first-child { text-align: left; }
  .fleet-panel tbody tr:last-child td { border-bottom: none; }
  .fleet-panel tbody tr:nth-child(odd) { background: #222; }
  .fleet-panel .pnl-pos { color: #4caf50; }
  .fleet-panel .pnl-neg { color: #f44336; }
</style>
</head>
<body>

<!-- ── Header ── -->
<div class="header">
  <div class="header-left">
    <h1>
      <span class="heis-element"><span class="num">35</span><span class="sym">Br</span></span><span class="heis-element"><span class="num">56</span><span class="sym">Ba</span></span>Heisenberg &mdash; Memecoin Lab
    </h1>
    <div class="status-pill">
      <span class="status-dot" id="status-dot"></span>
      <span id="status-text">Connecting...</span>
    </div>
  </div>
  <div class="header-right">
    <span id="mode-badge" class="mode-badge paper">PAPER</span>
    <span id="sol-gate-badge" class="sol-gate-badge pass" title="SOL gate status">SOL: —</span>
    <button id="pause-btn" class="pause-btn" onclick="togglePause()">⏸ Pause Trading</button>
    <span>Uptime: <span id="uptime">—</span></span>
    <span id="clock">—</span>
  </div>
</div>

<!-- ── PROFIT SECURED banner (always visible, shadow sim) ── -->
<div class="main" id="psweep-banner" style="border:1px solid #2e7d32;border-radius:8px;background:rgba(76,175,80,0.06);">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:0.5rem;">
    <h2 style="margin:0;">&#128176; PROFIT SECURED <span style="font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;">simulated high-water-mark sweep &middot; nothing moved</span></h2>
    <a href="#profitsweep" style="color:#4caf50;font-size:11px;letter-spacing:1px;">full breakdown &amp; per-bot &#9654;</a>
  </div>
  <div id="psweep-banner-totals" style="font-size:14px;margin:0.4rem 0;">Loading&hellip;</div>
  <table id="psweep-banner-table" style="font-size:12px;">
    <thead>
      <tr><th>Top banked bot</th><th>realized now</th><th>peak</th><th>HWM-50 secured</th></tr>
    </thead>
    <tbody></tbody>
  </table>
</div>

<div class="main" id="bot-reset-box" style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap;">
  <span style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;">Re-baseline a bot</span>
  <input id="reset-bot-id" placeholder="bot_id (e.g. champion_defender_v3)" style="font-size:12px;padding:3px 6px;width:240px;background:#111;color:#ddd;border:1px solid #333;border-radius:4px;"/>
  <button onclick="resetBot()" style="font-size:11px;padding:3px 10px;background:#5a1e1e;color:#f0c0c0;border:1px solid #7a2e2e;border-radius:4px;cursor:pointer;">Flatten + zero ledger</button>
  <span id="reset-result" style="font-size:11px;color:var(--muted);"></span>
</div>

<div class="main">

  <!-- ── DAILY GOAL Panel (the question this page answers first) ── -->
  <div class="fleet-panel" id="goal-panel">
    <h2>DAILY GOAL — $100 CLOSED P&amp;L, WALK-FORWARD LIVE SET (bots already profitable before today; CT day)</h2>
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <div style="font-size:28px;font-weight:700;" id="goal-today">$0</div>
      <div style="flex:1;min-width:200px;background:#222;border-radius:6px;height:14px;overflow:hidden;">
        <div id="goal-bar" style="height:100%;width:0%;background:#f44336;transition:width .5s;"></div>
      </div>
      <div id="goal-badge" style="font-size:12px;font-weight:700;letter-spacing:1px;">&nbsp;</div>
    </div>
    <div id="goal-contrib" style="font-size:11px;color:#888;margin-top:6px;"></div>
    <div id="goal-history" style="font-size:11px;color:#888;margin-top:4px;"></div>
  </div>

  <!-- ── GOAL CANDIDATES (the bots racing toward live) ── -->
  <div class="fleet-panel">
    <h2>GOAL CANDIDATES</h2>
    <table id="cand-table">
      <thead>
        <tr>
          <th>Bot</th>
          <th>Balance</th>
          <th>Open</th>
          <th>Trades</th>
          <th>WR</th>
          <th>P&amp;L</th>
          <th>$/tr</th>
          <th>Tput &times; $/tr</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>

  <!-- ── EXPERIMENTS (selection instrument — judged individually, collapsed) ── -->
  <details class="fleet-panel" id="experiments-panel">
    <summary style="cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:#888;">
      Experiments (<span id="exp-count">0</span>) — selection instrument, not a portfolio</summary>
    <table id="fleet-table" style="margin-top:10px;">
      <thead>
        <tr>
          <th>Bot</th>
          <th>Balance</th>
          <th>Open</th>
          <th>Trades</th>
          <th>WR</th>
          <th>P&amp;L</th>
          <th>$/tr</th>
          <th>Tput &times; $/tr</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </details>

  <!-- ── Top Stat Cards ── -->
  <div class="stat-row">
    <div class="stat-card">
      <div class="label">Total P&amp;L</div>
      <div class="value" id="sc-total-pnl">$0.00</div>
      <div class="sub" id="sc-total-pnl-sub">all time</div>
      <div class="sub" id="sc-total-pnl-live" style="font-size:10px;opacity:0.7;">live est: $0.00</div>
    </div>
    <div class="stat-card">
      <div class="label">Daily P&amp;L</div>
      <div class="value" id="sc-daily-pnl">$0.00</div>
      <div class="sub">today (UTC)</div>
    </div>
    <div class="stat-card">
      <div class="label">Win Rate</div>
      <div class="value" id="sc-win-rate">0%</div>
      <div class="sub" id="sc-wr-sub">0 wins / 0 trades</div>
    </div>
    <div class="stat-card">
      <div class="label">Total Trades</div>
      <div class="value" id="sc-trades">0</div>
      <div class="sub" id="sc-trades-sub">0 open</div>
    </div>
    <div class="stat-card">
      <div class="label">Account Balance</div>
      <div class="value" id="sc-balance">$2,000</div>
      <div class="sub" id="sc-balance-sub">$0 deployed &bull; $2,000 available</div>
    </div>
    <div class="stat-card">
      <div class="label">Max Drawdown</div>
      <div class="value" id="sc-max-dd">$0.00</div>
      <div class="sub" id="sc-max-dd-sub">0.0% from peak</div>
    </div>
    <div class="stat-card">
      <div class="label">Slippage Cost</div>
      <div class="value" id="sc-slippage">$0.00</div>
      <div class="sub" id="sc-slippage-sub">avg 0.00% per trade</div>
    </div>
    <div class="stat-card">
      <div class="label">DEX WS</div>
      <div class="value" id="sc-dex-ws" style="font-size:18px;">—</div>
      <div class="sub" id="sc-dex-ws-sub">checking...</div>
    </div>
  </div>

  <!-- ── P&L Chart ── -->
  <div class="card">
    <div class="card-title"><span class="dot"></span> Cumulative P&amp;L</div>
    <div class="chart-wrap">
      <canvas id="pnl-chart"></canvas>
    </div>
  </div>

  <!-- ── Positions + Feed ── -->
  <div class="two-col">

    <!-- Open Positions -->
    <div class="card">
      <div class="card-title"><span class="dot" style="background:var(--green)"></span> Open Positions — Smart Wallet</div>
      <div class="tbl-wrap">
        <table id="positions-table">
          <thead>
            <tr>
              <th>Token</th><th>Chain</th><th>Strategy</th>
              <th>Entry</th><th>Size</th><th>Unrealized</th><th>Hold</th><th>TP</th><th></th>
            </tr>
          </thead>
          <tbody id="positions-body">
            <tr><td colspan="8" class="empty">No open positions</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Live Event Feed -->
    <div class="card">
      <div class="card-title"><span class="dot" style="background:var(--yellow)"></span> Live Event Feed</div>
      <div id="event-feed">
        <div class="feed-item feed-info">
          <span class="feed-time">—</span>
          <span class="feed-msg">Waiting for events...</span>
        </div>
      </div>
    </div>

  </div>

  <!-- ── Seed Wallets ── -->
  <div class="card">
    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
      <span><span class="dot" style="background:#34d399"></span> Copy Wallets <span style="font-size:11px;color:var(--muted);font-weight:400;">— add/remove live</span></span>
      <span id="seed-wallet-count" style="font-size:11px;color:var(--muted);font-weight:400;">0 wallets</span>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input id="seed-wallet-address" type="text" placeholder="Solana wallet address…"
        style="flex:2;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px;outline:none;" />
      <input id="seed-wallet-score" type="number" min="0" max="100" value="75" placeholder="Score"
        style="width:70px;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px;outline:none;" />
      <button onclick="addSeedWallet()"
        style="background:#34d399;color:#0f172a;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;font-weight:700;white-space:nowrap;">+ Add</button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Wallet</th><th>Quality</th><th>Solscan</th><th></th></tr></thead>
        <tbody id="seed-wallets-body">
          <tr><td colspan="4" style="color:var(--muted);padding:12px;text-align:center;">No seed wallets yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── User Watchlist (Curator-Driven) ── -->
  <div class="card">
    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
      <span><span class="dot" style="background:#22c55e"></span> My Watchlist <span style="font-size:11px;color:#22c55e;font-weight:400;">— curator-picked tokens. Bypasses 8 buying-high filters. Hot-reload, no restart.</span></span>
      <span id="user-watchlist-count" style="font-size:11px;color:var(--muted);font-weight:400;">0 tokens</span>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input id="user-watchlist-add-address" type="text" placeholder="Token address (solana)…"
        style="flex:1;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px;outline:none;" />
      <button onclick="addUserWatchlist()"
        style="background:#22c55e;color:#0f172a;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;font-weight:700;white-space:nowrap;">+ Add to Watchlist</button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Token</th><th>MCap</th><th>Vol h1</th><th>24h</th><th>1h</th><th>5m</th><th>Liq</th><th></th></tr></thead>
        <tbody id="user-watchlist-body">
          <tr><td colspan="8" style="color:var(--muted);padding:12px;text-align:center;">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- Near-Miss Watchlist removed — replaced by My Watchlist (curator-driven). -->

  <!-- ── Strategy Breakdown ── -->
  <div class="three-col">
    <div class="card breakdown-card" id="strat-scanner">
      <div class="card-label">Strategy</div>
      <div class="card-name" style="color:var(--accent)">Scanner</div>
      <div class="stat-line"><span class="k">Trades</span><span id="st-sc-trades">0</span></div>
      <div class="stat-line"><span class="k">Win Rate</span><span id="st-sc-wr">0%</span></div>
      <div class="stat-line"><span class="k">Total P&amp;L</span><span id="st-sc-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Win</span><span id="st-sc-avgwin" class="green">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Loss</span><span id="st-sc-avgloss" class="red">$0.00</span></div>
    </div>
    <div class="card breakdown-card" id="strat-copy">
      <div class="card-label">Strategy</div>
      <div class="card-name" style="color:var(--green-lt)">Copy Trader</div>
      <div class="stat-line"><span class="k">Trades</span><span id="st-cp-trades">0</span></div>
      <div class="stat-line"><span class="k">Win Rate</span><span id="st-cp-wr">0%</span></div>
      <div class="stat-line"><span class="k">Total P&amp;L</span><span id="st-cp-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Win</span><span id="st-cp-avgwin" class="green">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Loss</span><span id="st-cp-avgloss" class="red">$0.00</span></div>
    </div>
    <div class="card breakdown-card" id="strat-scalper">
      <div class="card-label">Strategy</div>
      <div class="card-name" style="color:var(--yellow)">Scalper</div>
      <div class="stat-line"><span class="k">Trades</span><span id="st-sk-trades">0</span></div>
      <div class="stat-line"><span class="k">Win Rate</span><span id="st-sk-wr">0%</span></div>
      <div class="stat-line"><span class="k">Total P&amp;L</span><span id="st-sk-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Win</span><span id="st-sk-avgwin" class="green">$0.00</span></div>
      <div class="stat-line"><span class="k">Avg Loss</span><span id="st-sk-avgloss" class="red">$0.00</span></div>
    </div>
  </div>

  <!-- ── Active Strategies ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:#a78bfa"></span> Active Strategies</div>
    <div class="strategies-grid" id="active-strategies-grid">
      <div class="empty">Loading strategy status...</div>
    </div>
  </div>

  <!-- Chain Breakdown removed — single chain, info in top stat cards. -->
  <!-- Scalp Queue removed — was display:none, dead code. -->

  <!-- ── MC Recommendations ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:#a78bfa"></span> Micro-Cap Radar <span style="font-size:10px;opacity:0.5;margin-left:6px">seen but not bought</span></div>
    <div class="tbl-wrap">
      <table id="mc-rec-table">
        <thead><tr>
          <th>Time</th><th>Token</th><th>MCap</th><th>Liq</th>
          <th>Dev%</th><th>Snipers%</th><th>LP</th><th>Reason</th>
        </tr></thead>
        <tbody id="mc-rec-body"><tr><td colspan="9" style="text-align:center;opacity:0.4">Loading...</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- ── Trade History ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--yellow)"></span> Trade History</div>
    <div class="filter-row">
      <input class="filter-input" id="trade-search" placeholder="Search token, reason, chain..." oninput="filterTrades()">
      <select class="filter-select" id="trade-chain-filter" onchange="filterTrades()">
        <option value="">All Chains</option>
        <option value="sol">Solana</option>
      </select>
      <select class="filter-select" id="trade-strat-filter" onchange="filterTrades()">
        <option value="">All Strategies</option>
        <option value="scanner">Scanner</option>
        <option value="copy">Copy</option>
        <option value="scalper">Scalper</option>
      </select>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Token</th><th>Chain</th><th>Strategy</th>
            <th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>P&amp;L %</th><th>Reason</th>
          </tr>
        </thead>
        <tbody id="trade-history-body">
          <tr><td colspan="9" class="empty">No completed trades yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── Security Gate ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--red)"></span> Security Gate</div>
    <div class="sec-grid">
      <div class="sec-item">
        <div class="k">Total Checks</div>
        <div class="v blue" id="sec-total">0</div>
      </div>
      <div class="sec-item">
        <div class="k">Honeypots Blocked</div>
        <div class="v red" id="sec-honeypot">0</div>
      </div>
      <div class="sec-item">
        <div class="k">Tax Blocks</div>
        <div class="v yellow" id="sec-tax">0</div>
      </div>
      <div class="sec-item">
        <div class="k">Block Rate</div>
        <div class="v red" id="sec-rate">0%</div>
      </div>
      <div class="sec-item">
        <div class="k">Cache Size</div>
        <div class="v muted" id="sec-cache">0</div>
      </div>
      <div class="sec-item">
        <div class="k">Price Feed Ticks</div>
        <div class="v green" id="feed-ticks">0</div>
      </div>
    </div>
  </div>


</div><!-- /main -->

<!-- ── ATTRIBUTION Tab (lazy — visible only when URL hash = #attribution) ── -->
<div class="main" id="attribution-tab" style="display:none;">
  <div style="display:flex;align-items:center;gap:1rem;margin-bottom:0.5rem;">
    <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);margin:0;">ATTRIBUTION</h2>
    <a href="#" onclick="history.pushState('','',location.pathname);document.body.classList.remove('attr-open');document.getElementById('attribution-tab').style.display='none';return false;"
       style="font-size:11px;color:var(--muted);text-decoration:none;">&larr; close</a>
  </div>
  <div class="attr-grid">
    <div class="attr-panel">
      <h3>Filter Attribution</h3>
      <table id="attr-filters-table">
        <thead>
          <tr><th>Filter</th><th>Baseline n</th><th>Ablation n</th><th>&Delta; $/tr</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="attr-panel">
      <h3>Category Attribution</h3>
      <table id="attr-categories-table">
        <thead>
          <tr><th>Category</th><th>Baseline n</th><th>Ablation n</th><th>&Delta; $/tr</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div class="attr-panel" style="grid-column:1/-1;">
      <h3>Champion Preview</h3>
      <pre id="champion-preview">Loading&hellip;</pre>
    </div>
  </div>
</div>

<p style="text-align:center;margin:0.5rem 0 1.5rem;">
  <a href="#attribution" id="attribution-tab-link" style="color:#4caf50;font-size:12px;letter-spacing:1px;">&#9654; Open ATTRIBUTION tab</a>
</p>

<!-- ── PROFIT-SWEEP SIM Tab (shadow; visible only when URL hash = #profitsweep) ── -->
<div class="main" id="profitsweep-tab" style="display:none;">
  <div style="display:flex;align-items:center;gap:1rem;margin-bottom:0.5rem;">
    <h2 style="font-size:11px;text-transform:uppercase;letter-spacing:1.2px;color:var(--muted);margin:0;">PROFIT-SWEEP SIM (shadow — nothing is moved)</h2>
    <a href="#" onclick="history.pushState('','',location.pathname);document.body.classList.remove('psweep-open');document.getElementById('profitsweep-tab').style.display='none';return false;"
       style="font-size:11px;color:var(--muted);text-decoration:none;">&larr; close</a>
  </div>
  <p style="font-size:11px;color:var(--muted);margin:0 0 0.6rem;">
    Simulated profit banked to the cold wallet by replaying each bot's realized-P&amp;L curve.
    HWM = bank that % of every new profit high-water mark. Step = bank 50% per +25% of base position.
    Display-only: touches no capital, distorts no metric.
  </p>
  <div id="psweep-totals" style="font-size:13px;margin-bottom:0.5rem;"></div>
  <table id="psweep-table">
    <thead>
      <tr><th>Bot</th><th>realized now</th><th>realized peak</th>
          <th>HWM-50</th><th>HWM-100</th><th>Step+25%</th><th>at-risk (HWM-50)</th></tr>
    </thead>
    <tbody></tbody>
  </table>
</div>

<p style="text-align:center;margin:0.5rem 0 1.5rem;">
  <a href="#profitsweep" id="profitsweep-tab-link" style="color:#4caf50;font-size:12px;letter-spacing:1px;">&#9654; Open PROFIT-SWEEP SIM</a>
</p>

<script>
// ── State ──────────────────────────────────────────────────────────────────
let allTrades = [];
let feedLog   = [];
let pnlChart  = null;
let connected = false;
let startTime = Date.now();

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}
setInterval(updateClock, 1000);
updateClock();

// ── Chart setup ────────────────────────────────────────────────────────────
(function initChart() {
  const ctx = document.getElementById('pnl-chart').getContext('2d');
  pnlChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Cumulative P&L ($)',
        data: [],
        borderColor: '#3fb950',
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 3,
        pointBackgroundColor: '#3fb950',
        tension: 0.3,
        fill: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 300 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#161b22',
          borderColor: '#30363d',
          borderWidth: 1,
          titleColor: '#8b949e',
          bodyColor: '#e6edf3',
          callbacks: {
            label: ctx => ' $' + ctx.parsed.y.toFixed(2)
          }
        }
      },
      scales: {
        x: {
          grid: { color: '#21262d' },
          ticks: { color: '#8b949e', maxTicksLimit: 12, font: { size: 10 } }
        },
        y: {
          grid: { color: '#21262d' },
          ticks: {
            color: '#8b949e', font: { size: 10 },
            callback: v => '$' + v.toFixed(0)
          }
        }
      }
    }
  });
})();

// ── Formatting helpers ─────────────────────────────────────────────────────
function fmtUsd(v) {
  const n = parseFloat(v) || 0;
  const sign = n >= 0 ? '+' : '-';
  return sign + '$' + Math.abs(n).toFixed(2);
}
function pnlClass(v) { return parseFloat(v) >= 0 ? 'green' : 'red'; }
function fmtPct(v) {
  const n = parseFloat(v) || 0;
  return (n >= 0 ? '+' : '') + n.toFixed(1) + '%';
}
function fmtHold(secs) {
  if (!secs) return '—';
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso.slice(11,16); }
}
function chainBadge(chain) {
  const c = (chain||'').toLowerCase();
  if (c === 'solana' || c === 'sol') return '<span class="badge badge-sol">SOL</span>';
  return '<span class="badge">' + (chain||'?').toUpperCase() + '</span>';
}
function stratBadge(s) {
  const st = (s||'scanner').toLowerCase();
  if (st === 'copy')    return '<span class="badge badge-copy">COPY</span>';
  if (st === 'scalper') return '<span class="badge badge-scalper">SCALP</span>';
  if (st === 'pump')    return '<span class="badge badge-pump">PUMP</span>';
  return '<span class="badge badge-scanner">SCAN</span>';
}

// ── SSE connection ─────────────────────────────────────────────────────────
function connect() {
  const es = new EventSource('/events');

  es.onmessage = function(e) {
    connected = true;
    document.getElementById('status-dot').style.background = '#2ea043';
    document.getElementById('status-text').textContent = 'Live';
    try {
      const data = JSON.parse(e.data);
      updateDashboard(data);
    } catch(err) {
      console.warn('SSE parse error', err);
    }
  };

  es.onerror = function() {
    connected = false;
    document.getElementById('status-dot').style.background = '#f85149';
    document.getElementById('status-text').textContent = 'Reconnecting...';
    es.close();
    setTimeout(connect, 3000);
  };
}
connect();

// ── Main update function ───────────────────────────────────────────────────
function updateDashboard(d) {
  updateUptime(d.uptime);
  updateModeAndPause(d);
  updateStatCards(d);
  updatePnlChart(d.cumulative_pnl || []);
  updatePositions(d.positions || []);
  updateFeed(d.new_alerts || []);
  updateStrategies(d.strategies || {});
  updateActiveStrategies(d.active_strategies || {});
  updateChains(d.chains || {});
  updateSecurity(d.security || {}, d.price_feed || {});
  updateScalpQueue(d.scalp_queue || {});

  // Trade history is loaded separately from /api/trades (see loadTradeHistory below).
}

async function loadTradeHistory() {
  try {
    const res = await fetch('/api/trades');
    if (!res.ok) return;
    const trades = await res.json();
    if (Array.isArray(trades)) {
      allTrades = trades;
      filterTrades();
    }
  } catch (e) { console.warn('trade history fetch failed', e); }
}
loadTradeHistory();
setInterval(loadTradeHistory, 120000);  // 2026-05-18 raised 30s→120s (egress)

function updateUptime(u) {
  if (u) document.getElementById('uptime').textContent = u;
}

// ── Mode badge + pause button ───────────────────────────────────────────────
function updateModeAndPause(d) {
  const badge = document.getElementById('mode-badge');
  const btn   = document.getElementById('pause-btn');
  if (badge) {
    const live = d.live_mode === true;
    badge.textContent = live ? 'LIVE' : 'PAPER';
    badge.className   = 'mode-badge ' + (live ? 'live' : 'paper');
  }
  if (btn) {
    const paused = d.trading_paused === true;
    btn.textContent = paused ? '▶ Resume Trading' : '⏸ Pause Trading';
    btn.className   = 'pause-btn' + (paused ? ' paused' : '');
  }
}

async function togglePause() {
  const btn    = document.getElementById('pause-btn');
  const paused = btn.classList.contains('paused');
  const url    = paused ? '/api/resume' : '/api/pause';
  try {
    await fetch(url, { method: 'POST' });
  } catch(e) { console.error('pause/resume error', e); }
}

// ── SOL gate indicator ──────────────────────────────────────────────────
async function updateSolGate() {
  const badge = document.getElementById('sol-gate-badge');
  if (!badge) return;
  try {
    const res = await fetch('/api/sol-gate');
    const d = await res.json();
    if (!d.has_data) {
      badge.textContent = 'SOL: —';
      badge.className = 'sol-gate-badge stale';
      badge.title = 'No SOL data yet (scanner warming up)';
      return;
    }
    const stale = (d.snapshot_age_secs !== null && d.snapshot_age_secs > 600);
    const status = d.status || 'PASS';
    const h6 = d.sol_pc_h6;
    const h1 = d.sol_pc_h1;
    const h24 = d.sol_pc_h24;
    const fmt = (v) => (v === null || v === undefined) ? '—' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%';
    if (status === 'BLOCK') {
      badge.textContent = '🚫 SOL: BLOCK';
      badge.className = 'sol-gate-badge block';
      badge.title = 'Trading paused by filter_sol_macro_down\\n' +
        (d.reasons || []).join('\\n') +
        '\\n\\nh1=' + fmt(h1) + '  h6=' + fmt(h6) + '  h24=' + fmt(h24);
    } else {
      badge.textContent = 'SOL: ' + fmt(h24) + ' (24h)';
      badge.className = 'sol-gate-badge ' + (stale ? 'stale' : 'pass');
      badge.title = 'filter_sol_macro_down: PASS — bot allowed to trade\\n' +
        'h1=' + fmt(h1) + '  h6=' + fmt(h6) + '  h24=' + fmt(h24) +
        '\\nThresholds: h6<-0.3 OR h1<-0.7 = BLOCK' +
        (stale ? '\\n(WARN: data ' + Math.round(d.snapshot_age_secs/60) + 'min stale)' : '');
    }
  } catch(e) {
    badge.textContent = 'SOL: err';
    badge.className = 'sol-gate-badge stale';
  }
}
// Poll every 30s + once immediately
updateSolGate();
setInterval(updateSolGate, 60000);  // 2026-06-04 30s->60s (egress)

// ── Stat cards ─────────────────────────────────────────────────────────────
function updateStatCards(d) {
  const ov = d.overall || {};
  const pnl = ov.total_pnl || 0;
  const daily = d.daily_pnl || 0;
  const positions = d.positions || [];
  const chains = d.chains || {};

  const totalPnlEl = document.getElementById('sc-total-pnl');
  totalPnlEl.textContent = fmtUsd(pnl);
  totalPnlEl.className = 'value ' + pnlClass(pnl);
  const liveEst = pnl * 0.75;
  const liveEl = document.getElementById('sc-total-pnl-live');
  if (liveEl) { liveEl.textContent = 'live est: ' + fmtUsd(liveEst); liveEl.style.color = liveEst >= 0 ? '#4caf50' : '#f44336'; }

  const dailyEl = document.getElementById('sc-daily-pnl');
  dailyEl.textContent = fmtUsd(daily);
  dailyEl.className = 'value ' + pnlClass(daily);

  const wr = ov.win_rate || 0;
  const wrEl = document.getElementById('sc-win-rate');
  wrEl.textContent = wr.toFixed(1) + '%';
  wrEl.className = 'value ' + (wr >= 50 ? 'green' : wr >= 35 ? 'yellow' : 'red');
  document.getElementById('sc-wr-sub').textContent =
    (ov.wins || 0) + ' wins / ' + (ov.total_trades || 0) + ' trades';

  document.getElementById('sc-trades').textContent = ov.total_trades || 0;
  document.getElementById('sc-trades-sub').textContent = positions.length + ' open';

  // Account balance from risk managers
  const cap = d.capital || {};
  const totalCap  = cap.total     || 2000;
  const available = cap.available || totalCap;
  const deployed  = cap.deployed  || 0;
  document.getElementById('sc-balance').textContent = '$' + totalCap.toFixed(0);
  document.getElementById('sc-balance-sub').textContent =
    '$' + deployed.toFixed(0) + ' deployed • $' + available.toFixed(0) + ' available';

  // Drawdown
  const dd = d.drawdown || {};
  const maxDd = dd.max_drawdown || 0;
  const maxDdPct = dd.max_drawdown_pct || 0;
  const curDd = dd.current_drawdown || 0;
  const ddEl = document.getElementById('sc-max-dd');
  ddEl.textContent = '-$' + maxDd.toFixed(2);
  ddEl.style.color = maxDd > 0 ? 'var(--red)' : 'var(--muted)';
  document.getElementById('sc-max-dd-sub').textContent =
    maxDdPct.toFixed(1) + '% from peak' + (curDd > 0 ? ' • now -$' + curDd.toFixed(2) : '');

  // Slippage
  const sl = d.slippage || {};
  const slCost = sl.total_slippage_cost_usd || 0;
  const slAvg  = sl.avg_slippage_pct || 0;
  const slEl = document.getElementById('sc-slippage');
  slEl.textContent = '-$' + slCost.toFixed(2);
  slEl.style.color = slCost > 0 ? 'var(--yellow)' : 'var(--muted)';
  document.getElementById('sc-slippage-sub').textContent =
    'avg ' + slAvg.toFixed(2) + '% per trade';

  // DexScreener WS health indicator
  const ws = d.dexscreener_ws || {};
  const wsEl = document.getElementById('sc-dex-ws');
  const wsSubEl = document.getElementById('sc-dex-ws-sub');
  if (wsEl && wsSubEl) {
    if (ws.status === 'ok') {
      wsEl.textContent = '🟢 Live';
      wsEl.style.color = 'var(--green)';
      wsSubEl.textContent = 'connected';
    } else if (ws.status === 'broken') {
      wsEl.textContent = '🔴 Down';
      wsEl.style.color = 'var(--red)';
      wsSubEl.textContent = (ws.consecutive_failures || 0) + ' failures — endpoint broken';
    } else if (ws.status === 'reconnecting') {
      wsEl.textContent = '🟡 Retry';
      wsEl.style.color = 'var(--yellow)';
      wsSubEl.textContent = 'attempt ' + (ws.consecutive_failures || 0);
    } else {
      wsEl.textContent = '—';
      wsSubEl.textContent = 'polling only';
    }
  }
}

// ── P&L Chart ──────────────────────────────────────────────────────────────
function updatePnlChart(series) {
  if (!pnlChart || !series.length) return;
  pnlChart.data.labels = series.map(p => '#' + p.trade_num);
  pnlChart.data.datasets[0].data = series.map(p => p.cumulative);

  // Dynamic color based on last value
  const last = series[series.length - 1].cumulative;
  const color = last >= 0 ? '#3fb950' : '#f85149';
  pnlChart.data.datasets[0].borderColor = color;
  pnlChart.data.datasets[0].pointBackgroundColor = color;
  pnlChart.update('none');
}

// ── Open Positions ─────────────────────────────────────────────────────────
function updatePositions(positions) {
  const tbody = document.getElementById('positions-body');
  // AxiS 2026-06-11: this card shows SMART WALLET only — fleet bots' paper
  // probes live in the Bots tab; mixing $10 probes with follow positions
  // made the card unreadable.
  positions = (positions || []).filter(p => String(p.strategy || '').startsWith('smart_follow'));
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No open smart-wallet positions</td></tr>';
    return;
  }
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No open positions</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const pnlCls = pnlClass(p.pnl_usd);
    const mult = p.multiplier || 1;
    // Progress toward TP levels: 1.5x=TP1, 2x=TP2, 2.5x=TP3
    const pct = Math.min(((mult - 1) / 1.5) * 100, 100);
    const tpCls = mult >= 2.5 ? 'tp3' : mult >= 2 ? 'tp2' : 'tp1';
    const addr = p.token_address || '';
    const chartAddr = p.pair_address || addr;
    const chartUrl = chartAddr ? `https://dexscreener.com/solana/${chartAddr}` : '';
    return `<tr>
      <td style="font-weight:600">${chartUrl
        ? `<a href="${chartUrl}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;" title="View chart">$${p.symbol||'?'} ↗</a>`
        : `$${p.symbol||'?'}`}</td>
      <td>${chainBadge(p.chain)}</td>
      <td>${stratBadge(p.strategy)}</td>
      <td class="muted">$${(p.entry_price||0).toFixed(6)}</td>
      <td class="muted">$${(p.amount_usd||0).toFixed(0)}</td>
      <td class="${pnlCls}">${fmtUsd(p.pnl_usd)}</td>
      <td class="muted">${fmtHold(p.hold_secs)}</td>
      <td>
        <div class="progress-wrap" title="${mult.toFixed(2)}x">
          <div class="progress-fill ${tpCls}" style="width:${pct.toFixed(0)}%"></div>
        </div>
      </td>
      <td>
        <button onclick="manualSell('${addr}')"
          style="background:#f85149;color:#fff;border:none;border-radius:5px;padding:4px 10px;cursor:pointer;font-size:11px;font-weight:700;">Sell</button>
      </td>
    </tr>`;
  }).join('');
}

// ── Manual Sell ──────────────────────────────────────────────────────────
const _sellInFlight = new Set();
async function manualSell(tokenAddress) {
  if (!tokenAddress) return;
  if (_sellInFlight.has(tokenAddress)) return; // prevent double-fire
  if (!confirm('Sell 100% of this position?')) return;
  _sellInFlight.add(tokenAddress);
  try {
    const res = await fetch('/api/sell', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_address: tokenAddress, pct: 1.0})
    });
    const data = await res.json();
    if (data.ok) {
      alert('Sell order sent for ' + (data.symbol || 'token'));
    } else {
      alert('Sell failed: ' + (data.error || 'Unknown error'));
    }
  } catch(e) { alert('Request failed: ' + e); }
  finally { _sellInFlight.delete(tokenAddress); }
}

// ── Manual Buy ──────────────────────────────────────────────────────────
async function manualBuy(tokenAddress, tokenSymbol) {
  if (!tokenAddress) return;
  if (!confirm('Buy ' + tokenSymbol + '?')) return;
  try {
    const res = await fetch('/api/buy', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_address: tokenAddress, token_symbol: tokenSymbol})
    });
    const data = await res.json();
    if (data.ok) {
      alert('Buy order sent for ' + (data.symbol || tokenSymbol));
    } else {
      alert('Buy failed: ' + (data.error || 'Unknown error'));
    }
  } catch(e) { alert('Request failed: ' + e); }
}

async function overrideBuy(tokenAddress, tokenSymbol, score, reason) {
  if (!tokenAddress) return;
  const msg = '⚠ SCANNER BLOCKED THIS TOKEN\\n\\n' +
    'Score: ' + score + ' (minimum needed: 50)\\n' +
    'Reason blocked: ' + reason + '\\n\\n' +
    'The bot rejected $' + tokenSymbol + ' — buying it manually overrides all scanner filters.\\n\\n' +
    'Are you sure you want to buy?';
  if (!confirm(msg)) return;
  await manualBuy(tokenAddress, tokenSymbol);
}

// ── Watchlist (Near-Miss Signals) — DEPRECATED, removed from UI 2026-05-18 ─
// Kept as no-op so any orphan callers don't error.
async function loadWatchlist() { return; }
async function _deprecated_loadWatchlist() {
  try {
    const res = await fetch('/api/watchlist');
    const data = await res.json();
    const list = data.watchlist || [];
    const body = document.getElementById('watchlist-body');
    const count = document.getElementById('watchlist-count');
    if (count) count.textContent = list.length + ' token' + (list.length !== 1 ? 's' : '');
    if (!list.length) {
      body.innerHTML = '<tr><td colspan="7" style="color:var(--muted);padding:12px;text-align:center;">No near-miss tokens — waiting for signals</td></tr>';
      return;
    }
    body.innerHTML = list.map(t => {
      const ageMins = Math.floor((t.age_seconds || 0) / 60);
      const ageStr = ageMins >= 60 ? Math.floor(ageMins/60) + 'h ' + (ageMins%60) + 'm' : ageMins + 'm';
      const scoreColor = t.score >= 55 ? 'var(--yellow)' : 'var(--muted)';
      return `<tr>
        <td style="font-weight:600">${t.token_address
        ? `<a href="https://dexscreener.com/solana/${t.token_address}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;" title="View chart">$${t.symbol||'?'} ↗</a>`
        : `$${t.symbol||'?'}`}</td>
        <td style="color:${scoreColor};font-weight:700">${t.score}</td>
        <td class="muted">$${(t.mcap||0).toLocaleString()}</td>
        <td class="muted">$${(t.price||0).toFixed(8)}</td>
        <td class="muted" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(t.reason||'')}">${escHtml((t.reason||'').slice(0,40))}</td>
        <td class="muted">${ageStr}</td>
        <td><button onclick="overrideBuy('${t.token_address}','${escHtml(t.symbol||'?')}',${t.score},'${escHtml(t.reason||'')}')"
          style="background:#b45309;color:#fff;border:none;border-radius:5px;padding:4px 10px;cursor:pointer;font-size:11px;font-weight:700;" title="Scanner blocked this token — override at your own risk">Override</button></td>
      </tr>`;
    }).join('');
  } catch(e) { console.warn('Watchlist load error', e); }
}

loadWatchlist();
setInterval(loadWatchlist, 90000);  // 2026-06-04 30s->90s (egress)

// ── User Watchlist (Curator-Driven, Hot-Reload) ──────────────────────────
function fmtMcap(v) {
  if (!v) return '$0';
  if (v >= 1e9) return '$' + (v/1e9).toFixed(2) + 'B';
  if (v >= 1e6) return '$' + (v/1e6).toFixed(2) + 'M';
  if (v >= 1e3) return '$' + (v/1e3).toFixed(0) + 'k';
  return '$' + Math.round(v);
}
function fmtPct(v) {
  if (v === null || v === undefined) return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return '—';
  const sign = n > 0 ? '+' : '';
  const color = n > 0 ? 'var(--green-lt,#22c55e)' : (n < 0 ? 'var(--red,#ef4444)' : 'var(--muted)');
  return '<span style="color:' + color + '">' + sign + n.toFixed(1) + '%</span>';
}
async function loadUserWatchlist() {
  try {
    const res = await fetch('/api/user-watchlist');
    const data = await res.json();
    const tokens = data.tokens || [];
    const body = document.getElementById('user-watchlist-body');
    const count = document.getElementById('user-watchlist-count');
    if (count) count.textContent = tokens.length + ' token' + (tokens.length !== 1 ? 's' : '');
    if (!tokens.length) {
      body.innerHTML = '<tr><td colspan="8" style="color:var(--muted);padding:12px;text-align:center;">Empty — paste an address above to start farming runners</td></tr>';
      return;
    }
    body.innerHTML = tokens.map(t => {
      const addr = t.address;
      const sym = (t.symbol && t.symbol !== '?') ? t.symbol.slice(0, 12) : (addr ? addr.slice(0, 6) + '…' : '?');
      const mcapDisplay = t.mcap ? fmtMcap(t.mcap) : '<span class="muted">loading…</span>';
      return '<tr>' +
        '<td style="font-weight:600"><a href="https://dexscreener.com/solana/' + addr + '" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;" title="' + addr + '">$' + escHtml(sym) + ' &#x2197;</a></td>' +
        '<td class="muted">' + mcapDisplay + '</td>' +
        '<td class="muted">' + (t.vol_h1 ? fmtMcap(t.vol_h1) : '<span class="muted">—</span>') + '</td>' +
        '<td>' + fmtPct(t.pc_h24) + '</td>' +
        '<td>' + fmtPct(t.pc_h1) + '</td>' +
        '<td>' + fmtPct(t.pc_m5) + '</td>' +
        '<td class="muted">' + (t.liq_usd ? fmtMcap(t.liq_usd) : '<span class="muted">—</span>') + '</td>' +
        '<td><button onclick="removeUserWatchlist(\'' + addr + '\')" style="background:#b91c1c;color:#fff;border:none;border-radius:5px;padding:4px 10px;cursor:pointer;font-size:11px;font-weight:700;">Remove</button></td>' +
      '</tr>';
    }).join('');
  } catch(e) { console.warn('User watchlist load error', e); }
}
async function addUserWatchlist() {
  const inp = document.getElementById('user-watchlist-add-address');
  const addr = (inp.value || '').trim();
  if (!addr) { alert('Paste a token address first'); return; }
  try {
    const res = await fetch('/api/user-watchlist/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr})
    });
    const data = await res.json();
    if (data.ok) {
      inp.value = '';
      await loadUserWatchlist();
    } else {
      alert('Add failed: ' + (data.error || 'Unknown'));
    }
  } catch(e) { alert('Request failed: ' + e); }
}
async function removeUserWatchlist(addr) {
  if (!confirm('Remove ' + addr.slice(0, 8) + '… from watchlist?')) return;
  try {
    const res = await fetch('/api/user-watchlist/remove', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr})
    });
    const data = await res.json();
    if (data.ok) await loadUserWatchlist();
    else alert('Remove failed: ' + (data.error || 'Unknown'));
  } catch(e) { alert('Request failed: ' + e); }
}
loadUserWatchlist();
setInterval(loadUserWatchlist, 60000);  // 1m refresh — balance freshness vs egress

async function addToWatchlist() {
  const addr = document.getElementById('watchlist-add-address').value.trim();
  const sym  = document.getElementById('watchlist-add-symbol').value.trim();
  if (!addr) { alert('Paste a token address first'); return; }
  try {
    const res  = await fetch('/api/watchlist/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_address: addr, token_symbol: sym})
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('watchlist-add-address').value = '';
      document.getElementById('watchlist-add-symbol').value = '';
      await loadWatchlist();
    } else { alert('Error: ' + (data.error || 'Unknown')); }
  } catch(e) { alert('Request failed: ' + e); }
}

// ── Event Feed ─────────────────────────────────────────────────────────────
function updateFeed(alerts) {
  if (!alerts.length) return;
  const now = new Date().toLocaleTimeString();
  alerts.forEach(msg => {
    const cls = classifyAlert(msg);
    feedLog.unshift({ msg, time: now, cls });
  });
  feedLog = feedLog.slice(0, 200);
  renderFeed();
}
function classifyAlert(msg) {
  const m = msg.toLowerCase();
  if (m.includes('buy') || m.includes('enter') || m.includes('open')) return 'feed-buy';
  if (m.includes('sell') || m.includes('stop') || m.includes('close') || m.includes('exit')) return 'feed-sell';
  if (m.includes('signal') || m.includes('scan') || m.includes('detect')) return 'feed-sig';
  return 'feed-info';
}
function renderFeed() {
  const el = document.getElementById('event-feed');
  if (!feedLog.length) return;
  el.innerHTML = feedLog.map(f =>
    `<div class="feed-item ${f.cls}">
      <span class="feed-time">${f.time}</span>
      <span class="feed-msg">${escHtml(f.msg)}</span>
    </div>`
  ).join('');
}
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Strategy breakdown ─────────────────────────────────────────────────────
function updateStrategies(strategies) {
  const map = {
    scanner: ['st-sc-trades','st-sc-wr','st-sc-pnl','st-sc-avgwin','st-sc-avgloss'],
    copy:    ['st-cp-trades','st-cp-wr','st-cp-pnl','st-cp-avgwin','st-cp-avgloss'],
    scalper: ['st-sk-trades','st-sk-wr','st-sk-pnl','st-sk-avgwin','st-sk-avgloss'],
  };
  Object.entries(map).forEach(([key, ids]) => {
    const s = strategies[key] || {};
    document.getElementById(ids[0]).textContent = s.total_trades || 0;
    document.getElementById(ids[1]).textContent = (s.win_rate||0).toFixed(1) + '%';
    const pnlEl = document.getElementById(ids[2]);
    pnlEl.textContent = fmtUsd(s.total_pnl || 0);
    pnlEl.className = pnlClass(s.total_pnl || 0);
    document.getElementById(ids[3]).textContent = fmtUsd(s.avg_win || 0);
    document.getElementById(ids[4]).textContent = fmtUsd(s.avg_loss || 0);
  });
}

// ── Active Strategies status ───────────────────────────────────────────────
function timeAgo(isoStr) {
  if (!isoStr) return 'Never';
  try {
    const diffMs = Date.now() - new Date(isoStr).getTime();
    if (diffMs < 0) return 'Just now';
    const s = Math.floor(diffMs / 1000);
    if (s < 60)  return s + 's ago';
    const m = Math.floor(s / 60);
    if (m < 60)  return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24)  return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  } catch { return '—'; }
}

function updateActiveStrategies(strategies) {
  const grid = document.getElementById('active-strategies-grid');
  if (!grid) return;
  const list = Object.entries(strategies);
  if (!list.length) {
    grid.innerHTML = '<div class="empty">No strategy data yet</div>';
    return;
  }
  grid.innerHTML = list.map(([key, s]) => {
    const running = s.running !== false;
    const dotColor = running ? 'var(--green-lt)' : 'var(--red)';
    const badgeCls = running ? 'badge-running' : 'badge-stopped';
    const badgeTxt = running ? 'RUNNING' : 'STOPPED';
    const pnlCls   = (s.total_pnl || 0) >= 0 ? 'green' : 'red';
    const wr        = (s.win_rate || 0).toFixed(1);
    const wrCls     = (s.win_rate || 0) >= 50 ? 'green' : (s.win_rate || 0) >= 35 ? 'yellow' : 'red';

    // Build extra strategy-specific rows
    let extraRows = '';
    if (s.tokens_received != null)
      extraRows += `<div class="strat-stat"><span class="sk">Tokens received</span><span>${s.tokens_received}</span></div>`;
    if (s.active_cycles != null)
      extraRows += `<div class="strat-stat"><span class="sk">Active cycles</span><span>${s.active_cycles}</span></div>`;
    if (s.active_positions != null)
      extraRows += `<div class="strat-stat"><span class="sk">Active positions</span><span>${s.active_positions}</span></div>`;
    if (s.signals_fired != null)
      extraRows += `<div class="strat-stat"><span class="sk">Signals fired</span><span>${s.signals_fired}</span></div>`;
    if (s.reconnect_count != null)
      extraRows += `<div class="strat-stat"><span class="sk">Reconnects</span><span>${s.reconnect_count}</span></div>`;

    return `<div class="strat-status-card">
      <div class="strat-header">
        <span class="strat-dot" style="background:${dotColor}"></span>
        <span class="strat-name">${escHtml(s.display_name || key)}</span>
        <span class="strat-badge ${badgeCls}">${badgeTxt}</span>
      </div>
      <div class="strat-stat"><span class="sk">Last Buy</span><span>${timeAgo(s.last_buy)}</span></div>
      <div class="strat-stat"><span class="sk">Last Sell</span><span>${timeAgo(s.last_sell)}</span></div>
      <div class="strat-stat"><span class="sk">Trades today</span><span>${s.trades_today || 0}</span></div>
      <div class="strat-stat"><span class="sk">Win Rate</span><span class="${wrCls}">${wr}%</span></div>
      <div class="strat-stat"><span class="sk">Total P&amp;L</span><span class="${pnlCls}">${fmtUsd(s.total_pnl || 0)}</span></div>
      ${extraRows}
    </div>`;
  }).join('');
}

// ── Chain breakdown ────────────────────────────────────────────────────────
function updateChains(chains) {
  const c = chains['sol'] || {};
  const pnlEl = document.getElementById('ch-sol-pnl');
  pnlEl.textContent = fmtUsd(c.pnl || 0);
  pnlEl.className = pnlClass(c.pnl || 0);
  document.getElementById('ch-sol-cap').textContent = '$' + (c.capital || 0).toFixed(0);
  document.getElementById('ch-sol-pos').textContent = c.positions || 0;
}

// ── Scalp Queue ────────────────────────────────────────────────────────────
function updateScalpQueue(sq) {
  const card = document.getElementById('scalp-queue-card');
  if (!card) return;
  if (!sq || !sq.enabled) { card.style.display = 'none'; return; }
  card.style.display = '';
  document.getElementById('sq-watched').textContent = sq.watched || 0;
  document.getElementById('sq-open').textContent =
    (sq.open_positions || 0) + ' / ' + (sq.max_concurrent || 10);
  document.getElementById('sq-deployed').textContent = '$' + (sq.deployed_usd || 0).toFixed(0);
  document.getElementById('sq-available').textContent = '$' + (sq.available_usd || 0).toFixed(0);
  const dpnlEl = document.getElementById('sq-dpnl');
  dpnlEl.textContent = fmtUsd(sq.daily_pnl_usd || 0);
  dpnlEl.className = pnlClass(sq.daily_pnl_usd || 0);
  const hitEl = document.getElementById('sq-cap-hit');
  hitEl.textContent = sq.daily_loss_hit ? 'YES' : 'No';
  hitEl.className = sq.daily_loss_hit ? 'red' : '';
  document.getElementById('sq-cooldowns').textContent = sq.stop_cooldowns || 0;
}

// ── Security stats ─────────────────────────────────────────────────────────
function updateSecurity(sec, feed) {
  document.getElementById('sec-total').textContent   = sec.total_checks || 0;
  document.getElementById('sec-honeypot').textContent = sec.honeypot_blocked || sec.blocked || 0;
  document.getElementById('sec-tax').textContent     = sec.tax_blocked || 0;
  document.getElementById('sec-rate').textContent    = (sec.block_rate || 0).toFixed(1) + '%';
  document.getElementById('sec-cache').textContent   = sec.cache_size || 0;
  document.getElementById('feed-ticks').textContent  = feed.total_ticks || 0;
}

// ── MC Recommendations ─────────────────────────────────────────────────────
async function loadMcRecommendations() {
  try {
    const res  = await fetch('/api/mc-recommendations');
    const data = await res.json();
    const tbody = document.getElementById('mc-rec-body');
    if (!data || !data.length) {
      tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;opacity:0.4">No micro-caps seen yet</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(r => {
      const t    = r.time ? r.time.substring(11,16) : '—';
      const mcap = r.mcap >= 1000 ? '$' + (r.mcap/1000).toFixed(0) + 'k' : '$' + r.mcap;
      const liq  = r.liquidity >= 1000 ? '$' + (r.liquidity/1000).toFixed(0) + 'k' : '$' + r.liquidity;
      const lp   = r.lp_burned ? '<span style="color:var(--green)">burned</span>' : '<span style="color:var(--red)">unlocked</span>';
      const reason = r.reject_reason || '—';
      return `<tr>
        <td style="opacity:0.6">${t}</td>
        <td><strong>${r.dex_url ? `<a href="${r.dex_url}" target="_blank" style="color:var(--accent);text-decoration:none">${r.symbol || '?'}</a>` : (r.symbol || '?')}</strong></td>
        <td>${mcap}</td>
        <td>${liq}</td>
        <td style="color:${r.dev_pct > 20 ? 'var(--red)' : 'inherit'}">${r.dev_pct}%</td>
        <td style="color:${r.snipers_pct > 20 ? 'var(--red)' : 'inherit'}">${r.snipers_pct}%</td>
        <td>${lp}</td>
        <td style="opacity:0.7;font-size:10px">${reason}</td>
      </tr>`;
    }).join('');
  } catch(e) { console.warn('MC rec error', e); }
}
setInterval(loadMcRecommendations, 60000);  // 2026-06-04 15s->60s (egress)
loadMcRecommendations();

// ── Trade History ──────────────────────────────────────────────────────────
function filterTrades() {
  const q      = (document.getElementById('trade-search').value || '').toLowerCase();
  const chain  = (document.getElementById('trade-chain-filter').value || '').toLowerCase();
  const strat  = (document.getElementById('trade-strat-filter').value || '').toLowerCase();
  const tbody  = document.getElementById('trade-history-body');

  // sells only, newest first
  const sells = allTrades
    .filter(t => t.type === 'sell')
    .slice(-50)
    .reverse();

  const filtered = sells.filter(t => {
    const token   = (t.token || t.address || '').toLowerCase();
    const reason  = (t.reason || '').toLowerCase();
    const tchain  = normalizeChainKey(t.chain || '');
    const tstrat  = (t.strategy || '').toLowerCase();
    if (q     && !token.includes(q) && !reason.includes(q)) return false;
    if (chain && tchain !== chain) return false;
    if (strat && tstrat !== strat) return false;
    return true;
  });

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No trades match filter</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(t => {
    const pnl    = t.pnl || 0;
    const entry  = t.entry_price || 0;
    const exit   = t.exit_price || t.usd_received || 0;
    // pnl_pct is saved directly by the bot since the fix; fall back to pnl/amount_usd
    const pnlPct = t.pnl_pct != null && t.pnl_pct !== 0
      ? t.pnl_pct
      : (t.amount_usd > 0 ? (pnl / t.amount_usd * 100) : 0);
    const rowCls = pnl >= 0 ? 'row-win' : 'row-loss';
    return `<tr class="${rowCls}">
      <td class="muted">${fmtTime(t.time)}</td>
      <td style="font-weight:600">${(t.address)
        ? `<a href="https://dexscreener.com/solana/${t.address}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none;" title="View chart">$${t.token || t.address.slice(0,8) || '?'} ↗</a>`
        : `$${t.token || '?'}`}</td>
      <td>${chainBadge(t.chain)}</td>
      <td>${stratBadge(t.strategy)}</td>
      <td class="muted">${entry ? '$' + entry.toFixed(6) : '—'}</td>
      <td class="muted">${exit ? '$' + exit.toFixed(4) : '—'}</td>
      <td class="${pnlClass(pnl)}">${fmtUsd(pnl)}</td>
      <td class="${pnlClass(pnlPct)}">${fmtPct(pnlPct)}</td>
      <td class="muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(t.reason||'')}">
        ${escHtml((t.reason||'').slice(0,40))}
      </td>
    </tr>`;
  }).join('');
}

function normalizeChainKey(c) {
  const s = c.toLowerCase();
  if (s === 'solana' || s === 'sol') return 'sol';
  return s;
}

// ── Seed Wallets ────────────────────────────────────────────────────────────
async function loadSeedWallets() {
  try {
    const res  = await fetch('/api/seed-wallets');
    const data = await res.json();
    const wallets = data.wallets || {};
    const body  = document.getElementById('seed-wallets-body');
    if (!body) return;
    const count = document.getElementById('seed-wallet-count');
    const entries = Object.entries(wallets);
    if (count) count.textContent = `${entries.length} wallet${entries.length !== 1 ? 's' : ''}`;
    if (!entries.length) {
      body.innerHTML = '<tr><td colspan="4" style="color:var(--muted);padding:12px;text-align:center;">No seed wallets — add one above</td></tr>';
      return;
    }
    body.innerHTML = entries.map(([addr, score]) => `
      <tr>
        <td style="font-family:monospace;font-size:12px;">${addr.slice(0,8)}…${addr.slice(-6)}</td>
        <td>
          <input type="number" min="0" max="100" value="${score}"
            style="width:52px;background:#1a1a2e;border:1px solid #444;border-radius:4px;padding:2px 6px;color:#e2e8f0;font-size:12px;text-align:center;"
            onchange="updateSeedWalletScore('${addr}', this.value)"
            onkeydown="if(event.key==='Enter')this.blur()" />
          <span style="color:var(--muted);font-size:11px;">/100</span>
        </td>
        <td><a href="https://solscan.io/account/${addr}" target="_blank" style="color:#a78bfa;font-size:11px;">Solscan ↗</a></td>
        <td><button onclick="removeSeedWallet('${addr}')"
              style="background:transparent;color:var(--muted);border:none;cursor:pointer;font-size:14px;padding:2px 6px;">×</button></td>
      </tr>`).join('');
  } catch(e) { console.warn('Seed wallets load error', e); }
}

async function addSeedWallet() {
  const addr  = document.getElementById('seed-wallet-address').value.trim();
  const score = parseFloat(document.getElementById('seed-wallet-score').value) || 75;
  if (!addr) { alert('Paste a wallet address first'); return; }
  if (addr.length < 32) { alert("That doesn't look like a valid Solana address"); return; }
  try {
    const res  = await fetch('/api/seed-wallets/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr, score})
    });
    const data = await res.json();
    if (data.ok) {
      document.getElementById('seed-wallet-address').value = '';
      await loadSeedWallets();
    } else { alert(`Error: ${data.error}`); }
  } catch(e) { alert('Request failed'); }
}

async function removeSeedWallet(addr) {
  try {
    const res  = await fetch('/api/seed-wallets/remove', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr})
    });
    const data = await res.json();
    if (data.ok) { await loadSeedWallets(); }
    else { alert(`Error: ${data.error}`); }
  } catch(e) { alert('Request failed'); }
}

async function updateSeedWalletScore(addr, newScore) {
  const score = parseFloat(newScore);
  if (isNaN(score) || score < 0 || score > 100) return;
  try {
    await fetch('/api/seed-wallets/add', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address: addr, score})
    });
  } catch(e) {}
}

loadSeedWallets();
setInterval(loadSeedWallets, 60000);

// ── GOAL meter + FLEET panels ───────────────────────────────────────────────
let _candSet = null;
async function updateGoal() {
  try {
    const resp = await fetch("/api/goal");
    if (!resp.ok) return;
    const g = await resp.json();
    _candSet = new Set(g.candidate_bots);
    const tot = g.today.live_total;   // HEADLINE = walk-forward live set
    const el = document.getElementById("goal-today");
    el.textContent = (tot < 0 ? "-$" : "$") + Math.abs(tot).toFixed(0);
    el.style.color = tot >= g.goal_usd ? "#4caf50" : (tot >= 0 ? "#e0e0e0" : "#f44336");
    const pct = Math.max(0, Math.min(100, 100 * tot / g.goal_usd));
    const bar = document.getElementById("goal-bar");
    bar.style.width = pct + "%";
    bar.style.background = tot >= g.goal_usd ? "#4caf50" : (tot >= 0 ? "#ff9800" : "#f44336");
    const badge = document.getElementById("goal-badge");
    badge.textContent = tot >= g.goal_usd ? "MET ✓" : `$${(g.goal_usd - tot).toFixed(0)} to go`;
    badge.style.color = tot >= g.goal_usd ? "#4caf50" : "#888";
    const ranNames = (g.today.live_set || []).join(", ") || "none qualified yet";
    const full = g.today.full_total;
    document.getElementById("goal-contrib").textContent =
      `live set today (${(g.today.live_set || []).length}): ${ranNames}  ·  ` +
      `all-candidates ${full >= 0 ? "+" : "-"}$${Math.abs(full).toFixed(0)} (experiments included)`;
    const hist = g.history.map(h =>
      `${h.day.slice(5)} ${h.met ? "✓" : (h.live_total >= 0 ? "+" : "") + h.live_total.toFixed(0)}`).join("  ");
    document.getElementById("goal-history").textContent =
      (hist ? "live-set last days: " + hist + "  ·  " : "") +
      `streak: ${g.streak_complete_days} (need 5 before go-live talk)`;
  } catch (e) {
    console.error("updateGoal failed", e);
  }
}

function botRowHtml(b) {
  const wr = b.total_trades > 0 ? (100 * b.wins / b.total_trades).toFixed(0) + "%" : "--";
  const perTr = b.total_trades > 0 ? "$" + (b.total_pnl_realized / b.total_trades).toFixed(2) : "--";
  const pnlClass = b.total_pnl_realized > 0 ? "pnl-pos" : (b.total_pnl_realized < 0 ? "pnl-neg" : "");
  return `<tr>
    <td>${escHtml(b.bot_id)}</td>
    <td>$${b.balance_usd.toFixed(2)}</td>
    <td>${b.open_position_count}</td>
    <td>${b.total_trades}</td>
    <td>${wr}</td>
    <td class="${pnlClass}">$${b.total_pnl_realized.toFixed(2)}</td>
    <td>${perTr}</td>
    <td>$${b.total_pnl_realized.toFixed(2)}</td>
  </tr>`;
}

async function updateFleet() {
  try {
    if (_candSet === null) await updateGoal();
    const resp = await fetch("/api/leaderboard?sort=throughput_x_pnl");
    if (!resp.ok) return;
    const bots = await resp.json();
    const cand = document.querySelector("#cand-table tbody");
    const exp = document.querySelector("#fleet-table tbody");
    cand.innerHTML = ""; exp.innerHTML = "";
    if (!bots.length) {
      cand.innerHTML = '<tr><td colspan="8" class="empty">No bots registered yet</td></tr>';
      return;
    }
    let nExp = 0;
    for (const b of bots) {
      if (_candSet && _candSet.has(b.bot_id)) {
        cand.insertAdjacentHTML("beforeend", botRowHtml(b));
      } else {
        exp.insertAdjacentHTML("beforeend", botRowHtml(b));
        nExp++;
      }
    }
    if (!cand.innerHTML) cand.innerHTML = '<tr><td colspan="8" class="empty">candidate bots have no rows yet</td></tr>';
    document.getElementById("exp-count").textContent = nExp;
  } catch (e) {
    console.error("updateFleet failed", e);
  }
}
setInterval(updateFleet, 45000);  // 2026-06-04 15s->45s (egress)
setInterval(updateGoal, 60000);
updateGoal().then(updateFleet);
</script>

<!-- Breakout Strategy -->
<div id="breakout-panel" style="display:none;">

  <div class="stat-row" style="margin-top:20px;">
    <div class="stat-card">
      <div class="label">Breakout Capital</div>
      <div class="value" id="bk-capital">$0</div>
      <div class="sub">Binance.US &middot; paper</div>
    </div>
    <div class="stat-card">
      <div class="label">Available</div>
      <div class="value" id="bk-available">$0</div>
      <div class="sub" id="bk-deployed-sub">$0 deployed</div>
    </div>
    <div class="stat-card">
      <div class="label">Realized P&amp;L</div>
      <div class="value" id="bk-pnl">$0.00</div>
      <div class="sub">breakout-only</div>
    </div>
    <div class="stat-card">
      <div class="label">Open Positions</div>
      <div class="value" id="bk-open">0 / 4</div>
      <div class="sub" id="bk-watchlist-sub">0 symbols on watchlist</div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><span class="dot" style="background:#a78bfa"></span> Breakout Watchlist</div>
    <div id="bk-watchlist" style="display:flex;flex-wrap:wrap;gap:8px;padding:4px 0;">
      <span style="color:var(--muted);font-size:12px;">Waiting for first scan...</span>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--green)"></span> Breakout Open Positions</div>
    <div class="tbl-wrap">
      <table id="bk-positions">
        <thead><tr>
          <th>Symbol</th><th>Entry</th><th>Qty</th><th>TP</th><th>Stop</th>
          <th>Peak</th><th>Score</th><th>TP1</th>
        </tr></thead>
        <tbody><tr><td colspan="8" class="empty">No open breakout positions</td></tr></tbody>
      </table>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--yellow)"></span> Breakout Closed (last 20)</div>
    <div class="tbl-wrap">
      <table id="bk-closed">
        <thead><tr>
          <th>Symbol</th><th>Entry</th><th>Exit</th><th>PnL $</th><th>PnL %</th><th>Reason</th>
        </tr></thead>
        <tbody><tr><td colspan="6" class="empty">No closed trades yet</td></tr></tbody>
      </table>
    </div>
  </div>

</div>
<script>
(async function refreshBreakout() {
  try {
    const [s, w, p, c] = await Promise.all([
      fetch("/api/breakout/state").then(r => r.ok ? r.json() : null).catch(() => null),
      fetch("/api/breakout/watchlist").then(r => r.ok ? r.json() : []).catch(() => []),
      fetch("/api/breakout/positions").then(r => r.ok ? r.json() : []).catch(() => []),
      fetch("/api/breakout/closed?limit=20").then(r => r.ok ? r.json() : []).catch(() => []),
    ]);
    if (!s) return;
    document.getElementById("breakout-panel").style.display = "";
    document.getElementById("bk-capital").textContent = "$" + Math.round(s.total_capital).toLocaleString();
    document.getElementById("bk-available").textContent = "$" + Math.round(s.available).toLocaleString();
    document.getElementById("bk-deployed-sub").textContent = "$" + Math.round(s.deployed).toLocaleString() + " deployed";
    document.getElementById("bk-pnl").textContent = (s.realized_pnl >= 0 ? "+" : "-") + "$" + Math.abs(s.realized_pnl).toFixed(2);
    document.getElementById("bk-open").textContent = s.open_count + " / " + s.max_concurrent;
    document.getElementById("bk-watchlist-sub").textContent = w.length + " symbols on watchlist";
    const wrap = document.getElementById("bk-watchlist");
    if (w.length === 0) {
      wrap.innerHTML = '<span style="color:var(--muted);font-size:12px;">Empty &mdash; waiting for next scan</span>';
    } else {
      wrap.innerHTML = "";
      for (const sym of w) {
        const pill = document.createElement("span");
        pill.textContent = sym;
        pill.style.cssText = "background:#1c2128;border:1px solid var(--border2);border-radius:14px;padding:4px 10px;font-size:11px;font-weight:600;color:var(--text);";
        wrap.appendChild(pill);
      }
    }
    const tbody = document.querySelector("#bk-positions tbody");
    if (p.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty">No open breakout positions</td></tr>';
    } else {
      tbody.innerHTML = "";
      for (const row of p) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${row.symbol}</td><td>${row.entry_price.toFixed(6)}</td>
                        <td>${row.qty.toFixed(4)}</td><td>${row.tp_price.toFixed(6)}</td>
                        <td>${row.stop_price.toFixed(6)}</td><td>${row.peak_price.toFixed(6)}</td>
                        <td>${row.score}</td><td>${row.tp_hit ? "YES" : "NO"}</td>`;
        tbody.appendChild(tr);
      }
    }
    const ctbody = document.querySelector("#bk-closed tbody");
    if (c.length === 0) {
      ctbody.innerHTML = '<tr><td colspan="6" class="empty">No closed trades yet</td></tr>';
    } else {
      ctbody.innerHTML = "";
      for (const row of c) {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${row.symbol}</td><td>${row.entry_price.toFixed(6)}</td>
                        <td>${row.exit_price.toFixed(6)}</td>
                        <td>${row.pnl_usd >= 0 ? "+" : "-"}$${Math.abs(row.pnl_usd).toFixed(2)}</td>
                        <td>${row.pnl_pct >= 0 ? "+" : ""}${row.pnl_pct.toFixed(2)}%</td>
                        <td>${row.reason_exit}</td>`;
        ctbody.appendChild(tr);
      }
    }
  } catch (e) { console.error("breakout refresh failed", e); }
  setTimeout(refreshBreakout, 60000);  // 2026-06-04 10s->60s (egress: 4 fetches/cycle)
})();

// ── ATTRIBUTION tab ──────────────────────────────────────────────────────────
async function updateAttributionFilters() {
  try {
    const resp = await fetch("/api/attribution/filters");
    if (!resp.ok) return;
    const rows = await resp.json();
    const tbody = document.querySelector("#attr-filters-table tbody");
    tbody.innerHTML = "";
    for (const r of rows) {
      const delta = r.delta_per_tr;
      const deltaStr = delta === null ? "—" : ("$" + delta.toFixed(2));
      const cls = (delta || 0) > 0 ? "pnl-pos" : (delta || 0) < 0 ? "pnl-neg" : "";
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.filter}</td><td>${r.baseline_n}</td><td>${r.ablation_n}</td><td class="${cls}">${deltaStr}</td></tr>`);
    }
  } catch (e) { console.error("attr filters", e); }
}

async function updateAttributionCategories() {
  try {
    const resp = await fetch("/api/attribution/categories");
    if (!resp.ok) return;
    const rows = await resp.json();
    const tbody = document.querySelector("#attr-categories-table tbody");
    tbody.innerHTML = "";
    for (const r of rows) {
      const delta = r.delta_per_tr;
      const deltaStr = delta === null ? "—" : ("$" + delta.toFixed(2));
      const cls = (delta || 0) > 0 ? "pnl-pos" : (delta || 0) < 0 ? "pnl-neg" : "";
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.category}</td><td>${r.baseline_n}</td><td>${r.ablation_n}</td><td class="${cls}">${deltaStr}</td></tr>`);
    }
  } catch (e) { console.error("attr categories", e); }
}

async function updateChampionPreview() {
  try {
    const resp = await fetch("/api/champion_proposal");
    if (!resp.ok) return;
    const data = await resp.json();
    const el = document.getElementById("champion-preview");
    if (data.config) {
      el.textContent = JSON.stringify(data.config, null, 2);
    } else {
      el.textContent = "No champion proposal yet. Run scripts/sp4_champion_synthesis.py";
    }
  } catch (e) { console.error("champion preview", e); }
}

function maybeShowAttribution() {
  const tab = document.getElementById("attribution-tab");
  if (location.hash === "#attribution") {
    document.body.classList.add("attr-open");  // full-screen the tab (hide the rest)
    tab.style.display = "block";
    updateAttributionFilters();
    updateAttributionCategories();
    updateChampionPreview();
    window.scrollTo(0, 0);
  } else {
    document.body.classList.remove("attr-open");
    tab.style.display = "none";
  }
}

window.addEventListener("hashchange", maybeShowAttribution);
window.addEventListener("DOMContentLoaded", maybeShowAttribution);

// ── PROFIT-SWEEP SIM tab ──────────────────────────────────────────────────────
async function updateProfitSweepSim() {
  try {
    const resp = await fetch("/api/profit-sweep-sim");
    if (!resp.ok) return;
    const data = await resp.json();
    const t = data.totals || {};
    const fmt = (v) => "$" + (v || 0).toFixed(2);
    document.getElementById("psweep-totals").innerHTML =
      `<b>Fleet secured</b> &mdash; HWM-50: <span class="pnl-pos">${fmt(t.banked_hwm_50)}</span> &nbsp;|&nbsp; ` +
      `HWM-100: <span class="pnl-pos">${fmt(t.banked_hwm_100)}</span> &nbsp;|&nbsp; ` +
      `Step+25%: ${fmt(t.banked_step)} &nbsp;|&nbsp; realized now ${fmt(t.realized_now)} (peak ${fmt(t.realized_peak)})`;
    const tbody = document.querySelector("#psweep-table tbody");
    tbody.innerHTML = "";
    for (const r of (data.bots || [])) {
      if (r.realized_peak <= 0 && r.realized_now <= 0) continue;  // show movers/profitable
      const pc = (v) => (v > 0 ? "pnl-pos" : v < 0 ? "pnl-neg" : "");
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.bot_id}</td>` +
        `<td class="${pc(r.realized_now)}">$${r.realized_now.toFixed(2)}</td>` +
        `<td>$${r.realized_peak.toFixed(2)}</td>` +
        `<td class="pnl-pos">$${r.banked_hwm_50.toFixed(2)}</td>` +
        `<td class="pnl-pos">$${r.banked_hwm_100.toFixed(2)}</td>` +
        `<td>$${r.banked_step.toFixed(2)}</td>` +
        `<td class="${pc(r.at_risk_now)}">$${r.at_risk_now.toFixed(2)}</td></tr>`);
    }
  } catch (e) { console.error("profit-sweep sim", e); }
}

function maybeShowProfitSweep() {
  const tab = document.getElementById("profitsweep-tab");
  if (location.hash === "#profitsweep") {
    // Full-screen the tab: hide everything else (the tab is too short to scroll to
    // the top of the long dashboard, so scrolling never worked — hide-main does).
    document.body.classList.add("psweep-open");
    tab.style.display = "block";
    updateProfitSweepSim();
    window.scrollTo(0, 0);
  } else {
    document.body.classList.remove("psweep-open");
    tab.style.display = "none";
  }
}
window.addEventListener("hashchange", maybeShowProfitSweep);
window.addEventListener("DOMContentLoaded", maybeShowProfitSweep);

// Per-bot re-baseline (flatten + zero ledger)
async function resetBot() {
  const id = (document.getElementById("reset-bot-id").value || "").trim();
  const el = document.getElementById("reset-result");
  if (!id) { el.textContent = "enter a bot_id"; return; }
  if (!confirm("FULL RESET " + id + "?\\n\\nFlattens its open positions AND zeros its ledger (balance→$2000, realized=0). bot_state is backed up first. This is destructive.")) return;
  el.textContent = "resetting " + id + "…";
  try {
    const r = await fetch("/api/bots/" + encodeURIComponent(id) + "/reset", { method: "POST" });
    if (r.status === 401) {
      el.textContent = "\\u2717 login required — enter the dashboard username/password at the browser prompt";
      return;
    }
    let d = {};
    try { d = await r.json(); } catch (_) { el.textContent = "\\u2717 server error (HTTP " + r.status + ")"; return; }
    el.textContent = d.ok
      ? ("\\u2713 " + id + ": flattened " + d.flattened + " positions, ledger zeroed")
      : ("\\u2717 " + (d.error || "failed"));
  } catch (e) { el.textContent = "\\u2717 " + e; }
}

// Always-visible PROFIT SECURED banner (top of page)
async function updateProfitSweepBanner() {
  try {
    const resp = await fetch("/api/profit-sweep-sim");
    if (!resp.ok) return;
    const data = await resp.json();
    const t = data.totals || {};
    const fmt = (v) => "$" + (v || 0).toFixed(2);
    document.getElementById("psweep-banner-totals").innerHTML =
      `Fleet secured &mdash; <b style="font-size:18px;color:#4caf50;">HWM-50 ${fmt(t.banked_hwm_50)}</b> ` +
      `&nbsp;|&nbsp; HWM-100 <b style="color:#4caf50;">${fmt(t.banked_hwm_100)}</b> ` +
      `&nbsp;|&nbsp; +25%-step ${fmt(t.banked_step)} ` +
      `&nbsp;&middot;&nbsp; <span style="color:var(--muted);">realized now ${fmt(t.realized_now)} (peak ${fmt(t.realized_peak)})</span>`;
    const tbody = document.querySelector("#psweep-banner-table tbody");
    tbody.innerHTML = "";
    for (const r of (data.bots || []).slice(0, 5)) {
      const pc = (v) => (v > 0 ? "pnl-pos" : v < 0 ? "pnl-neg" : "");
      tbody.insertAdjacentHTML("beforeend",
        `<tr><td>${r.bot_id}</td>` +
        `<td class="${pc(r.realized_now)}">$${r.realized_now.toFixed(2)}</td>` +
        `<td>$${r.realized_peak.toFixed(2)}</td>` +
        `<td class="pnl-pos">$${r.banked_hwm_50.toFixed(2)}</td></tr>`);
    }
  } catch (e) { console.error("profit-sweep banner", e); }
}
window.addEventListener("DOMContentLoaded", updateProfitSweepBanner);
setInterval(updateProfitSweepBanner, 60000);
</script>
<div style="text-align:center;color:var(--muted);font-size:10px;letter-spacing:2px;padding:18px 0 24px;opacity:.55;font-style:italic;">
  &mdash; I am the one who knocks. &mdash;
</div>
</body>
</html>
"""


# ── Dashboard Server ──────────────────────────────────────────────────────────

class WebDashboard:
    """
    Lightweight aiohttp web server serving real-time bot stats.
    Uses Server-Sent Events for push updates; no polling on the client.
    Port is read from the PORT environment variable or defaults to 8080.
    """

    def __init__(self, port: int = None, tracker=None, trade_store=None):
        self.port = port or int(os.environ.get("PORT", 8080))
        self._tracker = tracker          # optional direct tracker ref
        self.trade_store = trade_store   # optional multi-bot TradeStore
        from dashboard.auth import basic_auth_middleware
        self.app = web.Application(middlewares=[basic_auth_middleware, gzip_middleware])
        self._stats_providers = []
        self._alert_buffer: list = []
        self._start_time = datetime.now(timezone.utc)
        self._scanners = {}              # chain_id → scanner, for live wallet reload
        self._seed_wallets_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "seed_wallets.json"
        )

        self._trader = None  # registered via register_trader()
        self._axiom_auth = None  # registered via register_axiom_auth()
        self._axiom_scanner = None  # registered via register_axiom_scanner()
        self._established_scanner = None  # registered via register_established_scanner()
        self._trading_paused = False  # pause/resume state
        self._live_mode = False  # set via register_trader
        self._anomaly_log: list = []  # rolling last-20 anomaly messages from watchdog

        # Strategy instances registered via register_strategies()
        self._strat_scanner = None
        self._strat_scalper = None
        self._strat_convergence = None
        self._strat_clustering = None
        self._strat_capitulation = None
        self._strat_smart_follow = None

        # ScalpQueue (backend feeder) + ScalpCapitalManager (independent pool)
        self._scalp_queue = None
        self._scalp_capital = None

        self.app.router.add_get("/",                        self._handle_index)
        self.app.router.add_get("/api/stats",               self._handle_stats)
        self.app.router.add_get("/api/trades",              self._handle_trades)
        self.app.router.add_get("/events",                  self._handle_sse)
        self.app.router.add_get("/api/seed-wallets",        self._handle_seed_wallets_get)
        self.app.router.add_post("/api/seed-wallets/add",   self._handle_seed_wallets_add)
        self.app.router.add_post("/api/seed-wallets/remove",self._handle_seed_wallets_remove)
        self.app.router.add_post("/api/sell",               self._handle_sell)
        self.app.router.add_get("/api/watchlist",           self._handle_watchlist)
        self.app.router.add_post("/api/watchlist/add",      self._handle_watchlist_add)
        self.app.router.add_get("/api/user-watchlist",      self._handle_user_watchlist_get)
        self.app.router.add_post("/api/user-watchlist/add", self._handle_user_watchlist_add)
        self.app.router.add_post("/api/user-watchlist/remove", self._handle_user_watchlist_remove)
        self.app.router.add_get("/api/positions",           self._handle_positions)
        self.app.router.add_post("/api/buy",                self._handle_buy)
        self.app.router.add_post("/api/update-axiom-token", self._handle_update_axiom_token)
        self.app.router.add_post("/api/axiom-relay",        self._handle_axiom_relay)
        self.app.router.add_post("/api/reset",              self._handle_reset)
        self.app.router.add_post("/api/profit-sweep/execute", self._handle_profit_sweep_execute)
        self.app.router.add_get("/api/profit-sweep/last-test", self._handle_profit_sweep_last_test)
        self.app.router.add_post("/api/reset-daily-pnl",     self._handle_reset_daily_pnl)
        self.app.router.add_post("/api/restore",             self._handle_restore)
        self.app.router.add_get("/api/closed-positions",   self._handle_closed_positions)
        self.app.router.add_get("/api/signal-events",      self._handle_signal_events)
        self.app.router.add_get("/api/pre-gate-events",    self._handle_pre_gate_events)
        self.app.router.add_get("/api/universe-recorder",  self._handle_universe_recorder)
        self.app.router.add_get("/api/follow-logs",         self._handle_follow_logs)
        self.app.router.add_get("/api/fresh-launches",  self._handle_fresh_launches)
        self.app.router.add_get("/api/ng-scorer-decisions",  self._handle_ng_scorer_decisions)
        self.app.router.add_get("/api/mc-recommendations", self._handle_mc_recommendations)
        self.app.router.add_get("/api/peak-traces",        self._handle_peak_traces)
        self.app.router.add_get("/api/peak-traces/{name}", self._handle_peak_trace_one)
        self.app.router.add_post("/api/pause",              self._handle_pause)
        self.app.router.add_post("/api/resume",             self._handle_resume)
        self.app.router.add_get("/api/strategies",          self._handle_strategies)
        self.app.router.add_get("/api/sol-gate",            self._handle_sol_gate)
        self.app.router.add_get("/api/diagnostics",         self._handle_diagnostics)
        self.app.router.add_get("/metrics",                 self._handle_metrics)
        self.app.router.add_get("/api/bots",                self._handle_api_bots)
        self.app.router.add_get("/api/goal",                self._handle_api_goal)
        self.app.router.add_get("/api/wallet-discovery",    self._handle_wallet_discovery)
        self.app.router.add_get("/api/regime-dial",         self._handle_regime_dial)
        self.app.router.add_get("/api/follow-capital",      self._handle_follow_capital)
        self.app.router.add_get("/api/meta-sensor",         self._handle_meta_sensor)
        self.app.router.add_get("/api/attention",            self._handle_attention)
        self.app.router.add_get("/api/pumpportal",           self._handle_pumpportal)
        self.app.router.add_get("/api/leaderboard",         self._handle_api_leaderboard)
        self.app.router.add_get("/api/bots/{bot_id}/trades",    self._handle_api_bot_trades)
        self.app.router.add_get("/api/bots/{bot_id}/positions", self._handle_api_bot_positions)
        self.app.router.add_get("/api/attribution/filters",   self._handle_attribution_filters)
        self.app.router.add_get("/api/attribution/categories", self._handle_attribution_categories)
        self.app.router.add_get("/api/attribution/regimes",   self._handle_attribution_regimes)
        self.app.router.add_get("/api/bots/{bot_id}/details", self._handle_bot_details)
        self.app.router.add_get("/api/profit-sweep-sim",      self._handle_profit_sweep_sim)
        self.app.router.add_get("/api/bots-unrealized",       self._handle_bots_unrealized)
        self.app.router.add_get("/api/shadow-readout",        self._handle_shadow_readout)
        self.app.router.add_get("/api/axiom-kol-probe",       self._handle_axiom_kol_probe)
        self.app.router.add_get("/api/axiom-kol-trades",      self._handle_axiom_kol_trades)
        self.app.router.add_post("/api/bots/{bot_id}/reset",  self._handle_bot_reset)
        self.app.router.add_post("/api/bots/{bot_id}/close/{token}",
                                 self._handle_bot_close_position)
        self.app.router.add_post("/api/bots/{bot_id}/reset-daily",
                                 self._handle_bot_reset_daily)
        self.app.router.add_get("/api/champion_proposal",     self._handle_champion_proposal)

    # ── Public API ──────────────────────────────────────────────────────────

    def register_scanner(self, chain_id: str, scanner):
        """Register a scanner so the dashboard can live-reload wallets into it."""
        self._scanners[chain_id] = scanner

    def register_provider(self, provider):
        """Register an object that provides stats via get_dashboard_stats()."""
        self._stats_providers.append(provider)
        # If the provider looks like a tracker, keep a reference for /api/trades
        if self._tracker is None and hasattr(provider, "get_all_trades"):
            self._tracker = provider

    def register_trader(self, trader):
        """Register the trader for manual sell/buy actions from the dashboard."""
        self._trader = trader
        self._live_mode = bool(getattr(trader, "private_key", ""))

    def register_axiom_auth(self, auth_manager):
        """Register the Axiom auth manager so tokens can be hot-updated via API."""
        self._axiom_auth = auth_manager

    def register_axiom_scanner(self, scanner):
        """Register the AxiomScanner so relay tokens can be injected via /api/axiom-relay."""
        self._axiom_scanner = scanner

    def register_established_scanner(self, scanner):
        """Register the AxiomTrendingScanner so mc_candidates feed into the Radar panel."""
        self._established_scanner = scanner

    def register_scalp_queue(self, scalp_queue, scalp_capital):
        """Register ScalpQueue feeder + ScalpCapitalManager for the Scalp Queue dashboard panel."""
        self._scalp_queue = scalp_queue
        self._scalp_capital = scalp_capital

    def register_breakout(self, *, state, capital, db):
        """Wire breakout strategy state, capital manager, and DB to dashboard."""
        self._breakout_state = state
        self._breakout_capital = capital
        self._breakout_db = db
        self.app.router.add_get("/api/breakout/state",      self._handle_breakout_state)
        self.app.router.add_get("/api/breakout/watchlist",  self._handle_breakout_watchlist)
        self.app.router.add_get("/api/breakout/positions",  self._handle_breakout_positions)
        self.app.router.add_get("/api/breakout/closed",     self._handle_breakout_closed)

    async def _handle_breakout_state(self, request):
        return web.Response(
            text=json.dumps(self._breakout_capital.stats()),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_breakout_watchlist(self, request):
        return web.Response(
            text=json.dumps(self._breakout_state.watchlist),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_breakout_positions(self, request):
        out = []
        for pos in self._breakout_state.open_positions.values():
            out.append({
                "symbol": pos.symbol,
                "entry_time": pos.entry_time,
                "entry_price": pos.entry_price,
                "qty": pos.qty,
                "cost_usd": pos.cost_usd,
                "score": pos.score,
                "resistance_level": pos.resistance_level,
                "tp_price": pos.tp_price,
                "stop_price": pos.stop_price,
                "peak_price": pos.peak_price,
                "tp_hit": pos.tp_hit,
                "score_breakdown": pos.score_breakdown,
                "reason_entry": pos.reason_entry,
            })
        return web.Response(
            text=json.dumps(out),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_breakout_closed(self, request):
        try:
            limit = int(request.query.get("limit", 50))
        except (ValueError, TypeError):
            limit = 50
        rows = self._breakout_db.get_closed_positions(limit=limit)
        return web.Response(
            text=json.dumps(rows),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    def register_strategies(
        self,
        scanner=None,
        scalper=None,
        convergence=None,
        clustering=None,
        capitulation=None,
        smart_follow=None,
    ):
        """Register strategy instances so the Active Strategies panel can show live status."""
        if smart_follow is not None:
            self._strat_smart_follow = smart_follow
        if scanner is not None:
            self._strat_scanner = scanner
        if scalper is not None:
            self._strat_scalper = scalper
        if convergence is not None:
            self._strat_convergence = convergence
        if clustering is not None:
            self._strat_clustering = clustering
        if capitulation is not None:
            self._strat_capitulation = capitulation

    def add_alert(self, message: str):
        """Buffer a live alert for the event feed."""
        self._alert_buffer.append(message)
        if len(self._alert_buffer) > 200:
            self._alert_buffer = self._alert_buffer[-200:]

    async def run(self):
        """Start the aiohttp server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        logger.info(f"[Dashboard] Web dashboard running at http://0.0.0.0:{self.port}")

    # ── HTTP Handlers ────────────────────────────────────────────────────────

    async def _handle_index(self, request):
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def _handle_stats(self, request):
        if getattr(self, "warming", False):
            # early-bound server during boot (2026-06-11 deploy-502 fix):
            # answer honestly instead of an edge 502.
            return web.json_response({"warming": True, "live_mode": None,
                                      "uptime": "booting",
                                      "note": "fleet loading — full stats shortly"})
        try:
            stats = await self._build_stats()
        except Exception as e:
            return web.json_response({"warming": True, "error": str(e)[:120]},
                                     status=503)
        # Quarantine label (2026-05-29): /api/stats reflects the LEGACY single-bot
        # trader + aggregate, NOT the 121-bot fleet. Marked so consumers don't
        # mistake its 'overall'/'daily_pnl' for fleet performance — use /api/bots,
        # /api/bots-unrealized, or /api/leaderboard for the fleet.
        if isinstance(stats, dict):
            stats["scope"] = "legacy_single_bot_plus_aggregate"
            stats["scope_note"] = ("NOT the multi-bot fleet — see /api/bots, "
                                   "/api/bots-unrealized, /api/leaderboard for fleet metrics")
        return web.Response(
            text=json.dumps(stats),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_trades(self, request):
        """GET /api/trades — bandwidth-limited.

        Default returns the most recent 200 trades (~200KB). Caller can request
        more via ?limit=N (capped at 5000) or ALL via ?all=1 (~20MB — use sparingly).
        Egress optimization shipped 2026-05-08 after Railway billing audit
        showed ~2TB/period from a dashboard tab polling the unbounded endpoint.
        """
        trades = []
        if self._tracker is not None:
            try:
                trades = self._tracker.get_all_trades()
            except Exception as e:
                logger.debug(f"[Dashboard] trades provider error: {e}")
        # Option B split (2026-05-23): multi-bot records live in
        # trades_multi.json owned by MultiBotTradeStore. Merge them in at
        # read time. Reading is safe — no shared mutable state, both files
        # are atomic-rewrite by their respective owners.
        if self.trade_store is not None:
            try:
                trades = list(trades) + self.trade_store.load_trades()
            except Exception as e:
                logger.debug(f"[Dashboard] multi-bot trades read error: {e}")
        # Apply pagination unless ?all=1 is set
        try:
            want_all = request.query.get('all', '0') in ('1', 'true', 'yes')
            want_full = request.query.get('full', '0') in ('1', 'true', 'yes')
            limit = int(request.query.get('limit', 200))
            limit = max(1, min(limit, 5000))
        except Exception:
            want_all = False
            want_full = False
            limit = 200
        # EGRESS BURST CONTROL (2026-06-04): the heavy payloads (full=1 ~69MB,
        # all=1 ~20MB) are budget-gated. Beyond the shared budget, DOWNGRADE to the
        # trimmed 200-record response (~99% byte cut) so a looping / fan-out consumer
        # can't pull tens of MB repeatedly. A single or occasional heavy pull always
        # passes. Normal dashboard polling uses the trimmed path and is never gated.
        egress_throttled = False
        if (want_all or want_full) and not _egress_allow_heavy():
            want_all = False
            want_full = False
            limit = min(limit, 200)
            egress_throttled = True
        # Always sort newest-first when paginating — even when total < limit.
        # Previously this branch only triggered when len > limit, so callers
        # asking for "limit=2000" against a 1702-record store got chronological
        # order (oldest first) and the most recent sells got hidden in the
        # tail of the response. Now newest is always first.
        if not want_all and isinstance(trades, list):
            try:
                trades = sorted(trades, key=lambda t: t.get('time') or '', reverse=True)[:limit]
            except Exception:
                trades = trades[-limit:]
        # Egress trim 2026-05-18: drop entry_meta from response unless ?full=1.
        # Dashboard JS never reads entry_meta — shipping it adds ~25KB per trade
        # (50+ feature keys) and was driving response size to 5.4MB per /api/trades
        # poll. Removing it cuts to ~300KB. Audit/postmortem callers can request
        # the full payload with ?full=1 (subject to the egress budget above).
        if not want_full and isinstance(trades, list):
            _TRADE_KEEP = {
                'type', 'strategy', 'bot_id', 'chain', 'token', 'address',
                'entry_price', 'exit_price', 'usd_received',
                'pnl', 'pnl_pct', 'time', 'reason',
                'max_drawdown_pct', 'hold_secs',
                'entry_market_cap_usd', 'entry_age_hours', 'entry_volume_h1_usd',
                'pair_address', 'peak_pnl_pct', 'peak_pnl_at_secs',
                'realized_slippage_pct',
            }
            trades = [
                {k: v for k, v in t.items() if k in _TRADE_KEEP}
                for t in trades if isinstance(t, dict)
            ]
        _hdrs = {"Access-Control-Allow-Origin": "*"}
        if egress_throttled:
            # Tell the caller it got the trimmed payload due to the egress budget.
            # Header alone was MISSED twice by json.load consumers (false -$42 and
            # -$79 goal meters) -> the throttled body is now a dict with an explicit
            # flag. Normal (non-throttled) responses keep their list shape, so
            # dashboard JS is unaffected (it never requests heavy payloads).
            _hdrs["X-Egress-Throttled"] = "1"
            trades = {"egress_throttled": True,
                      "note": ("heavy payload downgraded to trimmed 200 records by the "
                               "egress budget — retry after ~60s for the full pull"),
                      "trades": trades}
        # Serialize heavy payloads OFF the event loop: json.dumps of a full=1/all=1
        # body (tens of MB) blocked the loop long enough to starve concurrent
        # requests (/api/stats returned empty bodies 3x on 2026-06-10 during
        # analysis pulls). Threaded dumps keeps the server responsive.
        if want_all or want_full:
            _body = await asyncio.to_thread(json.dumps, trades)
        else:
            _body = json.dumps(trades)
        return web.Response(
            text=_body,
            content_type="application/json",
            headers=_hdrs
        )

    # ── Seed Wallet Helpers ──────────────────────────────────────────────────

    def _load_seed_wallets(self) -> dict:
        try:
            with open(self._seed_wallets_path) as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f"[Dashboard] Could not load seed_wallets.json: {e}")
            return {}

    def _save_seed_wallets(self, wallets: dict):
        try:
            os.makedirs(os.path.dirname(self._seed_wallets_path), exist_ok=True)
            with open(self._seed_wallets_path, "w") as f:
                json.dump(wallets, f, indent=2)
        except Exception as e:
            logger.warning(f"[Dashboard] Could not save seed_wallets.json: {e}")

    async def _handle_seed_wallets_get(self, request):
        wallets = self._load_seed_wallets()
        return web.Response(
            text=json.dumps({"ok": True, "wallets": wallets}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_seed_wallets_add(self, request):
        try:
            body    = await request.json()
            address = body.get("address", "").strip()
            score   = float(body.get("score", 75.0))
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        if not address or len(address) < 32:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid wallet address"}),
                status=400, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        score = max(0.0, min(100.0, score))
        wallets = self._load_seed_wallets()
        wallets[address] = score
        self._save_seed_wallets(wallets)
        # Live-reload into running strategies
        for scanner in self._scanners.values():
            cwcs = getattr(scanner, "_convergence_strategy", None)
            if cwcs and hasattr(cwcs, "add_wallet"):
                cwcs.add_wallet(address, quality_score=score)
            copy_trader = getattr(scanner, "_copy_trader", None)
            if copy_trader and hasattr(copy_trader, "add_wallet"):
                copy_trader.add_wallet(address)
        logger.info(f"[Dashboard] Seed wallet added: {address[:12]}… score={score:.0f}")
        return web.Response(
            text=json.dumps({"ok": True, "address": address, "score": score}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_seed_wallets_remove(self, request):
        try:
            body    = await request.json()
            address = body.get("address", "").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        wallets = self._load_seed_wallets()
        removed = address in wallets
        wallets.pop(address, None)
        self._save_seed_wallets(wallets)
        for scanner in self._scanners.values():
            strategy = getattr(scanner, "_convergence_strategy", None)
            if strategy and hasattr(strategy, "remove_wallet"):
                strategy.remove_wallet(address)
            copy_trader = getattr(scanner, "_copy_trader", None)
            if copy_trader and hasattr(copy_trader, "remove_wallet"):
                copy_trader.remove_wallet(address)
        logger.info(f"[Dashboard] Seed wallet removed: {address[:12]}… (was_present={removed})")
        return web.Response(
            text=json.dumps({"ok": True, "removed": removed}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_profit_sweep_last_test(self, request):
        """GET /api/profit-sweep/last-test — read-only readout of the last sweep test-fire
        result (dry or live). No money, no auth needed — lets us reliably verify the
        boot test result without scraping flooded logs."""
        import os as _os
        from pathlib import Path as _Path
        cors = {"Access-Control-Allow-Origin": "*"}
        path = _Path(_os.environ.get("DATA_DIR") or "/data") / ".profit_sweep_last_test.json"
        if not path.exists():
            return web.json_response({"exists": False}, headers=cors)
        try:
            return web.json_response({"exists": True, "data": json.loads(path.read_text())},
                                     headers=cors)
        except Exception as e:
            return web.json_response({"exists": True, "error": str(e)}, headers=cors)

    async def _handle_profit_sweep_execute(self, request):
        """POST /api/profit-sweep/execute — fire ONE profit sweep (the manual $5 test).
        DEFENSE IN DEPTH: (1) auth middleware gates this POST and fails CLOSED in live;
        (2) inert unless PROFIT_SWEEP_ENABLED; (3) dry_run defaults True — a LIVE send
        needs body {"dry_run": false, "confirm": "SEND"}; (4) hard USD cap from
        core.profit_sweeper.test_cap_usd() ($5 default). Body: {dry_run, confirm}."""
        cors = {"Access-Control-Allow-Origin": "*"}
        from core import profit_sweeper as _ps
        if not _ps.enabled():
            return web.json_response(
                {"ok": False, "error": "PROFIT_SWEEP_ENABLED is off — sweep disabled"},
                status=403, headers=cors)
        if not self._trader:
            return web.json_response({"ok": False, "error": "Trader not registered"},
                                     status=500, headers=cors)
        try:
            body = await request.json()
        except Exception:
            body = {}
        dry_run = bool(body.get("dry_run", True))
        if not dry_run and str(body.get("confirm", "")) != "SEND":
            return web.json_response(
                {"ok": False, "error": 'live send requires {"dry_run": false, "confirm": "SEND"}'},
                status=400, headers=cors)
        max_usd = _ps.test_cap_usd()
        try:
            result = await self._trader.execute_profit_sweep(dry_run=dry_run, max_usd=max_usd)
        except Exception as e:
            logger.error(f"[Dashboard] profit-sweep execute error: {e}")
            return web.json_response({"ok": False, "error": str(e)}, status=500, headers=cors)
        return web.json_response({"ok": True, "dry_run": dry_run, "max_usd_cap": max_usd,
                                  "result": result}, headers=cors)

    async def _handle_sell(self, request):
        """POST /api/sell — manually sell an open position."""
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            pct = float(body.get("pct", 1.0))
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not self._trader:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Trader not registered"}),
                status=500, content_type="application/json", headers=cors,
            )
        # Defensive lowercase: open_positions keys are lowercase per recent
        # case-preservation fix. If the JSON sent mixed-case, .get() would
        # silently miss and return 404 (the original 2026-04-29 manual-sell bug).
        addr_key = token_address.lower()
        position = self._trader.open_positions.get(addr_key)
        if not position:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Position not found"}),
                status=404, content_type="application/json", headers=cors,
            )
        pct = max(0.01, min(1.0, pct))
        try:
            # Pass the canonical mint case from the Position so trader.sell's
            # Jupiter call works (base58 case-sensitive).
            result = await self._trader.sell(
                position.token_address, position.token_symbol,
                f"Manual sell from dashboard ({pct*100:.0f}%)", pct=pct
            )
            # trader.sell returns {ok, reason, pnl_usd} — propagate so the
            # user actually sees if the swap failed (quote retry-exhaustion,
            # already-selling, etc) instead of always seeing "ok" while the
            # swap silently fails. Backward-compatible: if result is None
            # (older trader builds) treat as success.
            if isinstance(result, dict) and result.get("ok") is False:
                self.add_alert(
                    f"Manual sell FAILED: {position.token_symbol} — {result.get('reason')}"
                )
                return web.Response(
                    text=json.dumps({
                        "ok": False, "symbol": position.token_symbol,
                        "error": f"Sell failed: {result.get('reason','unknown')}",
                    }),
                    status=502, content_type="application/json", headers=cors,
                )
            pnl = result.get("pnl_usd") if isinstance(result, dict) else None
            self.add_alert(
                f"Manual sell: {position.token_symbol} ({pct*100:.0f}%)"
                + (f" pnl=${pnl:+.2f}" if pnl is not None else "")
            )
            return web.Response(
                text=json.dumps({
                    "ok": True, "symbol": position.token_symbol, "pct": pct,
                    "pnl_usd": pnl,
                }),
                content_type="application/json", headers=cors,
            )
        except Exception as e:
            logger.error(f"[Dashboard] Manual sell error: {e}")
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e)}),
                status=500, content_type="application/json", headers=cors,
            )

    async def _handle_positions(self, request):
        """GET /api/positions — direct read of all open positions from the trader."""
        cors = {"Access-Control-Allow-Origin": "*"}
        positions = []
        if self._trader:
            now = datetime.now(timezone.utc)
            for addr, pos in self._trader.open_positions.items():
                entry = getattr(pos, "entry_price_usd", 0)
                current = getattr(pos, "current_price_usd", 0) or entry
                amount = getattr(pos, "amount_usd", 0) or getattr(pos, "amount_sol_spent", 0)
                multiplier = (current / entry) if entry > 0 else 1.0
                pnl_usd = getattr(pos, "pnl_usd", (multiplier - 1) * amount)
                entry_time = getattr(pos, "entry_time", None)
                hold_secs = int((now - entry_time).total_seconds()) if entry_time else 0
                positions.append({
                    "token_address": addr,
                    "symbol": getattr(pos, "token_symbol", addr[:8]),
                    "chain": getattr(pos, "chain_id", "solana"),
                    "strategy": getattr(pos, "strategy", "scanner"),
                    "entry_price": entry,
                    "current_price": current,
                    "pnl_usd": round(pnl_usd, 2),
                    "multiplier": round(multiplier, 4),
                    "hold_secs": hold_secs,
                    "amount_usd": amount,
                    "reason": getattr(pos, "reason", ""),
                    "pair_address": getattr(pos, "pair_address", ""),
                })
        return web.Response(
            text=json.dumps({"ok": True, "positions": positions, "count": len(positions)}),
            content_type="application/json", headers=cors,
        )

    async def _handle_watchlist(self, request):
        """GET /api/watchlist — return recommended tokens from scanner watchlists."""
        cors = {"Access-Control-Allow-Origin": "*"}
        combined = []
        for chain_id, scanner in self._scanners.items():
            if hasattr(scanner, "get_watchlist"):
                for item in scanner.get_watchlist():
                    item["chain"] = chain_id
                    combined.append(item)
        combined.sort(key=lambda x: x["score"], reverse=True)
        return web.Response(
            text=json.dumps({"ok": True, "watchlist": combined[:20]}),
            content_type="application/json", headers=cors,
        )

    async def _handle_watchlist_add(self, request):
        """POST /api/watchlist/add — manually pin a token to the recommended watchlist."""
        import time as _time
        import aiohttp as _aiohttp
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            token_symbol  = body.get("token_symbol", "").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not token_address:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Missing token_address"}),
                status=400, content_type="application/json", headers=cors,
            )
        # If no symbol provided, look it up from DexScreener
        if not token_symbol:
            try:
                dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                async with _aiohttp.ClientSession() as _sess:
                    async with _sess.get(dex_url, timeout=_aiohttp.ClientTimeout(total=5)) as _r:
                        if _r.status == 200:
                            _data = await _r.json(content_type=None)
                            _pairs = _data.get("pairs") or []
                            if _pairs:
                                token_symbol = _pairs[0].get("baseToken", {}).get("symbol", "") or ""
            except Exception:
                pass
            if not token_symbol:
                token_symbol = token_address[:8]
        entry = {
            "symbol": token_symbol,
            "score": 60,   # placeholder — manual entry
            "timestamp": _time.time(),
            "price": 0.0,
            "mcap": 0,
            "reason": "Manual — pinned from dashboard",
            "dex_url": f"https://dexscreener.com/solana/{token_address}",
            "age_seconds": 0,
        }
        added = False
        for scanner in self._scanners.values():
            if hasattr(scanner, "watchlist"):
                scanner.watchlist[token_address] = entry
                added = True
        if not added:
            return web.Response(
                text=json.dumps({"ok": False, "error": "No scanners available"}),
                status=500, content_type="application/json", headers=cors,
            )
        return web.Response(
            text=json.dumps({"ok": True, "symbol": token_symbol}),
            content_type="application/json", headers=cors,
        )

    # ── User watchlist (curator-driven April-era specialization) ────────────
    def _get_dip_scanner(self):
        """Find the DipScanner instance among registered scanners."""
        for sc in self._scanners.values():
            # DipScanner has add_user_watchlist method; others don't.
            if hasattr(sc, "add_user_watchlist"):
                return sc
            # Some scanners wrap a dip_scanner attribute.
            inner = getattr(sc, "dip_scanner", None)
            if inner is not None and hasattr(inner, "add_user_watchlist"):
                return inner
        return None

    async def _handle_user_watchlist_get(self, request):
        """GET /api/user-watchlist — return curator-curated tokens with DexScreener enrichment."""
        import aiohttp as _aiohttp
        cors = {"Access-Control-Allow-Origin": "*"}
        dip = self._get_dip_scanner()
        if dip is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "DipScanner not registered", "tokens": []}),
                content_type="application/json", headers=cors,
            )
        addrs = dip.get_user_watchlist()
        # Always seed placeholder rows first — DexScreener enrichment is best-effort.
        # This way the dashboard NEVER shows "Empty" while there are addresses on
        # the list (prior bug: DS rate-limit / non-200 caused tokens=[] with count>0).
        tokens = [
            {
                "address": a,
                "symbol": "?",
                "name": "?",
                "mcap": 0,
                "price": 0,
                "vol_h24": 0, "vol_h1": 0, "vol_m5": 0,
                "pc_h24": None, "pc_h6": None, "pc_h1": None, "pc_m5": None,
                "liq_usd": 0,
                "dex_url": f"https://dexscreener.com/solana/{a}",
            } for a in addrs
        ]
        # Address → index in tokens list, for in-place enrichment.
        idx_by_addr = {a: i for i, a in enumerate(addrs)}
        if addrs:
            # DexScreener /tokens/{csv} truncates the response — returns at most
            # ~30 pairs total per call, and high-liquidity tokens have multiple
            # pools that dominate the response. With 30 addresses per batch we
            # routinely get coverage for only ~20-23 unique tokens. We need a
            # second pass for the addresses that came back empty.
            best: dict = {}

            async def _fetch_batch(sess, batch):
                """Query DS, fold each pair into `best` (keep highest-liq per base)."""
                url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                try:
                    async with sess.get(url, timeout=_aiohttp.ClientTimeout(total=10)) as r:
                        if r.status != 200:
                            logger.warning(f"[Dashboard] user-watchlist DS status {r.status}")
                            return
                        data = await r.json(content_type=None)
                    for p in (data or {}).get("pairs", []) or []:
                        base = (p.get("baseToken") or {}).get("address", "").lower()
                        if not base:
                            continue
                        liq = float((p.get("liquidity") or {}).get("usd") or 0)
                        if base not in best or liq > float((best[base].get("liquidity") or {}).get("usd") or 0):
                            best[base] = p
                except Exception as be:
                    logger.warning(f"[Dashboard] user-watchlist DS batch err: {be}")

            try:
                async with _aiohttp.ClientSession() as sess:
                    # Pass 1: big batches (30) for efficiency
                    for i in range(0, len(addrs), 30):
                        await _fetch_batch(sess, addrs[i:i + 30])
                    # Pass 2: re-query missing addresses in smaller batches (10)
                    # so DS doesn't drop them. Lowercase compare because DS
                    # normalizes base addresses.
                    missing = [a for a in addrs if a.lower() not in best]
                    for i in range(0, len(missing), 10):
                        await _fetch_batch(sess, missing[i:i + 10])
                for addr, p in best.items():
                    i = idx_by_addr.get(addr)
                    if i is None:
                        continue
                    bt = p.get("baseToken", {}) or {}
                    pc = p.get("priceChange", {}) or {}
                    vol = p.get("volume", {}) or {}
                    tokens[i].update({
                        "symbol": bt.get("symbol", "?"),
                        "name": bt.get("name", "?"),
                        "mcap": p.get("marketCap") or p.get("fdv") or 0,
                        "price": float(p.get("priceUsd") or 0),
                        "vol_h24": vol.get("h24", 0),
                        "vol_h1": vol.get("h1", 0),
                        "vol_m5": vol.get("m5", 0),
                        "pc_h24": pc.get("h24"),
                        "pc_h6": pc.get("h6"),
                        "pc_h1": pc.get("h1"),
                        "pc_m5": pc.get("m5"),
                        "liq_usd": float((p.get("liquidity") or {}).get("usd") or 0),
                    })
            except Exception as e:
                logger.warning(f"[Dashboard] user-watchlist enrichment err: {e}")
        return web.Response(
            text=json.dumps({"ok": True, "count": len(addrs), "tokens": tokens}),
            content_type="application/json", headers=cors,
        )

    async def _handle_user_watchlist_add(self, request):
        """POST /api/user-watchlist/add — add address. Body: {address: "..."}"""
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            addr = (body.get("address") or "").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not addr:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Missing address"}),
                status=400, content_type="application/json", headers=cors,
            )
        dip = self._get_dip_scanner()
        if dip is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "DipScanner not registered"}),
                status=500, content_type="application/json", headers=cors,
            )
        added = dip.add_user_watchlist(addr)
        return web.Response(
            text=json.dumps({"ok": True, "added": added, "address": addr.lower(), "count": len(dip.get_user_watchlist())}),
            content_type="application/json", headers=cors,
        )

    async def _handle_user_watchlist_remove(self, request):
        """POST /api/user-watchlist/remove — remove address. Body: {address: "..."}"""
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            addr = (body.get("address") or "").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not addr:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Missing address"}),
                status=400, content_type="application/json", headers=cors,
            )
        dip = self._get_dip_scanner()
        if dip is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "DipScanner not registered"}),
                status=500, content_type="application/json", headers=cors,
            )
        removed = dip.remove_user_watchlist(addr)
        return web.Response(
            text=json.dumps({"ok": True, "removed": removed, "address": addr.lower(), "count": len(dip.get_user_watchlist())}),
            content_type="application/json", headers=cors,
        )

    async def _handle_buy(self, request):
        """POST /api/buy — manually buy a token from the watchlist."""
        import aiohttp as _aiohttp
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            token_symbol = body.get("token_symbol", "").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not self._trader:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Trader not registered"}),
                status=500, content_type="application/json", headers=cors,
            )
        if not token_address:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Missing token_address"}),
                status=400, content_type="application/json", headers=cors,
            )
        if token_address in self._trader.open_positions:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Already holding this token"}),
                status=400, content_type="application/json", headers=cors,
            )
        # Resolve symbol if missing or looks like an address fragment
        if not token_symbol or token_symbol.lower() == token_address[:len(token_symbol)].lower():
            try:
                dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                async with _aiohttp.ClientSession() as _sess:
                    async with _sess.get(dex_url, timeout=_aiohttp.ClientTimeout(total=5)) as _r:
                        if _r.status == 200:
                            _data = await _r.json(content_type=None)
                            _pairs = _data.get("pairs") or []
                            if _pairs:
                                token_symbol = _pairs[0].get("baseToken", {}).get("symbol", "") or token_symbol
            except Exception:
                pass
            if not token_symbol:
                token_symbol = "MANUAL"
        try:
            await self._trader.buy(
                token_address=token_address,
                token_symbol=token_symbol,
                reason="Manual buy from dashboard",
                signal_score=0
            )
            if token_address.lower() not in self._trader.open_positions:
                return web.Response(
                    text=json.dumps({"ok": False, "error": "Buy failed — check logs (price unavailable or risk limit hit)"}),
                    status=400, content_type="application/json", headers=cors,
                )
            self.add_alert(f"Manual buy: {token_symbol or token_address[:8]}")
            return web.Response(
                text=json.dumps({"ok": True, "symbol": token_symbol}),
                content_type="application/json", headers=cors,
            )
        except Exception as e:
            logger.error(f"[Dashboard] Manual buy error: {e}")
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e)}),
                status=500, content_type="application/json", headers=cors,
            )

    async def _handle_update_axiom_token(self, request):
        """POST /api/update-axiom-token — hot-update Axiom tokens without restart.
        Body: {"access_token": "...", "refresh_token": "...", "secret": "..."}
        The secret must match TOKEN_UPDATE_SECRET env var (defaults to 'changeme').
        """
        import os as _os
        import base64 as _b64, json as _json, time as _t
        cors = {"Access-Control-Allow-Origin": "*"}
        expected_secret = _os.environ.get("TOKEN_UPDATE_SECRET", "changeme")
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if body.get("secret") != expected_secret:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Unauthorized"}),
                status=401, content_type="application/json", headers=cors,
            )
        access = body.get("access_token", "").strip()
        refresh = body.get("refresh_token", "").strip()
        if not access:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Missing access_token"}),
                status=400, content_type="application/json", headers=cors,
            )
        if not self._axiom_auth:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Axiom auth not registered"}),
                status=500, content_type="application/json", headers=cors,
            )
        # Update in-memory tokens
        self._axiom_auth.auth_token = access
        if refresh:
            self._axiom_auth.refresh_token = refresh
        # Reset the cached client so it rebuilds with new token
        self._axiom_auth._client = None
        # Parse real expiry from JWT
        try:
            payload = access.split('.')[1]
            payload += '=' * (4 - len(payload) % 4)
            data = _json.loads(_b64.urlsafe_b64decode(payload))
            exp = float(data.get('exp', 0))
            ttl = exp - _t.time()
        except Exception:
            ttl = 0
        logger.info(
            f"[Dashboard] Axiom token hot-updated via API — "
            f"TTL={ttl:.0f}s ({ttl/60:.1f} min)"
        )
        return web.Response(
            text=json.dumps({"ok": True, "ttl_seconds": int(ttl)}),
            content_type="application/json", headers=cors,
        )

    async def _handle_axiom_relay(self, request):
        """POST /api/axiom-relay — ingest token events forwarded from local relay script.
        Body: {"secret": "...", "tokens": [<raw_token_dict>, ...]}
        The secret must match TOKEN_UPDATE_SECRET env var.
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        expected_secret = _os.environ.get("TOKEN_UPDATE_SECRET", "changeme")
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if body.get("secret") != expected_secret:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Unauthorized"}),
                status=401, content_type="application/json", headers=cors,
            )
        if not self._axiom_scanner:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Scanner not registered"}),
                status=503, content_type="application/json", headers=cors,
            )
        tokens = body.get("tokens", [])
        processed = 0
        for raw in tokens:
            try:
                await self._axiom_scanner._process_token(raw)
                processed += 1
            except Exception as e:
                logger.warning(f"[Dashboard] axiom-relay token error: {e}")
        logger.info(f"[Dashboard] /api/axiom-relay — processed {processed}/{len(tokens)} tokens")
        return web.Response(
            text=json.dumps({"ok": True, "processed": processed}),
            content_type="application/json", headers=cors,
        )

    async def _handle_closed_positions(self, request):
        """GET /api/closed-positions — returns append-only closed position history."""
        import csv, os as _os
        from dashboard.tracker import CLOSED_LOG_FILE
        try:
            from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _co
        except Exception:
            _co = ""
        cors = {"Access-Control-Allow-Origin": "*"}
        rows = []
        if _os.path.exists(CLOSED_LOG_FILE):
            try:
                with open(CLOSED_LOG_FILE, newline="") as f:
                    reader = csv.DictReader(f)
                    has_drawdown = "max_drawdown_pct" in (reader.fieldnames or [])
                    for row in reader:
                        if not has_drawdown:
                            overflow = row.pop(None, None)
                            row["max_drawdown_pct"] = overflow[0] if isinstance(overflow, list) and overflow else ""
                        # Skip pre-cutoff rows. closed_positions.csv keys vary;
                        # try common time-ish fields.
                        if _co:
                            ts = row.get("close_time") or row.get("closed_at") or row.get("time") or ""
                            if ts < _co:
                                continue
                        rows.append(row)
            except Exception as e:
                return web.Response(text=json.dumps({"error": str(e)}),
                                    status=500, content_type="application/json", headers=cors)
        return web.Response(text=json.dumps(rows), content_type="application/json", headers=cors)

    async def _handle_signal_events(self, request):
        """GET /api/signal-events — returns full-population signal events.

        Query params:
          ?stats=1  — return just counts (file size, total records)
          ?limit=N  — return last N JSONL records (default 500)
          ?tail=1   — return last N records as a JSON array
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        data_dir = _os.environ.get('DATA_DIR', '.')
        path = _os.path.join(data_dir, 'signal_events.jsonl')

        if not _os.path.exists(path):
            return web.Response(
                text=json.dumps({"exists": False, "path": path}),
                content_type="application/json", headers=cors)

        if request.query.get('stats') == '1':
            try:
                size = _os.path.getsize(path)
                with open(path, encoding='utf-8') as f:
                    lines = sum(1 for _ in f)
                return web.Response(
                    text=json.dumps({
                        "exists": True, "path": path,
                        "size_bytes": size, "records": lines,
                    }),
                    content_type="application/json", headers=cors)
            except Exception as e:
                return web.Response(
                    text=json.dumps({"error": str(e)}),
                    status=500, content_type="application/json", headers=cors)

        try:
            limit = int(request.query.get('limit', '500'))
        except ValueError:
            limit = 500
        try:
            with open(path, encoding='utf-8') as f:
                all_lines = f.readlines()
            tail = all_lines[-limit:]
            records = []
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return web.Response(
                text=json.dumps(records),
                content_type="application/json", headers=cors)
        except Exception as e:
            return web.Response(
                text=json.dumps({"error": str(e)}),
                status=500, content_type="application/json", headers=cors)

    async def _handle_pre_gate_events(self, request):
        """GET /api/pre-gate-events — returns Axiom scanner gate events.

        Each record: ts, token, addr, mcap_usd, liq_usd, outcome, micro_cap_path.
        Used to mine which gates over-filter pre-signal tokens. Shadow only.

        Query params:
          ?stats=1  — return just counts (file size, total records)
          ?limit=N  — return last N JSONL records (default 500)
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        # Try DATA_DIR first, fall back to cwd (matches recorder behavior).
        data_dir = _os.environ.get('DATA_DIR') or '/data'
        path = _os.path.join(data_dir, 'pre_gate_events.jsonl')
        if not _os.path.exists(path):
            fallback = 'pre_gate_events.jsonl'
            if _os.path.exists(fallback):
                path = fallback
            else:
                return web.Response(
                    text=json.dumps({"exists": False, "checked_paths": [path, fallback]}),
                    content_type="application/json", headers=cors)

        if request.query.get('stats') == '1':
            try:
                size = _os.path.getsize(path)
                with open(path, encoding='utf-8') as f:
                    lines = sum(1 for _ in f)
                return web.Response(
                    text=json.dumps({
                        "exists": True, "path": path,
                        "size_bytes": size, "records": lines,
                    }),
                    content_type="application/json", headers=cors)
            except Exception as e:
                return web.Response(
                    text=json.dumps({"error": str(e)}),
                    status=500, content_type="application/json", headers=cors)

        try:
            limit = int(request.query.get('limit', '500'))
        except ValueError:
            limit = 500
        try:
            with open(path, encoding='utf-8') as f:
                all_lines = f.readlines()
            tail = all_lines[-limit:]
            records = []
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return web.Response(
                text=json.dumps(records),
                content_type="application/json", headers=cors)
        except Exception as e:
            return web.Response(
                text=json.dumps({"error": str(e)}),
                status=500, content_type="application/json", headers=cors)

    async def _handle_follow_logs(self, request):
        """GET /api/follow-logs — serve smart_follow's signal + exit logs.

        smart_money_follow writes follow_signals.jsonl (which elite wallets triggered
        each fire) and follow_exits.jsonl (when followed elites sold + their hold/return)
        to DATA_DIR. This read-only endpoint exposes the tails so scripts/follow_wallet_audit.py
        can run off-box to find junk wallets + calibrate exits. ?limit=N per file (default 500).
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        dd = _os.environ.get('DATA_DIR') or '.'
        try:
            limit = max(1, min(int(request.query.get('limit', '500')), 2000))
        except ValueError:
            limit = 500
        out = {}
        for key, fname in (("signals", "follow_signals.jsonl"), ("exits", "follow_exits.jsonl")):
            recs = []
            p = _os.path.join(dd, fname)
            if not _os.path.exists(p):
                p = fname  # local fallback
            try:
                if _os.path.exists(p):
                    with open(p, encoding='utf-8') as f:
                        for line in f.readlines()[-limit:]:
                            line = line.strip()
                            if line:
                                try:
                                    recs.append(json.loads(line))
                                except json.JSONDecodeError:
                                    pass
            except Exception:
                pass
            out[key] = recs
        out["distinct_signal_tokens"] = len({r.get("token") for r in out.get("signals", []) if r.get("token")})
        return web.Response(text=json.dumps(out), content_type="application/json", headers=cors)

    async def _handle_universe_recorder(self, request):
        """GET /api/universe-recorder — returns universe-recorder dip events.

        The universe recorder runs as a bundled daemon thread inside main.py
        and writes broader-universe dip observations (tokens the bot saw but
        did NOT enter on) to {DATA_DIR}/universe_recorder/events.jsonl.

        Used to mine for NEW entry opportunities — patterns visible in the
        broader DexScreener/GeckoTerminal universe that our current trigger
        set doesn't catch.

        Query params:
          ?stats=1  — return just counts (file size, total records)
          ?limit=N  — return last N JSONL records (default 1000)
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        # Recorder uses RECORDER_DATA_DIR with default /data/universe_recorder on Railway.
        recorder_dir = (
            _os.environ.get('RECORDER_DATA_DIR')
            or _os.path.join(_os.environ.get('DATA_DIR') or '/data', 'universe_recorder')
        )
        path = _os.path.join(recorder_dir, 'events.jsonl')
        if not _os.path.exists(path):
            fallback = _os.path.join('.universe_recorder', 'events.jsonl')
            if _os.path.exists(fallback):
                path = fallback
            else:
                return web.Response(
                    text=json.dumps({"exists": False, "checked_paths": [path, fallback]}),
                    content_type="application/json", headers=cors)

        if request.query.get('stats') == '1':
            try:
                size = _os.path.getsize(path)
                with open(path, encoding='utf-8') as f:
                    lines = sum(1 for _ in f)
                return web.Response(
                    text=json.dumps({
                        "exists": True, "path": path,
                        "size_bytes": size, "records": lines,
                    }),
                    content_type="application/json", headers=cors)
            except Exception as e:
                return web.Response(
                    text=json.dumps({"error": str(e)}),
                    status=500, content_type="application/json", headers=cors)

        try:
            limit = int(request.query.get('limit', '1000'))
        except ValueError:
            limit = 1000
        # EGRESS BURST CONTROL (2026-06-04): hard-cap the limit (was unbounded —
        # limit=20000 pulls were a major egress source) and budget-gate large pulls.
        # Beyond the shared heavy budget, large requests are clamped to 1000 records.
        limit = max(1, min(limit, 5000))
        if limit > 1000 and not _egress_allow_heavy():
            limit = 1000
        try:
            with open(path, encoding='utf-8') as f:
                all_lines = f.readlines()
            tail = all_lines[-limit:]
            records = []
            for line in tail:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return web.Response(
                text=json.dumps(records),
                content_type="application/json", headers=cors)
        except Exception as e:
            return web.Response(
                text=json.dumps({"error": str(e)}),
                status=500, content_type="application/json", headers=cors)

    async def _handle_fresh_launches(self, request):
        """GET /api/fresh-launches — returns the dedicated fresh-launch (<2h)
        outcome corpus the universe recorder persists to
        {RECORDER_DATA_DIR}/fresh_launches.jsonl (2026-05-30). Same record shape
        as /api/universe-recorder but fresh-only and long-retention (200MB cap),
        so it accumulates across regimes — feeds scripts/fresh_launch_recorder.py
        to validate whether the fresh-launch class needs situational handling.

        Query params: ?stats=1 (counts only) | ?limit=N (last N, default 20000).
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        recorder_dir = (
            _os.environ.get('RECORDER_DATA_DIR')
            or _os.path.join(_os.environ.get('DATA_DIR') or '/data', 'universe_recorder')
        )
        path = _os.path.join(recorder_dir, 'fresh_launches.jsonl')
        if not _os.path.exists(path):
            fallback = _os.path.join('.universe_recorder', 'fresh_launches.jsonl')
            if _os.path.exists(fallback):
                path = fallback
            else:
                return web.Response(
                    text=json.dumps({"exists": False, "checked_paths": [path, fallback]}),
                    content_type="application/json", headers=cors)
        if request.query.get('stats') == '1':
            try:
                size = _os.path.getsize(path)
                with open(path, encoding='utf-8') as f:
                    lines = sum(1 for _ in f)
                return web.Response(
                    text=json.dumps({"exists": True, "path": path,
                                     "size_bytes": size, "records": lines}),
                    content_type="application/json", headers=cors)
            except Exception as e:
                return web.Response(text=json.dumps({"error": str(e)}),
                                    status=500, content_type="application/json", headers=cors)
        try:
            limit = int(request.query.get('limit', '20000'))
        except ValueError:
            limit = 20000
        try:
            with open(path, encoding='utf-8') as f:
                all_lines = f.readlines()
            records = []
            for line in all_lines[-limit:]:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return web.Response(text=json.dumps(records),
                                content_type="application/json", headers=cors)
        except Exception as e:
            return web.Response(text=json.dumps({"error": str(e)}),
                                status=500, content_type="application/json", headers=cors)

    async def _handle_ng_scorer_decisions(self, request):
        """GET /api/ng-scorer-decisions — the enforced never-green scorer's live
        decision log (DATA_DIR/ng_scorer/decisions.jsonl). Enforced blocks leave NO
        trade record and Railway logs evaporate in ~30min, so this is the only
        durable record of what the gate actually did in production.

        ?stats=1 -> counts + block-rate + per-bot; ?limit=N -> last N (default 5000).
        Join to the universe recorder's forward peaks (by token+time) for live
        precision/winner-kill once outcomes resolve.
        """
        import os as _os
        from collections import Counter
        cors = {"Access-Control-Allow-Origin": "*"}
        path = _os.path.join(_os.environ.get('DATA_DIR') or '/data', 'ng_scorer', 'decisions.jsonl')
        if not _os.path.exists(path):
            fb = _os.path.join('.', 'ng_scorer', 'decisions.jsonl')
            if _os.path.exists(fb):
                path = fb
            else:
                return web.Response(
                    text=json.dumps({"exists": False, "checked_paths": [path, fb]}),
                    content_type="application/json", headers=cors)
        try:
            with open(path, encoding='utf-8') as f:
                recs = [json.loads(l) for l in f if l.strip()]
        except Exception as e:
            return web.Response(text=json.dumps({"error": str(e)}), status=500,
                                content_type="application/json", headers=cors)
        if request.query.get('stats') == '1':
            n = len(recs)
            blk = sum(1 for r in recs if r.get('blocked'))
            bybot = Counter(r.get('bot') for r in recs)
            bybot_blk = Counter(r.get('bot') for r in recs if r.get('blocked'))
            return web.Response(text=json.dumps({
                "exists": True, "path": path, "records": n, "blocked": blk,
                "block_rate_pct": round(100 * blk / n, 1) if n else 0,
                "per_bot": {b: {"n": bybot[b], "blocked": bybot_blk.get(b, 0)} for b in bybot},
            }), content_type="application/json", headers=cors)
        try:
            limit = int(request.query.get('limit', '5000'))
        except ValueError:
            limit = 5000
        return web.Response(text=json.dumps(recs[-limit:]),
                            content_type="application/json", headers=cors)

    async def _handle_peak_traces(self, request):
        """GET /api/peak-traces — list peak recorder trace files written by
        the live position recorder. Returns array of {name, size, mtime, tok}.
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        data_dir = _os.environ.get('DATA_DIR', '.')
        traces_dir = _os.path.join(data_dir, 'live_traces')
        out = []
        if _os.path.isdir(traces_dir):
            for name in sorted(_os.listdir(traces_dir)):
                if not name.endswith('.json'):
                    continue
                full = _os.path.join(traces_dir, name)
                try:
                    st = _os.stat(full)
                    out.append({
                        'name': name,
                        'size': st.st_size,
                        'mtime': st.st_mtime,
                    })
                except Exception:
                    continue
        return web.Response(text=json.dumps(out),
                            content_type="application/json", headers=cors)

    async def _handle_peak_trace_one(self, request):
        """GET /api/peak-traces/{name} — return raw JSON of one trace."""
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        name = request.match_info.get('name', '')
        # safety: only allow letters, digits, underscores, dashes, dots
        import re as _re
        if not _re.match(r'^[A-Za-z0-9._-]+$', name):
            return web.Response(text=json.dumps({"error": "bad name"}),
                                status=400, content_type="application/json",
                                headers=cors)
        data_dir = _os.environ.get('DATA_DIR', '.')
        path = _os.path.join(data_dir, 'live_traces', name)
        if not _os.path.exists(path):
            return web.Response(text=json.dumps({"error": "not found"}),
                                status=404, content_type="application/json",
                                headers=cors)
        try:
            with open(path) as fh:
                content = fh.read()
            return web.Response(text=content, content_type="application/json",
                                headers=cors)
        except Exception as e:
            return web.Response(text=json.dumps({"error": str(e)}),
                                status=500, content_type="application/json",
                                headers=cors)

    async def _handle_mc_recommendations(self, request):
        """GET /api/mc-recommendations — micro-caps seen but not bought."""
        cors = {"Access-Control-Allow-Origin": "*"}
        candidates = []
        if self._axiom_scanner is not None:
            raw = getattr(self._axiom_scanner, "mc_candidates", [])
            candidates.extend(raw)
        if self._established_scanner is not None:
            raw2 = getattr(self._established_scanner, "mc_candidates", [])
            candidates.extend(raw2)
        # Sort newest first by time string (HH:MM:SS)
        candidates.sort(key=lambda c: c.get("time", ""), reverse=True)
        candidates = candidates[:40]
        return web.Response(
            text=json.dumps(candidates),
            content_type="application/json",
            headers=cors,
        )

    async def _handle_reset(self, request):
        """POST /api/reset — clear all trade history and reset P&L to zero.
        Body: {"secret": "..."}
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        expected_secret = _os.environ.get("TOKEN_UPDATE_SECRET", "changeme")
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if body.get("secret") != expected_secret:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Unauthorized"}),
                status=401, content_type="application/json", headers=cors,
            )
        # Clear tracker in memory and on disk
        if self._tracker:
            self._tracker.trades.clear()
            self._tracker._save_trades()
        # Also wipe the file directly in case tracker ref is stale
        trade_log = _os.path.join(_os.environ.get("DATA_DIR", "."), "trades.json")
        try:
            with open(trade_log, "w") as f:
                import json as _json
                _json.dump([], f)
        except Exception:
            pass
        # Reset RiskManager capital back to total_capital so the dashboard
        # and position sizing start fresh from the configured capital amount.
        rm = getattr(self._trader, "risk_manager", None) if self._trader else None
        if rm is not None:
            rm.available_capital = rm.total_capital
            rm.daily_pnl = 0.0
            rm.total_pnl = 0.0
            rm.trades_today = 0
            rm._save_state()
            logger.info(f"[Reset] RiskManager capital restored to ${rm.total_capital:.0f}")
        # Also wipe the risk_state.json so a restart doesn't reload old state
        risk_state_file = _os.path.join(_os.environ.get("DATA_DIR", "."), "risk_state.json")
        try:
            with open(risk_state_file, "w") as f:
                import json as _json
                _json.dump({"available_capital": rm.total_capital if rm else 2000.0}, f)
        except Exception:
            pass
        return web.Response(
            text=json.dumps({"ok": True, "message": "Trade history and capital reset"}),
            content_type="application/json", headers=cors,
        )

    async def _handle_restore(self, request):
        """POST /api/restore — restore trade history from a provided list.
        Body: {"secret": "...", "trades": [...]}
        Used after an accidental /api/reset wipe — uploads a previously-saved
        trades.json snapshot back into the tracker + /data/trades.json. Capital
        and risk state are NOT touched (use /api/reset-daily-pnl separately
        if needed).
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        expected_secret = _os.environ.get("TOKEN_UPDATE_SECRET", "changeme")
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if body.get("secret") != expected_secret:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Unauthorized"}),
                status=401, content_type="application/json", headers=cors,
            )
        trades = body.get("trades")
        if not isinstance(trades, list):
            return web.Response(
                text=json.dumps({"ok": False, "error": "Body must include 'trades' as a list"}),
                status=400, content_type="application/json", headers=cors,
            )
        mode = body.get("mode", "replace")  # "replace" (default) or "append"
        trade_log = _os.path.join(_os.environ.get("DATA_DIR", "."), "trades.json")
        try:
            import json as _json
            if mode == "append" and _os.path.exists(trade_log):
                with open(trade_log) as f:
                    existing = _json.load(f) or []
                merged = list(existing) + list(trades)
            else:
                merged = list(trades)
            with open(trade_log, "w") as f:
                _json.dump(merged, f)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": f"write failed: {e}"}),
                status=500, content_type="application/json", headers=cors,
            )
        if self._tracker:
            try:
                if mode == "replace":
                    self._tracker.trades.clear()
                self._tracker.trades.extend(trades)
            except Exception:
                pass
        logger.info(f"[Restore] mode={mode} added={len(trades)} total_now={len(merged)}")
        return web.Response(
            text=json.dumps({"ok": True, "added": len(trades), "total": len(merged)}),
            content_type="application/json", headers=cors,
        )

    async def _handle_reset_daily_pnl(self, request):
        """POST /api/reset-daily-pnl — zero ONLY the daily P&L gate without
        touching trades, capital, or total_pnl. Used to keep the bot from
        pausing on the daily-loss-limit during multi-day forward tests where
        we want to preserve trade history for analysis.
        Body: {"secret": "..."}
        """
        import os as _os
        cors = {"Access-Control-Allow-Origin": "*"}
        expected_secret = _os.environ.get("TOKEN_UPDATE_SECRET", "changeme")
        try:
            body = await request.json()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json", headers=cors,
            )
        if body.get("secret") != expected_secret:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Unauthorized"}),
                status=401, content_type="application/json", headers=cors,
            )
        rm = getattr(self._trader, "risk_manager", None) if self._trader else None
        if rm is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "RiskManager not available"}),
                status=500, content_type="application/json", headers=cors,
            )
        prior = rm.daily_pnl
        rm.daily_pnl = 0.0
        rm._save_state()
        logger.info(f"[Reset-Daily-PnL] daily_pnl ${prior:+.2f} → $0.00 (trades/capital untouched)")
        return web.Response(
            text=json.dumps({"ok": True, "prior_daily_pnl": round(prior, 2), "new_daily_pnl": 0.0}),
            content_type="application/json", headers=cors,
        )

    def _build_active_strategies(self) -> dict:
        """Return a dict of strategy_key → status dict for the Active Strategies panel."""
        import time as _time

        now_iso = datetime.now(timezone.utc).isoformat()
        today_prefix = datetime.now(timezone.utc).date().isoformat()

        try:
            from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _co
        except Exception:
            _co = ""

        # smart_follow attribution (2026-06-08): smart_follow shares one position per
        # token with the scanner (first opener tags the position), so its EXITS are
        # recorded as strategy='scanner' and the card under-reported (showed ~flat while
        # it was actually taking TP1/TP2). Attribute by ORIGIN: any trade on a token
        # smart_follow BOUGHT counts toward smart_follow, regardless of the sell's tag.
        _sf_tokens = set()
        if self._tracker is not None:
            _sf_tokens = {
                (t.get("address") or "").lower()
                for t in self._tracker.trades
                if t.get("strategy") == "smart_follow" and t.get("type") == "buy"
                and t.get("address")
            }

        def _match(t, strategy_key: str) -> bool:
            if strategy_key == "smart_follow":
                return (t.get("address") or "").lower() in _sf_tokens
            return t.get("strategy") == strategy_key

        def _last_trade_times(strategy_key: str):
            """Scan tracker trades to find last buy/sell ISO timestamps for a strategy."""
            last_buy = None
            last_sell = None
            if self._tracker is None:
                return last_buy, last_sell
            for t in reversed(self._tracker.trades):
                ttype = t.get("type", "")
                if not _match(t, strategy_key):
                    continue
                ts = t.get("time")
                if _co and (ts or "") < _co:
                    continue
                if ttype == "buy" and last_buy is None:
                    last_buy = ts
                elif ttype == "sell" and last_sell is None:
                    last_sell = ts
                if last_buy and last_sell:
                    break
            return last_buy, last_sell

        def _trades_today(strategy_key: str) -> int:
            if self._tracker is None:
                return 0
            return sum(
                1 for t in self._tracker.trades
                if _match(t, strategy_key)
                and t.get("type") == "sell"
                and (not _co or (t.get("time") or "") >= _co)
                and t.get("time", "")[:10] == today_prefix
            )

        def _strategy_pnl_and_wr(strategy_key: str):
            if self._tracker is None:
                return 0.0, 0.0
            try:
                from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _co
            except Exception:
                _co = ""
            sells = [t for t in self._tracker.trades
                     if t.get("type") == "sell"
                     and _match(t, strategy_key)
                     and (not _co or (t.get("time") or "") >= _co)]
            if not sells:
                return 0.0, 0.0
            total_pnl = sum(t.get("pnl", 0) for t in sells)
            wins = sum(1 for t in sells if t.get("pnl", 0) > 0)
            win_rate = wins / len(sells) * 100
            return total_pnl, win_rate

        result = {}

        # ── MultiSourceScanner / Axiom feed ──────────────────────────────
        last_buy, last_sell = _last_trade_times("scanner")
        total_pnl, win_rate = _strategy_pnl_and_wr("scanner")
        scanner_running = self._strat_scanner is not None
        axiom_extra = {}
        if self._axiom_scanner is not None:
            axiom_extra = {
                "tokens_received":  getattr(self._axiom_scanner, "tokens_received", None),
                "signals_fired":    getattr(self._axiom_scanner, "signals_fired", None),
                "reconnect_count":  getattr(self._axiom_scanner, "reconnect_count", None),
            }
            axiom_extra = {k: v for k, v in axiom_extra.items() if v is not None}
        result["scanner"] = {
            "display_name": "Scanner",
            "running": scanner_running,
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("scanner"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
            **axiom_extra,
        }

        # ── Scalper ────────────────────────────────────────────────────────
        last_buy, last_sell = _last_trade_times("scalper")
        total_pnl, win_rate = _strategy_pnl_and_wr("scalper")
        scalper_extra = {}
        if self._strat_scalper is not None:
            try:
                sc_stats = self._strat_scalper.get_stats()
                scalper_extra = {
                    "active_positions": sc_stats.get("active_positions"),
                    "active_cycles":    sc_stats.get("active_cycles"),
                }
                scalper_extra = {k: v for k, v in scalper_extra.items() if v is not None}
            except Exception:
                pass
        result["scalper"] = {
            "display_name": "Position Scalper",
            "running": self._strat_scalper is not None,
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("scalper"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
            **scalper_extra,
        }

        # ── CrossWalletConvergence ─────────────────────────────────────────
        last_buy, last_sell = _last_trade_times("convergence")
        total_pnl, win_rate = _strategy_pnl_and_wr("convergence")
        result["convergence"] = {
            "display_name": "Cross-Wallet Convergence",
            "running": self._strat_convergence is not None,
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("convergence"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
        }

        # ── WalletClustering ──────────────────────────────────────────────
        last_buy, last_sell = _last_trade_times("clustering")
        total_pnl, win_rate = _strategy_pnl_and_wr("clustering")
        result["clustering"] = {
            "display_name": "Wallet Clustering",
            "running": self._strat_clustering is not None,
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("clustering"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
        }

        # ── CapitulationReversal ──────────────────────────────────────────
        last_buy, last_sell = _last_trade_times("capitulation")
        total_pnl, win_rate = _strategy_pnl_and_wr("capitulation")
        result["capitulation"] = {
            "display_name": "Capitulation Reversal",
            "running": self._strat_capitulation is not None,
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("capitulation"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
        }

        # ── SmartMoneyFollow (free-RPC follow signal) ─────────────────────
        last_buy, last_sell = _last_trade_times("smart_follow")
        total_pnl, win_rate = _strategy_pnl_and_wr("smart_follow")
        result["smart_follow"] = {
            "display_name": "Smart-Money Follow",
            "running": self._strat_smart_follow is not None,
            "signals_fired": getattr(self._strat_smart_follow, "signals_fired", None),
            "last_buy": last_buy,
            "last_sell": last_sell,
            "trades_today": _trades_today("smart_follow"),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(win_rate, 1),
        }

        return result

    async def _handle_strategies(self, request):
        """GET /api/strategies — return active strategy status for the dashboard."""
        cors = {"Access-Control-Allow-Origin": "*"}
        data = self._build_active_strategies()
        return web.Response(
            text=json.dumps({"ok": True, "strategies": data}),
            content_type="application/json",
            headers=cors,
        )

    async def _handle_sol_gate(self, request):
        """GET /api/sol-gate — current SOL macro-down gate status.

        Reflects filter_sol_macro_down (commit 9fe8366): BLOCK if
        sol_pc_h6 < -0.3 OR sol_pc_h1 < -0.7. Reads scanner.last_sol_features
        snapshot updated each cycle (~5min stale max).
        """
        import time as _time_mod
        cors = {"Access-Control-Allow-Origin": "*"}
        feats: dict = {}
        ts = 0.0
        for chain_id, scanner in (self._scanners or {}).items():
            sf = getattr(scanner, "last_sol_features", None)
            sf_ts = getattr(scanner, "last_sol_features_ts", 0.0)
            if isinstance(sf, dict) and sf_ts > ts:
                feats = sf
                ts = sf_ts
        h6 = feats.get("sol_pc_h6")
        h1 = feats.get("sol_pc_h1")
        h24 = feats.get("sol_pc_h24")
        price = feats.get("sol_price") or feats.get("sol")
        reasons = []
        if isinstance(h6, (int, float)) and h6 < -0.3:
            reasons.append(f"sol_pc_h6={h6:+.2f}%<-0.3")
        if isinstance(h1, (int, float)) and h1 < -0.7:
            reasons.append(f"sol_pc_h1={h1:+.2f}%<-0.7")
        status = "BLOCK" if reasons else "PASS"
        age_secs = max(0, _time_mod.time() - ts) if ts else None
        payload = {
            "status": status,
            "reasons": reasons,
            "sol_price_usd": price,
            "sol_pc_h1": h1,
            "sol_pc_h6": h6,
            "sol_pc_h24": h24,
            "thresholds": {"h6": -0.3, "h1": -0.7},
            "snapshot_age_secs": age_secs,
            "has_data": bool(feats),
        }
        return web.Response(
            text=json.dumps(payload),
            content_type="application/json", headers=cors,
        )

    async def _handle_pause(self, request):
        """POST /api/pause — pause all new trade entries."""
        cors = {"Access-Control-Allow-Origin": "*"}
        self._trading_paused = True
        if self._tracker:
            self._tracker.buying_paused = True
        # Also gate the trader directly — env var TRADING_PAUSED only loads
        # at process start, so the env-based gate doesn't respond to live
        # dashboard toggles.  This in-memory flag covers the gap.
        if self._trader:
            self._trader._dashboard_paused = True
        logger.info("[Dashboard] Trading PAUSED via dashboard")
        return web.Response(
            text=json.dumps({"ok": True, "paused": True}),
            content_type="application/json", headers=cors,
        )

    async def _handle_resume(self, request):
        """POST /api/resume — resume trade entries."""
        cors = {"Access-Control-Allow-Origin": "*"}
        self._trading_paused = False
        if self._tracker:
            self._tracker.buying_paused = False
        if self._trader:
            self._trader._dashboard_paused = False
        logger.info("[Dashboard] Trading RESUMED via dashboard")
        return web.Response(
            text=json.dumps({"ok": True, "paused": False}),
            content_type="application/json", headers=cors,
        )

    # ── Multi-bot fleet endpoints ────────────────────────────────────────────

    def _build_bot_rows(self):
        """Build the per-bot summary list from trade_store. Returns [] if not wired.

        Perf: loads trades.json ONCE then buckets by bot_id, instead of
        re-reading the file per bot. With 49 bots this fix is the difference
        between 32s and 1s.

        Cutoff: trade counts + open-position counts exclude pairs whose BUY
        was pre-cutoff (MIN_TRADE_TIMESTAMP from sp4_common). bot_state
        balance/in_flight/realized are already cutoff-clean from the SP5
        reset migration.
        """
        if self.trade_store is None:
            return []
        try:
            from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _cutoff
        except Exception:
            _cutoff = ""
        bots = []
        state_dir = self.trade_store.data_dir / "bot_state"
        if not state_dir.exists():
            return []
        # Skip bots whose config is disabled so RETIRED bots leave the dashboard.
        # Their bot_state files linger (recreated at boot), but a disabled config
        # means the bot isn't trading and shouldn't be shown. Cached 60s; fail-OPEN
        # (show all) if configs can't be read, so this never blanks the dashboard.
        import time as _t, pathlib as _pl
        if (not hasattr(self, "_enabled_ids_cache")
                or _t.monotonic() - getattr(self, "_enabled_ids_cache_ts", 0.0) > 60):
            _en = set()
            try:
                _cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
                for _p in _cfg_dir.glob("*.json"):
                    try:
                        _d = json.loads(_p.read_text())
                        if _d.get("enabled", True):
                            _en.add(_d.get("bot_id") or _p.stem)
                    except Exception:
                        _en.add(_p.stem)  # unreadable single config -> show it
                if not _en:
                    _en = None  # empty -> fail-open
            except Exception:
                _en = None
            self._enabled_ids_cache = _en
            self._enabled_ids_cache_ts = _t.monotonic()
        _enabled_ids = self._enabled_ids_cache
        all_trades = self.trade_store.load_trades()
        # Filter by trade time >= cutoff. Catches:
        #  - pre-cutoff buys (zombies + legacy single-bot trades)
        #  - pre-cutoff orphan sells (pnl=0 records with no matching buy)
        # bot_state.balance is already cutoff-aligned via SP5 reset, so the
        # only inconsistency would be post-cutoff zombie cleanup sells —
        # but restoration drops pre-cutoff buys from in-memory state, so
        # those can't close after restart.
        def _post_cutoff(t):
            if not _cutoff:
                return True
            return (t.get("time") or "") >= _cutoff
        trades_by_bot: dict[str, list] = {}
        for t in all_trades:
            if not _post_cutoff(t):
                continue
            # Skip synthetic "cancelled on restart" sells — they're bookkeeping
            # records (pnl=0, hold=0) the tracker inserts to close orphaned
            # positions across restarts. Counting them inflates total_trades
            # and torpedoes the win rate denominator.
            if t.get("type") == "sell" and "cancelled on restart" in (t.get("reason") or ""):
                continue
            bid = t.get("bot_id", "baseline_v1")
            trades_by_bot.setdefault(bid, []).append(t)
        for path in sorted(state_dir.glob("*.json")):
            try:
                state = json.loads(path.read_text())
                bot_id = state["bot_id"]
                if _enabled_ids is not None and bot_id not in _enabled_ids:
                    continue  # retired/disabled bot — keep it off the dashboard
                trades = trades_by_bot.get(bot_id, [])
                # Per-bot re-baseline cutoff (dashboard reset): drop this bot's
                # pre-reset trades so a reset bot reads as a clean slate.
                _ra = state.get("reset_after_iso")
                if _ra:
                    trades = [t for t in trades if (t.get("time") or "") >= _ra]
                buys = [t for t in trades if t.get("type") == "buy"]
                sells = [t for t in trades if t.get("type") == "sell"]
                total_pnl = sum(s.get("pnl", 0) for s in sells)
                # Open positions: per-token (num_buys - num_sells), clamped
                # to >= 0. Old approach used (token, entry_price) matching
                # but sell records store entry_price=None (only buys carry
                # their own entry_price), so EVERY buy looked unmatched and
                # the count was inflated ~3-4x. Per-token subtraction handles
                # both partial-fill cases (TP1+TP2 = 2 sells for 1 buy →
                # closed) and cross-cutoff cases (clamp to 0).
                # Count only FULLY-CLOSED sells as closing a position (P1
                # partial sells: TP1 emits a sell with fully_closed=False and
                # leaves the position open — counting it would undercount).
                # Legacy sells lack the field → default True (all full closes
                # pre-P1).
                # 2026-05-27 fix: prefer the REAL persisted position book.
                # bot_state now carries open_positions (the live manager's book),
                # so the count is exact. The legacy buys-minus-sells formula
                # below over-counted re-entered tokens and restart-orphaned
                # positions (counts ran 6-7x over max_concurrent). Fall back to
                # it only for states written before the fix (no open_positions key).
                if "open_positions" in state:
                    open_count = len(state.get("open_positions") or [])
                else:
                    from collections import Counter
                    buys_per_token = Counter(b.get("token") for b in buys)
                    sells_per_token = Counter(
                        s.get("token") for s in sells if s.get("fully_closed", True)
                    )
                    open_count = sum(
                        max(0, buys_per_token[tok] - sells_per_token.get(tok, 0))
                        for tok in buys_per_token
                    )
                # Per-POSITION trade count + win rate (2026-05-27 audit #7).
                # A position exits via TP1+TP2(+trail) = 2-3 sell records; counting
                # raw sells inflated total_trades and skewed WR/$. Aggregate sells by
                # (token, entry_price) into one position with one net outcome. (Sells
                # now carry entry_price; re-entries at an identical price can still
                # merge — rare given price precision.)
                from collections import defaultdict as _dd
                _pos_pnl = _dd(float)
                for s in sells:
                    _pos_pnl[(s.get("token"), s.get("entry_price"))] += (s.get("pnl") or 0)
                n_positions = len(_pos_pnl)
                n_wins = sum(1 for v in _pos_pnl.values() if v > 0)
                bots.append({
                    "bot_id": bot_id,
                    "balance_usd": state["balance_usd"],
                    "in_flight_usd": state["in_flight_usd"],
                    "realized_pnl_total_usd": state["realized_pnl_total_usd"],
                    "daily_pnl_usd": state["daily_pnl_usd"],
                    "open_position_count": open_count,
                    "total_trades": n_positions,
                    "wins": n_wins,
                    "total_pnl_realized": total_pnl,
                })
            except Exception as e:
                logger.warning("api/bots skipped %s: %s", path, e)
                continue
        return bots

    def _build_profit_sweep_sim(self):
        """Read-only SHADOW: simulated banked profit per bot under 3 policies,
        by replaying each bot's time-ordered realized-pnl curve. Moves nothing,
        touches no ledger — display only. See core/profit_sweep_sim.py +
        docs/superpowers/specs/2026-05-25-profit-sweep-design.md."""
        if self.trade_store is None:
            return {"bots": [], "totals": {}}
        from core.profit_sweep_sim import simulate_bot
        try:
            from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _cutoff
        except Exception:
            _cutoff = ""
        import pathlib as _pl
        base_pos = {}
        try:
            _cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
            for _p in _cfg_dir.glob("*.json"):
                try:
                    _d = json.loads(_p.read_text())
                    if _d.get("enabled", True):
                        base_pos[_d.get("bot_id") or _p.stem] = float(
                            _d.get("base_position_usd", 20.0))
                except Exception:
                    pass
        except Exception:
            pass
        sells_by_bot: dict[str, list] = {}
        for t in self.trade_store.load_trades():
            if (t.get("type") or "") != "sell":
                continue
            if _cutoff and (t.get("time") or "") < _cutoff:
                continue
            if "cancelled on restart" in (t.get("reason") or ""):
                continue
            sells_by_bot.setdefault(t.get("bot_id", "baseline_v1"), []).append(t)
        rows = []
        tot = {"banked_hwm_50": 0.0, "banked_hwm_100": 0.0, "banked_step": 0.0,
               "realized_now": 0.0, "realized_peak": 0.0, "at_risk_now": 0.0}
        for bid, sells in sells_by_bot.items():
            if bid not in base_pos:
                continue  # disabled/retired bot
            sells.sort(key=lambda s: s.get("time", ""))
            pnls = [float(s.get("pnl") or 0) for s in sells]
            sim = simulate_bot(pnls, 0.25 * base_pos.get(bid, 20.0))
            sim["bot_id"] = bid
            rows.append(sim)
            for k in tot:
                tot[k] += sim.get(k, 0.0)
        rows.sort(key=lambda r: r["banked_hwm_50"], reverse=True)
        return {"bots": rows, "totals": {k: round(v, 2) for k, v in tot.items()}}

    async def _handle_profit_sweep_sim(self, request):
        """GET /api/profit-sweep-sim — read-only shadow sim of banked profit
        under HWM-50 / HWM-100 / +25%-step. Display only; nothing is moved."""
        return web.json_response(self._build_profit_sweep_sim())

    def _find_scanner_with_bot(self, bot_id):
        for s in self._scanners.values():
            if bot_id in (getattr(s, "bot_position_managers", {}) or {}):
                return s
        return None

    async def _handle_bot_close_position(self, request):
        """POST /api/bots/{bot_id}/close/{token} — manually close ONE of a
        bot's open positions at the current market price (paper). Built
        2026-06-13 (AxiS: force-close the chameleon's last grandfathered
        position so the conviction tune can apply). Uses the same exit path
        as every organic sell (slippage fill, tombstones, attribution)."""
        import types as _types
        cors = {"Access-Control-Allow-Origin": "*"}
        bot_id = request.match_info["bot_id"]
        token = request.match_info["token"]
        scanner = self._find_scanner_with_bot(bot_id)
        if scanner is None:
            return web.json_response({"ok": False, "error": f"bot {bot_id} not live"},
                                     status=404, headers=cors)
        pm = scanner.bot_position_managers[bot_id]
        pos = pm.get_position(token)
        if pos is None:
            return web.json_response({"ok": False, "error": f"no open {token} for {bot_id}"},
                                     status=404, headers=cors)
        price = await scanner._get_current_price_for(
            token, address=getattr(pos, "address", "") or "",
            pair_address=getattr(pos, "pair_address", "") or "")
        if not price or price <= 0:
            return web.json_response({"ok": False, "error": "no current price; retry"},
                                     status=502, headers=cors)
        decision = _types.SimpleNamespace(
            reason="manual sell (dashboard force-close)", kind="MANUAL",
            sell_fraction=1.0)
        try:
            await scanner._execute_bot_sell(bot_id, token, decision, price, time.time())
            self.add_alert(f"Manual force-close: {bot_id} {token} @ {price:.6g}")
            return web.json_response({"ok": True, "bot_id": bot_id, "token": token,
                                      "price": price}, headers=cors)
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)},
                                     status=500, headers=cors)

    async def _handle_bot_reset_daily(self, request):
        """POST /api/bots/{bot_id}/reset-daily — zero ONLY today's daily-loss
        budget + stamp reset_after_iso (so the boot re-derive won't re-pull
        pre-reset trades). Does NOT touch realized P&L or open positions.
        Built 2026-06-13 to unblock the chameleon whose floor was consumed by
        pre-experiment default-geometry trades."""
        cors = {"Access-Control-Allow-Origin": "*"}
        bot_id = request.match_info["bot_id"]
        scanner = self._find_scanner_with_bot(bot_id)
        if scanner is None:
            return web.json_response({"ok": False, "error": f"bot {bot_id} not live"},
                                     status=404, headers=cors)
        cap = scanner.bot_capitals.get(bot_id)
        if cap is None:
            return web.json_response({"ok": False, "error": "no capital for bot"},
                                     status=404, headers=cors)
        before = cap.daily_pnl_usd
        cap.reset_daily()
        scanner._save_bot_state(bot_id)
        self.add_alert(f"reset-daily {bot_id}: daily_pnl {before:+.2f} -> 0.00")
        return web.json_response({"ok": True, "bot_id": bot_id,
                                  "daily_pnl_before": round(before, 2),
                                  "reset_after_iso": cap.reset_after_iso}, headers=cors)

    async def _handle_bot_reset(self, request):
        """POST /api/bots/{bot_id}/reset — FULL re-baseline: flatten open
        positions, zero the capital ledger, and stamp a per-bot trade cutoff so
        the bot reads as a clean slate. Destructive; bot_state backed up first.
        Behind the dashboard's basic auth. 2026-05-29."""
        import datetime as _dt
        cors = {"Access-Control-Allow-Origin": "*"}
        bot_id = request.match_info["bot_id"]
        scanner = self._find_scanner_with_bot(bot_id)
        if scanner is None or self.trade_store is None:
            return web.json_response({"ok": False, "error": f"bot {bot_id} not live"},
                                     status=404, headers=cors)
        now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
        try:
            sd = self.trade_store.data_dir / "bot_state"
            src = sd / f"{bot_id}.json"
            if src.exists():
                bdir = self.trade_store.data_dir / "bot_state_backups"
                bdir.mkdir(exist_ok=True)
                (bdir / f"{bot_id}.{now_iso.replace(':', '-')}.json").write_text(src.read_text())
            pm = scanner.bot_position_managers[bot_id]
            n_pos = len(getattr(pm, "_positions", {}) or {})
            try:
                pm._positions.clear()
                if hasattr(pm, "_last_close_time"):
                    pm._last_close_time.clear()
            except Exception:
                pass
            cap = scanner.bot_capitals.get(bot_id)
            if cap is not None:
                cap.balance_usd = 2000.0  # all bots start at $2000 paper capital
                cap.in_flight_usd = 0.0
                cap.realized_pnl_total_usd = 0.0
                cap.daily_pnl_usd = 0.0
                cap.reset_after_iso = now_iso
            scanner._save_bot_state(bot_id)
            self.add_alert(f"RESET {bot_id}: flattened {n_pos} positions + zeroed ledger")
            logger.info("[Dashboard] RESET bot=%s flattened=%d reset_after=%s", bot_id, n_pos, now_iso)
            return web.json_response({"ok": True, "bot_id": bot_id, "flattened": n_pos,
                                      "reset_after": now_iso}, headers=cors)
        except Exception as e:
            logger.error("[Dashboard] reset failed bot=%s: %s", bot_id, e)
            return web.json_response({"ok": False, "error": str(e)}, status=500, headers=cors)

    async def _handle_bots_unrealized(self, request):
        """GET /api/bots-unrealized — per-bot realized + UNREALIZED (open
        positions marked at live DexScreener price). Cheap: one batched external
        call over the unique open tokens (free; not Railway egress). Honors the
        per-bot reset cutoff. 2026-05-29."""
        rows = self._build_bot_rows()
        # collect open positions from bot_state
        open_by_bot = {}
        tokens = set()
        if self.trade_store is not None:
            sd = self.trade_store.data_dir / "bot_state"
            for r in rows:
                try:
                    st = json.loads((sd / f"{r['bot_id']}.json").read_text())
                except Exception:
                    continue
                ops = st.get("open_positions") or []
                if ops:
                    open_by_bot[r["bot_id"]] = ops
                    for p in ops:
                        a = (p.get("address") or "").lower()
                        if a:
                            tokens.add(a)
        price = {}
        toks = list(tokens)
        try:
            try:
                from curl_cffi import requests as _cf
                getj = lambda u: _cf.get(u, impersonate="chrome", timeout=12).json()
            except Exception:
                import urllib.request as _u
                getj = lambda u: json.loads(_u.urlopen(u, timeout=12).read().decode())
            for i in range(0, len(toks), 30):
                d = getj("https://api.dexscreener.com/latest/dex/tokens/" + ",".join(toks[i:i + 30]))
                for pr in (d.get("pairs") or []):
                    a = (pr.get("baseToken", {}).get("address") or "").lower()
                    pu = float(pr.get("priceUsd") or 0)
                    liq = float(pr.get("liquidity", {}).get("usd", 0) or 0)
                    if a and pu > 0 and (a not in price or liq > price[a][1]):
                        price[a] = (pu, liq)
        except Exception as e:
            logger.warning("[Dashboard] unrealized price fetch err: %s", e)
        px = {k: v[0] for k, v in price.items()}
        out = []
        for r in rows:
            unreal = 0.0
            for p in open_by_bot.get(r["bot_id"], []):
                a = (p.get("address") or "").lower()
                entry = float(p.get("entry_price") or 0)
                size = float(p.get("size_usd") or 0) * float(p.get("remaining_fraction") or 1.0)
                cur = px.get(a)
                if entry > 0 and size > 0 and cur:
                    unreal += size * (cur / entry - 1.0)
            realized = float(r.get("total_pnl_realized") or 0)
            out.append({"bot_id": r["bot_id"], "realized": round(realized, 2),
                        "unrealized": round(unreal, 2), "total": round(realized + unreal, 2),
                        "open": len(open_by_bot.get(r["bot_id"], [])),
                        "total_trades": r.get("total_trades", 0)})
        out.sort(key=lambda x: x["total"], reverse=True)
        return web.json_response({"bots": out, "priced_tokens": len(px), "open_tokens": len(toks)})

    async def _handle_shadow_readout(self, request):
        """GET /api/shadow-readout — summarize live exit/entry SHADOWs so they
        don't need manual mining. Currently the SOL-bail shadow: did bailing on
        the SOL-macro turn beat the actual exit? saved_pp>0 = saved. 2026-05-29."""
        if self.trade_store is None:
            return web.json_response({"sol_bail": {}})
        sells = [t for t in self.trade_store.load_trades()
                 if (t.get("type") or "") == "sell"
                 and t.get("sol_bail_shadow_saved_pp") is not None]
        n = len(sells)
        saved = [t for t in sells if float(t.get("sol_bail_shadow_saved_pp") or 0) > 0]
        hurt = [t for t in sells if float(t.get("sol_bail_shadow_saved_pp") or 0) < 0]
        agg = sum(float(t.get("sol_bail_shadow_saved_pp") or 0) for t in sells)
        return web.json_response({"sol_bail": {
            "n": n,
            "would_have_saved_n": len(saved),
            "would_have_hurt_n": len(hurt),
            "net_saved_pp_sum": round(agg, 1),
            "verdict": ("SAVE" if agg > 0 else "HURT" if agg < 0 else "neutral"),
            "note": "saved_pp = bail P&L − actual exit P&L, summed in percentage points; "
                    "n is positions where SOL-macro was down + pre-TP1 + not green.",
        }})

    async def _handle_axiom_kol_probe(self, request):
        """GET /api/axiom-kol-probe — one-shot feasibility probe of Axiom Vision's
        top-trader (KOL) feed for the copy-trade frontier (2026-06-06). Read-only;
        one vision-kols-v2 call per hit. Temporary — replaced by the collector once
        the data shape is confirmed."""
        if not self._axiom_auth:
            return web.json_response({"error": "axiom_auth_not_registered"})
        try:
            from feeds.axiom_kol_probe import probe_vision_kols
            return web.json_response(await probe_vision_kols(self._axiom_auth))
        except Exception as e:
            return web.json_response({"error": f"{type(e).__name__}: {e}"})

    async def _handle_axiom_kol_trades(self, request):
        """GET /api/axiom-kol-trades?wallet=X — confirm how to pull one KOL's trades
        (copy-trade collector feasibility). Defaults to decu (+$104k/30d) if no wallet."""
        if not self._axiom_auth:
            return web.json_response({"error": "axiom_auth_not_registered"})
        wallet = request.query.get("wallet") or "4vw54BmAogeRV3vPKWyFet5yf8DTLcREzdSzx4rw9Ud9"
        try:
            from feeds.axiom_kol_probe import probe_kol_trades
            return web.json_response(await probe_kol_trades(self._axiom_auth, wallet))
        except Exception as e:
            return web.json_response({"error": f"{type(e).__name__}: {e}"})

    async def _handle_api_bots(self, request):
        """GET /api/bots — list all bots with balance/pnl/open count."""
        return web.json_response(self._build_bot_rows())

    async def _handle_pumpportal(self, request):
        """GET /api/pumpportal — realtime firehose status."""
        pp = getattr(self, "pumpportal", None)
        if pp is None:
            return web.json_response({"enabled": False})
        return web.json_response(pp.summary())

    async def _handle_attention(self, request):
        """GET /api/attention — the attention-layer feed (boost velocity board)."""
        af = getattr(self, "attention_feed", None)
        if af is None:
            return web.json_response({"enabled": False})
        return web.json_response(af.summary())

    async def _handle_follow_capital(self, request):
        """GET /api/follow-capital — smart-wallet pool status + sweep ledger."""
        fc = getattr(self, "follow_capital", None)
        if fc is None:
            return web.json_response({"enabled": False, "note": "pool not wired"})
        return web.json_response(fc.status())

    async def _handle_meta_sensor(self, request):
        """GET /api/meta-sensor — the wallet-panel day-meta scoreboard:
        per-archetype WR/n over 6h/24h windows. Measure-only (no bot reads
        it at buy time until the pre-registered forward bar is met)."""
        ms = getattr(self, "meta_sensor", None)
        if ms is None:
            return web.json_response({"enabled": False, "note": "sensor not wired"})
        try:
            board = ms.scoreboard()
            try:
                from core.meta_chameleon import status as _cham_status
                board["chameleon"] = _cham_status()
            except Exception:
                pass
            return web.json_response(board)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_regime_dial(self, request):
        """GET /api/regime-dial — P7 dial live state (signals + multipliers)."""
        try:
            from core.regime_dial import get_dial
            return web.json_response(await asyncio.to_thread(get_dial().current))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_wallet_discovery(self, request):
        """GET /api/wallet-discovery — continuous-discovery status + the
        cross-day recurrent wallet candidates (the protocol's validator)."""
        wd = getattr(self, "wallet_discovery", None)
        if wd is None:
            return web.json_response({"enabled": False,
                                      "note": "wallet discovery not wired"})
        return web.json_response(await asyncio.to_thread(wd.summary))

    async def _handle_api_goal(self, request):
        """GET /api/goal — $100/day goal meter, WALK-FORWARD live set headline.

        Mirrors scripts/goal_tracker.py exactly (single source: candidate set,
        goal, daily bucketing, live-set selection). HEADLINE = the bots that
        were already net-positive over the trailing window BEFORE the day
        started (what flipping live would actually have run) — AxiS 2026-06-10:
        'measure the goal based on if we ran the profitable bots.' The full
        candidate-set aggregate is kept as the secondary number.
        """
        try:
            from scripts.goal_tracker import (
                CANDIDATE_BOTS, GOAL_USD_PER_DAY, LIVE_SET_TRAILING_DAYS,
                LIVE_SET_MIN_CLOSES, build_daily, live_set_for_day)
        except Exception:
            return web.json_response({"error": "goal_tracker unavailable"}, status=500)
        # Same two sources as /api/trades: tracker (strategy-tagged records —
        # smart_follow lives HERE) + multi-bot trade_store.
        trades = []
        if self._tracker is not None:
            try:
                trades = list(self._tracker.get_all_trades())
            except Exception:
                pass
        if self.trade_store is not None:
            try:
                trades = trades + self.trade_store.load_trades()
            except Exception:
                pass
        daily = await asyncio.to_thread(build_daily, trades)
        days = sorted(daily)
        today_ct = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%d")
        today_bots = daily.get(today_ct, {})
        live_today = live_set_for_day(daily, today_ct)
        live_total = round(sum(r["pnl"] for b, r in today_bots.items()
                               if b in live_today), 2)
        streak = 0  # complete CT days only, on the LIVE-SET meter
        history = []
        for day in days:
            if day >= today_ct:
                continue
            ls = live_set_for_day(daily, day)
            lt = round(sum(r["pnl"] for b, r in daily[day].items() if b in ls), 2)
            ft = round(sum(r["pnl"] for r in daily[day].values()), 2)
            met = lt >= GOAL_USD_PER_DAY
            streak = streak + 1 if met else 0
            history.append({"day": day, "live_total": lt, "full_total": ft,
                            "met": met, "ran": len(ls)})
        return web.json_response({
            "goal_usd": GOAL_USD_PER_DAY,
            "candidate_bots": sorted(CANDIDATE_BOTS),
            "live_set_rule": (f"net-positive over trailing {LIVE_SET_TRAILING_DAYS}d "
                              f"with >={LIVE_SET_MIN_CLOSES} closes, as of day start"),
            "today": {
                "day": today_ct,
                "live_total": live_total,
                "live_set": sorted(live_today),
                "full_total": round(sum(r["pnl"] for r in today_bots.values()), 2),
                "by_bot": {b: round(r["pnl"], 2) for b, r in
                           sorted(today_bots.items(), key=lambda kv: -kv[1]["pnl"])},
            },
            "history": history[-7:],
            "streak_complete_days": streak,
        })

    async def _handle_api_leaderboard(self, request):
        """GET /api/leaderboard?sort=X — sortable fleet leaderboard."""
        sort = request.query.get("sort", "total_pnl_realized")
        bots = self._build_bot_rows()
        if sort == "throughput_x_pnl":
            bots.sort(
                key=lambda b: (b["total_trades"] * (b["total_pnl_realized"] / b["total_trades"]))
                              if b["total_trades"] > 0 else 0.0,
                reverse=True,
            )
        elif sort == "pnl_per_trade":
            bots.sort(
                key=lambda b: (b["total_pnl_realized"] / b["total_trades"])
                              if b["total_trades"] > 0 else 0.0,
                reverse=True,
            )
        else:
            bots.sort(key=lambda b: b.get(sort, 0), reverse=True)
        return web.json_response(bots)

    async def _handle_api_bot_trades(self, request):
        """GET /api/bots/{bot_id}/trades — per-bot trade history."""
        bot_id = request.match_info["bot_id"]
        limit = int(request.query.get("limit", 50))
        if self.trade_store is None:
            return web.json_response([])
        trades = self.trade_store.load_trades(bot_id=bot_id)
        return web.json_response(trades[-limit:])

    async def _handle_api_bot_positions(self, request):
        """GET /api/bots/{bot_id}/positions — per-bot open positions.

        Prefer the REAL persisted book (bot_state[bot_id].open_positions), which
        the position-manager writes losslessly (entry_price/size_usd/address/
        remaining_fraction/tp1_hit). The legacy buys-minus-sells ledger inference
        below over-counts re-entered + restart-orphaned positions (it ran 3-4x
        over the real book — 13 inferred vs 3 real for champ_size_8x on
        2026-05-27) — kept only as a fallback for states written before the
        position-persistence fix (no open_positions key). This mirrors the fix
        already applied to /api/bots open_position_count; this endpoint was
        missed in that pass, so every --unrealized mark over it was inflated.

        amount_usd is emitted as the EFFECTIVE exposure (size_usd scaled by
        remaining_fraction so post-TP1 positions aren't marked at full size),
        matching the field the UI (p.amount_usd) and unrealized tooling read.
        """
        bot_id = request.match_info["bot_id"]
        if self.trade_store is None:
            return web.json_response([])
        state_path = self.trade_store.data_dir / "bot_state" / f"{bot_id}.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
                if "open_positions" in state:
                    out = []
                    for p in (state.get("open_positions") or []):
                        sz = float(p.get("size_usd") or 0.0)
                        rem = p.get("remaining_fraction")
                        eff = sz * float(rem) if (rem is not None and rem > 0) else sz
                        out.append({
                            "token": p.get("token"),
                            "address": p.get("address", ""),
                            "pair_address": p.get("pair_address", ""),
                            "entry_price": p.get("entry_price"),
                            "size_usd": sz,
                            "amount_usd": eff,
                            "remaining_fraction": rem,
                            "tp1_hit": p.get("tp1_hit"),
                            "peak_pnl_pct": p.get("peak_pnl_pct"),
                            "entry_time": p.get("entry_time"),
                        })
                    return web.json_response(out)
            except Exception as e:
                logger.warning("api/positions state read failed for %s: %s", bot_id, e)
        # Fallback: legacy ledger inference (pre-persistence-fix states only)
        trades = self.trade_store.load_trades(bot_id=bot_id)
        buys_by_token = {}
        for t in trades:
            if t.get("type") == "buy":
                buys_by_token[t["token"]] = t
            elif t.get("type") == "sell":
                buys_by_token.pop(t["token"], None)
        return web.json_response(list(buys_by_token.values()))

    # ── SP4 Attribution endpoints ────────────────────────────────────────────

    async def _handle_attribution_filters(self, request):
        """GET /api/attribution/filters — per-filter ablation contribution table."""
        from scripts.sp4_common import pair_buys_sells, compute_metrics
        from scripts.sp4_filter_attribution import ABLATION_FILTER_MAP
        from collections import defaultdict
        if self.trade_store is None:
            return web.json_response([])
        trades = self.trade_store.load_trades()
        paired = pair_buys_sells(trades)
        by_bot = defaultdict(list)
        for p in paired:
            by_bot[p.bot_id].append(p)
        baseline = compute_metrics(by_bot.get("baseline_v1", []))
        base_per = baseline.pnl_per_trade or 0.0
        rows = []
        for bot_id, filter_name in ABLATION_FILTER_MAP.items():
            ab = compute_metrics(by_bot.get(bot_id, []))
            ab_per = ab.pnl_per_trade if ab.pnl_per_trade is not None else None
            delta = (base_per - ab_per) if ab_per is not None else None
            rows.append({
                "filter": filter_name, "baseline_n": baseline.sample_n,
                "ablation_n": ab.sample_n,
                "baseline_per_tr": base_per, "ablation_per_tr": ab_per,
                "delta_per_tr": delta,
            })
        rows.sort(key=lambda r: r["delta_per_tr"] if r["delta_per_tr"] is not None else -1e9, reverse=True)
        return web.json_response(rows)

    async def _handle_attribution_categories(self, request):
        """GET /api/attribution/categories — per-category ablation contribution table."""
        from scripts.sp4_common import pair_buys_sells, compute_metrics
        from scripts.sp4_category_attribution import CATEGORY_BOTS
        from collections import defaultdict
        if self.trade_store is None:
            return web.json_response([])
        trades = self.trade_store.load_trades()
        paired = pair_buys_sells(trades)
        by_bot = defaultdict(list)
        for p in paired:
            by_bot[p.bot_id].append(p)
        baseline = compute_metrics(by_bot.get("baseline_v1", []))
        base_per = baseline.pnl_per_trade or 0.0
        rows = []
        for bot_id in CATEGORY_BOTS:
            ab = compute_metrics(by_bot.get(bot_id, []))
            ab_per = ab.pnl_per_trade if ab.pnl_per_trade is not None else None
            delta = (base_per - ab_per) if ab_per is not None else None
            category = bot_id.replace("no_", "").replace("_filters", "")
            rows.append({
                "category": category, "baseline_n": baseline.sample_n,
                "ablation_n": ab.sample_n,
                "baseline_per_tr": base_per, "ablation_per_tr": ab_per,
                "delta_per_tr": delta,
            })
        rows.sort(key=lambda r: r["delta_per_tr"] if r["delta_per_tr"] is not None else -1e9, reverse=True)
        return web.json_response(rows)

    async def _handle_attribution_regimes(self, request):
        """GET /api/attribution/regimes?bot_id=X — per-bot regime breakdown."""
        bot_id = request.query.get("bot_id", "baseline_v1")
        from scripts.sp4_common import pair_buys_sells
        from scripts.sp4_regime_stratify import sol_h1_bucket, pc_h24_bucket
        from collections import defaultdict
        if self.trade_store is None:
            return web.json_response({})
        trades = self.trade_store.load_trades(bot_id=bot_id)
        paired = pair_buys_sells(trades)
        sol_buckets = defaultdict(list)
        pch_buckets = defaultdict(list)
        for p in paired:
            sol_h1 = (p.buy_meta or {}).get("sol_pc_h1")
            pch = (p.buy_meta or {}).get("pc_h24")
            sol_buckets[sol_h1_bucket(sol_h1)].append(p.realized_pnl_usd)
            pch_buckets[pc_h24_bucket(pch)].append(p.realized_pnl_usd)

        def _summary(values):
            if not values:
                return {"n": 0, "per_tr": None}
            return {"n": len(values), "per_tr": sum(values) / len(values)}

        return web.json_response({
            "bot_id": bot_id,
            "sol_h1": {b: _summary(sol_buckets.get(b, []))
                       for b in ["red", "flat", "green", "unknown"]},
            "pc_h24": {b: _summary(pch_buckets.get(b, []))
                       for b in ["deep_red", "red", "flat", "green", "pumped", "unknown"]},
        })

    async def _handle_bot_details(self, request):
        """GET /api/bots/{bot_id}/details — per-bot drill-down metrics."""
        bot_id = request.match_info["bot_id"]
        from scripts.sp4_common import pair_buys_sells, compute_metrics
        if self.trade_store is None:
            return web.json_response({})
        trades = self.trade_store.load_trades(bot_id=bot_id)
        paired = pair_buys_sells(trades)
        metrics = compute_metrics(paired)
        return web.json_response({
            "bot_id": bot_id,
            "sample_n": metrics.sample_n,
            "pnl_per_trade": metrics.pnl_per_trade,
            "total_pnl_usd": metrics.total_pnl_usd,
            "win_rate": metrics.win_rate,
            "avg_win_usd": metrics.avg_win_usd,
            "avg_loss_usd": metrics.avg_loss_usd,
            "best_trade_usd": metrics.best_trade_usd,
            "worst_trade_usd": metrics.worst_trade_usd,
            "recent_trades": [
                {"token": p.token, "pnl_usd": p.realized_pnl_usd, "time": p.time}
                for p in paired[-20:]
            ],
        })

    async def _handle_champion_proposal(self, request):
        """GET /api/champion_proposal — current proposed champion config + reasoning."""
        from pathlib import Path as _Path
        import json as _json
        project_root = _Path(__file__).parent.parent
        cfg_path = project_root / "config" / "bots" / "champion_proposal.json"
        reasoning_path = project_root / "reports" / "champion_synthesis.md"
        payload = {}
        if cfg_path.exists():
            payload["config"] = _json.loads(cfg_path.read_text())
        if reasoning_path.exists():
            payload["reasoning_md"] = reasoning_path.read_text()
        return web.json_response(payload)

    async def _handle_diagnostics(self, request):
        """GET /api/diagnostics — structured health snapshot for all critical systems."""
        import time as _time
        mono_now = _time.monotonic()
        cors = {"Access-Control-Allow-Origin": "*"}

        # ── Uptime ───────────────────────────────────────────────────────────
        uptime_mins = round((datetime.now(timezone.utc) - self._start_time).total_seconds() / 60, 1)

        # ── DexScreener WS ───────────────────────────────────────────────────
        dex_feed = getattr(self._trader, "_dex_price_feed", None) if self._trader else None
        ws_connected  = getattr(dex_feed, "ws_connected", False) if dex_feed else False
        ws_failures   = getattr(dex_feed, "ws_consecutive_failures", 0) if dex_feed else 0

        # ── Scanner health ───────────────────────────────────────────────────
        scanner_stats = []
        for scanner in self._scanners.values():
            last_buy = getattr(scanner, "_last_buy_time", 0)
            mins_since_buy = round((mono_now - last_buy) / 60, 1) if last_buy > 0 else None
            scanner_stats.append({
                "chain":                       getattr(getattr(scanner, "chain", None), "name", "unknown"),
                "signals_fired":               getattr(scanner, "signals_fired", 0),
                "last_buy_mins_ago":           mins_since_buy,
                "watchlist_depth":             len(getattr(scanner, "_dip_watchlist", {})),
                "blocked_h6_extended":         getattr(scanner, "signals_blocked_h6_extended", 0),
                "blocked_pump_cooldown":       getattr(scanner, "signals_blocked_pump_cooldown", 0),
                "blocked_stale_nocandle":      getattr(scanner, "signals_blocked_stale_nocandle", 0),
                "blocked_atm_nocandle":        getattr(scanner, "signals_blocked_atm_nocandle", 0),
                "blocked_tick_momentum":       getattr(scanner, "signals_blocked_tick_momentum", 0),
                "blocked_score":               getattr(scanner, "signals_blocked_score", 0),
                "blocked_security":            getattr(scanner, "signals_blocked_security", 0),
            })

        # ── Positions ────────────────────────────────────────────────────────
        open_positions = len(self._trader.open_positions) if self._trader else 0

        # ── Recent anomalies logged by watchdog ──────────────────────────────
        anomalies = list(self._anomaly_log)

        diag = {
            "uptime_mins":    uptime_mins,
            "dexscreener_ws": {
                "connected":            ws_connected,
                "consecutive_failures": ws_failures,
                "status": "ok" if ws_connected else ("broken" if ws_failures >= 10 else "reconnecting"),
            },
            "scanners":       scanner_stats,
            "open_positions": open_positions,
            "anomalies":      anomalies,
            "trading_paused": self._trading_paused,
        }
        return web.Response(
            text=json.dumps(diag),
            content_type="application/json",
            headers=cors,
        )

    async def _handle_metrics(self, request):
        """GET /metrics — Prometheus exposition format.

        Plain-text metrics endpoint scrapeable by Prometheus/Grafana.
        No prometheus_client dependency — handcrafted text format keeps
        the surface small and avoids new pip deps. Mirrors the
        /api/diagnostics fields but in counter/gauge shape.

        Format spec: https://prometheus.io/docs/instrumenting/exposition_formats/
        All metric names prefixed with `multichain_bot_`.
        """
        import time as _time
        mono_now = _time.monotonic()
        lines: list = []

        def _emit(name: str, help_: str, type_: str, value, labels: dict | None = None):
            lines.append(f"# HELP {name} {help_}")
            lines.append(f"# TYPE {name} {type_}")
            if labels:
                _lbl = ",".join(f'{k}="{v}"' for k, v in labels.items())
                lines.append(f"{name}{{{_lbl}}} {value}")
            else:
                lines.append(f"{name} {value}")

        try:
            uptime_mins = (datetime.now(timezone.utc) - self._start_time).total_seconds() / 60.0
            _emit("multichain_bot_uptime_minutes",
                  "Bot uptime in minutes since process start",
                  "gauge", round(uptime_mins, 2))

            dex_feed = getattr(self._trader, "_dex_price_feed", None) if self._trader else None
            ws_connected = 1 if getattr(dex_feed, "ws_connected", False) else 0
            ws_failures = int(getattr(dex_feed, "ws_consecutive_failures", 0) or 0)
            _emit("multichain_bot_ws_connected",
                  "DexScreener WS connection state (1=connected, 0=down)",
                  "gauge", ws_connected)
            _emit("multichain_bot_ws_consecutive_failures",
                  "Consecutive WS reconnect failures",
                  "gauge", ws_failures)

            open_positions = len(self._trader.open_positions) if self._trader else 0
            _emit("multichain_bot_open_positions",
                  "Currently open positions (all strategies)",
                  "gauge", open_positions)

            _emit("multichain_bot_trading_paused",
                  "Trading paused flag (1=paused, 0=live)",
                  "gauge", 1 if self._trading_paused else 0)

            _emit("multichain_bot_anomalies_recent",
                  "Recent anomalies retained in ring buffer",
                  "gauge", len(self._anomaly_log))

            for scanner in self._scanners.values():
                chain = getattr(getattr(scanner, "chain", None), "name", "unknown")
                _emit("multichain_bot_signals_fired",
                      "Signals fired by scanner (cumulative)",
                      "counter",
                      int(getattr(scanner, "signals_fired", 0) or 0),
                      {"chain": chain})
                last_buy = getattr(scanner, "_last_buy_time", 0) or 0
                if last_buy > 0:
                    secs_since_buy = max(0, mono_now - last_buy)
                    _emit("multichain_bot_seconds_since_last_buy",
                          "Seconds since last buy fired by scanner",
                          "gauge", round(secs_since_buy, 1),
                          {"chain": chain})
                # Blocked-signal counters (each in its own counter line per chain)
                for reason_attr, reason_label in [
                    ("signals_blocked_h6_extended", "h6_extended"),
                    ("signals_blocked_pump_cooldown", "pump_cooldown"),
                    ("signals_blocked_stale_nocandle", "stale_nocandle"),
                    ("signals_blocked_atm_nocandle", "atm_nocandle"),
                    ("signals_blocked_tick_momentum", "tick_momentum"),
                    ("signals_blocked_score", "score"),
                    ("signals_blocked_security", "security"),
                ]:
                    val = int(getattr(scanner, reason_attr, 0) or 0)
                    _emit("multichain_bot_signals_blocked",
                          "Signals blocked by scanner reason (cumulative)",
                          "counter", val,
                          {"chain": chain, "reason": reason_label})
        except Exception as _e:
            lines.append(f"# ERROR generating metrics: {_e}")

        body = "\n".join(lines) + "\n"
        return web.Response(
            text=body,
            content_type="text/plain; version=0.0.4",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _handle_sse(self, request):
        """Server-Sent Events stream — pushes fresh stats every 2s."""
        response = web.StreamResponse(
            headers={
                "Content-Type":       "text/event-stream",
                "Cache-Control":      "no-cache",
                "Connection":         "keep-alive",
                "X-Accel-Buffering":  "no",
                "Access-Control-Allow-Origin": "*",
            }
        )
        await response.prepare(request)

        try:
            while True:
                stats = await self._build_stats(consume_alerts=True)
                payload = json.dumps(stats)
                await response.write(f"data: {payload}\n\n".encode())
                await asyncio.sleep(2.0)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            logger.debug(f"[Dashboard] SSE client disconnected: {e}")

        return response

    # ── Stats builder ────────────────────────────────────────────────────────

    async def _build_stats(self, consume_alerts: bool = False) -> dict:
        """Aggregate stats from all registered providers."""
        uptime = datetime.now(timezone.utc) - self._start_time
        hours   = int(uptime.total_seconds() // 3600)
        minutes = int((uptime.total_seconds() % 3600) // 60)

        if consume_alerts:
            new_alerts = self._alert_buffer.copy()
            self._alert_buffer.clear()
        else:
            new_alerts = list(self._alert_buffer)

        stats = {
            "uptime": f"{hours}h {minutes}m",
            "new_alerts": new_alerts,
            "live_mode": self._live_mode,
            "trading_paused": self._trading_paused,
            "overall": {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_pnl": 0,
                "avg_win": 0, "avg_loss": 0,
                "best_trade": 0, "worst_trade": 0,
            },
            "daily_pnl": 0,
            "strategies": {
                "scanner": {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_loss": 0},
                "copy":    {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_loss": 0},
                "scalper": {"total_trades": 0, "win_rate": 0, "total_pnl": 0, "avg_win": 0, "avg_loss": 0},
            },
            "chains": {
                "sol":  {"pnl": 0, "capital": 0, "positions": 0},
            },
            "capital":    {"total": 0, "available": 0, "deployed": 0},
            "security":   {},
            "price_feed": {},
            "positions":  [],
            "recent_trades": [],
            "cumulative_pnl": [],
            "all_trades": [],
            "wallets": [],
        }

        for provider in self._stats_providers:
            try:
                provider_stats = provider.get_dashboard_stats()
                # Accumulate capital across all risk managers instead of overwriting
                if "capital" in provider_stats:
                    cap = provider_stats.pop("capital")
                    stats["capital"]["total"]     += cap.get("total", 0)
                    stats["capital"]["available"] += cap.get("available", 0)
                    stats["capital"]["deployed"]  += cap.get("deployed", 0)
                self._deep_merge(stats, provider_stats)
            except Exception as e:
                logger.debug(f"[Dashboard] Stats provider error: {e}")

        # Attach cumulative P&L series and full trade list from tracker
        if self._tracker is not None:
            try:
                stats["cumulative_pnl"] = self._tracker.get_cumulative_pnl()
            except Exception:
                pass
            # Trade history is fetched separately via /api/trades on a slow poll.
            # Keeping all_trades out of the SSE payload — at ~16KB/trade × 400+
            # records, streaming this 2 Hz was the dominant Railway egress cost.
            try:
                stats["drawdown"] = self._tracker.get_drawdown_stats()
            except Exception:
                stats["drawdown"] = {}

        # Attach slippage stats from paper simulator
        if self._trader is not None:
            slip_sim = getattr(self._trader, "paper_slippage", None)
            if slip_sim is not None:
                stats["slippage"] = slip_sim.get_stats()
            else:
                stats["slippage"] = {}

        # Attach live-mode execution stats — populated only when PAPER_MODE=false.
        # Surfaces swap reliability, realized slippage, and SOL gas reserve.
        if self._trader is not None:
            try:
                stats["execution"] = self._trader.get_execution_stats()
            except Exception as e:
                logger.debug(f"[Dashboard] execution stats build error: {e}")
                stats["execution"] = {}

        # Attach DexScreener WebSocket health — surfaces broken endpoints immediately
        if self._trader is not None:
            dex_feed = getattr(self._trader, "_dex_price_feed", None)
            if dex_feed is not None:
                failures = getattr(dex_feed, "ws_consecutive_failures", 0)
                stats["dexscreener_ws"] = {
                    "connected": getattr(dex_feed, "ws_connected", False),
                    "consecutive_failures": failures,
                    "status": "ok" if getattr(dex_feed, "ws_connected", False) else (
                        "broken" if failures >= 10 else "reconnecting"
                    ),
                }
            else:
                stats["dexscreener_ws"] = {"connected": False, "consecutive_failures": 0, "status": "no_feed"}

        # Add active strategy status for the Active Strategies panel
        try:
            stats["active_strategies"] = self._build_active_strategies()
        except Exception as e:
            logger.debug(f"[Dashboard] active_strategies build error: {e}")
            stats["active_strategies"] = {}

        # ── ScalpQueue panel ────────────────────────────────────────
        if self._scalp_queue is not None and self._scalp_capital is not None:
            try:
                sq = self._scalp_queue
                sc = self._scalp_capital
                stats["scalp_queue"] = {
                    "enabled": True,
                    "watched": len(getattr(sq, "_watch", {})),
                    "stop_cooldowns": len(getattr(sq, "_stop_cooldowns", {})),
                    "open_positions": len(getattr(sc, "_open", {})),
                    "max_concurrent": getattr(sc, "max_concurrent", 10),
                    "total_capital": round(getattr(sc, "total_capital", 0.0), 2),
                    "deployed_usd": round(sc.deployed_usd(), 2),
                    "available_usd": round(sc.available_usd(), 2),
                    "daily_pnl_usd": round(getattr(sc, "_daily_pnl", 0.0), 2),
                    "daily_loss_limit": round(getattr(sc, "daily_loss_limit", 0.0), 2),
                    "daily_loss_hit": bool(getattr(sc, "_daily_loss_hit", False)),
                }
            except Exception as e:
                logger.debug(f"[Dashboard] scalp_queue panel error: {e}")
                stats["scalp_queue"] = {"enabled": False}
        else:
            stats["scalp_queue"] = {"enabled": False}

        # Override positions list with direct trader view — always fresh, no indirection
        if self._trader is not None:
            now = datetime.now(timezone.utc)
            # Price priority: Axiom WS → Solana RPC/Jupiter → DexScreener → pos.current_price_usd → entry
            _axiom_feed = getattr(self._trader, "_axiom_price_feed", None)
            _rpc_feed   = getattr(self._trader, "_rpc_price_feed",   None)
            _dex_feed   = getattr(self._trader, "_dex_price_feed",   None)
            direct_positions = []
            for addr, pos in self._trader.open_positions.items():
                entry = getattr(pos, "entry_price_usd", 0)
                axiom_price = _axiom_feed.price_cache.get(addr, 0) if _axiom_feed else 0
                rpc_price   = _rpc_feed.price_cache.get(addr, 0)   if _rpc_feed   else 0
                dex_price   = _dex_feed.price_cache.get(addr, 0)   if _dex_feed   else 0
                current = axiom_price or rpc_price or dex_price or getattr(pos, "current_price_usd", 0) or entry
                amount = getattr(pos, "amount_usd", 0) or getattr(pos, "amount_sol_spent", 0)
                multiplier = (current / entry) if entry > 0 else 1.0
                pnl_usd = (multiplier - 1) * amount if entry > 0 else getattr(pos, "pnl_usd", 0)
                entry_time = getattr(pos, "entry_time", None)
                hold_secs = int((now - entry_time).total_seconds()) if entry_time else 0
                direct_positions.append({
                    "token_address": addr,
                    "symbol": getattr(pos, "token_symbol", addr[:8]),
                    "chain": getattr(pos, "chain_id", "solana"),
                    "strategy": getattr(pos, "strategy", "scanner"),
                    "entry_price": entry,
                    "current_price": current,
                    "pnl_usd": round(pnl_usd, 2),
                    "multiplier": round(multiplier, 4),
                    "hold_secs": hold_secs,
                    "amount_usd": amount,
                    "pair_address": getattr(pos, "pair_address", ""),
                })
            stats["positions"] = direct_positions

        return stats

    def _deep_merge(self, base: dict, update: dict):
        """Recursively merge update into base in-place."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
