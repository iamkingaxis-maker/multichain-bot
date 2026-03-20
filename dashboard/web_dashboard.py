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
import re
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)

# Keywords that are worth showing in the feed
_FEED_KEYWORDS = re.compile(
    r"bought|sold|buy signal|stop loss|tp1|tp2|tp3|stall|avg down|"
    r"pyramid|moon bag|security|honeypot|blocked|rug|error|warning|"
    r"market restricted|market conditions|kill switch|manual sell",
    re.IGNORECASE
)


class DashboardLogHandler(logging.Handler):
    """Captures matching log lines and pushes them to the dashboard feed."""

    def __init__(self, dashboard):
        super().__init__()
        self._dashboard = dashboard

    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            if _FEED_KEYWORDS.search(msg):
                # Strip PAPER/TELEGRAM DISABLED noise, keep it short
                clean = re.sub(r"\[TELEGRAM DISABLED\]\s*", "", msg)
                clean = re.sub(r"\[PAPER\]\s*", "", clean)
                self._dashboard.add_alert(clean.strip())
        except Exception:
            pass

# ── HTML ─────────────────────────────────────────────────────────────────────

HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Multichain Memecoin Bot</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0d1117;
    --card:      #161b22;
    --border:    #30363d;
    --border2:   #21262d;
    --text:      #e6edf3;
    --muted:     #8b949e;
    --accent:    #58a6ff;
    --green:     #2ea043;
    --green-lt:  #3fb950;
    --red:       #f85149;
    --yellow:    #d29922;
    --sol:       #9945ff;
    --base:      #0052ff;
    --bnb:       #f3ba2f;
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
  .badge-base { background: #0052ff22; color: #4f8cff; }
  .badge-bnb  { background: #f3ba2f22; color: var(--bnb); }
  .badge-scanner { background: #58a6ff22; color: var(--accent); }
  .badge-copy    { background: #2ea04322; color: var(--green-lt); }
  .badge-scalper { background: #d2992222; color: var(--yellow); }

  /* ── Token links ── */
  a.token-link { color: inherit; text-decoration: none; }
  a.token-link:hover { text-decoration: underline; color: var(--accent); }

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
  .breakdown-card .stat-line .k { color: var(--muted); }

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
  .progress-fill.sl  { background: var(--red); }

  /* Manual sell button */
  .sell-btn {
    background: #f8514915; border: 1px solid #f8514940;
    color: var(--red); border-radius: 5px; padding: 3px 10px;
    font-size: 11px; font-family: inherit; cursor: pointer;
    transition: background 0.15s;
  }
  .sell-btn:hover { background: #f8514930; }
  .sell-btn:disabled { opacity: 0.4; cursor: default; }
</style>
</head>
<body>

<!-- ── Header ── -->
<div class="header">
  <div class="header-left">
    <h1>&#9889; Multichain Memecoin Bot</h1>
    <div class="status-pill">
      <span class="status-dot" id="status-dot"></span>
      <span id="status-text">Connecting...</span>
    </div>
  </div>
  <div class="header-right">
    <span>Uptime: <span id="uptime">—</span></span>
    <button id="pause-btn" onclick="togglePause()" style="
      padding:5px 14px; border-radius:6px; border:none; cursor:pointer;
      font-size:12px; font-weight:700; letter-spacing:0.5px;
      background:var(--green); color:#0d1117; transition:background 0.2s;">
      ▶ BUYING ON
    </button>
    <span id="clock">—</span>
  </div>
</div>

<div class="main">

  <!-- ── Top Stat Cards ── -->
  <div class="stat-row">
    <div class="stat-card">
      <div class="label">Total P&amp;L</div>
      <div class="value" id="sc-total-pnl">$0.00</div>
      <div class="sub" id="sc-total-pnl-sub">all time</div>
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
      <div class="card-title"><span class="dot" style="background:var(--green)"></span> Open Positions</div>
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

  <!-- ── Token Recommendations ── -->
  <div class="card">
    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
      <span><span class="dot" style="background:#a78bfa"></span> Token Recommendations</span>
      <span style="font-size:11px;color:var(--muted);font-weight:400;">Tokens the bot is watching — buy manually or wait for auto-entry</span>
    </div>
    <div class="tbl-wrap">
      <table id="recs-table">
        <thead>
          <tr>
            <th>Token</th><th>Chain</th><th>Score</th><th>MCap</th>
            <th>h1 Vol</th><th>Dip%</th><th>h1%</th><th>Watching</th><th>Risk</th><th></th>
          </tr>
        </thead>
        <tbody id="recs-body">
          <tr><td colspan="10" class="empty">No tokens being watched yet</td></tr>
        </tbody>
      </table>
    </div>
  </div>

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

  <!-- ── Chain Breakdown ── -->
  <div class="three-col">
    <div class="card breakdown-card">
      <div class="card-label">Chain</div>
      <div class="card-name" style="color:var(--sol)">Solana</div>
      <div class="stat-line"><span class="k">P&amp;L</span><span id="ch-sol-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Capital In</span><span id="ch-sol-cap">$0</span></div>
      <div class="stat-line"><span class="k">Open Positions</span><span id="ch-sol-pos">0</span></div>
    </div>
    <div class="card breakdown-card">
      <div class="card-label">Chain</div>
      <div class="card-name" style="color:#4f8cff">Base</div>
      <div class="stat-line"><span class="k">P&amp;L</span><span id="ch-base-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Capital In</span><span id="ch-base-cap">$0</span></div>
      <div class="stat-line"><span class="k">Open Positions</span><span id="ch-base-pos">0</span></div>
    </div>
    <div class="card breakdown-card">
      <div class="card-label">Chain</div>
      <div class="card-name" style="color:var(--bnb)">BNB Chain</div>
      <div class="stat-line"><span class="k">P&amp;L</span><span id="ch-bnb-pnl">$0.00</span></div>
      <div class="stat-line"><span class="k">Capital In</span><span id="ch-bnb-cap">$0</span></div>
      <div class="stat-line"><span class="k">Open Positions</span><span id="ch-bnb-pos">0</span></div>
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
        <option value="base">Base</option>
        <option value="bnb">BNB</option>
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
  const sign = n >= 0 ? '+' : '';
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
  if (c === 'base')                   return '<span class="badge badge-base">BASE</span>';
  if (c === 'bsc' || c === 'bnb')    return '<span class="badge badge-bnb">BNB</span>';
  return '<span class="badge">' + (chain||'?').toUpperCase() + '</span>';
}
function stratBadge(s) {
  const st = (s||'scanner').toLowerCase();
  if (st === 'copy')    return '<span class="badge badge-copy">COPY</span>';
  if (st === 'scalper') return '<span class="badge badge-scalper">SCALP</span>';
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
  updateStatCards(d);
  updatePnlChart(d.cumulative_pnl || []);
  updatePositions(d.positions || []);
  updateFeed(d.new_alerts || []);
  updateStrategies(d.strategies || {});
  updateChains(d.chains || {});
  updateSecurity(d.security || {}, d.price_feed || {});
  updatePauseBtn(d.buying_paused || false);

  // Reload all trades for history table
  if (d.all_trades !== undefined) {
    allTrades = d.all_trades;
    filterTrades();
  }
}

// ── Pause buying toggle ────────────────────────────────────────────────────
function updatePauseBtn(paused) {
  const btn = document.getElementById('pause-btn');
  if (!btn) return;
  if (paused) {
    btn.textContent = '⏸ BUYING PAUSED';
    btn.style.background = '#f85149';
    btn.style.color = '#fff';
  } else {
    btn.textContent = '▶ BUYING ON';
    btn.style.background = 'var(--green)';
    btn.style.color = '#0d1117';
  }
}

async function togglePause() {
  const btn = document.getElementById('pause-btn');
  btn.disabled = true;
  try {
    const resp = await fetch('/api/pause', {method: 'POST'});
    const data = await resp.json();
    if (data.ok) updatePauseBtn(data.paused);
  } catch(e) {
    console.warn('pause toggle failed', e);
  }
  btn.disabled = false;
}

function updateUptime(u) {
  if (u) document.getElementById('uptime').textContent = u;
}

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
  if (!positions.length) {
    tbody.innerHTML = '<tr><td colspan="9" class="empty">No open positions</td></tr>';
    return;
  }
  tbody.innerHTML = positions.map(p => {
    const pnlCls = pnlClass(p.pnl_usd);
    const mult = p.multiplier || 1;
    // Progress toward TP levels: 1.5x=TP1, 2x=TP2, 2.5x=TP3
    const pct = Math.max(0, Math.min(((mult - 1) / 1.5) * 100, 100));
    const tpCls = mult >= 2.5 ? 'tp3' : mult >= 2 ? 'tp2' : mult >= 1.5 ? 'tp1' : mult < 1 ? 'sl' : '';
    // Label: show next TP target or moon bag if past TP3
    const tpLabel = mult >= 2.5 ? '🌙' : mult >= 2.0 ? '→TP3' : mult >= 1.5 ? '→TP2' : mult >= 1.0 ? '→TP1' : 'SL';
    const addr = escHtml(p.token_address || '');
    const sym  = escHtml(p.symbol || '?');
    const chain = escHtml(p.chain || '');
    return `<tr>
      <td style="font-weight:600">${tokenLink(p.symbol, p.chain, p.token_address)}</td>
      <td>${chainBadge(p.chain)}</td>
      <td>${stratBadge(p.strategy)}</td>
      <td class="muted">$${(p.entry_price||0).toFixed(6)}</td>
      <td class="muted">$${(p.amount_usd||0).toFixed(0)}</td>
      <td class="${pnlCls}">${fmtUsd(p.pnl_usd)}</td>
      <td class="muted">${fmtHold(p.hold_secs)}</td>
      <td style="white-space:nowrap">
        <div class="progress-wrap" style="vertical-align:middle">
          <div class="progress-fill ${tpCls}" style="width:${pct.toFixed(0)}%"></div>
        </div>
        <span style="font-size:11px;margin-left:4px;color:var(--muted)">${mult.toFixed(2)}× ${tpLabel}</span>
      </td>
      <td>${addr ? `<button class="sell-btn" onclick="sellPosition('${addr}','${chain}','${sym}',this)">SELL</button>` : ''}</td>
    </tr>`;
  }).join('');
}

async function sellPosition(tokenAddress, chain, symbol, btn) {
  if (!confirm(`Sell ALL $${symbol} on ${chain}?`)) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const resp = await fetch('/api/sell', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_address: tokenAddress, chain, symbol})
    });
    const data = await resp.json();
    if (data.ok) {
      btn.textContent = '✓';
      btn.style.color = 'var(--green-lt)';
    } else {
      btn.textContent = 'ERR';
      btn.style.color = 'var(--red)';
      btn.disabled = false;
      alert('Sell failed: ' + (data.error || 'unknown error'));
    }
  } catch(e) {
    btn.textContent = 'ERR';
    btn.disabled = false;
    alert('Sell request failed: ' + e.message);
  }
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

function dexLink(chain, addr) {
  const c = (chain || '').toLowerCase();
  const path = (c === 'solana' || c === 'sol') ? 'solana'
             : (c === 'base')                  ? 'base'
             : (c === 'bsc' || c === 'bnb')    ? 'bsc'
             :                                   'solana';
  return `https://dexscreener.com/${path}/${addr}`;
}

function tokenLink(symbol, chain, addr) {
  const sym = escHtml(symbol || '?');
  if (!addr) return `$${sym}`;
  const url  = dexLink(chain, addr);
  const tip  = escHtml(addr);
  return `<a class="token-link" href="${url}" target="_blank" rel="noopener" title="${tip}">$${sym}</a>`;
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

// ── Chain breakdown ────────────────────────────────────────────────────────
function updateChains(chains) {
  ['sol','base','bnb'].forEach(k => {
    const c = chains[k] || {};
    const pnlEl = document.getElementById('ch-' + k + '-pnl');
    pnlEl.textContent = fmtUsd(c.pnl || 0);
    pnlEl.className = pnlClass(c.pnl || 0);
    document.getElementById('ch-' + k + '-cap').textContent = '$' + (c.capital || 0).toFixed(0);
    document.getElementById('ch-' + k + '-pos').textContent = c.positions || 0;
  });
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
    const pnlPct = entry > 0 ? ((exit / entry) - 1) * 100 : 0;
    const rowCls = pnl >= 0 ? 'row-win' : 'row-loss';
    return `<tr class="${rowCls}">
      <td class="muted">${fmtTime(t.time)}</td>
      <td style="font-weight:600">${tokenLink(t.token, t.chain, t.address)}</td>
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
  if (s === 'base') return 'base';
  if (s === 'bsc' || s === 'bnb') return 'bnb';
  return s;
}

// ── Token Recommendations ───────────────────────────────────────────────────
async function loadRecommendations() {
  try {
    const res = await fetch('/api/recommendations');
    const recs = await res.json();
    const tbody = document.getElementById('recs-body');
    if (!recs.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="empty">No tokens being watched yet</td></tr>';
      return;
    }
    tbody.innerHTML = recs.map(r => {
      const chainColor = r.chain_id === 'solana' ? 'var(--sol)' : r.chain_id === 'base' ? '#4f8cff' : 'var(--bnb)';
      const dipColor   = r.dip_pct < -20 ? 'var(--green)' : r.dip_pct < -10 ? 'var(--yellow)' : 'var(--red)';
      const h1Color    = r.price_change_h1 >= 0 ? 'var(--green)' : 'var(--red)';
      const riskColor  = r.risk_level === 'LOW' ? 'var(--green)' : r.risk_level === 'MEDIUM' ? 'var(--yellow)' : 'var(--red)';
      const watching   = r.watching_min < 60 ? `${r.watching_min}m` : `${Math.floor(r.watching_min/60)}h`;
      const mcap       = r.mcap >= 1000000 ? `$${(r.mcap/1000000).toFixed(1)}M` : `$${Math.round(r.mcap/1000)}k`;
      const vol        = r.volume_h1 >= 1000 ? `$${Math.round(r.volume_h1/1000)}k` : `$${Math.round(r.volume_h1)}`;
      return `<tr>
        <td><a href="${r.dex_url}" target="_blank" style="color:var(--accent);text-decoration:none;">$${r.token_symbol}</a></td>
        <td><span style="color:${chainColor};font-weight:600;">${r.chain}</span></td>
        <td>${r.score}</td>
        <td>${mcap}</td>
        <td>${vol}</td>
        <td style="color:${dipColor};font-weight:600;">${r.dip_pct > 0 ? '+' : ''}${r.dip_pct}%</td>
        <td style="color:${h1Color}">${r.price_change_h1 > 0 ? '+' : ''}${r.price_change_h1.toFixed(1)}%</td>
        <td style="color:var(--muted)">${watching}</td>
        <td style="color:${riskColor};font-size:11px;">${r.risk_level}</td>
        <td><button onclick="manualBuy('${r.token_address}','${r.chain_id}','${r.token_symbol}')"
            style="background:#a78bfa;color:#fff;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;font-size:12px;">
            Buy</button></td>
      </tr>`;
    }).join('');
  } catch(e) { console.warn('Recs load error', e); }
}

async function manualBuy(tokenAddress, chainId, symbol) {
  if (!confirm(`Manually buy $${symbol} on ${chainId}?`)) return;
  try {
    const res = await fetch('/api/manual-buy', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({token_address: tokenAddress, chain_id: chainId})
    });
    const data = await res.json();
    if (data.ok) {
      alert(`Buy order sent for $${symbol}`);
      loadRecommendations();
    } else {
      alert(`Error: ${data.error}`);
    }
  } catch(e) { alert('Request failed'); }
}

loadRecommendations();
setInterval(loadRecommendations, 30000);
</script>
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

    def __init__(self, port: int = None, tracker=None):
        self.port = port or int(os.environ.get("PORT", 8080))
        self._tracker = tracker          # optional direct tracker ref
        self.app = web.Application()
        self._stats_providers = []
        self._alert_buffer: list = []
        self._start_time = datetime.now(timezone.utc)

        self._sell_traders: dict = {}   # chain_name.lower() → trader instance
        self._scanners: dict = {}       # chain_name.lower() → scanner instance

        self.app.router.add_get("/",                  self._handle_index)
        self.app.router.add_get("/api/stats",         self._handle_stats)
        self.app.router.add_get("/api/trades",        self._handle_trades)
        self.app.router.add_get("/api/score-stats",   self._handle_score_stats)
        self.app.router.add_get("/events",            self._handle_sse)
        self.app.router.add_post("/api/reset",        self._handle_reset)
        self.app.router.add_post("/api/sell",         self._handle_sell)
        self.app.router.add_post("/api/pause",        self._handle_pause)
        self.app.router.add_get("/api/blacklist",     self._handle_blacklist_get)
        self.app.router.add_post("/api/blacklist/remove", self._handle_blacklist_remove)
        self.app.router.add_get("/api/recommendations",  self._handle_recommendations)
        self.app.router.add_post("/api/manual-buy",       self._handle_manual_buy)

    # ── Public API ──────────────────────────────────────────────────────────

    def register_provider(self, provider):
        """Register an object that provides stats via get_dashboard_stats()."""
        self._stats_providers.append(provider)
        # If the provider looks like a tracker, keep a reference for /api/trades
        if self._tracker is None and hasattr(provider, "get_all_trades"):
            self._tracker = provider

    def register_sell_trader(self, chain_name: str, trader):
        """Register a trader so the dashboard can trigger manual sells."""
        self._sell_traders[chain_name.lower()] = trader

    def register_scanner(self, chain_name: str, scanner):
        """Register a scanner so the dashboard can surface its watchlist recommendations."""
        self._scanners[chain_name.lower()] = scanner

    def add_alert(self, message: str):
        """Buffer a live alert for the event feed."""
        self._alert_buffer.append(message)
        if len(self._alert_buffer) > 200:
            self._alert_buffer = self._alert_buffer[-200:]

    async def run(self):
        """Start the aiohttp server."""
        # Attach log handler so bot activity flows to the event feed automatically
        logging.getLogger().addHandler(DashboardLogHandler(self))

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        logger.info(f"[Dashboard] Web dashboard running at http://0.0.0.0:{self.port}")

    # ── HTTP Handlers ────────────────────────────────────────────────────────

    async def _handle_index(self, request):
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def _handle_stats(self, request):
        stats = await self._build_stats()
        return web.Response(
            text=json.dumps(stats),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_reset(self, request):
        """Clear all trade history, open positions, and reset stats to zero."""
        import sqlite3, os
        db = os.path.join(os.environ.get("DATA_DIR", "."), "trades.db")

        # 1. Clear trade history (in-memory + DB)
        if self._tracker is not None:
            try:
                self._tracker.trades.clear()
                with sqlite3.connect(db) as conn:
                    conn.execute("DELETE FROM trades")
                    conn.commit()
            except Exception as e:
                logger.warning(f"[Dashboard] Reset trades error: {e}")

        # 2. Close all open positions — wipe in-memory dicts and DB table
        for trader in self._sell_traders.values():
            try:
                trader.open_positions.clear()
            except Exception as e:
                logger.warning(f"[Dashboard] Clear positions error: {e}")
        try:
            with sqlite3.connect(db) as conn:
                conn.execute("DELETE FROM open_positions")
                conn.commit()
        except Exception as e:
            logger.warning(f"[Dashboard] Reset open_positions error: {e}")

        # 3. Reset risk managers so capital and daily PnL return to baseline
        for trader in self._sell_traders.values():
            try:
                trader.risk_manager.reset()
            except Exception as e:
                logger.warning(f"[Dashboard] Risk manager reset error: {e}")

        # Write a flag file so restore_positions() skips loading on the next restart.
        # This is needed because PositionManager background tasks re-save positions
        # to the DB in the seconds between reset and restart, undoing the DB delete.
        # The flag ensures the restart starts with 0 positions regardless.
        try:
            flag = os.path.join(os.environ.get("DATA_DIR", "."), ".positions_reset")
            with open(flag, "w") as f:
                f.write("1")
        except Exception as e:
            logger.warning(f"[Dashboard] Reset flag write error: {e}")

        self._alert_buffer.clear()
        self._start_time = datetime.now(timezone.utc)
        logger.info("[Dashboard] Full reset via /api/reset — trades, positions, capital cleared")
        return web.Response(
            text=json.dumps({"ok": True}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_pause(self, request):
        """Toggle buy pause on/off."""
        if self._tracker is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": "no tracker"}),
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        if self._tracker.buying_paused:
            self._tracker.resume_buying()
            state = "resumed"
        else:
            self._tracker.pause_buying()
            state = "paused"
        return web.Response(
            text=json.dumps({"ok": True, "state": state, "paused": self._tracker.buying_paused}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_blacklist_get(self, request):
        """List all active rug blacklist entries."""
        entries = self._tracker.get_blacklist() if self._tracker else []
        return web.Response(
            text=json.dumps({"ok": True, "entries": entries}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_blacklist_remove(self, request):
        """Remove a token from the rug blacklist by address."""
        try:
            body = await request.json()
            addr = body.get("token_address", "").strip()
            if not addr:
                return web.Response(
                    text=json.dumps({"ok": False, "error": "token_address required"}),
                    content_type="application/json",
                    headers={"Access-Control-Allow-Origin": "*"}
                )
            removed = self._tracker.remove_from_blacklist(addr) if self._tracker else False
            logger.info(f"[Dashboard] Blacklist cleared for {addr} (was_present={removed})")
            return web.Response(
                text=json.dumps({"ok": True, "removed": removed}),
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        except Exception as e:
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e)}),
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

    async def _handle_recommendations(self, request):
        """Return tokens the bot is watching, eligible for manual buy."""
        recs = []
        for scanner in self._scanners.values():
            try:
                recs.extend(scanner.get_watchlist_recommendations())
            except Exception:
                pass
        recs.sort(key=lambda x: x["score"], reverse=True)
        return web.Response(
            text=json.dumps(recs),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_manual_buy(self, request):
        """Manually buy a watchlist token, bypassing the dip-entry check."""
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            chain_id      = body.get("chain_id", "").strip().lower()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        scanner = self._scanners.get(chain_id)
        if not scanner:
            return web.Response(
                text=json.dumps({"ok": False, "error": f"No scanner for chain: {chain_id}"}),
                status=404, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        entry = scanner._dip_watchlist.get(token_address.lower())
        if not entry or not entry.get("signal"):
            return web.Response(
                text=json.dumps({"ok": False, "error": "Token not in watchlist"}),
                status=404, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        signal     = entry["signal"]
        risk_level = entry.get("risk_level", "UNKNOWN")
        scanner._dip_watchlist.pop(token_address.lower(), None)

        import asyncio
        asyncio.create_task(scanner._fire_chart_buy(signal, risk_level))

        logger.info(
            f"[Dashboard] Manual buy triggered: {signal.token_symbol} "
            f"on {scanner.chain.name}"
        )
        return web.Response(
            text=json.dumps({"ok": True}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_sell(self, request):
        """Manually sell all of a position by token_address and chain."""
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            chain = body.get("chain", "").strip().lower()
            symbol = body.get("symbol", "?").strip()
        except Exception:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Invalid JSON"}),
                status=400, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        # Normalize chain key
        if chain in ("solana", "sol"):
            chain = "solana"
        elif chain == "base":
            chain = "base"
        elif chain in ("bsc", "bnb", "bnb chain"):
            chain = "bnb chain"

        trader = self._sell_traders.get(chain)
        if trader is None:
            return web.Response(
                text=json.dumps({"ok": False, "error": f"No trader for chain: {chain}"}),
                status=404, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

        try:
            await trader.sell(token_address, symbol, reason="Manual sell via dashboard", pct=1.0)
            logger.info(f"[Dashboard] Manual sell: {symbol} ({token_address}) on {chain}")
            return web.Response(
                text=json.dumps({"ok": True}),
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )
        except Exception as e:
            logger.error(f"[Dashboard] Manual sell error: {e}")
            return web.Response(
                text=json.dumps({"ok": False, "error": str(e)}),
                status=500, content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"}
            )

    async def _handle_score_stats(self, request):
        """Return win/loss/pnl broken down by score bucket."""
        if self._tracker is None:
            return web.Response(text=json.dumps([]), content_type="application/json",
                                headers={"Access-Control-Allow-Origin": "*"})
        buckets = [(0,49),(50,59),(60,69),(70,79),(80,89),(90,100)]
        result = []
        for lo, hi in buckets:
            s = self._tracker.get_stats_by_score_range(lo, hi)
            s["range"] = f"{lo}–{hi}"
            result.append(s)
        return web.Response(text=json.dumps(result), content_type="application/json",
                            headers={"Access-Control-Allow-Origin": "*"})

    async def _handle_trades(self, request):
        trades = []
        if self._tracker is not None:
            try:
                trades = self._tracker.get_all_trades()
            except Exception as e:
                logger.debug(f"[Dashboard] trades provider error: {e}")
        return web.Response(
            text=json.dumps(trades),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    async def _handle_sse(self, request):
        """Server-Sent Events stream — pushes fresh stats every 3 seconds."""
        response = web.StreamResponse(
            headers={
                "Content-Type":  "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection":    "keep-alive",
                "Access-Control-Allow-Origin": "*",
            }
        )
        await response.prepare(request)

        try:
            while True:
                stats = await self._build_stats(consume_alerts=True)
                payload = json.dumps(stats)
                await response.write(f"data: {payload}\n\n".encode())
                await asyncio.sleep(3)
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
            "buying_paused": self._tracker.buying_paused if self._tracker else False,
            "new_alerts": new_alerts,
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
                "base": {"pnl": 0, "capital": 0, "positions": 0},
                "bnb":  {"pnl": 0, "capital": 0, "positions": 0},
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
            try:
                stats["all_trades"] = self._tracker.get_all_trades()
            except Exception:
                pass

        return stats

    def _deep_merge(self, base: dict, update: dict):
        """Recursively merge update into base in-place."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
