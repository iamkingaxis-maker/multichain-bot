"""
Web Dashboard
Real-time browser dashboard for monitoring the bot.
Access from any device on your network at http://localhost:8080

Serves a single-page dark-mode dashboard with:
  - Server-Sent Events for real-time push updates (no polling)
  - Wallet truth (on-chain SOL delta) + live swaps — the only honest P&L
  - Honest book / live-slot race scoreboards + per-bot leaderboard
  - Robinhood Chain paper-lane card (/api/rh-paper)
  - Full trade history table with search
  - Live event feed

2026-07-10 cleanup (AxiS): retired the sim-era sections — simulated-ledger
stat tiles (Total/Daily P&L, Win Rate, Total Trades, Account Balance, DEX WS),
the simulated cumulative P&L chart, Copy Wallets, Open Positions (Smart
Wallet), and Security Gate. UI ONLY — the backend API endpoints those
sections read (/api/seed-wallets*, /api/sell, SSE stats payload) are kept
untouched for tooling.
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
<title>Heisenberg // Fleet Ops</title>
<style>
  /* ── WEB3 OPS THEME (2026-07-01 redesign) ─────────────────────────
     Dark glass + neon. Accents: cyan #00e5ff -> violet #7c4dff,
     gain green #00ff9d, loss red-pink #ff2965, warn amber #ffb020.
     Monospace tabular numerals everywhere. CSS-only grid/scanline
     background texture. No new CDNs (Chart.js was already here). */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0a0e17;
    --bg2:       #0d1220;
    --glass:     rgba(19, 26, 41, 0.55);
    --glass2:    rgba(13, 18, 32, 0.65);
    --border:    rgba(255, 255, 255, 0.08);
    --border-hi: rgba(0, 229, 255, 0.30);
    --text:      #dbe4f5;
    --muted:     #6b7a99;
    --cyan:      #00e5ff;
    --violet:    #7c4dff;
    --green:     #00ff9d;
    --red:       #ff2965;
    --amber:     #ffb020;
    --grad:      linear-gradient(90deg, #00e5ff, #7c4dff);
  }

  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', ui-monospace, 'Cascadia Code', Consolas, monospace;
    font-variant-numeric: tabular-nums;
    font-size: 13px;
    min-height: 100vh;
    position: relative;
  }
  /* grid texture */
  body::before {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background-image:
      linear-gradient(rgba(0,229,255,0.030) 1px, transparent 1px),
      linear-gradient(90deg, rgba(0,229,255,0.030) 1px, transparent 1px);
    background-size: 44px 44px;
    mask-image: radial-gradient(ellipse at 50% 0%, #000 30%, transparent 85%);
    -webkit-mask-image: radial-gradient(ellipse at 50% 0%, #000 30%, transparent 85%);
  }
  /* scanlines */
  body::after {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 0;
    background: repeating-linear-gradient(0deg,
      rgba(255,255,255,0.012) 0px, rgba(255,255,255,0.012) 1px,
      transparent 1px, transparent 3px);
  }
  .header, .main, .strip { position: relative; z-index: 1; }

  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: rgba(0,229,255,0.18); border-radius: 3px; }

  a { color: var(--cyan); }

  /* ── Header ── */
  .header {
    background: rgba(10, 14, 23, 0.82);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border-bottom: 1px solid var(--border);
    padding: 12px 24px;
    display: flex; align-items: center; justify-content: space-between;
    gap: 12px; flex-wrap: wrap;
    position: sticky; top: 0; z-index: 100;
  }
  .header-left { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
  .logo {
    font-size: 17px; font-weight: 800; letter-spacing: 2px;
    background: var(--grad);
    -webkit-background-clip: text; background-clip: text; color: transparent;
    text-shadow: 0 0 24px rgba(0,229,255,0.25);
    white-space: nowrap;
  }
  .logo .hex { -webkit-text-fill-color: var(--cyan); }
  .logo-sub {
    font-size: 10px; letter-spacing: 3px; color: var(--muted);
    text-transform: uppercase;
  }
  .status-pill {
    display: flex; align-items: center; gap: 7px;
    background: var(--glass2); border: 1px solid var(--border);
    border-radius: 20px; padding: 4px 12px; font-size: 11px; color: var(--muted);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green); flex-shrink: 0;
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%,100% { opacity: 1; box-shadow: 0 0 0 0 rgba(0,255,157,0.45); }
    50%     { opacity: .65; box-shadow: 0 0 0 5px rgba(0,255,157,0.05); }
  }
  .header-right { display: flex; align-items: center; gap: 14px; color: var(--muted); font-size: 11px; flex-wrap: wrap; }
  #clock { color: var(--text); font-size: 12px; }
  .mode-badge {
    font-size: 11px; font-weight: 700; letter-spacing: 1.5px;
    padding: 3px 12px; border-radius: 20px; text-transform: uppercase;
  }
  .mode-badge.paper { background: rgba(0,255,157,0.08); color: var(--green); border: 1px solid rgba(0,255,157,0.35); }
  .mode-badge.live  { background: rgba(255,41,101,0.10); color: var(--red);  border: 1px solid rgba(255,41,101,0.45);
                      animation: pulse 2s ease-in-out infinite; }
  .pause-btn {
    font-size: 11px; font-weight: 600; padding: 4px 14px; border-radius: 8px;
    border: 1px solid var(--border); cursor: pointer; transition: all .15s;
    background: var(--glass2); color: var(--text); font-family: inherit;
  }
  .pause-btn:hover { border-color: var(--border-hi); box-shadow: 0 0 14px rgba(0,229,255,0.15); }
  .pause-btn.paused { background: rgba(255,41,101,0.12); color: var(--red); border-color: rgba(255,41,101,0.4); }

  /* ── Status strip ── */
  .strip {
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
    max-width: 1800px; margin: 14px auto 0; padding: 0 20px;
  }
  .chip {
    display: inline-flex; align-items: center; gap: 6px;
    background: var(--glass2); border: 1px solid var(--border);
    backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
    border-radius: 10px; padding: 5px 11px;
    font-size: 11px; color: var(--text); white-space: nowrap;
    transition: border-color .15s, box-shadow .15s;
  }
  .chip:hover { border-color: var(--border-hi); box-shadow: 0 0 12px rgba(0,229,255,0.10); }
  .chip .k { color: var(--muted); text-transform: uppercase; letter-spacing: 1px; font-size: 9px; }
  .chip.ok    { border-color: rgba(0,255,157,0.30); }
  .chip.ok .v { color: var(--green); }
  .chip.bad   { border-color: rgba(255,41,101,0.35); }
  .chip.bad .v { color: var(--red); }
  .chip.warn  { border-color: rgba(255,176,32,0.35); }
  .chip.warn .v { color: var(--amber); }

  /* ── Layout ── */
  .main { padding: 16px 20px 40px; display: flex; flex-direction: column; gap: 18px; max-width: 1800px; margin: 0 auto; }

  /* ── Glass cards ── */
  .card {
    background: var(--glass);
    backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 18px 20px;
    overflow: hidden;
    transition: border-color .2s, box-shadow .2s;
  }
  .card:hover { border-color: var(--border-hi); box-shadow: 0 0 28px rgba(0,229,255,0.06); }
  .card-title {
    font-size: 11px; text-transform: uppercase; letter-spacing: 1.6px;
    color: var(--muted); margin-bottom: 14px;
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }
  .card-title .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 8px var(--cyan); }
  .sim-tag {
    font-size: 9px; letter-spacing: 1px; text-transform: uppercase;
    color: var(--amber); border: 1px solid rgba(255,176,32,0.35);
    border-radius: 8px; padding: 1px 7px; background: rgba(255,176,32,0.06);
  }

  /* ── HONEST BOOK hero ── */
  .hero {
    background: linear-gradient(160deg, rgba(0,229,255,0.06) 0%, rgba(124,77,255,0.06) 55%, var(--glass) 100%);
    border: 1px solid rgba(0,229,255,0.22);
    box-shadow: 0 0 34px rgba(0,229,255,0.07), inset 0 1px 0 rgba(255,255,255,0.05);
  }
  .hero .card-title { color: var(--cyan); }
  .hb-stats { display: flex; gap: 30px; flex-wrap: wrap; margin: 4px 0 14px; }
  .hb-stat .lbl { font-size: 9px; text-transform: uppercase; letter-spacing: 1.4px; color: var(--muted); margin-bottom: 3px; }
  .hb-stat .val { font-size: 24px; font-weight: 700; line-height: 1.1; }
  .hb-stat .val.big { font-size: 30px; }
  .hb-note { font-size: 10px; color: var(--muted); margin-top: 10px; line-height: 1.5; }

  /* ── LIVE-SLOT RACE ── */
  .race-cells { display: inline-flex; gap: 3px; vertical-align: middle; }
  .race-cell {
    width: 13px; height: 13px; border-radius: 3px;
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.07);
  }
  .race-cell.g { background: rgba(0,255,157,0.45); border-color: rgba(0,255,157,0.6); box-shadow: 0 0 6px rgba(0,255,157,0.25); }
  .race-cell.r { background: rgba(255,41,101,0.40); border-color: rgba(255,41,101,0.55); }
  .race-bar-badge {
    display: inline-block; padding: 2px 8px; border-radius: 8px;
    font-size: 9px; font-weight: 700; letter-spacing: 0.8px; white-space: nowrap;
    background: var(--glass2); color: var(--muted); border: 1px solid var(--border);
  }
  .race-bar-badge.lit {
    background: rgba(0,255,157,0.12); color: var(--green);
    border: 1px solid rgba(0,255,157,0.5); box-shadow: 0 0 10px rgba(0,255,157,0.18);
  }

  /* ── LIVE TRADING card ── */
  .live-card { border: 1px solid rgba(255,41,101,0.20); }
  .live-card.is-live {
    border: 1px solid rgba(255,41,101,0.55);
    box-shadow: 0 0 30px rgba(255,41,101,0.12);
    background: linear-gradient(160deg, rgba(255,41,101,0.07) 0%, var(--glass) 60%);
  }
  .live-pill {
    font-size: 11px; font-weight: 800; letter-spacing: 1.5px;
    padding: 4px 11px; border-radius: 8px;
    background: rgba(255,41,101,0.12); color: var(--red); border: 1px solid rgba(255,41,101,0.5);
    animation: pulse 2s ease-in-out infinite;
  }
  .live-pill.armed { background: var(--glass2); color: var(--muted); border: 1px solid var(--border); animation: none; }
  .live-head { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; margin-bottom: 12px; }
  .live-big { font-size: 22px; font-weight: 700; }
  .live-sub { font-size: 11px; color: var(--muted); }
  .live-grid { display: flex; gap: 28px; flex-wrap: wrap; margin: 10px 0 6px; }
  .live-stat .lbl { font-size: 9px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); }
  .live-stat .val { font-size: 15px; font-weight: 700; }
  .live-risk { display: flex; gap: 24px; flex-wrap: wrap; margin-top: 12px; }
  .live-bar-wrap { min-width: 220px; flex: 1; }
  .live-bar-lbl { font-size: 9px; color: var(--muted); text-transform: uppercase;
    letter-spacing: 1.2px; margin-bottom: 4px; display: flex; justify-content: space-between; }
  .live-bar { height: 7px; border-radius: 4px; background: rgba(255,255,255,0.05); overflow: hidden; }
  .live-bar > div { height: 100%; border-radius: 4px; transition: width .4s; }
  .live-sweep { font-size: 11px; color: var(--muted); margin-top: 12px;
    border-top: 1px solid var(--border); padding-top: 8px; }

  /* ── Columns ── */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }

  /* ── Tables ── */
  .tbl-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left; padding: 7px 10px;
    font-size: 9px; text-transform: uppercase; letter-spacing: 1.4px;
    color: var(--muted); border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }
  td { padding: 7px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); vertical-align: middle; font-size: 12px; }
  tr:last-child td { border-bottom: none; }
  tbody tr { transition: background .12s; }
  tbody tr:hover { background: rgba(0,229,255,0.035); }
  tr.row-win  { background: rgba(0,255,157,0.028); }
  tr.row-loss { background: rgba(255,41,101,0.028); }

  .num-table th, .num-table td { text-align: right; }
  .num-table th:first-child, .num-table td:first-child { text-align: left; }

  /* ── Badges ── */
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 8px;
    font-size: 9px; font-weight: 700; letter-spacing: 0.5px; white-space: nowrap;
  }
  .badge-sol     { background: rgba(124,77,255,0.14); color: #b39dff; }
  .badge-scanner { background: rgba(0,229,255,0.10); color: var(--cyan); }
  .badge-copy    { background: rgba(0,255,157,0.10); color: var(--green); }
  .badge-scalper { background: rgba(255,176,32,0.12); color: var(--amber); }

  /* ── Progress bar ── */
  .progress-wrap { width: 80px; height: 5px; background: rgba(255,255,255,0.06); border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; }
  .progress-fill { height: 100%; border-radius: 3px; background: var(--green); transition: width 0.4s; }
  .progress-fill.tp1 { background: var(--amber); }
  .progress-fill.tp2 { background: #ff7a45; }
  .progress-fill.tp3 { background: var(--green); }

  /* ── Event Feed ── */
  #event-feed { height: 280px; overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }
  .feed-item {
    padding: 6px 8px; border-radius: 6px; font-size: 11px;
    display: flex; gap: 8px; align-items: flex-start;
    border-left: 2px solid transparent;
  }
  .feed-buy  { border-color: var(--green); background: rgba(0,255,157,0.05); }
  .feed-sell { border-color: var(--red);   background: rgba(255,41,101,0.05); }
  .feed-sig  { border-color: var(--amber); background: rgba(255,176,32,0.05); }
  .feed-info { border-color: var(--cyan);  background: rgba(0,229,255,0.05); }
  .feed-time { color: var(--muted); white-space: nowrap; font-size: 10px; flex-shrink: 0; }
  .feed-msg  { color: var(--text); flex: 1; word-break: break-word; }

  /* ── Active Strategies ── */
  .strategies-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
  .strat-status-card {
    background: var(--glass2); border: 1px solid var(--border);
    border-radius: 12px; padding: 13px 15px;
    transition: border-color .2s;
  }
  .strat-status-card:hover { border-color: var(--border-hi); }
  .strat-status-card .strat-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .strat-status-card .strat-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .strat-status-card .strat-name { font-size: 12px; font-weight: 700; flex: 1; }
  .strat-status-card .strat-badge { font-size: 9px; font-weight: 700; letter-spacing: 0.5px; padding: 2px 7px; border-radius: 8px; }
  .strat-status-card .strat-stat { display: flex; justify-content: space-between; font-size: 11px; padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
  .strat-status-card .strat-stat:last-child { border-bottom: none; }
  .strat-status-card .strat-stat .sk { color: var(--muted); }
  .badge-running { background: rgba(0,255,157,0.10); color: var(--green); }
  .badge-stopped { background: rgba(255,41,101,0.10); color: var(--red); }

  /* ── Filters / inputs / buttons ── */
  .filter-row { display: flex; gap: 10px; margin-bottom: 14px; align-items: center; flex-wrap: wrap; }
  .filter-input, .filter-select, .txt-input {
    background: var(--glass2); border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); font-size: 12px; padding: 6px 12px; outline: none;
    font-family: inherit;
  }
  .filter-input { flex: 1; min-width: 180px; }
  .filter-input:focus, .txt-input:focus { border-color: var(--border-hi); box-shadow: 0 0 12px rgba(0,229,255,0.12); }
  .filter-select { color: var(--muted); }
  .btn {
    border: none; border-radius: 8px; padding: 6px 16px; cursor: pointer;
    font-size: 12px; font-weight: 700; white-space: nowrap; font-family: inherit;
    transition: box-shadow .15s, filter .15s;
  }
  .btn:hover { filter: brightness(1.15); box-shadow: 0 0 14px rgba(0,229,255,0.25); }
  .btn-grad { background: var(--grad); color: #06121a; }
  .btn-danger { background: rgba(255,41,101,0.85); color: #fff; }
  .btn-danger:hover { box-shadow: 0 0 14px rgba(255,41,101,0.35); }

  /* ── Utility ── */
  .green  { color: var(--green); }
  .red    { color: var(--red); }
  .yellow { color: var(--amber); }
  .blue   { color: var(--cyan); }
  .muted  { color: var(--muted); }
  .empty  { color: var(--muted); text-align: center; padding: 28px 0; font-size: 12px; }
  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red); }

  /* ── Mobile ── */
  @media (max-width: 600px) {
    body { font-size: 12px; }
    .header { padding: 10px 12px; gap: 8px; }
    .logo { font-size: 14px; }
    .header-right { gap: 8px 10px; font-size: 10px; }
    .header-right .uptime-lbl { display: none; }
    .strip { padding: 0 10px; gap: 6px; }
    .chip { padding: 4px 8px; font-size: 10px; }
    .main { padding: 12px 10px 28px; gap: 12px; }
    .card { padding: 12px 12px; border-radius: 12px; }
    .hb-stats { gap: 18px; }
    .hb-stat .val { font-size: 19px; }
    .hb-stat .val.big { font-size: 23px; }
    .strategies-grid { grid-template-columns: 1fr; gap: 10px; }
    th, td { padding: 6px 6px; font-size: 11px; }
    th { font-size: 8px; }
    #event-feed { height: 240px; }
  }
</style>
</head>
<body>

<!-- ── Header ── -->
<div class="header">
  <div class="header-left">
    <div>
      <div class="logo"><span class="hex">&#x2B22;</span> HEISENBERG</div>
      <div class="logo-sub">memecoin fleet // ops console</div>
    </div>
    <div class="status-pill">
      <span class="status-dot" id="status-dot"></span>
      <span id="status-text">Connecting...</span>
    </div>
  </div>
  <div class="header-right">
    <span id="mode-badge" class="mode-badge paper">PAPER</span>
    <button id="pause-btn" class="pause-btn" onclick="togglePause()">&#9208; Pause Trading</button>
    <span><span class="uptime-lbl">Uptime: </span><span id="uptime">&mdash;</span></span>
    <span id="clock">&mdash;</span>
  </div>
</div>

<!-- ── Status strip (mode / SOL / gates / activity) ── -->
<div class="strip" id="status-strip">
  <span class="chip" id="chip-sol"><span class="k">SOL</span> <span class="v" id="sol-strip">&mdash;</span></span>
  <span class="chip" id="chip-solmacro" title="filter_sol_macro_down"><span class="k">SOL MACRO</span> <span class="v">&mdash;</span></span>
  <span class="chip" id="chip-greenday" title="GREEN_DAY_MODE regime gate"><span class="k">GREEN DAY</span> <span class="v">&mdash;</span></span>
  <span class="chip" id="chip-bel" title="BREAKEVEN_LOCK_MODE exit gate"><span class="k">BE LOCK</span> <span class="v">&mdash;</span></span>
  <span class="chip" id="chip-hours" title="Trading-hours window (Central Time)"><span class="k">HOURS CT</span> <span class="v">&mdash;</span></span>
  <span class="chip" id="chip-open"><span class="k">OPEN POS</span> <span class="v" id="strip-open">&mdash;</span></span>
  <span class="chip" id="chip-buys"><span class="k">BUYS TODAY</span> <span class="v" id="strip-buys">&mdash;</span></span>
</div>

<div class="main">

  <!-- ── HONEST BOOK (the scoreboard that decides things) ── -->
  <div class="card hero" id="honest-book">
    <div class="card-title">
      <span class="dot"></span> HONEST BOOK (scrubbed)
      <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; position-level, frac-weighted, latency-spike scrubbed &middot; the decision-grade numbers</span>
    </div>
    <div class="hb-stats">
      <div class="hb-stat"><div class="lbl">Today scrub &Sigma;pp</div><div class="val big" id="hb-sum">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Mean %/pos</div><div class="val" id="hb-mean">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Median</div><div class="val" id="hb-median">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Win rate</div><div class="val" id="hb-win">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Positions</div><div class="val" id="hb-n">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Tokens (deduped)</div><div class="val" id="hb-tokens">&mdash;</div></div>
      <div class="hb-stat"><div class="lbl">Spikes excluded</div><div class="val yellow" id="hb-spikes">&mdash;</div></div>
    </div>
    <div class="tbl-wrap">
      <table class="num-table" id="hb-table">
        <thead><tr>
          <th>day</th><th>n</th><th>mean %</th><th>med %</th><th>&Sigma; pp</th>
          <th>win %</th><th>tokens</th><th>tok mean %</th><th>spikes (pp)</th>
        </tr></thead>
        <tbody><tr><td colspan="9" class="empty">Loading honest book&hellip;</td></tr></tbody>
      </table>
    </div>
    <div class="hb-note" id="hb-note">
      Scrub rule: pnl&gt;0 AND first-sell hold&lt;10s AND mae&ge;0 = unrealizable latency spike (excluded, reported separately).
      Tokens column dedupes mirror bots. Enforce / go-live decisions quote THESE columns.
    </div>
  </div>

  <!-- ── WALLET TRUTH (on-chain, the ONLY honest live number) ── -->
  <div class="card" id="wallet-truth-panel">
    <div class="card-title">
      <span class="dot"></span> WALLET TRUTH
      <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; on-chain SOL delta since go-live baseline &middot; the exact wallet gain/loss, nothing simulated</span>
    </div>
    <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:baseline;padding:6px 2px;">
      <div><div style="color:var(--muted);font-size:11px;">LIVE Δ (SOL)</div>
        <div id="wt-delta" style="font-size:26px;font-weight:700;">—</div></div>
      <div><div style="color:var(--muted);font-size:11px;">wallet now</div>
        <div id="wt-now" style="font-size:16px;">—</div></div>
      <div><div style="color:var(--muted);font-size:11px;">baseline</div>
        <div id="wt-base" style="font-size:16px;">—</div></div>
      <div><div style="color:var(--muted);font-size:11px;">open live positions</div>
        <div id="wt-open" style="font-size:16px;">—</div></div>
      <div><div style="color:var(--muted);font-size:11px;">deployed (cost basis)</div>
        <div id="wt-deployed" style="font-size:16px;">—</div></div>
      <div id="wt-note" style="color:var(--muted);font-size:12px;align-self:center;"></div>
    </div>
  </div>

  <!-- ── LIVE-SLOT RACE (per-bot — who earns the live slot) ── -->
  <div class="card" id="race-panel">
    <div class="card-title">
      <span class="dot"></span> LIVE-SLOT RACE
      <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; per-bot scrubbed per-token/day, last 7d &middot; bar: &ge;+2.0/tok on &ge;5 days, n&ge;30 tokens</span>
    </div>
    <div class="tbl-wrap">
      <table class="num-table" id="race-table">
        <thead><tr>
          <th>bot</th><th>7d mean/tok</th><th style="text-align:left;">last 7 days</th>
          <th>tokens n</th><th>green days</th><th style="text-align:left;">live bar</th>
        </tr></thead>
        <tbody><tr><td colspan="6" class="empty">Loading race&hellip;</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- ── LIVE TRADING (the real-money bots) — read-only ── -->
  <div class="card live-card" id="live-card">
    <div class="card-title" style="color:var(--red);">
      <span class="dot" style="background:var(--red);box-shadow:0 0 8px var(--red);"></span> LIVE TRADING
      <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; the real-money bots (read-only)</span>
    </div>
    <div class="live-head">
      <span class="live-pill armed" id="live-pill">PAPER &mdash; ARMED</span>
      <span class="live-big" id="live-today">Today: $0.00</span>
      <span class="live-sub" id="live-realized">realized total: $0.00</span>
    </div>
    <div class="live-grid">
      <div class="live-stat"><div class="lbl">Wallet</div><div class="val" id="live-wallet">&mdash;</div></div>
      <div class="live-stat"><div class="lbl">Floor (working capital)</div><div class="val" id="live-floor">&mdash;</div></div>
      <div class="live-stat"><div class="lbl">Above floor (sweepable)</div><div class="val" id="live-abovefloor">&mdash;</div></div>
      <div class="live-stat"><div class="lbl">Open positions</div><div class="val" id="live-open">0</div></div>
    </div>
    <div class="tbl-wrap">
      <table class="num-table">
        <thead><tr><th>bot</th><th>today $</th><th>total $</th><th>open</th><th>WR%</th><th>size</th></tr></thead>
        <tbody id="live-tbody"><tr><td colspan="6" class="muted">Loading&hellip;</td></tr></tbody>
      </table>
    </div>
    <div class="live-risk">
      <div class="live-bar-wrap">
        <div class="live-bar-lbl"><span>Inflight</span><span id="live-inflight-lbl">$0 / $0</span></div>
        <div class="live-bar"><div id="live-inflight-bar" style="width:0%;background:var(--cyan)"></div></div>
      </div>
      <div class="live-bar-wrap">
        <div class="live-bar-lbl"><span>Daily loss vs kill</span><span id="live-kill-lbl">$0 / -$0</span></div>
        <div class="live-bar"><div id="live-kill-bar" style="width:0%;background:var(--red)"></div></div>
      </div>
      <div class="live-stat" style="align-self:center;">
        <div class="lbl">Per-token cap</div><div class="val" id="live-pertoken">&mdash;</div>
      </div>
    </div>
    <div class="live-sweep" id="live-sweep">Profit-sweep: &mdash;</div>
  </div>

  <!-- ── DAILY GOAL ── -->
  <div class="card" id="goal-panel">
    <div class="card-title"><span class="dot" style="background:var(--green);box-shadow:0 0 8px var(--green);"></span>
      DAILY GOAL &mdash; $100 closed P&amp;L, walk-forward live set (CT day)
      <span class="sim-tag">paper ledger</span>
    </div>
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <div style="font-size:28px;font-weight:700;" id="goal-today">$0</div>
      <div style="flex:1;min-width:200px;background:rgba(255,255,255,0.05);border-radius:6px;height:12px;overflow:hidden;">
        <div id="goal-bar" style="height:100%;width:0%;background:var(--red);transition:width .5s;"></div>
      </div>
      <div id="goal-badge" style="font-size:12px;font-weight:700;letter-spacing:1px;">&nbsp;</div>
    </div>
    <div id="goal-contrib" style="font-size:11px;color:var(--muted);margin-top:8px;"></div>
    <div id="goal-history" style="font-size:11px;color:var(--muted);margin-top:4px;"></div>
  </div>

  <!-- ── GOAL CANDIDATES + EXPERIMENTS fleet tables ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--violet);box-shadow:0 0 8px var(--violet);"></span>
      GOAL CANDIDATES <span class="sim-tag">simulated ledger &mdash; not live truth</span>
    </div>
    <div class="tbl-wrap">
      <table class="num-table" id="cand-table">
        <thead><tr>
          <th>Bot</th><th>Balance</th><th>Open</th><th>Trades</th><th>WR</th><th>P&amp;L</th><th>$/tr</th>
        </tr></thead>
        <tbody></tbody>
      </table>
    </div>
    <details id="experiments-panel" style="margin-top:14px;">
      <summary style="cursor:pointer;font-size:10px;text-transform:uppercase;letter-spacing:1.4px;color:var(--muted);">
        Experiments (<span id="exp-count">0</span>) &mdash; selection instrument, not a portfolio</summary>
      <div class="tbl-wrap">
        <table class="num-table" id="fleet-table" style="margin-top:10px;">
          <thead><tr>
            <th>Bot</th><th>Balance</th><th>Open</th><th>Trades</th><th>WR</th><th>P&amp;L</th><th>$/tr</th>
          </tr></thead>
          <tbody></tbody>
        </table>
      </div>
    </details>
  </div>

  <!-- ── Robinhood Chain (paper lane — ledger pushed from local sessions) ── -->
  <div class="card" id="rh-paper-panel">
    <div class="card-title">
      <span class="dot" style="background:var(--violet);box-shadow:0 0 8px var(--violet);"></span> Robinhood Chain
      <span class="sim-tag">paper</span>
      <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; RH-chain memecoin lane &middot; per-session; ledger pushed via /api/rh-paper/ingest</span>
    </div>
    <div style="display:flex;gap:28px;flex-wrap:wrap;align-items:baseline;padding:6px 2px;">
      <div><div style="color:var(--muted);font-size:11px;">day P&amp;L (paper)</div>
        <div id="rh-day-pnl" style="font-size:26px;font-weight:700;">&mdash;</div></div>
      <div><div style="color:var(--muted);font-size:11px;">entries</div>
        <div id="rh-entries" style="font-size:16px;">&mdash;</div></div>
      <div><div style="color:var(--muted);font-size:11px;">exits</div>
        <div id="rh-exits" style="font-size:16px;">&mdash;</div></div>
      <div><div style="color:var(--muted);font-size:11px;">median detect&rarr;fill</div>
        <div id="rh-lag" style="font-size:16px;">&mdash;</div></div>
      <div id="rh-note" style="color:var(--muted);font-size:12px;align-self:center;"></div>
    </div>
    <div class="tbl-wrap">
      <table class="num-table" id="rh-table">
        <thead><tr>
          <th>time (UTC)</th><th>ev</th><th>sym</th><th>usd</th><th>P&amp;L $</th><th>P&amp;L %</th><th>lat s</th>
        </tr></thead>
        <tbody><tr><td colspan="7" class="empty">Loading RH ledger&hellip;</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- ── Live Event Feed ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--amber);box-shadow:0 0 8px var(--amber);"></span> Live Event Feed</div>
    <div id="event-feed">
      <div class="feed-item feed-info">
        <span class="feed-time">&mdash;</span>
        <span class="feed-msg">Waiting for events...</span>
      </div>
    </div>
  </div>

  <!-- ── Curator watchlist ── -->
  <div class="card">
      <div class="card-title" style="justify-content:space-between;">
        <span><span class="dot" style="background:var(--cyan);"></span> My Watchlist <span style="text-transform:none;letter-spacing:0;color:var(--muted);">&mdash; curator picks, bypasses buying-high filters, hot-reload</span></span>
        <span id="user-watchlist-count" style="text-transform:none;letter-spacing:0;">0 tokens</span>
      </div>
      <div style="display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;">
        <input id="user-watchlist-add-address" type="text" placeholder="Token address (solana)…" class="txt-input" style="flex:1;min-width:180px;" />
        <button onclick="addUserWatchlist()" class="btn btn-grad">+ Add</button>
      </div>
      <div class="tbl-wrap">
        <table>
          <thead><tr><th>Token</th><th>MCap</th><th>Vol h1</th><th>24h</th><th>1h</th><th>5m</th><th>Liq</th><th></th></tr></thead>
          <tbody id="user-watchlist-body">
            <tr><td colspan="8" class="empty">Loading…</td></tr>
          </tbody>
        </table>
      </div>
    </div>

  <!-- ── Active Strategies ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--violet);box-shadow:0 0 8px var(--violet);"></span> Active Strategies</div>
    <div class="strategies-grid" id="active-strategies-grid">
      <div class="empty">Loading strategy status...</div>
    </div>
  </div>

  <!-- ── Trade History ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--amber);box-shadow:0 0 8px var(--amber);"></span> Trade History</div>
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
      </select>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr>
          <th>Time</th><th>Token</th><th>Chain</th><th>Strategy</th>
          <th>Entry</th><th>Exit</th><th>P&amp;L $</th><th>P&amp;L %</th><th>Reason</th>
        </tr></thead>
        <tbody id="trade-history-body">
          <tr><td colspan="9" class="empty">No completed trades yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- ── Operator tools ── -->
  <div class="card">
    <div class="card-title"><span class="dot" style="background:var(--muted);"></span> Operator &mdash; re-baseline a bot</div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
      <input id="reset-bot-id" placeholder="bot_id (e.g. badday_flush)" class="txt-input" style="width:240px;" />
      <button onclick="resetBot()" class="btn btn-danger">Flatten + zero ledger</button>
      <span id="reset-result" style="font-size:11px;color:var(--muted);"></span>
    </div>
    <div style="font-size:10px;color:var(--muted);margin-top:10px;">
      Destructive: flattens open positions and zeros the bot's paper ledger. bot_state is backed up first. Requires dashboard login.
    </div>
  </div>

</div><!-- /main -->

<script>
// ── State ──────────────────────────────────────────────────────────────────
let allTrades = [];
let feedLog   = [];
let connected = false;

// ── Clock ──────────────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString();
}
setInterval(updateClock, 1000);
updateClock();

// ── Formatting helpers ─────────────────────────────────────────────────────
function fmtUsd(v) {
  const n = parseFloat(v) || 0;
  const sign = n >= 0 ? '+' : '-';
  return sign + '$' + Math.abs(n).toFixed(2);
}
function pnlClass(v) { return parseFloat(v) >= 0 ? 'green' : 'red'; }
function fmtHold(secs) {
  if (!secs) return '—';
  const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
function fmtTime(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleTimeString(); } catch { return iso.slice(11,16); }
}
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
  const color = n > 0 ? 'var(--green)' : (n < 0 ? 'var(--red)' : 'var(--muted)');
  return '<span style="color:' + color + '">' + sign + n.toFixed(1) + '%</span>';
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
  return '<span class="badge badge-scanner">SCAN</span>';
}
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── SSE connection ─────────────────────────────────────────────────────────
function connect() {
  const es = new EventSource('/events');
  es.onmessage = function(e) {
    connected = true;
    document.getElementById('status-dot').style.background = 'var(--green)';
    document.getElementById('status-text').textContent = 'Live';
    try {
      const data = JSON.parse(e.data);
      updateDashboard(data);
    } catch(err) { console.warn('SSE parse error', err); }
  };
  es.onerror = function() {
    connected = false;
    document.getElementById('status-dot').style.background = 'var(--red)';
    document.getElementById('status-text').textContent = 'Reconnecting...';
    es.close();
    setTimeout(connect, 3000);
  };
}
connect();

// ── Main SSE update ────────────────────────────────────────────────────────
function updateDashboard(d) {
  // 2026-07-10 cleanup: sim-era renderers (stat cards, cumulative chart,
  // smart-wallet positions, security gate, DEX WS) retired — the SSE payload
  // still carries those fields for API consumers; the UI just ignores them.
  updateUptime(d.uptime);
  updateModeAndPause(d);
  updateFeed(d.new_alerts || []);
  updateActiveStrategies(d.active_strategies || {});
}

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
  try { await fetch(url, { method: 'POST' }); }
  catch(e) { console.error('pause/resume error', e); }
}

// ── HONEST BOOK (scrubbed scoreboard) ───────────────────────────────────────
function _hbSign(v, dec) {
  const n = Number(v) || 0;
  return (n >= 0 ? '+' : '') + n.toFixed(dec === undefined ? 2 : dec);
}
async function updateHonestBook() {
  try {
    const r = await fetch('/api/honest-book');
    if (!r.ok) return;
    const d = await r.json();
    const days = d.days || [];
    const today = d.today_utc || '';
    const trow = days.find(x => x.day === today) ||
      { n: 0, scrub_mean: 0, scrub_median: 0, scrub_sum: 0, win_pct: 0, n_tokens: 0, tok_mean: 0, spikes_excluded: 0, spike_pp: 0 };
    const set = (id, txt, cls) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.textContent = txt;
      if (cls !== undefined) el.className = cls;
    };
    set('hb-sum',    _hbSign(trow.scrub_sum, 0) + 'pp', 'val big ' + pnlClass(trow.scrub_sum));
    set('hb-mean',   _hbSign(trow.scrub_mean) + '%',    'val ' + pnlClass(trow.scrub_mean));
    set('hb-median', _hbSign(trow.scrub_median) + '%',  'val ' + pnlClass(trow.scrub_median));
    set('hb-win',    (trow.win_pct || 0).toFixed(0) + '%',
        'val ' + ((trow.win_pct || 0) >= 50 ? 'green' : (trow.win_pct || 0) >= 35 ? 'yellow' : 'red'));
    set('hb-n',      String(trow.n));
    set('hb-tokens', String(trow.n_tokens));
    set('hb-spikes', trow.spikes_excluded + ' (' + _hbSign(trow.spike_pp, 0) + 'pp)');
    const tbody = document.querySelector('#hb-table tbody');
    const last7 = days.slice(-7).reverse();
    if (!last7.length) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty">No scrubbed positions yet</td></tr>';
    } else {
      tbody.innerHTML = last7.map(x => `<tr>
        <td>${x.day === today ? '<b>' + x.day + '</b>' : x.day}</td>
        <td>${x.n}</td>
        <td class="${pnlClass(x.scrub_mean)}">${_hbSign(x.scrub_mean)}</td>
        <td class="${pnlClass(x.scrub_median)}">${_hbSign(x.scrub_median)}</td>
        <td class="${pnlClass(x.scrub_sum)}">${_hbSign(x.scrub_sum, 0)}</td>
        <td>${(x.win_pct || 0).toFixed(0)}</td>
        <td>${x.n_tokens}</td>
        <td class="${pnlClass(x.tok_mean)}">${_hbSign(x.tok_mean)}</td>
        <td class="muted">${x.spikes_excluded} (${_hbSign(x.spike_pp, 0)})</td>
      </tr>`).join('');
    }
    const p = d.pooled || {};
    const note = document.getElementById('hb-note');
    if (note && p.n) {
      note.innerHTML = 'Scrub rule: ' + escHtml(d.spike_rule || '') +
        ' &middot; POOLED: n=' + p.n + ' mean ' + _hbSign(p.mean) + '% median ' + _hbSign(p.median) +
        '% win ' + (p.win_pct || 0).toFixed(0) + '% &middot; spikes excluded ' + p.spikes_excluded +
        ' (' + _hbSign(p.spike_pp, 0) + 'pp). Enforce / go-live decisions quote THESE columns.';
    }
    if (d.buys_today_utc !== undefined) {
      document.getElementById('strip-buys').textContent = d.buys_today_utc;
    }
  } catch (e) { console.warn('honest-book fetch failed', e); }
}
updateHonestBook();
setInterval(updateHonestBook, 120000);

// ── WALLET TRUTH (on-chain SOL delta — the only honest live number) ─────────
async function updateWalletTruth() {
  try {
    const r = await fetch('/api/wallet-truth');
    if (!r.ok) return;
    const d = await r.json();
    const delta = document.getElementById('wt-delta');
    if (!delta) return;
    if (typeof d.delta_sol === 'number') {
      const s = d.delta_sol;
      delta.textContent = (s >= 0 ? '+' : '') + s.toFixed(4) + ' SOL';
      delta.style.color = s >= 0 ? 'var(--green, #2ecc71)' : 'var(--red, #e74c3c)';
    } else {
      delta.textContent = d.paper_mode ? 'paper mode' : '—';
      delta.style.color = 'var(--muted)';
    }
    document.getElementById('wt-now').textContent =
      (typeof d.sol_now === 'number') ? d.sol_now.toFixed(4) + ' SOL' : '—';
    document.getElementById('wt-base').textContent =
      (typeof d.baseline_sol === 'number') ? d.baseline_sol.toFixed(4) + ' SOL' : '—';
    document.getElementById('wt-open').textContent =
      (d.open_live_positions == null) ? '—' : String(d.open_live_positions);
    document.getElementById('wt-deployed').textContent =
      (typeof d.deployed_usd === 'number' && d.deployed_usd > 0)
        ? '$' + d.deployed_usd.toFixed(2) + ' (cost)' : '—';
    document.getElementById('wt-note').textContent =
      (d.stale ? 'STALE (rpc error) · ' : '') + (d.note || '') +
      (typeof d.delta_sol === 'number' && d.open_live_positions > 0
        ? ' Δ dips while positions are open — final truth on close.' : '');
  } catch (e) {}
}
setInterval(updateWalletTruth, 60000);
updateWalletTruth();

// ── LIVE-SLOT RACE (per-bot scrubbed 7d scoreboard) ─────────────────────────
async function updateRace() {
  try {
    const r = await fetch('/api/race');
    if (!r.ok) return;
    const d = await r.json();
    const tbody = document.querySelector('#race-table tbody');
    if (!tbody) return;
    const bots = d.bots || [];
    if (!bots.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="empty">No badday_* sells in the last 7 days</td></tr>';
      return;
    }
    const win = d.window_days || [];
    tbody.innerHTML = bots.map(b => {
      const byDay = {};
      (b.per_day || []).forEach(p => { byDay[p.day] = p; });
      const cells = win.map(day => {
        const p = byDay[day];
        if (!p) return `<span class="race-cell" title="${day}: no tokens"></span>`;
        return `<span class="race-cell ${p.green ? 'g' : 'r'}" title="${day}: ${_hbSign(p.mean_per_token)}%/tok (${p.tokens} tok)"></span>`;
      }).join('');
      const lb = b.live_bar || {};
      const badge = lb.pace
        ? '<span class="race-bar-badge lit" title="on live-bar pace: >=+2.0/tok on >=5 days AND n>=30">LIVE BAR</span>'
        : `<span class="race-bar-badge" title="live bar: >=+2.0/tok days AND distinct tokens">${lb.met_days || 0}/5d &middot; n ${b.distinct_tokens_7d}/30</span>`;
      return `<tr>
        <td>${escHtml(b.bot_id)}</td>
        <td class="${pnlClass(b.mean_per_token_7d)}">${_hbSign(b.mean_per_token_7d)}%</td>
        <td style="text-align:left;"><span class="race-cells">${cells}</span></td>
        <td>${b.distinct_tokens_7d}</td>
        <td class="${(b.green_days || 0) * 2 > (b.day_count || 0) ? 'green' : ''}">${b.green_days}/${b.day_count}</td>
        <td style="text-align:left;">${badge}</td>
      </tr>`;
    }).join('');
  } catch (e) { console.warn('race fetch failed', e); }
}
updateRace();
setInterval(updateRace, 60000);

// ── Status strip: gates + SOL ────────────────────────────────────────────────
function _chip(id, txt, state, title) {
  const el = document.getElementById(id);
  if (!el) return;
  el.querySelector('.v').textContent = txt;
  el.className = 'chip' + (state ? ' ' + state : '');
  if (title) el.title = title;
}
async function updateGates() {
  try {
    const r = await fetch('/api/gates');
    if (!r.ok) return;
    const d = await r.json();
    const sol = d.sol || {};
    const f = (v) => (v === null || v === undefined) ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(1) + '%';
    if (sol.has_data) {
      const px = sol.price_usd ? '$' + Number(sol.price_usd).toFixed(2) + ' ' : '';
      _chip('chip-sol', px + 'h1 ' + f(sol.pc_h1) + ' · h6 ' + f(sol.pc_h6) + ' · h24 ' + f(sol.pc_h24),
            (sol.pc_h24 || 0) >= 0 ? 'ok' : 'bad');
    } else {
      _chip('chip-sol', 'warming up', 'warn');
    }
    const g = d.gates || {};
    const macroBlocked = sol.strict_status === 'BLOCK';
    _chip('chip-solmacro',
          (g.sol_macro_mode || '?') + (sol.has_data ? (macroBlocked ? ' · BLOCK' : ' · PASS') : ''),
          macroBlocked ? 'bad' : 'ok',
          'SOL_MACRO_GATE_MODE=' + g.sol_macro_mode + (sol.reasons && sol.reasons.length ? ' | ' + sol.reasons.join(', ') : ''));
    _chip('chip-greenday', g.green_day_mode || 'off',
          g.green_day_mode === 'enforce' ? 'bad' : (g.green_day_mode === 'shadow' ? 'warn' : ''));
    _chip('chip-bel', g.breakeven_lock_mode || 'shadow',
          g.breakeven_lock_mode === 'enforce' ? 'ok' : (g.breakeven_lock_mode === 'shadow' ? 'warn' : ''));
    const h = g.hours || {};
    if (h.start_ct !== null && h.start_ct !== undefined) {
      _chip('chip-hours',
            String(h.start_ct).padStart(2,'0') + '–' + String(h.end_ct).padStart(2,'0') +
            (h.in_window ? ' · OPEN' : ' · CLOSED') + (h.now_ct ? ' (' + h.now_ct + ')' : ''),
            h.in_window ? 'ok' : 'bad');
    }
  } catch (e) { console.warn('gates fetch failed', e); }
}
updateGates();
setInterval(updateGates, 60000);

// ── Trade history ──────────────────────────────────────────────────────────
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
setInterval(loadTradeHistory, 120000);  // 2026-05-18 raised 30s->120s (egress)

function normalizeChainKey(c) {
  const s = c.toLowerCase();
  if (s === 'solana' || s === 'sol') return 'sol';
  return s;
}

function filterTrades() {
  const q      = (document.getElementById('trade-search').value || '').toLowerCase();
  const chain  = (document.getElementById('trade-chain-filter').value || '').toLowerCase();
  const strat  = (document.getElementById('trade-strat-filter').value || '').toLowerCase();
  const tbody  = document.getElementById('trade-history-body');

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
      <td>${fmtPct(pnlPct)}</td>
      <td class="muted" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${escHtml(t.reason||'')}">
        ${escHtml((t.reason||'').slice(0,40))}
      </td>
    </tr>`;
  }).join('');
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
    const dotColor = running ? 'var(--green)' : 'var(--red)';
    const badgeCls = running ? 'badge-running' : 'badge-stopped';
    const badgeTxt = running ? 'RUNNING' : 'STOPPED';
    const pnlCls   = (s.total_pnl || 0) >= 0 ? 'green' : 'red';
    const wr        = (s.win_rate || 0).toFixed(1);
    const wrCls     = (s.win_rate || 0) >= 50 ? 'green' : (s.win_rate || 0) >= 35 ? 'yellow' : 'red';

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

// ── User Watchlist (curator-driven, hot-reload) ──────────────────────────
async function loadUserWatchlist() {
  try {
    const res = await fetch('/api/user-watchlist');
    const data = await res.json();
    const tokens = data.tokens || [];
    const body = document.getElementById('user-watchlist-body');
    const count = document.getElementById('user-watchlist-count');
    if (count) count.textContent = tokens.length + ' token' + (tokens.length !== 1 ? 's' : '');
    if (!tokens.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">Empty — paste an address above to start farming runners</td></tr>';
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
        '<td><button onclick="removeUserWatchlist(\'' + addr + '\')" class="btn btn-danger" style="padding:4px 10px;font-size:11px;">Remove</button></td>' +
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
setInterval(loadUserWatchlist, 60000);

// ── GOAL meter + FLEET tables ───────────────────────────────────────────────
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
    el.style.color = tot >= g.goal_usd ? "var(--green)" : (tot >= 0 ? "var(--text)" : "var(--red)");
    const pct = Math.max(0, Math.min(100, 100 * tot / g.goal_usd));
    const bar = document.getElementById("goal-bar");
    bar.style.width = pct + "%";
    bar.style.background = tot >= g.goal_usd ? "var(--green)" : (tot >= 0 ? "var(--amber)" : "var(--red)");
    const badge = document.getElementById("goal-badge");
    badge.textContent = tot >= g.goal_usd ? "MET ✓" : `$${(g.goal_usd - tot).toFixed(0)} to go`;
    badge.style.color = tot >= g.goal_usd ? "var(--green)" : "var(--muted)";
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
  const cls = b.total_pnl_realized > 0 ? "pnl-pos" : (b.total_pnl_realized < 0 ? "pnl-neg" : "");
  return `<tr>
    <td>${escHtml(b.bot_id)}</td>
    <td>$${b.balance_usd.toFixed(2)}</td>
    <td>${b.open_position_count}</td>
    <td>${b.total_trades}</td>
    <td>${wr}</td>
    <td class="${cls}">$${b.total_pnl_realized.toFixed(2)}</td>
    <td>${perTr}</td>
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
      cand.innerHTML = '<tr><td colspan="7" class="empty">No bots registered yet</td></tr>';
      return;
    }
    let nExp = 0, totOpen = 0;
    for (const b of bots) {
      totOpen += (b.open_position_count || 0);
      if (_candSet && _candSet.has(b.bot_id)) {
        cand.insertAdjacentHTML("beforeend", botRowHtml(b));
      } else {
        exp.insertAdjacentHTML("beforeend", botRowHtml(b));
        nExp++;
      }
    }
    if (!cand.innerHTML) cand.innerHTML = '<tr><td colspan="7" class="empty">candidate bots have no rows yet</td></tr>';
    document.getElementById("exp-count").textContent = nExp;
    document.getElementById("strip-open").textContent = totOpen;  // fleet-wide open positions
  } catch (e) {
    console.error("updateFleet failed", e);
  }
}
setInterval(updateFleet, 45000);  // 2026-06-04 15s->45s (egress)
setInterval(updateGoal, 60000);
updateGoal().then(updateFleet);

// ── LIVE TRADING card (read-only view of the real-money bots) ──
function _liveMoney(v) {
  if (v === null || v === undefined || isNaN(v)) return "--";
  const n = Number(v);
  return (n < 0 ? "-$" : "$") + Math.abs(n).toFixed(2);
}
function _liveMoneyClass(v) {
  if (v === null || v === undefined || isNaN(v)) return "";
  return Number(v) > 0 ? "green" : (Number(v) < 0 ? "red" : "");
}
async function updateLive() {
  try {
    const resp = await fetch("/api/live");
    if (!resp.ok) return;
    const d = await resp.json();
    const card = document.getElementById("live-card");
    const pill = document.getElementById("live-pill");
    if (d.live_mode) {
      card.classList.add("is-live"); pill.classList.remove("armed");
      pill.textContent = "● LIVE";
    } else {
      card.classList.remove("is-live"); pill.classList.add("armed");
      pill.textContent = "PAPER — ARMED, NOT LIVE YET";
    }
    const t = d.totals || {};
    const today = document.getElementById("live-today");
    today.textContent = "Today: " + _liveMoney(t.today_pnl_usd);
    today.className = "live-big " + _liveMoneyClass(t.today_pnl_usd);
    document.getElementById("live-realized").textContent =
      "realized total: " + _liveMoney(t.realized_pnl_usd) + " (simulated ledger — not live truth)";

    const w = d.wallet || {};
    document.getElementById("live-wallet").textContent =
      (w.sol_balance === null || w.sol_balance === undefined)
        ? "-- (paper)"
        : w.sol_balance.toFixed(3) + " SOL" +
          (w.usd_value !== null && w.usd_value !== undefined ? " (" + _liveMoney(w.usd_value) + ")" : "");
    document.getElementById("live-floor").textContent =
      w.floor_usd !== null && w.floor_usd !== undefined ? "$" + Number(w.floor_usd).toFixed(0) : "--";
    const af = document.getElementById("live-abovefloor");
    af.textContent = _liveMoney(w.above_floor_usd);
    af.className = "val " + _liveMoneyClass(w.above_floor_usd);
    document.getElementById("live-open").textContent = (t.open_positions ?? 0);

    const tb = document.getElementById("live-tbody");
    tb.innerHTML = "";
    for (const b of (d.bots || [])) {
      const wr = b.win_rate_pct === null || b.win_rate_pct === undefined ? "--" : b.win_rate_pct.toFixed(0) + "%";
      const sz = b.base_position_usd === null || b.base_position_usd === undefined ? "--" : "$" + Number(b.base_position_usd).toFixed(0);
      tb.insertAdjacentHTML("beforeend",
        `<tr>
          <td>${escHtml(b.bot_id)}${b.registered ? "" : ' <span class="live-sub">(not registered)</span>'}</td>
          <td class="${_liveMoneyClass(b.daily_pnl_usd)}">${_liveMoney(b.daily_pnl_usd)}</td>
          <td class="${_liveMoneyClass(b.realized_pnl_total_usd)}">${_liveMoney(b.realized_pnl_total_usd)}</td>
          <td>${b.open_position_count ?? 0}</td>
          <td>${wr}</td>
          <td>${sz}</td>
        </tr>`);
    }
    if (!tb.innerHTML) tb.innerHTML = '<tr><td colspan="6" class="muted">no live bots</td></tr>';

    const c = d.caps || {};
    const inUsed = Number(c.inflight_used_usd || 0), inCap = Number(c.inflight_max_usd || 0);
    document.getElementById("live-inflight-lbl").textContent =
      "$" + inUsed.toFixed(0) + " / $" + inCap.toFixed(0);
    document.getElementById("live-inflight-bar").style.width =
      (inCap > 0 ? Math.min(100, 100 * inUsed / inCap) : 0) + "%";
    const dp = Number(c.daily_pnl_usd || 0), kill = Number(c.daily_kill_usd || 0);
    const lossUsed = dp < 0 ? Math.abs(dp) : 0;
    document.getElementById("live-kill-lbl").textContent =
      _liveMoney(dp) + " / -$" + kill.toFixed(0);
    document.getElementById("live-kill-bar").style.width =
      (kill > 0 ? Math.min(100, 100 * lossUsed / kill) : 0) + "%";
    document.getElementById("live-pertoken").textContent =
      (c.per_token_max_positions ?? "--") + " / $" + (c.per_token_max_usd ?? "--");

    const ps = d.profit_sweep || {};
    const cold = ps.cold_wallet ? (ps.cold_wallet.slice(0, 4) + ".." + ps.cold_wallet.slice(-4)) : "unset";
    const swept = (ps.total_swept_usd === null || ps.total_swept_usd === undefined)
      ? "n/a (not tracked)" : _liveMoney(ps.total_swept_usd);
    document.getElementById("live-sweep").textContent =
      "Profit-sweep: " + (ps.enabled ? "ENABLED" : "disabled") +
      (ps.dry_run ? " (dry-run)" : " (LIVE)") +
      "  ·  floor $" + (ps.floor_usd !== null && ps.floor_usd !== undefined ? Number(ps.floor_usd).toFixed(0) : "--") +
      "  ·  cold " + cold +
      "  ·  swept " + swept;
  } catch (e) {
    console.error("updateLive failed", e);
  }
}
setInterval(updateLive, 45000);
updateLive();

// ── Robinhood Chain (paper lane — /api/rh-paper) ─────────────────────────────
async function updateRhPaper() {
  try {
    const r = await fetch('/api/rh-paper');
    if (!r.ok) return;
    const d = await r.json();
    const tbody = document.querySelector('#rh-table tbody');
    const note = document.getElementById('rh-note');
    if (!d.available) {
      document.getElementById('rh-day-pnl').textContent = '—';
      document.getElementById('rh-entries').textContent = '—';
      document.getElementById('rh-exits').textContent = '—';
      document.getElementById('rh-lag').textContent = '—';
      if (note) note.textContent = d.note || 'no data yet';
      if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="empty">No RH paper ledger yet</td></tr>';
      return;
    }
    const pnl = Number(d.day_pnl_usd) || 0;
    const el = document.getElementById('rh-day-pnl');
    el.textContent = (pnl < 0 ? '-$' : '$') + Math.abs(pnl).toFixed(2);
    el.style.color = pnl > 0 ? 'var(--green)' : (pnl < 0 ? 'var(--red)' : 'var(--text)');
    document.getElementById('rh-entries').textContent = d.entries || 0;
    document.getElementById('rh-exits').textContent = d.exits || 0;
    const lag = (d.lag || {}).median_lat_total_s;
    document.getElementById('rh-lag').textContent =
      (lag === null || lag === undefined) ? '—' : Number(lag).toFixed(2) + 's';
    if (note) note.textContent =
      'day = ' + (d.day_utc || 'UTC') + ' · paper fills (real pool quotes), not live';
    const rows = (d.trades || []).slice(-8).reverse();
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty">No trades in ledger yet</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(t => {
      const isSell = t.ev === 'sell';
      const pnlU = (isSell && typeof t.pnl_usd === 'number') ? t.pnl_usd : null;
      const usd = typeof t.usd === 'number' ? t.usd
        : (typeof t.usd_out === 'number' ? t.usd_out : null);
      return `<tr>
        <td class="muted">${escHtml(String(t.ts || '').slice(5, 16).replace('T', ' '))}</td>
        <td class="${isSell ? 'red' : 'green'}">${escHtml(t.ev || '?')}</td>
        <td style="font-weight:600">$${escHtml(t.sym || '?')}</td>
        <td class="muted">${usd === null ? '—' : '$' + usd.toFixed(2)}</td>
        <td class="${pnlU === null ? 'muted' : pnlClass(pnlU)}">${pnlU === null ? '—' : fmtUsd(pnlU)}</td>
        <td class="${typeof t.pnl_pct === 'number' ? pnlClass(t.pnl_pct) : 'muted'}">${typeof t.pnl_pct === 'number' ? fmtPct(t.pnl_pct) : '—'}</td>
        <td class="muted">${typeof t.lat_total_s === 'number' ? t.lat_total_s.toFixed(2) : '—'}</td>
      </tr>`;
    }).join('');
  } catch (e) { console.warn('rh-paper fetch failed', e); }
}
updateRhPaper();
setInterval(updateRhPaper, 120000);

// ── Per-bot re-baseline (flatten + zero ledger) ─────────────────────────────
async function resetBot() {
  const id = (document.getElementById("reset-bot-id").value || "").trim();
  const el = document.getElementById("reset-result");
  if (!id) { el.textContent = "enter a bot_id"; return; }
  if (!confirm("FULL RESET " + id + "?\n\nFlattens its open positions AND zeros its ledger. bot_state is backed up first. This is destructive.")) return;
  el.textContent = "resetting " + id + "…";
  try {
    const r = await fetch("/api/bots/" + encodeURIComponent(id) + "/reset", { method: "POST" });
    if (r.status === 401) {
      el.textContent = "✗ login required — enter the dashboard username/password at the browser prompt";
      return;
    }
    let d = {};
    try { d = await r.json(); } catch (_) { el.textContent = "✗ server error (HTTP " + r.status + ")"; return; }
    el.textContent = d.ok
      ? ("✓ " + id + ": flattened " + d.flattened + " positions, ledger zeroed")
      : ("✗ " + (d.error || "failed"));
  } catch (e) { el.textContent = "✗ " + e; }
}
</script>
<div style="position:relative;z-index:1;text-align:center;color:var(--muted);font-size:10px;letter-spacing:3px;padding:18px 0 24px;opacity:.5;">
  &#x2B22; I AM THE ONE WHO KNOCKS &#x2B22;
</div>
</body>
</html>
"""


