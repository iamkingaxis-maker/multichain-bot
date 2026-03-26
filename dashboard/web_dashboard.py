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
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)

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
  .mode-badge {
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
    padding: 3px 10px; border-radius: 20px; text-transform: uppercase;
  }
  .mode-badge.paper { background: #1f3a5f; color: #58a6ff; border: 1px solid #388bfd40; }
  .mode-badge.live  { background: #3d1f1f; color: #f85149; border: 1px solid #f8514940; }
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
    <span id="mode-badge" class="mode-badge paper">PAPER</span>
    <button id="pause-btn" class="pause-btn" onclick="togglePause()">⏸ Pause Trading</button>
    <span>Uptime: <span id="uptime">—</span></span>
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

  <!-- ── Recommended Tokens (Watchlist) ── -->
  <div class="card">
    <div class="card-title" style="display:flex;justify-content:space-between;align-items:center;">
      <span><span class="dot" style="background:var(--yellow)"></span> Recommended Tokens <span style="font-size:11px;color:var(--muted);font-weight:400;">— near-miss signals (score 45-64)</span></span>
      <span id="watchlist-count" style="font-size:11px;color:var(--muted);font-weight:400;">0 tokens</span>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input id="watchlist-add-address" type="text" placeholder="Token address to monitor…"
        style="flex:2;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px;outline:none;" />
      <input id="watchlist-add-symbol" type="text" placeholder="Symbol (optional)"
        style="width:100px;background:#1a1a2e;border:1px solid #333;border-radius:6px;padding:6px 10px;color:#e2e8f0;font-size:13px;outline:none;" />
      <button onclick="addToWatchlist()"
        style="background:var(--yellow);color:#0f172a;border:none;border-radius:6px;padding:6px 16px;cursor:pointer;font-size:13px;font-weight:700;white-space:nowrap;">+ Watch</button>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Token</th><th>Score</th><th>MCap</th><th>Price</th><th>Reason</th><th>Age</th><th></th></tr></thead>
        <tbody id="watchlist-body">
          <tr><td colspan="7" style="color:var(--muted);padding:12px;text-align:center;">No recommended tokens yet</td></tr>
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
  <div class="card breakdown-card">
    <div class="card-label">Chain</div>
    <div class="card-name" style="color:var(--sol)">Solana</div>
    <div class="stat-line"><span class="k">P&amp;L</span><span id="ch-sol-pnl">$0.00</span></div>
    <div class="stat-line"><span class="k">Capital In</span><span id="ch-sol-cap">$0</span></div>
    <div class="stat-line"><span class="k">Open Positions</span><span id="ch-sol-pos">0</span></div>
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
  updateChains(d.chains || {});
  updateSecurity(d.security || {}, d.price_feed || {});

  // Reload all trades for history table
  if (d.all_trades !== undefined) {
    allTrades = d.all_trades;
    filterTrades();
  }
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
  try {
    await fetch(url, { method: 'POST' });
  } catch(e) { console.error('pause/resume error', e); }
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
    const chartUrl = addr ? `https://dexscreener.com/solana/${addr}` : '';
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

// ── Watchlist (Recommended Tokens) ──────────────────────────────────────
async function loadWatchlist() {
  try {
    const res = await fetch('/api/watchlist');
    const data = await res.json();
    const list = data.watchlist || [];
    const body = document.getElementById('watchlist-body');
    const count = document.getElementById('watchlist-count');
    if (count) count.textContent = list.length + ' token' + (list.length !== 1 ? 's' : '');
    if (!list.length) {
      body.innerHTML = '<tr><td colspan="7" style="color:var(--muted);padding:12px;text-align:center;">No recommended tokens — waiting for near-miss signals</td></tr>';
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
        <td><button onclick="manualBuy('${t.token_address}','${escHtml(t.symbol||'?')}')"
          style="background:#2ea043;color:#fff;border:none;border-radius:5px;padding:4px 10px;cursor:pointer;font-size:11px;font-weight:700;">Buy</button></td>
      </tr>`;
    }).join('');
  } catch(e) { console.warn('Watchlist load error', e); }
}

loadWatchlist();
setInterval(loadWatchlist, 30000);

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

// ── Chain breakdown ────────────────────────────────────────────────────────
function updateChains(chains) {
  const c = chains['sol'] || {};
  const pnlEl = document.getElementById('ch-sol-pnl');
  pnlEl.textContent = fmtUsd(c.pnl || 0);
  pnlEl.className = pnlClass(c.pnl || 0);
  document.getElementById('ch-sol-cap').textContent = '$' + (c.capital || 0).toFixed(0);
  document.getElementById('ch-sol-pos').textContent = c.positions || 0;
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
        self._scanners = {}              # chain_id → scanner, for live wallet reload
        self._seed_wallets_path = os.path.join(
            os.environ.get("DATA_DIR", "/data"), "seed_wallets.json"
        )

        self._trader = None  # registered via register_trader()
        self._axiom_auth = None  # registered via register_axiom_auth()
        self._trading_paused = False  # pause/resume state
        self._live_mode = False  # set via register_trader

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
        self.app.router.add_get("/api/positions",           self._handle_positions)
        self.app.router.add_post("/api/buy",                self._handle_buy)
        self.app.router.add_post("/api/update-axiom-token", self._handle_update_axiom_token)
        self.app.router.add_post("/api/reset",              self._handle_reset)
        self.app.router.add_get("/api/closed-positions",   self._handle_closed_positions)
        self.app.router.add_post("/api/pause",              self._handle_pause)
        self.app.router.add_post("/api/resume",             self._handle_resume)

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
        stats = await self._build_stats()
        return web.Response(
            text=json.dumps(stats),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

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
        position = self._trader.open_positions.get(token_address)
        if not position:
            return web.Response(
                text=json.dumps({"ok": False, "error": "Position not found"}),
                status=404, content_type="application/json", headers=cors,
            )
        pct = max(0.01, min(1.0, pct))
        try:
            await self._trader.sell(
                token_address, position.token_symbol,
                f"Manual sell from dashboard ({pct*100:.0f}%)", pct=pct
            )
            self.add_alert(f"Manual sell: {position.token_symbol} ({pct*100:.0f}%)")
            return web.Response(
                text=json.dumps({"ok": True, "symbol": position.token_symbol, "pct": pct}),
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
        cors = {"Access-Control-Allow-Origin": "*"}
        try:
            body = await request.json()
            token_address = body.get("token_address", "").strip()
            token_symbol  = body.get("token_symbol", "").strip() or token_address[:8]
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

    async def _handle_buy(self, request):
        """POST /api/buy — manually buy a token from the watchlist."""
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
        try:
            await self._trader.buy(
                token_address=token_address,
                token_symbol=token_symbol or "MANUAL",
                reason="Manual buy from dashboard",
                signal_score=0
            )
            if token_address not in self._trader.open_positions:
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

    async def _handle_closed_positions(self, request):
        """GET /api/closed-positions — returns append-only closed position history."""
        import csv, os as _os
        from dashboard.tracker import CLOSED_LOG_FILE
        cors = {"Access-Control-Allow-Origin": "*"}
        rows = []
        if _os.path.exists(CLOSED_LOG_FILE):
            try:
                with open(CLOSED_LOG_FILE, newline="") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
            except Exception as e:
                return web.Response(text=json.dumps({"error": str(e)}),
                                    status=500, content_type="application/json", headers=cors)
        return web.Response(text=json.dumps(rows), content_type="application/json", headers=cors)

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
        return web.Response(
            text=json.dumps({"ok": True, "message": "Trade history cleared"}),
            content_type="application/json", headers=cors,
        )

    async def _handle_pause(self, request):
        """POST /api/pause — pause all new trade entries."""
        cors = {"Access-Control-Allow-Origin": "*"}
        self._trading_paused = True
        if self._tracker:
            self._tracker.buying_paused = True
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
        logger.info("[Dashboard] Trading RESUMED via dashboard")
        return web.Response(
            text=json.dumps({"ok": True, "paused": False}),
            content_type="application/json", headers=cors,
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
            try:
                stats["all_trades"] = self._tracker.get_all_trades()
            except Exception:
                pass

        # Override positions list with direct trader view — always fresh, no indirection
        if self._trader is not None:
            now = datetime.now(timezone.utc)
            direct_positions = []
            for addr, pos in self._trader.open_positions.items():
                entry = getattr(pos, "entry_price_usd", 0)
                current = getattr(pos, "current_price_usd", 0) or entry
                amount = getattr(pos, "amount_usd", 0) or getattr(pos, "amount_sol_spent", 0)
                multiplier = (current / entry) if entry > 0 else 1.0
                pnl_usd = getattr(pos, "pnl_usd", (multiplier - 1) * amount)
                entry_time = getattr(pos, "entry_time", None)
                hold_secs = int((now - entry_time).total_seconds()) if entry_time else 0
                direct_positions.append({
                    "token_address": addr,
                    "symbol": getattr(pos, "token_symbol", addr[:8]),
                    "chain": getattr(pos, "chain_id", "solana"),
                    "strategy": getattr(pos, "strategy", "scanner"),
                    "entry_price": entry,
                    "pnl_usd": round(pnl_usd, 2),
                    "multiplier": round(multiplier, 4),
                    "hold_secs": hold_secs,
                    "amount_usd": amount,
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