# ── Unified filter-shadow read helper ─────────────────────────────────────────

def read_filter_shadow_payload(data_dir: str) -> dict:
    """Pure, BLOCKING read of the two precomputed shadow-P&L JSONs (run off the
    loop via asyncio.to_thread). Returns the unified /api/filter-shadow payload.

    FAIL-OPEN: if neither precomputed file exists yet (scorer hasn't run), or a
    file is unreadable, the corresponding section is {} and a note is set —
    never raises. Reads only the SMALL precomputed JSONs, never the raw .jsonl.
    raw_counts are cheap line-counts of the source logs for an at-a-glance
    'is data flowing' signal."""
    import datetime as _dt

    def _load_json(name: str) -> dict:
        try:
            p = os.path.join(data_dir, name)
            if not os.path.exists(p):
                return {}
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _count_lines(name: str) -> int:
        try:
            p = os.path.join(data_dir, name)
            if not os.path.exists(p):
                return 0
            n = 0
            with open(p, "rb") as f:
                for _ in f:
                    n += 1
            return n
        except Exception:
            return 0

    filters = _load_json("filter_shadow_pnl.json")
    gates = _load_json("shadow_gate_pnl.json")
    payload = {
        "ok": True,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "filters": filters,
        "gates": gates,
        "raw_counts": {
            "filter_log_lines": _count_lines("filter_shadow_log.jsonl"),
            "gate_log_lines": _count_lines("shadow_gate_events.jsonl"),
        },
    }
    if not filters and not gates:
        payload["note"] = "scorer not yet run"
    return payload


# ── Honest-book scrub (mirrors scripts/honest_book.py) ───────────────────────

def compute_honest_book(trades: list, days: int = 10,
                        spike_hold_secs: float = 10.0) -> dict:
    """Pure, BLOCKING scrub of the trade ledger into the HONEST BOOK scoreboard
    (run off the loop via asyncio.to_thread). Reproduces scripts/honest_book.py
    EXACTLY: position = sells grouped by (bot_id, address, round(entry_price, 12)),
    frac-weighted return; spike = ret>0 AND first-sell hold_secs<10 AND mae>=0
    (unrealizable latency print — excluded, reported separately); per-token
    dedup across mirror bots reported alongside. badday_* fleet only.

    Never raises for a malformed record — skips it. Returns per-day rows plus
    the pooled scrubbed figures and today's buy count (all bots, UTC day)."""
    import statistics as _st

    def _fl(x):
        try:
            v = float(x)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pos: dict = {}
    buys_today = 0
    for t in trades:
        if not isinstance(t, dict):
            continue
        ttype = t.get("type")
        if ttype == "buy" and str(t.get("time", ""))[:10] == today_utc:
            buys_today += 1
        if ttype != "sell":
            continue
        p = _fl(t.get("pnl_pct"))
        if p is None or not str(t.get("bot_id", "")).startswith("badday"):
            continue
        k = (t.get("bot_id"), t.get("address") or t.get("token"),
             round(_fl(t.get("entry_price")) or 0, 12))
        r = pos.setdefault(k, {"ret": 0.0, "day": str(t.get("time"))[:10],
                               "tok": t.get("address") or t.get("token"),
                               "first_hold": None, "first_mae": None})
        r["ret"] += p * (_fl(t.get("sell_fraction")) or 1.0)
        h = _fl(t.get("hold_secs"))
        if r["first_hold"] is None or (h is not None and h < r["first_hold"]):
            r["first_hold"] = h
            r["first_mae"] = _fl(t.get("mae_pct"))

    def _is_spike(r):
        return (r["ret"] > 0 and r["first_hold"] is not None
                and r["first_hold"] < spike_hold_secs
                and r["first_mae"] is not None and r["first_mae"] >= 0)

    by_day: dict = {}
    for r in pos.values():
        by_day.setdefault(r["day"], []).append(r)

    out_days = []
    for day in sorted(by_day)[-days:]:
        rows = by_day[day]
        keep = [r["ret"] for r in rows if not _is_spike(r)]
        spikes = [r["ret"] for r in rows if _is_spike(r)]
        toks: dict = {}
        for r in rows:
            if not _is_spike(r):
                toks.setdefault(r["tok"], []).append(r["ret"])
        tok_means = [_st.mean(v) for v in toks.values()]
        out_days.append({
            "day": day,
            "n": len(keep),
            "scrub_mean": round(_st.mean(keep), 2) if keep else 0.0,
            "scrub_median": round(_st.median(keep), 2) if keep else 0.0,
            "scrub_sum": round(sum(keep), 1),
            "win_pct": round(100.0 * sum(1 for x in keep if x > 0) / len(keep), 1)
                       if keep else 0.0,
            "n_tokens": len(toks),
            "tok_mean": round(_st.mean(tok_means), 2) if tok_means else 0.0,
            "spikes_excluded": len(spikes),
            "spike_pp": round(sum(spikes), 1),
            "raw_n": len(rows),
            "raw_sum": round(sum(r["ret"] for r in rows), 1),
        })

    allr = list(pos.values())
    keep = [r["ret"] for r in allr if not _is_spike(r)]
    spikes = [r["ret"] for r in allr if _is_spike(r)]
    pooled = {}
    if keep:
        pooled = {
            "n": len(keep),
            "mean": round(_st.mean(keep), 2),
            "median": round(_st.median(keep), 2),
            "win_pct": round(100.0 * sum(1 for x in keep if x > 0) / len(keep), 1),
            "spikes_excluded": len(spikes),
            "spike_pp": round(sum(spikes), 1),
        }
    return {
        "ok": True,
        "scope": "badday_* fleet, position-level (frac-weighted), pnl_pct units",
        "spike_rule": (f"pnl>0 AND first-sell hold<{spike_hold_secs:.0f}s AND "
                       "mae>=0 = unrealizable spike (excluded)"),
        "today_utc": today_utc,
        "buys_today_utc": buys_today,
        "days": out_days,
        "pooled": pooled,
    }


# ── Live-slot race (mirrors scripts/bot_leaderboard.py) ──────────────────────

def compute_race(trades: list, enabled_ids=None, days: int = 7,
                 spike_hold_secs: float = 10.0) -> dict:
    """Pure, BLOCKING per-bot LIVE-SLOT RACE scoreboard over the last `days`
    UTC calendar days (run off the loop via asyncio.to_thread). Reproduces
    scripts/bot_leaderboard.py math: sells joined per bot per token, weight =
    pnl_pct * sell_fraction, SCRUB = exclude sells with pnl_pct>0 AND
    hold_secs<10 (the slim-record heuristic; the position-level scrub lives
    in compute_honest_book), per-day green = mean of per-token nets > 0.

    Live bar per bot: per-token mean >= +2.0pp on >= 5 days AND >= 30
    distinct tokens. `pace` = both legs met inside the window.

    enabled_ids: optional set of enabled bot ids (config/bots). Restricts
    rows to enabled bots AND emits zero rows for enabled badday_* bots with
    no in-window sells, so idle candidates stay visible. None = fail-open
    (every badday_* bot with sells appears).

    Never raises for a malformed record — skips it. Aggregates only (a few
    KB payload) — no trade lists (egress discipline)."""
    import statistics as _st
    from datetime import timedelta as _td

    def _fl(x):
        try:
            v = float(x)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    today = datetime.now(timezone.utc).date()
    window = [(today - _td(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    win_set = set(window)

    per_tok: dict = {}    # bot -> token -> net pp (sell-fraction weighted)
    per_day: dict = {}    # bot -> day -> token -> net pp
    for t in trades:
        if not isinstance(t, dict) or t.get("type") != "sell":
            continue
        b = str(t.get("bot_id", ""))
        if not b.startswith("badday_"):
            continue
        if enabled_ids is not None and b not in enabled_ids:
            continue
        p = _fl(t.get("pnl_pct"))
        if p is None:
            continue
        day = str(t.get("time", ""))[:10]
        if day not in win_set:
            continue
        h = _fl(t.get("hold_secs"))
        if p > 0 and h is not None and h < spike_hold_secs:
            continue  # SCRUB: unrealizable latency spike
        w = p * (_fl(t.get("sell_fraction")) or 1.0)
        tok = t.get("address") or t.get("token")
        per_tok.setdefault(b, {}).setdefault(tok, 0.0)
        per_tok[b][tok] += w
        per_day.setdefault(b, {}).setdefault(day, {}).setdefault(tok, 0.0)
        per_day[b][day][tok] += w

    bot_ids = set(per_tok)
    if enabled_ids is not None:
        bot_ids |= {b for b in enabled_ids if str(b).startswith("badday_")}

    rows = []
    for b in sorted(bot_ids):
        days_map = per_day.get(b, {})
        pd_rows, green, met = [], 0, 0
        for day in window:
            dtoks = days_map.get(day)
            if not dtoks:
                continue
            m = _st.mean(list(dtoks.values()))
            g = m > 0
            green += 1 if g else 0
            met += 1 if m >= 2.0 else 0
            pd_rows.append({"day": day, "tokens": len(dtoks),
                            "mean_per_token": round(m, 2), "green": g})
        vals = list(per_tok.get(b, {}).values())
        n7 = len(vals)
        n_ok = n7 >= 30
        rows.append({
            "bot_id": b,
            "per_day": pd_rows,
            "distinct_tokens_7d": n7,
            "mean_per_token_7d": round(_st.mean(vals), 2) if vals else 0.0,
            "green_days": green,
            "day_count": len(pd_rows),
            "live_bar": {"met_days": met, "n_ok": n_ok,
                         "pace": met >= 5 and n_ok},
        })
    rows.sort(key=lambda r: r["mean_per_token_7d"], reverse=True)
    return {
        "ok": True,
        "window_days": window,
        "scrub_rule": (f"sell excluded when pnl_pct>0 AND "
                       f"hold_secs<{spike_hold_secs:.0f}s (latency spike)"),
        "live_bar_rule": ("per-token mean >= +2.0pp on >= 5 days "
                          "AND >= 30 distinct tokens"),
        "bots": rows,
    }


# ── Robinhood Chain paper lane (/api/rh-paper) ────────────────────────────────

RH_PAPER_MAX_LINES = 50_000


def rh_paper_dedup_key(row: dict) -> tuple:
    """De-dup identity for an RH paper ledger row: (ts, ev, pool)."""
    return (str(row.get("ts", "")), str(row.get("ev", "")),
            str(row.get("pool", "")))


def merge_rh_paper_rows(existing: list, incoming: list,
                        max_lines: int = RH_PAPER_MAX_LINES) -> tuple:
    """Pure merge for POST /api/rh-paper/ingest. Appends incoming ledger rows
    to existing, de-duped on (ts, ev, pool) — re-pushing the same session
    ledger is idempotent. Non-dict rows and rows missing ev or ts are
    skipped. Result is capped at max_lines by truncating the OLDEST (front),
    matching the append-order file. Returns (merged_rows, added_count).
    Never raises on malformed rows."""
    merged = [r for r in (existing or []) if isinstance(r, dict)]
    seen = {rh_paper_dedup_key(r) for r in merged}
    added = 0
    for r in (incoming or []):
        if not isinstance(r, dict) or not r.get("ev") or not r.get("ts"):
            continue
        k = rh_paper_dedup_key(r)
        if k in seen:
            continue
        seen.add(k)
        merged.append(r)
        added += 1
    if len(merged) > max_lines:
        merged = merged[-max_lines:]
    return merged, added


def compute_rh_paper_summary(rows: list, last_n: int = 20,
                             today_utc: str = None) -> dict:
    """Pure aggregation for GET /api/rh-paper over the RH paper-lane ledger
    (scripts/rh_paper_lane.py events: ev=buy|sell). Reports:
      - entries / exits: total buy / sell events in the ledger
      - day_pnl_usd: sum of sell pnl_usd for today's UTC date
      - trades: the last `last_n` events (raw rows, for the dashboard table)
      - lag.median_lat_total_s: median detect->fill latency across buys
    Skips malformed rows; never raises."""
    import statistics as _st
    if today_utc is None:
        today_utc = datetime.now(timezone.utc).date().isoformat()
    entries = exits = 0
    day_pnl = 0.0
    lats = []
    clean = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ev = r.get("ev")
        if ev == "buy":
            entries += 1
            lat = r.get("lat_total_s")
            if isinstance(lat, (int, float)):
                lats.append(float(lat))
        elif ev == "sell":
            exits += 1
            if str(r.get("ts", ""))[:10] == today_utc:
                p = r.get("pnl_usd")
                if isinstance(p, (int, float)):
                    day_pnl += float(p)
        else:
            continue
        clean.append(r)
    return {
        "available": True,
        "entries": entries,
        "exits": exits,
        "day_utc": today_utc,
        "day_pnl_usd": round(day_pnl, 2),
        "trades": clean[-last_n:],
        "lag": {"median_lat_total_s":
                round(_st.median(lats), 2) if lats else None,
                "n": len(lats)},
    }


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
        self.app.router.add_get("/api/regime-patterns",     self._handle_regime_patterns)
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
        self.app.router.add_get("/api/live",                self._handle_api_live)
        self.app.router.add_get("/api/fast-watch",          self._handle_api_fast_watch)
        self.app.router.add_get("/api/fill-speed",          self._handle_api_fill_speed)
        self.app.router.add_get("/api/live-swaps",          self._handle_api_live_swaps)
        self.app.router.add_get("/api/live-real-pnl",       self._handle_api_live_real_pnl)
        self.app.router.add_get("/api/live-faithful-pnl",   self._handle_api_live_faithful_pnl)
        self.app.router.add_get("/api/exit-trigger-recon",   self._handle_api_exit_trigger_recon)
        self.app.router.add_get("/api/rt-shadow",           self._handle_api_rt_shadow)
        self.app.router.add_get("/api/paper-live-skips",    self._handle_api_paper_live_skips)
        self.app.router.add_get("/api/fill-probe",          self._handle_api_fill_probe)
        self.app.router.add_get("/api/top-bots",            self._handle_api_top_bots)
        self.app.router.add_get("/api/filter-shadow",       self._handle_api_filter_shadow)
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
        # 2026-07-01 redesign: honest scoreboard + status-strip gates (both GET,
        # UI-additive — no existing route or response shape was touched).
        self.app.router.add_get("/api/honest-book",           self._handle_api_honest_book)
        self.app.router.add_get("/api/gates",                 self._handle_api_gates)
        # 2026-07-04: LIVE-SLOT RACE — per-bot scrubbed 7d scoreboard (GET,
        # UI-additive, aggregates only).
        self.app.router.add_get("/api/race",                  self._handle_api_race)
        self.app.router.add_get("/api/wallet-truth",          self._handle_api_wallet_truth)
        self.app.router.add_post("/api/wallet-truth/rebase",  self._handle_api_wallet_truth_rebase)
        # 2026-07-10: Robinhood Chain paper lane — read + push its session
        # ledger (GET public like other reads; POST behind Basic auth via the
        # app-wide middleware, same as every write endpoint).
        self.app.router.add_get("/api/rh-paper",              self._handle_api_rh_paper)
        self.app.router.add_post("/api/rh-paper/ingest",      self._handle_api_rh_paper_ingest)

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

    async def _handle_regime_patterns(self, request):
        """GET /api/regime-patterns — latest in-bot hourly regime-pattern snapshot
        (winner-vs-loser entry-feature separators + current regime). Written each hour by
        core/regime_pattern_miner.py on the scan loop. Deterministic, no LLM, no API."""
        import json as _json, os as _os
        from aiohttp import web as _web
        _path = _os.path.join(_os.environ.get("DATA_DIR", "."), "_hourly_patterns_latest.json")
        try:
            with open(_path) as _fh:
                return _web.json_response(_json.loads(_fh.read()))
        except Exception:
            return _web.json_response({"status": "no patterns yet — the miner runs hourly on the scan loop"})

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

    async def _handle_api_fast_watch(self, request):
        """GET /api/fast-watch — read-only observability for the fast-watch
        armed-hit-rate, tick coverage, and per-bot breakdown.

        Replaces fragile railway-log scraping of the
        '[fast-watch] hit-rate buy ... armed=<bool>' and
        '[fast-watch] tick armed=N polled=M fired=K' lines. No money path,
        no behavior change — fast-watch stays shadow."""
        cors = {"Access-Control-Allow-Origin": "*"}
        dip = self._get_dip_scanner()
        if dip is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "DipScanner not registered"}),
                content_type="application/json", headers=cors,
            )
        stats = getattr(dip, "_fw_stats", {}) or {}
        # Compute hit_rate via the scanner's own helper when available; fall back
        # to inline math so the endpoint never 500s on a partial stats dict.
        try:
            hit_rate = type(dip).fw_hit_rate(stats)
        except Exception:
            h = stats.get("armed_hits", 0)
            m = stats.get("armed_misses", 0)
            hit_rate = (h / (h + m)) if (h + m) > 0 else None
        flags = {
            "FAST_WATCH_MODE": os.environ.get("FAST_WATCH_MODE", "off"),
            "JUPITER_PRICE_PRIMARY": os.environ.get("JUPITER_PRICE_PRIMARY"),
            "ONCHAIN_WS_MODE": os.environ.get("ONCHAIN_WS_MODE", "off"),
            "PAPER_PER_TOKEN_CAP_MODE": os.environ.get("PAPER_PER_TOKEN_CAP_MODE"),
            "EXIT_REPRICE_MODE": os.environ.get("EXIT_REPRICE_MODE", "off"),
        }
        payload = {
            "ok": True,
            "armed_hits": stats.get("armed_hits", 0),
            "armed_misses": stats.get("armed_misses", 0),
            "hit_rate": hit_rate,
            "by_bot": stats.get("by_bot", {}),
            "last_tick": stats.get("last_tick", {}),
            "ticks": stats.get("ticks", 0),
            "would_fire": stats.get("would_fire", 0),
            "flags": flags,
        }
        return web.Response(
            text=json.dumps(payload), content_type="application/json", headers=cors,
        )

    async def _handle_api_filter_shadow(self, request):
        """GET /api/filter-shadow — UNIFIED read of both shadow-P&L scorers.

        Reads the small PRECOMPUTED JSONs the in-bot scorer writes
        (filter_shadow_pnl.json = forward-candle per-filter; shadow_gate_pnl.json
        = trade-join per routing-gate) OFF THE EVENT LOOP via asyncio.to_thread.
        It NEVER reads the raw multi-MB .jsonl logs on the loop. FAIL-OPEN:
        missing files -> {ok:true, filters:{}, gates:{}, note:...}."""
        cors = {"Access-Control-Allow-Origin": "*"}
        data_dir = os.environ.get("DATA_DIR", "/data")
        try:
            payload = await asyncio.to_thread(read_filter_shadow_payload, data_dir)
        except Exception as e:
            payload = {"ok": True, "filters": {}, "gates": {},
                       "note": f"read error (fail-open): {e}"}
        return web.Response(
            text=json.dumps(payload), content_type="application/json", headers=cors,
        )

    async def _handle_api_live_swaps(self, request):
        """GET /api/live-swaps — COMPLETE live-swap telemetry (the probe data we pull
        WITHOUT SSH). Reads DATA_DIR/live_swaps.jsonl OFF the event loop via
        asyncio.to_thread and returns recent records + a summary (n, success rate,
        median/p90 total_latency_ms + execute_duration_ms, median/mean
        fill_vs_mid_slippage_pct, 429 totals, failure_reason histogram). Fail-open:
        missing file -> empty summary. No money path, no behavior change."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import asyncio as _asyncio
        from core.live_swap_log import (read_live_swaps as _read,
                                        summarize_live_swaps as _summarize,
                                        LOG_BASENAME as _BN)
        path = os.path.join(os.environ.get("DATA_DIR", "/data"), _BN)
        mode = os.environ.get("LIVE_SWAP_LOG_MODE", "on")
        try:
            limit = int(request.query.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        try:
            recs = await _asyncio.to_thread(_read, path)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "LIVE_SWAP_LOG_MODE": mode}),
                content_type="application/json", headers=cors,
            )
        try:
            summary = await _asyncio.to_thread(_summarize, recs)
        except Exception as e:
            summary = {"error": str(e)}
        # Part 2 (exit-tail design): the per-liquidity-bucket EXIT slip table
        # (thin/mid/deep slip_p50/p90/n) a human reads to set LIQ_EXIT_FLOOR_USD.
        # Read-only, fail-open -> {}; never reads the live gate at decision time.
        try:
            from core.fill_calibration import (
                calibrate_exit_from_live_swaps as _cal_exit)
            exit_slip = await _asyncio.to_thread(_cal_exit, recs)
        except Exception:
            exit_slip = {}
        payload = {
            "ok": True,
            "LIVE_SWAP_LOG_MODE": mode,
            "n_records": len(recs),
            "summary": summary,
            "exit_slip_by_liquidity": exit_slip,
            "recent": recs[-max(0, limit):] if recs else [],
            "note": ("fill_vs_mid_slippage_pct>0 = ADVERSE (paid up on buy / got less "
                     "on sell). durations are monotonic-ms; ts is wall-clock ISO."),
        }
        return web.Response(
            text=json.dumps(payload, default=str), content_type="application/json",
            headers=cors,
        )

    async def _read_hot_wallet_sol(self):
        """On-chain SOL balance of HOT_WALLET_ADDRESS (public key), so the real-P&L
        view works even when paused (PAPER_MODE=true), where the trader's live-only
        cache is empty. Off-loop urllib getBalance, 120s TTL cache, fail-open -> None.
        Never reads the private key — only the public address env."""
        import time as _t
        addr = (os.environ.get("HOT_WALLET_ADDRESS") or "").strip()
        rpc = (os.environ.get("SOLANA_RPC_URL") or "").strip()
        if not addr or not rpc:
            return None
        now = _t.monotonic()
        cache = getattr(self, "_hot_sol_cache", None)
        if cache and now - cache[0] < 120.0:
            return cache[1]

        def _fetch():
            import urllib.request as _u
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                               "params": [addr]}).encode()
            req = _u.Request(rpc, data=body, headers={"Content-Type": "application/json"})
            with _u.urlopen(req, timeout=8) as r:
                d = json.loads(r.read())
            return d["result"]["value"] / 1e9
        try:
            import asyncio as _asyncio
            sol = await _asyncio.to_thread(_fetch)
            self._hot_sol_cache = (now, sol)
            return sol
        except Exception as e:
            logger.debug("hot-wallet balance read failed: %s", e)
            return cache[1] if cache else None

    async def _sol_price_usd_cached(self):
        """SOL/USD with a 300s TTL cache, off-loop, fail-open -> None. Fallback for
        the real-P&L view when the trader's live-only price cache is empty (paper
        mode). Free public endpoint (Coinbase spot), no key."""
        import time as _t
        now = _t.monotonic()
        cache = getattr(self, "_sol_px_cache", None)
        if cache and now - cache[0] < 300.0:
            return cache[1]

        def _fetch():
            import urllib.request as _u
            req = _u.Request("https://api.coinbase.com/v2/prices/SOL-USD/spot",
                             headers={"User-Agent": "multichain-bot"})
            with _u.urlopen(req, timeout=8) as r:
                return float(json.loads(r.read())["data"]["amount"])
        try:
            import asyncio as _asyncio
            px = await _asyncio.to_thread(_fetch)
            self._sol_px_cache = (now, px)
            return px
        except Exception as e:
            logger.debug("sol price fetch failed: %s", e)
            return cache[1] if cache else None

    async def _handle_api_live_faithful_pnl(self, request):
        """GET /api/live-faithful-pnl — paper vs LIVE-FAITHFUL P&L fidelity gap.

        Reconstructs the realized P&L a funded live bot would have booked by EXCLUDING
        paper buys whose entry_meta daily_halt_would_block / reentry_cap_would_block was
        True (the trades a live_probe bot's daily-loss halt / re-entry cap would have
        skipped, but paper twins still booked). delta_usd = paper_total - live_faithful_total
        == sum of would-blocked realized $.

        Reads the SAME ledger source as /api/trades + /api/top-bots (tracker records +
        MultiBotTradeStore) OFF the event loop. Result cached 300s (ledger read + FIFO
        pairing is expensive) like _read_hot_wallet_sol / _sol_price_usd_cached.
        Read-only, fail-open: any error -> {"ok": false, "error": ...} with HTTP 200."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import time as _t
        now = _t.monotonic()
        cache = getattr(self, "_live_faithful_cache", None)
        if cache and now - cache[0] < 300.0:
            return web.Response(text=cache[1], content_type="application/json",
                                headers=cors)
        try:
            import asyncio as _asyncio
            from core.live_faithful_pnl import compute_live_faithful as _compute

            def _load_trades():
                trades = []
                if self._tracker is not None:
                    try:
                        trades = list(self._tracker.get_all_trades())
                    except Exception:
                        trades = []
                if self.trade_store is not None:
                    try:
                        trades = trades + self.trade_store.load_trades()
                    except Exception:
                        pass
                return trades

            trades = await _asyncio.to_thread(_load_trades)
            result = await _asyncio.to_thread(_compute, trades)
            payload = {"ok": True, **result}
            text = json.dumps(payload, default=str)
            self._live_faithful_cache = (now, text)
            return web.Response(text=text, content_type="application/json",
                                headers=cors)
        except Exception as e:
            logger.debug("live-faithful-pnl failed: %s", e)
            return web.Response(text=json.dumps({"ok": False, "error": str(e)}),
                                content_type="application/json", headers=cors)

    async def _handle_api_exit_trigger_recon(self, request):
        """GET /api/exit-trigger-recon — paper-vs-fresh EXIT-TRIGGER divergence lens.

        Reads DATA_DIR/exit_trigger_recon.jsonl (written by dip_scanner's
        _maybe_exit_trigger_recon under EXIT_TRIGGER_RECON_MODE=shadow) OFF the event
        loop via asyncio.to_thread, then summarizes how often paper's STALE-price exit
        DECISION (peak/tp1/tp2/trail/stop/never_runner/floor/HOLD) diverges from a FRESH
        Jupiter-priced re-tick of the same pre-tick state, and which way paper's booked
        exit pnl is biased on disagreements (pnl_delta = stale - fresh; >0 == paper
        OVERSTATES). Result cached 300s like /api/live-faithful-pnl. Read-only,
        fail-open: any error -> {"ok": false, "error": ...} with HTTP 200."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import time as _t
        now = _t.monotonic()
        mode = os.environ.get("EXIT_TRIGGER_RECON_MODE", "off")
        cache = getattr(self, "_exit_trigger_recon_cache", None)
        if cache and now - cache[0] < 300.0:
            return web.Response(text=cache[1], content_type="application/json",
                                headers=cors)
        try:
            import asyncio as _asyncio
            from core.exit_trigger_recon_summary import (
                summarize_exit_trigger_recon as _summarize)
            path = os.path.join(
                os.environ.get("DATA_DIR", "/data"), "exit_trigger_recon.jsonl")

            def _read(p):
                out = []
                try:
                    if not os.path.exists(p):
                        return out
                    with open(p) as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                out.append(json.loads(line))
                            except Exception:
                                continue
                except Exception:
                    pass
                return out

            recs = await _asyncio.to_thread(_read, path)
            summary = await _asyncio.to_thread(_summarize, recs)
            payload = {
                "ok": True,
                "EXIT_TRIGGER_RECON_MODE": mode,
                "n_records": len(recs),
                "summary": summary,
            }
            text = json.dumps(payload, default=str)
            self._exit_trigger_recon_cache = (now, text)
            return web.Response(text=text, content_type="application/json",
                                headers=cors)
        except Exception as e:
            logger.debug("exit-trigger-recon failed: %s", e)
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e)}),
                content_type="application/json", headers=cors)

    async def _handle_api_rt_shadow(self, request):
        """GET /api/rt-shadow — running tally of RT-trigger shadow divergences
        (stale snapshot pc_h1 vs FRESH-price pc_h1). The enforce-readiness signal
        is catastrophic_miss_rate: the fraction where the stale snapshot does NOT
        see a deep dip but the fresh price does (the HERALD class the live bot was
        blind to). Read-only, off-loop, fail-open."""
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            import asyncio as _asyncio
            from core.rt_shadow_stats import snapshot as _snap
            data = await _asyncio.to_thread(_snap)
        except Exception as e:
            return web.Response(text=json.dumps({"ok": False, "error": str(e)}),
                                content_type="application/json", headers=cors)
        payload = {"ok": True, "RT_TRIGGER_MODE": os.environ.get("RT_TRIGGER_MODE", "off"),
                   **data}
        return web.Response(text=json.dumps(payload, default=str),
                            content_type="application/json", headers=cors)

    async def _handle_api_live_real_pnl(self, request):
        """GET /api/live-real-pnl — HONEST live P&L from REAL on-chain fills.

        The dashboard's per-bot realized_pnl_total_usd (bot_state ledger) is
        SIMULATED (snapshot-priced, paper-dominated) and reported +$185 while the
        live wallet drained ~$48 (2026-06-28). This endpoint reads the persisted
        live_swaps.jsonl real-fill log OFF the event loop, pairs buys/sells at the
        actual fill amounts, anchors on the real on-chain wallet balance, and
        contrasts against the simulated ledger so the gap (drift + slippage +
        unsold corpses) is explicit. Read-only, fail-open, no money path."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import asyncio as _asyncio
        from core.live_swap_log import (read_live_swaps as _read,
                                        LOG_BASENAME as _BN)
        from core.live_pnl import (summarize_real_pnl as _summ,
                                   realized_by_token as _byt,
                                   realized_by_bot as _bybot)
        path = os.path.join(os.environ.get("DATA_DIR", "/data"), _BN)
        try:
            recs = await _asyncio.to_thread(_read, path)
        except Exception as e:
            return web.Response(text=json.dumps({"ok": False, "error": str(e)}),
                                content_type="application/json", headers=cors)
        # SOL price + simulated-ledger total from the live-pool snapshot (off-loop).
        sol_price = None
        sim_usd = None
        try:
            pool = await _asyncio.to_thread(self._build_live_pool)
            sol_price = (pool.get("wallet") or {}).get("sol_price_usd")
            sim_usd = (pool.get("totals") or {}).get("realized_pnl_usd")
        except Exception:
            pass
        # Fallback SOL price (trader cache is empty in paper mode) so the USD
        # fields populate even while paused.
        if not sol_price:
            sol_price = await self._sol_price_usd_cached()
        # Ground-truth wallet balance: on-chain (works while paused too).
        wallet_sol = await self._read_hot_wallet_sol()
        wallet_usd = (round(wallet_sol * sol_price, 2)
                      if (wallet_sol is not None and sol_price) else None)
        try:
            summary = _summ(recs, sol_price_usd=sol_price,
                            simulated_ledger_usd=sim_usd)
        except Exception as e:
            summary = {"error": str(e)}
        # Worst unsold corpses (buys that never sold = real money, never booked).
        corpses = []
        try:
            bt = _byt(recs)
            corpses = sorted(
                ({"token": t, **v} for t, v in bt.items()
                 if v["n_buys"] > 0 and v["n_sells"] == 0),
                key=lambda d: d["net_sol"])[:15]
        except Exception:
            pass
        payload = {
            "ok": True,
            "explainer": ("REAL P&L from on-chain fills + wallet balance. The "
                          "per-bot realized_pnl_total_usd tile is SIMULATED "
                          "(snapshot-priced, paper-dominated) and must NOT be "
                          "trusted for live money. gap_vs_simulated_usd = how much "
                          "the simulated ledger overstates reality."),
            "n_records": len(recs),
            "wallet": {"sol_balance": (round(wallet_sol, 6)
                                       if wallet_sol is not None else None),
                       "usd_value": wallet_usd,
                       "sol_price_usd": sol_price,
                       "source": "on-chain getBalance(HOT_WALLET_ADDRESS)"},
            "real_pnl": summary,
            "by_bot": _bybot(recs, sol_price_usd=sol_price),
            "worst_unsold_corpses": corpses,
        }
        return web.Response(text=json.dumps(payload, default=str),
                            content_type="application/json", headers=cors)

    async def _handle_api_paper_live_skips(self, request):
        """GET /api/paper-live-skips — the paper-vs-live 1:1 SKIP scoreboard.
        Reads DATA_DIR/paper_live_reconcile.jsonl OFF the event loop via
        asyncio.to_thread and returns recent records + a summary (n,
        paper_only_n, by_skip_reason histogram) — i.e. per paper buy, whether
        live would take it and WHY NOT. Fail-open: missing file -> empty summary.
        No money path, no behavior change."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import asyncio as _asyncio
        from core.paper_live_reconcile import (read_paper_live_reconcile as _read,
                                               summarize_reconcile as _summarize,
                                               LOG_BASENAME as _BN)
        path = os.path.join(os.environ.get("DATA_DIR", "/data"), _BN)
        mode = os.environ.get("PAPER_LIVE_RECONCILE_MODE", "on")
        try:
            limit = int(request.query.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        try:
            recs = await _asyncio.to_thread(_read, path)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "PAPER_LIVE_RECONCILE_MODE": mode}),
                content_type="application/json", headers=cors,
            )
        try:
            summary = await _asyncio.to_thread(_summarize, recs)
        except Exception as e:
            summary = {"error": str(e)}
        payload = {
            "ok": True,
            "PAPER_LIVE_RECONCILE_MODE": mode,
            "n_records": len(recs),
            "summary": summary,
            "recent": recs[-max(0, limit):] if recs else [],
            "note": ("paper_only_n = paper_took AND NOT live_would_take; "
                     "by_skip_reason histograms WHY live skipped those trades."),
        }
        return web.Response(
            text=json.dumps(payload, default=str), content_type="application/json",
            headers=cors,
        )

    async def _handle_api_fill_probe(self, request):
        """GET /api/fill-probe — the QUOTE-BASED fill-accuracy scoreboard.

        Reads DATA_DIR/fill_probe.jsonl OFF the event loop (asyncio.to_thread)
        and summarizes whether PAPER's modeled fill matches the REAL on-chain
        cost implied by the live Jupiter quote: median/p90 real_impact_pct,
        real_total_cost_pct, real_drift_pct, and the KEY metric model_error_pct
        (median/p90 + the fraction of trades where |model_error| > 2 = paper
        materially wrong), bucketed by liquidity (thin/mid/deep). Fail-open:
        missing file -> empty summary. No money path, read-only."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import asyncio as _asyncio
        from core.fill_probe import (read_fill_probes as _read,
                                     summarize_fill_probes as _summarize,
                                     LOG_BASENAME as _BN)
        path = os.path.join(os.environ.get("DATA_DIR", "/data"), _BN)
        mode = os.environ.get("FILL_PROBE_MODE", "off")
        try:
            limit = int(request.query.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50
        try:
            recs = await _asyncio.to_thread(_read, path)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "FILL_PROBE_MODE": mode}),
                content_type="application/json", headers=cors,
            )
        try:
            summary = await _asyncio.to_thread(_summarize, recs)
        except Exception as e:
            summary = {"error": str(e)}
        payload = {
            "ok": True,
            "FILL_PROBE_MODE": mode,
            "n_records": len(recs),
            "summary": summary,
            "recent": recs[-max(0, limit):] if recs else [],
            "note": ("model_error_pct = paper_total_cost - real_total_cost; "
                     ">0 = paper too optimistic/cheap vs the real quote, <0 = too "
                     "pessimistic. frac_abs_error_gt_2 = fraction where paper is "
                     "materially wrong. Bucketed by liquidity (thin<30k/mid/deep>=100k)."),
        }
        return web.Response(
            text=json.dumps(payload, default=str), content_type="application/json",
            headers=cors,
        )

    async def _handle_api_top_bots(self, request):
        """GET /api/top-bots — the curated TOP-BOTS scoreboard.

        Replaces noisy fleet-daily P&L with a clean per-bot view of the PROVEN
        top bots, measured the durable way (realized $/trade + WR + downside
        tail at n>=30). Reads the SAME trade set as /api/trades — tracker
        get_all_trades() + the live append-mode ledger (trade_store) — OFF the
        event loop, then aggregates via the pure core.top_bots helper.
        Fail-open: any error -> ok:False/empty, never 500-crashes the dashboard.
        No money path, read-only."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import asyncio as _asyncio
        try:
            from core.top_bots import compute_top_bots, top_bots_set
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "bots": [], "scoreboard": {}}),
                content_type="application/json", headers=cors,
            )

        def _load_trades():
            # Mirror _handle_trades: tracker records + append-mode ledger.
            trades = []
            if self._tracker is not None:
                try:
                    trades = list(self._tracker.get_all_trades())
                except Exception:
                    trades = []
            if self.trade_store is not None:
                try:
                    trades = trades + self.trade_store.load_trades()
                except Exception:
                    pass
            return trades

        try:
            bots = top_bots_set()
            trades = await _asyncio.to_thread(_load_trades)
            scoreboard = await _asyncio.to_thread(compute_top_bots, trades, bots)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "bots": [], "scoreboard": {}}),
                content_type="application/json", headers=cors,
            )
        payload = {"ok": True, "bots": bots, "scoreboard": scoreboard}
        return web.Response(
            text=json.dumps(payload, default=str),
            content_type="application/json", headers=cors,
        )

    async def _handle_api_fill_speed(self, request):
        """GET /api/fill-speed — read-only observability for the FORWARD fill-speed
        capture (fast would-fill price vs main-sweep fill, shadow). Tails the
        DATA_DIR/fill_speed_forward.jsonl log and reports counts + the median
        delta_pct (sweep vs fast at capture; the OFFLINE joiner adds realized P&L by
        joining to closed trades). No money path, no behavior change."""
        cors = {"Access-Control-Allow-Origin": "*"}
        import statistics as _stats
        import asyncio as _asyncio
        path = os.path.join(os.environ.get("DATA_DIR", "/data"),
                            "fill_speed_forward.jsonl")
        mode = os.environ.get("FILL_SPEED_LOG_MODE", "shadow")

        def _read_recs(_p):
            # Loop-safe: the jsonl grows, so read it OFF the event loop.
            out = []
            if os.path.exists(_p):
                with open(_p) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            out.append(json.loads(line))
                        except Exception:
                            continue
            return out

        try:
            recs = await _asyncio.to_thread(_read_recs, path)
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e),
                                 "FILL_SPEED_LOG_MODE": mode}),
                content_type="application/json", headers=cors,
            )
        # PHANTOM GUARD: a near-zero/bad fast_price yields an absurd delta_pct ratio
        # (e.g. +130,396%) that poisons mean/stdev/sum. Drop |delta|>PHANTOM_BOUND as
        # corrupt (same "drop |>300|" convention used for phantom P&L elsewhere).
        try:
            _phantom_bound = float(os.environ.get("FILL_SPEED_PHANTOM_BOUND", "300"))
        except (TypeError, ValueError):
            _phantom_bound = 300.0
        _all_deltas = [r.get("delta_pct") for r in recs
                       if isinstance(r.get("delta_pct"), (int, float))]
        deltas = [d for d in _all_deltas if abs(d) <= _phantom_bound]
        phantom_dropped = len(_all_deltas) - len(deltas)
        leads = [r.get("lead_secs") for r in recs
                 if isinstance(r.get("lead_secs"), (int, float))]
        by_bot: dict = {}
        _bot_deltas: dict = {}
        for r in recs:
            by_bot[r.get("bot")] = by_bot.get(r.get("bot"), 0) + 1
            _d = r.get("delta_pct")
            if isinstance(_d, (int, float)) and abs(_d) <= _phantom_bound:
                _bot_deltas.setdefault(r.get("bot"), []).append(_d)

        def _pctile(vals, q):
            if not vals:
                return None
            s = sorted(vals)
            i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
            return s[i]

        # Full distribution of delta_pct (= fast-vs-sweep entry edge; since the exit
        # is identical, this IS the realized per-trade P&L edge of the faster fill).
        # Positive delta = fast fill got in CHEAPER (better). fast_cheaper_pct is the
        # win-rate of filling faster.
        distribution = None
        if deltas:
            distribution = {
                "n": len(deltas),
                "phantom_dropped": phantom_dropped,
                "fast_cheaper_pct": round(
                    100.0 * sum(1 for d in deltas if d > 0) / len(deltas), 1),
                "mean_delta_pct": round(_stats.mean(deltas), 4),
                "median_delta_pct": round(_stats.median(deltas), 4),
                "stdev_delta_pct": (round(_stats.pstdev(deltas), 4)
                                    if len(deltas) >= 2 else None),
                "p10": round(_pctile(deltas, 0.10), 4),
                "p25": round(_pctile(deltas, 0.25), 4),
                "p75": round(_pctile(deltas, 0.75), 4),
                "p90": round(_pctile(deltas, 0.90), 4),
                "min": round(min(deltas), 4),
                "max": round(max(deltas), 4),
                "sum_delta_pct": round(sum(deltas), 4),
                "per_bot_median": {
                    b: round(_stats.median(v), 4)
                    for b, v in _bot_deltas.items() if len(v) >= 3
                },
                # Per-bot full record for the enforce decision. Judge on MEAN +
                # win-rate at n>=30 (the median misleads under the left skew).
                "per_bot": {
                    b: {
                        "n": len(v),
                        "mean_delta_pct": round(_stats.mean(v), 4),
                        "median_delta_pct": round(_stats.median(v), 4),
                        "fast_cheaper_pct": round(
                            100.0 * sum(1 for d in v if d > 0) / len(v), 1),
                        "sum_delta_pct": round(sum(v), 4),
                    }
                    for b, v in _bot_deltas.items() if len(v) >= 3
                },
            }
        payload = {
            "ok": True,
            "FILL_SPEED_LOG_MODE": mode,
            "n_records": len(recs),
            "median_delta_pct": (_stats.median(deltas) if deltas else None),
            "median_lead_secs": (_stats.median(leads) if leads else None),
            "distribution": distribution,
            "by_bot": by_bot,
            "recent": recs[-10:],
            "note": ("delta_pct>0 = faster fill got in cheaper (= realized P&L edge, "
                     "same exit). distribution = full fast-vs-sweep spread."),
        }
        return web.Response(
            text=json.dumps(payload), content_type="application/json", headers=cors,
        )

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
            try:
                from core.panel_refresher import status as _pr_status
                board["panel_refresher"] = _pr_status()
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

    def _enabled_live_probe_ids(self):
        """bot_ids of ENABLED live_probe bots (cached 60s). Used to correct the
        leaderboard's SIMULATED P&L for real-money bots."""
        import time as _t, pathlib as _pl
        if (hasattr(self, "_lp_ids_cache")
                and _t.monotonic() - getattr(self, "_lp_ids_cache_ts", 0.0) <= 60):
            return self._lp_ids_cache
        ids = set()
        try:
            _cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
            for p in _cfg_dir.glob("*.json"):
                try:
                    d = json.loads(p.read_text())
                    if d.get("live_probe") and d.get("enabled"):
                        ids.add(d.get("bot_id") or p.stem)
                except Exception:
                    pass
        except Exception:
            pass
        self._lp_ids_cache = ids
        self._lp_ids_cache_ts = _t.monotonic()
        return ids

    async def _probe_onchain_pnl_usd(self):
        """Live probe's REAL realized P&L (USD) = on-chain wallet delta_sol ×
        sol_price. The authoritative number — it captures manual sells the swap
        log misses (the TESTPACK 2026-07-07 orphan the operator sold in Phantom).
        Returns None on any failure -> leave the simulated value untouched."""
        try:
            sol_now = await self._read_hot_wallet_sol()
            if sol_now is None:
                return None
            base_path = os.path.join(os.environ.get("DATA_DIR", "/data"),
                                     "live_wallet_baseline.json")
            with open(base_path) as f:
                baseline = json.load(f)
            delta_sol = float(sol_now) - float(baseline["sol"])
            sol_price = await self._sol_price_usd_cached()
            if not sol_price:
                return None
            return round(delta_sol * float(sol_price), 2)
        except Exception:
            return None

    async def _handle_api_leaderboard(self, request):
        """GET /api/leaderboard?sort=X — sortable fleet leaderboard."""
        sort = request.query.get("sort", "total_pnl_realized")
        bots = self._build_bot_rows()
        # LIVE-PROBE P&L CORRECTION (2026-07-07): the per-bot ledger is SIMULATED
        # and booked a +$79 fantasy TESTPACK fill that never hit the chain, so it
        # read +$19 while the wallet was -$13.7. Replace the live probe's row P&L
        # with the ON-CHAIN wallet delta (authoritative — captures the manual
        # orphan sell). Only when EXACTLY ONE enabled live_probe exists, so the
        # single wallet delta attributes cleanly to one bot.
        try:
            _probe_ids = self._enabled_live_probe_ids()
            if len(_probe_ids) == 1:
                _real = await self._probe_onchain_pnl_usd()
                if _real is not None:
                    for _b in bots:
                        if _b.get("bot_id") in _probe_ids:
                            _b["realized_pnl_total_usd"] = _real
                            _b["total_pnl_realized"] = _real
                            # daily has no clean on-chain baseline; show the same
                            # wallet-delta truth rather than leave a fantasy green
                            # daily next to the corrected total (2026-07-07).
                            _b["daily_pnl_usd"] = _real
                            _b["daily_is_since_baseline"] = True
                            _b["live_onchain_truth"] = True
        except Exception:
            pass
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

    # ── LIVE TRADING pool (read-only view of the 4 real-money bots) ───────────
    # The ONLY 4 bots that trade real money. Everything else is paper. This view
    # is strictly read-only — it computes nothing that moves money or touches a
    # config. All per-bot P&L comes from the SAME authoritative source as the
    # leaderboard (_build_bot_rows -> bot_state realized/daily), NOT the
    # known-corrupted per-trade pnl feed.
    LIVE_BOT_IDS = (
        "badday_flush_conviction_live",
        "badday_flush_live",
        "deepflush_timebox_live",
        "timebox_probe_5mgreen_live",
    )

    def _live_base_sizes(self) -> dict:
        """Map live bot_id -> base_position_usd from its config (default 20).
        Cached 60s; fail-open to {} so a missing config never 500s the view."""
        import time as _t, pathlib as _pl
        if (hasattr(self, "_live_base_cache")
                and _t.monotonic() - getattr(self, "_live_base_cache_ts", 0.0) <= 60):
            return self._live_base_cache
        out = {}
        try:
            _cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
            for bid in self.LIVE_BOT_IDS:
                try:
                    p = _cfg_dir / f"{bid}.json"
                    if p.exists():
                        d = json.loads(p.read_text())
                        out[bid] = float(d.get("base_position_usd", 20.0))
                except Exception:
                    pass
        except Exception:
            pass
        self._live_base_cache = out
        self._live_base_cache_ts = _t.monotonic()
        return out

    def _build_live_pool(self) -> dict:
        """Read-only snapshot of the 4 real-money bots. Defensive throughout:
        any missing value falls back to null/0 so this never raises a 500."""
        import os as _os

        def _envf(name, default):
            try:
                return float(_os.environ.get(name, default))
            except Exception:
                try:
                    return float(default)
                except Exception:
                    return None

        def _envs(name, default=None):
            v = _os.environ.get(name, default)
            return v if v not in ("", None) else default

        def _envbool(name, default=False):
            v = _os.environ.get(name)
            if v is None:
                return default
            return v.strip().lower() in ("1", "true", "yes", "on")

        live_ids = set(self.LIVE_BOT_IDS)
        base_sizes = self._live_base_sizes()

        # Per-bot rows from the authoritative source (bot_state), filtered to live.
        rows = []
        try:
            all_rows = self._build_bot_rows()
        except Exception as e:
            logger.warning("api/live _build_bot_rows failed: %s", e)
            all_rows = []
        by_id = {r.get("bot_id"): r for r in all_rows}

        tot_today = 0.0
        tot_realized = 0.0
        tot_open = 0
        tot_open_usd = 0.0
        for bid in self.LIVE_BOT_IDS:
            r = by_id.get(bid)
            base = base_sizes.get(bid)
            if r is None:
                rows.append({
                    "bot_id": bid,
                    "registered": False,
                    "daily_pnl_usd": None,
                    "realized_pnl_total_usd": None,
                    "open_position_count": 0,
                    "total_trades": 0,
                    "wins": 0,
                    "win_rate_pct": None,
                    "base_position_usd": base,
                })
                continue
            tt = int(r.get("total_trades") or 0)
            wins = int(r.get("wins") or 0)
            wr = round(100.0 * wins / tt, 1) if tt > 0 else None
            daily = r.get("daily_pnl_usd")
            realized = r.get("realized_pnl_total_usd")
            oc = int(r.get("open_position_count") or 0)
            rows.append({
                "bot_id": bid,
                "registered": True,
                "daily_pnl_usd": daily,
                "realized_pnl_total_usd": realized,
                "open_position_count": oc,
                "total_trades": tt,
                "wins": wins,
                "win_rate_pct": wr,
                "base_position_usd": base,
            })
            try:
                tot_today += float(daily or 0.0)
            except Exception:
                pass
            try:
                tot_realized += float(realized or 0.0)
            except Exception:
                pass
            tot_open += oc
            # Open notional: prefer the real persisted in_flight_usd; fall back to
            # open_count * base size when in_flight isn't available.
            inflight = r.get("in_flight_usd")
            if isinstance(inflight, (int, float)) and inflight > 0:
                tot_open_usd += float(inflight)
            elif base is not None:
                tot_open_usd += oc * float(base)

        # Wallet balance (live only; None in paper). Trader caches SOL balance.
        wallet_sol = None
        wallet_usd = None
        sol_price = None
        try:
            tr = getattr(self, "_trader", None)
            if tr is not None:
                _sb = getattr(tr, "_sol_balance", -1.0)
                if isinstance(_sb, (int, float)) and _sb >= 0:
                    wallet_sol = round(float(_sb), 4)
                _sp = getattr(tr, "_sol_price_usd", None) or getattr(tr, "sol_price_usd", None)
                if isinstance(_sp, (int, float)) and _sp > 0:
                    sol_price = round(float(_sp), 2)
                    if wallet_sol is not None:
                        wallet_usd = round(wallet_sol * float(_sp), 2)
        except Exception as e:
            logger.debug("api/live wallet read failed: %s", e)

        floor_usd = _envf("WORKING_CAPITAL_FLOOR_USD", "1104")
        above_floor_usd = None
        if wallet_usd is not None and floor_usd is not None:
            above_floor_usd = round(wallet_usd - floor_usd, 2)

        # Caps / risk envelope.
        inflight_cap = _envf("PROBE_AGG_INFLIGHT_MAX_USD", "1000")
        daily_kill = _envf("PROBE_AGG_DAILY_KILL_USD", "150")
        per_token_max_positions = _envf("LIVE_PER_TOKEN_MAX_POSITIONS", "4")
        per_token_max_usd = _envf("LIVE_PER_TOKEN_MAX_USD", "400")

        # Profit-sweep config + any persisted state (floor HWM + last-sweep ts).
        # Cumulative total-swept is NOT tracked anywhere (sweep state persists only
        # floor_hwm_usd + last_sweep_ts), so total_swept is reported null.
        sweep_last_ts = None
        sweep_floor_hwm_usd = None
        try:
            from pathlib import Path as _Path
            _sf = _Path(_os.environ.get("DATA_DIR") or "/data") / ".profit_sweep_state.json"
            if _sf.exists():
                _d = json.loads(_sf.read_text())
                _lt = _d.get("last_sweep_ts")
                if _lt:
                    sweep_last_ts = float(_lt)
                _fh = _d.get("floor_hwm_usd")
                if _fh:
                    sweep_floor_hwm_usd = float(_fh)
        except Exception as e:
            logger.debug("api/live sweep state read failed: %s", e)

        profit_sweep = {
            "enabled": _envbool("PROFIT_SWEEP_ENABLED", False),
            "dry_run": _envbool("PROFIT_SWEEP_DRY_RUN", True),
            "floor_usd": floor_usd,
            "cold_wallet": _envs("PROFIT_WALLET_ADDRESS"),
            "last_sweep_ts": sweep_last_ts,
            "floor_hwm_usd": sweep_floor_hwm_usd,
            "total_swept_usd": None,  # not tracked anywhere in the codebase
            "note": ("total_swept is not persisted; only floor_hwm_usd + "
                     "last_sweep_ts are tracked in .profit_sweep_state.json"),
        }

        return {
            "live_mode": bool(self._live_mode),
            "bot_ids": list(self.LIVE_BOT_IDS),
            "bots": rows,
            "totals": {
                "today_pnl_usd": round(tot_today, 2),
                "realized_pnl_usd": round(tot_realized, 2),
                "open_positions": tot_open,
                "open_notional_usd": round(tot_open_usd, 2),
            },
            "wallet": {
                "sol_balance": wallet_sol,
                "usd_value": wallet_usd,
                "sol_price_usd": sol_price,
                "floor_usd": floor_usd,
                "above_floor_usd": above_floor_usd,
            },
            "caps": {
                "inflight_max_usd": inflight_cap,
                "inflight_used_usd": round(tot_open_usd, 2),
                "daily_kill_usd": daily_kill,
                "daily_pnl_usd": round(tot_today, 2),
                "per_token_max_positions": per_token_max_positions,
                "per_token_max_usd": per_token_max_usd,
            },
            "profit_sweep": profit_sweep,
        }

    async def _handle_api_live(self, request):
        """GET /api/live — read-only snapshot of the 4 real-money bots.

        Reuses _build_bot_rows (the leaderboard's authoritative bot_state source)
        for per-bot realized/daily P&L; never sums the corrupted per-trade pnl.
        Fully defensive — returns a best-effort payload rather than a 500."""
        try:
            return web.json_response(self._build_live_pool())
        except Exception as e:
            logger.warning("api/live failed: %s", e)
            return web.json_response({
                "live_mode": bool(getattr(self, "_live_mode", False)),
                "bot_ids": list(self.LIVE_BOT_IDS),
                "bots": [], "totals": {}, "wallet": {}, "caps": {},
                "profit_sweep": {}, "error": str(e),
            })

    async def _handle_api_bot_trades(self, request):
        """GET /api/bots/{bot_id}/trades — per-bot trade history.

        EGRESS (2026-07-04): full entry_meta makes 1000 records ~3.3MB even
        gzipped, and daily research agents pull this repeatedly. Optional
        ?meta_keys=a,b,c projects entry_meta to just those keys server-side
        (a typical decode needs 5-10 of ~200 fields -> ~10-20x lighter).
        Omit the param for full meta (unchanged default)."""
        bot_id = request.match_info["bot_id"]
        limit = int(request.query.get("limit", 50))
        if self.trade_store is None:
            return web.json_response([])
        trades = self.trade_store.load_trades(bot_id=bot_id)
        out = trades[-limit:]
        mk = (request.query.get("meta_keys") or "").strip()
        if mk:
            keys = {k.strip() for k in mk.split(",") if k.strip()}
            slim = []
            for t in out:
                t2 = dict(t)
                em = t2.get("entry_meta")
                if isinstance(em, dict):
                    t2["entry_meta"] = {k: em.get(k) for k in keys if k in em}
                slim.append(t2)
            out = slim
        return web.json_response(out)

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

    async def _handle_api_honest_book(self, request):
        """GET /api/honest-book — THE scoreboard, scrubbed (2026-07-01 redesign).

        Mirrors scripts/honest_book.py over the same merged ledger /api/trades
        serves (tracker + multi-bot trade_store): position-level frac-weighted
        returns with unrealizable latency spikes excluded and per-token dedup.
        Cached 120s — load_trades is the heavy part and the dashboard polls
        this at 120s anyway. Compute runs off the event loop."""
        import time as _t
        cache = getattr(self, "_honest_book_cache", None)
        if cache and (_t.monotonic() - cache[0]) < 120:
            return web.json_response(cache[1])
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
        try:
            payload = await asyncio.to_thread(compute_honest_book, trades)
        except Exception as e:
            logger.warning("api/honest-book failed: %s", e)
            return web.json_response({"ok": False, "error": str(e)[:200],
                                      "days": [], "pooled": {}})
        self._honest_book_cache = (_t.monotonic(), payload)
        return web.json_response(payload)

    async def _handle_api_wallet_truth(self, request):
        """GET /api/wallet-truth — the ON-CHAIN wallet delta, nothing else.

        AxiS 2026-07-05: 'live probe lost $40 while the dashboard showed -$7.
        That cannot ever happen. The dashboard should show my exact wallet
        amount gained or lost.' The June incident (+$185 shown vs -$48 real)
        came from the simulated realized-P&L ledger. This endpoint reads the
        HOT WALLET's SOL balance from the chain (public RPC, no key, no
        Helius) and reports the delta vs a persisted baseline:
          - baseline auto-created on first call while LIVE (PAPER_MODE=false),
            stored at {DATA_DIR}/live_wallet_baseline.json
          - POST /api/wallet-truth/rebase (Basic auth) resets the baseline
            (use after deposits/withdrawals so the delta stays honest)
        SOL balance excludes SPL tokens by construction, so AxiS's personal
        holdings (GFOF, …Cmoon) never enter the number; open live positions
        temporarily depress the delta until they close (shown as context).
        Cached 60s; fail-open (rpc error -> last known + stale flag)."""
        import time as _t
        cache = getattr(self, "_wt_cache", None)
        if cache and (_t.monotonic() - cache[0]) < 60:
            return web.json_response(cache[1])
        wallet = os.environ.get(
            "HOT_WALLET_PUBKEY", "Ao8uMKCyprmHVjwzw93bRKxPJHBnRtC6XkMc85zzyjPL")
        base_path = os.path.join(os.environ.get("DATA_DIR", "/data"),
                                 "live_wallet_baseline.json")

        def _fetch():
            import urllib.request as _ur
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                               "params": [wallet]}).encode()
            req = _ur.Request("https://api.mainnet-beta.solana.com", data=body,
                              headers={"Content-Type": "application/json"})
            r = json.loads(_ur.urlopen(req, timeout=15).read())
            return float(r["result"]["value"]) / 1e9

        out = {"ok": True, "wallet": wallet[:6] + "…" + wallet[-4:]}
        try:
            sol_now = await asyncio.to_thread(_fetch)
            out["sol_now"] = round(sol_now, 6)
            # baseline: load, or create on first live call
            baseline = None
            try:
                with open(base_path) as f:
                    baseline = json.load(f)
            except Exception:
                pass
            paper = str(os.environ.get("PAPER_MODE", "true")).lower() != "false"
            if baseline is None and not paper:
                baseline = {"sol": sol_now, "ts": time.time()}
                try:
                    with open(base_path, "w") as f:
                        json.dump(baseline, f)
                except Exception:
                    pass
            if baseline:
                out["baseline_sol"] = round(float(baseline["sol"]), 6)
                out["baseline_ts"] = baseline.get("ts")
                out["delta_sol"] = round(sol_now - float(baseline["sol"]), 6)
            else:
                out["note"] = "baseline arms on first call in LIVE mode"
            out["paper_mode"] = paper
            # context: open live positions (they hold value outside SOL).
            # deployed_sol = COST BASIS (sum amount_sol_spent) — the exact
            # lamports that left the wallet for currently-open BOT positions.
            # Deliberately NOT marked-to-market (AxiS 2026-07-05: no
            # fluctuating valuations in the truth panel) and structurally
            # blind to personal holdings (the trader's book only contains
            # bot-bought positions). delta_sol + deployed_sol ~= break-even.
            try:
                # 公牛 round-trip 2026-07-06: trader.positions was the WRONG
                # book (read 0 during a real hold). Live positions live in the
                # per-bot state stores — read bot_state/*.json for live_probe
                # bots directly (the restore source of truth).
                import glob as _gl
                import pathlib as _pl
                n_open = 0
                dep_usd = 0.0
                cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
                data_dir = os.environ.get("DATA_DIR", "/data")
                for cfg_p in _gl.glob(str(cfg_dir / "*.json")):
                    try:
                        cfg = json.load(open(cfg_p))
                        if not (cfg.get("live_probe") and cfg.get("enabled")):
                            continue
                        st_p = os.path.join(data_dir, "bot_state",
                                            cfg["bot_id"] + ".json")
                        st = json.load(open(st_p))
                        opens = st.get("open_positions") or []
                        if isinstance(opens, dict):
                            opens = list(opens.values())
                        n_open += len(opens)
                        for p_ in opens:
                            v = (p_ or {}).get("size_usd")
                            if isinstance(v, (int, float)):
                                dep_usd += float(v)
                    except Exception:
                        continue
                out["open_live_positions"] = n_open
                out["deployed_usd"] = round(dep_usd, 2)
            except Exception:
                pass
            # SELL-PATH CANARY status (2026-07-10 incident): positive,
            # queryable evidence the exit path can size sells — mirrored onto
            # the trader by the canary loop. None = canary not armed (paper /
            # spawn failed) — visibly distinct from "healthy".
            try:
                out["sell_canary"] = getattr(self._trader,
                                             "_sell_canary_status", None)
            except Exception:
                pass
            self._wt_cache = (_t.monotonic(), out)
        except Exception as e:
            out = dict((cache[1] if cache else {"ok": False}),
                       stale=True, error=str(e)[:80])
        return web.json_response(out)

    async def _handle_api_wallet_truth_rebase(self, request):
        """POST /api/wallet-truth/rebase — reset the baseline to the current
        on-chain balance (after deposits/withdrawals). Basic-auth protected
        like every write endpoint."""
        wallet = os.environ.get(
            "HOT_WALLET_PUBKEY", "Ao8uMKCyprmHVjwzw93bRKxPJHBnRtC6XkMc85zzyjPL")
        base_path = os.path.join(os.environ.get("DATA_DIR", "/data"),
                                 "live_wallet_baseline.json")
        try:
            import urllib.request as _ur
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                               "params": [wallet]}).encode()
            req = _ur.Request("https://api.mainnet-beta.solana.com", data=body,
                              headers={"Content-Type": "application/json"})
            r = json.loads(await asyncio.to_thread(
                lambda: _ur.urlopen(req, timeout=15).read()))
            sol_now = float(r["result"]["value"]) / 1e9
            with open(base_path, "w") as f:
                json.dump({"sol": sol_now, "ts": time.time()}, f)
            self._wt_cache = None
            return web.json_response({"ok": True, "baseline_sol": round(sol_now, 6)})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)[:80]}, status=500)

    async def _handle_api_race(self, request):
        """GET /api/race — LIVE-SLOT RACE: per-bot scrubbed per-token per-day
        scoreboard over the last 7 UTC days (mirrors scripts/bot_leaderboard.py).
        Computed server-side over the same merged ledger /api/trades serves
        (tracker + multi-bot trade_store) — NO external calls. Cached 60s
        (the compute is O(trades)); payload is aggregates only, a few KB
        (egress discipline). Compute runs off the event loop."""
        import time as _t
        cache = getattr(self, "_race_cache", None)
        if cache and (_t.monotonic() - cache[0]) < 60:
            return web.json_response(cache[1])
        # Enabled bot ids from config/bots (60s cache; fail-OPEN -> None so a
        # config-read hiccup never blanks the race panel).
        if (not hasattr(self, "_race_enabled_ids")
                or _t.monotonic() - getattr(self, "_race_enabled_ids_ts", 0.0) > 60):
            en = None
            try:
                import pathlib as _pl
                cfg_dir = _pl.Path(__file__).resolve().parent.parent / "config" / "bots"
                found = set()
                for p in cfg_dir.glob("*.json"):
                    try:
                        d = json.loads(p.read_text())
                        if d.get("enabled", True):
                            found.add(d.get("bot_id") or p.stem)
                    except Exception:
                        found.add(p.stem)  # unreadable single config -> show it
                en = found or None
            except Exception:
                en = None
            self._race_enabled_ids = en
            self._race_enabled_ids_ts = _t.monotonic()
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
        try:
            payload = await asyncio.to_thread(compute_race, trades,
                                              self._race_enabled_ids)
        except Exception as e:
            logger.warning("api/race failed: %s", e)
            return web.json_response({"ok": False, "error": str(e)[:200],
                                      "bots": []})
        self._race_cache = (_t.monotonic(), payload)
        return web.json_response(payload)

    # ── Robinhood Chain paper lane ────────────────────────────────────────

    def _rh_paper_ledger_path(self) -> str:
        """bot_state/rh_paper_trades.jsonl under the same data dir the fleet
        bot_state stores use (trade_store.data_dir on Railway = DATA_DIR)."""
        if self.trade_store is not None:
            try:
                return str(self.trade_store.data_dir / "bot_state"
                           / "rh_paper_trades.jsonl")
            except Exception:
                pass
        return os.path.join(os.environ.get("DATA_DIR", "/data"),
                            "bot_state", "rh_paper_trades.jsonl")

    @staticmethod
    def _rh_paper_read_rows(path: str) -> list:
        """BLOCKING jsonl read (run off the loop). Skips malformed lines."""
        rows = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if isinstance(r, dict):
                    rows.append(r)
        return rows

    async def _handle_api_rh_paper(self, request):
        """GET /api/rh-paper — Robinhood Chain paper-lane summary.

        The RH lane (scripts/rh_paper_lane.py) runs per-session on the local
        machine and pushes its ledger here via /api/rh-paper/ingest; gaps
        between sessions are normal. Reads bot_state/rh_paper_trades.jsonl
        and returns aggregates + the last 20 events. available:false (not an
        error) when no ledger has been pushed yet."""
        path = self._rh_paper_ledger_path()
        if not os.path.exists(path):
            return web.json_response({
                "available": False,
                "note": "RH paper lane runs per-session; no data uploaded yet",
            })
        try:
            rows = await asyncio.to_thread(self._rh_paper_read_rows, path)
            payload = await asyncio.to_thread(compute_rh_paper_summary, rows)
        except Exception as e:
            logger.warning("api/rh-paper failed: %s", e)
            return web.json_response({"available": False,
                                      "note": "ledger read failed",
                                      "error": str(e)[:200]}, status=500)
        return web.json_response(payload)

    async def _handle_api_rh_paper_ingest(self, request):
        """POST /api/rh-paper/ingest — append a JSON array of RH paper-lane
        ledger rows to bot_state/rh_paper_trades.jsonl, de-duped on
        (ts, ev, pool) so re-pushing a whole session ledger is idempotent.
        File capped at RH_PAPER_MAX_LINES (oldest truncated). Basic-auth
        protected like every write endpoint (app-wide middleware)."""
        try:
            incoming = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid JSON"},
                                     status=400)
        if not isinstance(incoming, list):
            return web.json_response(
                {"ok": False, "error": "expected a JSON array of ledger rows"},
                status=400)
        path = self._rh_paper_ledger_path()
        # ?replace=1 — FULL-SYNC: the posted array becomes the ledger (still
        # de-duped/validated through the same merge, just from empty). Needed
        # for corrections: append-mode dedupe on (ts,ev,pool) means a fixed
        # row (same key, new values) can never overwrite its bad original
        # (BILLY slice-cost phantom, 2026-07-10). Auth'd like every write.
        replace = str(request.query.get("replace", "")).lower() in ("1", "true")

        def _merge_and_write():
            existing = ([] if replace else
                        (self._rh_paper_read_rows(path)
                         if os.path.exists(path) else []))
            merged, added = merge_rh_paper_rows(existing, incoming)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in merged:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            os.replace(tmp, path)
            return added, len(merged)

        try:
            added, total = await asyncio.to_thread(_merge_and_write)
        except Exception as e:
            logger.warning("api/rh-paper/ingest failed: %s", e)
            return web.json_response({"ok": False, "error": str(e)[:200]},
                                     status=500)
        return web.json_response({"ok": True, "added": added,
                                  "skipped": len(incoming) - added,
                                  "total_lines": total})

    async def _handle_api_gates(self, request):
        """GET /api/gates — compact status-strip payload: PAPER/LIVE mode, SOL
        macro snapshot (same scanner source as /api/sol-gate), regime-gate env
        modes (SOL_MACRO / GREEN_DAY / BREAKEVEN_LOCK) and the CT trading-hours
        window. Read-only; env + in-memory reads only, never raises."""
        import time as _time_mod
        feats: dict = {}
        ts = 0.0
        for _chain_id, scanner in (self._scanners or {}).items():
            sf = getattr(scanner, "last_sol_features", None)
            sf_ts = getattr(scanner, "last_sol_features_ts", 0.0)
            if isinstance(sf, dict) and sf_ts > ts:
                feats = sf
                ts = sf_ts
        h1 = feats.get("sol_pc_h1")
        h6 = feats.get("sol_pc_h6")
        h24 = feats.get("sol_pc_h24")
        price = feats.get("sol_price") or feats.get("sol")
        reasons = []
        if isinstance(h6, (int, float)) and h6 < -0.3:
            reasons.append(f"sol_pc_h6={h6:+.2f}%<-0.3")
        if isinstance(h1, (int, float)) and h1 < -0.7:
            reasons.append(f"sol_pc_h1={h1:+.2f}%<-0.7")
        hours = {"start_ct": None, "end_ct": None, "now_ct": None, "in_window": None}
        try:
            from zoneinfo import ZoneInfo as _ZI
            _now_ct = datetime.now(_ZI("America/Chicago"))
            _s = int(os.environ.get("TRADING_START_HOUR_CT", "3"))
            _e = int(os.environ.get("TRADING_END_HOUR_CT", "17"))
            hours = {"start_ct": _s, "end_ct": _e,
                     "now_ct": _now_ct.strftime("%H:%M"),
                     "in_window": _s <= _now_ct.hour < _e}
        except Exception as e:
            logger.debug("api/gates hours calc failed: %s", e)
        return web.json_response({
            "live_mode": bool(self._live_mode),
            "trading_paused": bool(self._trading_paused),
            "sol": {
                "price_usd": price,
                "pc_h1": h1, "pc_h6": h6, "pc_h24": h24,
                # strict-threshold verdict (h6<-0.3 OR h1<-0.7), independent of
                # the deployed SOL_MACRO_GATE_MODE — shown alongside the mode.
                "strict_status": "BLOCK" if reasons else "PASS",
                "reasons": reasons,
                "snapshot_age_secs": (max(0.0, _time_mod.time() - ts) if ts else None),
                "has_data": bool(feats),
            },
            "gates": {
                "sol_macro_mode": os.environ.get("SOL_MACRO_GATE_MODE", "strict").strip().lower(),
                "green_day_mode": os.environ.get("GREEN_DAY_MODE", "off").strip().lower(),
                "breakeven_lock_mode": os.environ.get("BREAKEVEN_LOCK_MODE", "shadow").strip().lower(),
                "hours": hours,
            },
        })

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

        # EGRESS FIX (2026-07-02, $13.65/273GB billing line): recent_trades
        # records carried full entry_meta (~32KB each -> 1.62MB of the 1.71MB
        # /api/stats payload) and the dashboard polls this endpoint all day.
        # Slim to the fields the UI renders; full records stay on /api/trades.
        try:
            _RT_KEEP = ("token", "address", "bot_id", "type", "time", "pnl_pct",
                        "pnl_usd", "amount_usd", "entry_price", "exit_price",
                        "price", "sell_fraction", "exit_kind", "exit_reason",
                        "strategy", "chain", "peak_pnl_pct")
            _rt = stats.get("recent_trades")
            if isinstance(_rt, list) and _rt:
                stats["recent_trades"] = [
                    {k: t.get(k) for k in _RT_KEEP if k in t}
                    if isinstance(t, dict) else t
                    for t in _rt[:50]
                ]
        except Exception:
            pass

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
