"""
Web Dashboard
Real-time browser dashboard for monitoring the bot.
Access from any device on your network at http://localhost:8080

Shows:
  - Live PnL per strategy and chain
  - Open positions with current P&L
  - Wallet leaderboard
  - Security check stats
  - Price feed health
  - Trade history
  - Real-time Telegram-style alerts
"""

import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Optional
from aiohttp import web

logger = logging.getLogger(__name__)

HTML_DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Multi-Chain Bot Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #e6edf3; font-family: 'Courier New', monospace; font-size: 13px; }
  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 24px; display: flex; align-items: center; justify-content: space-between; }
  .header h1 { font-size: 18px; color: #58a6ff; }
  .status-dot { width: 10px; height: 10px; border-radius: 50%; background: #2ea043; display: inline-block; margin-right: 8px; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; padding: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; }
  .card h2 { font-size: 12px; text-transform: uppercase; color: #8b949e; margin-bottom: 12px; letter-spacing: 1px; }
  .stat { display: flex; justify-content: space-between; margin-bottom: 8px; padding: 6px 0; border-bottom: 1px solid #21262d; }
  .stat:last-child { border-bottom: none; }
  .stat-label { color: #8b949e; }
  .stat-value { font-weight: bold; }
  .green { color: #2ea043; }
  .red { color: #f85149; }
  .yellow { color: #d29922; }
  .blue { color: #58a6ff; }
  .chain-badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }
  .sol { background: #9945ff22; color: #9945ff; }
  .base { background: #0052ff22; color: #4f8cff; }
  .bnb { background: #f3ba2f22; color: #f3ba2f; }
  .trade-row { padding: 8px 0; border-bottom: 1px solid #21262d; display: flex; justify-content: space-between; align-items: center; }
  .trade-row:last-child { border-bottom: none; }
  .strat-badge { padding: 2px 6px; border-radius: 4px; font-size: 10px; }
  .scanner { background: #58a6ff22; color: #58a6ff; }
  .copy { background: #2ea04322; color: #2ea043; }
  .scalper { background: #d2992222; color: #d29922; }
  .wallet-row { padding: 6px 0; border-bottom: 1px solid #21262d; display: flex; justify-content: space-between; }
  .score-bar { width: 60px; height: 6px; background: #21262d; border-radius: 3px; overflow: hidden; display: inline-block; vertical-align: middle; margin-left: 8px; }
  .score-fill { height: 100%; border-radius: 3px; }
  #alerts { height: 200px; overflow-y: auto; }
  .alert-item { padding: 6px; border-bottom: 1px solid #21262d; font-size: 12px; }
  .alert-time { color: #8b949e; font-size: 10px; }
  .uptime { color: #8b949e; font-size: 12px; }
  .big-stat { font-size: 28px; font-weight: bold; margin: 8px 0; }
  .refresh-info { color: #8b949e; font-size: 11px; text-align: right; padding: 8px 16px; }
</style>
</head>
<body>
<div class="header">
  <h1>⚡ Multi-Chain Memecoin Bot</h1>
  <div>
    <span class="status-dot"></span>
    <span id="status-text" class="uptime">Connecting...</span>
  </div>
</div>

<div class="grid">

  <!-- Overall PnL -->
  <div class="card">
    <h2>📊 Overall Performance</h2>
    <div id="overall-pnl" class="big-stat green">$0.00</div>
    <div class="stat"><span class="stat-label">Total Trades</span><span class="stat-value" id="total-trades">0</span></div>
    <div class="stat"><span class="stat-label">Win Rate</span><span class="stat-value" id="win-rate">0%</span></div>
    <div class="stat"><span class="stat-label">Best Trade</span><span class="stat-value green" id="best-trade">$0</span></div>
    <div class="stat"><span class="stat-label">Worst Trade</span><span class="stat-value red" id="worst-trade">$0</span></div>
    <div class="stat"><span class="stat-label">Daily PnL</span><span class="stat-value" id="daily-pnl">$0</span></div>
  </div>

  <!-- Strategy Breakdown -->
  <div class="card">
    <h2>🎯 Strategy Breakdown</h2>
    <div class="stat"><span class="stat-label"><span class="strat-badge scanner">SCANNER</span></span><span class="stat-value" id="scanner-pnl">$0</span></div>
    <div class="stat"><span class="stat-label">Trades / Win Rate</span><span class="stat-value" id="scanner-stats">0 / 0%</span></div>
    <div class="stat"><span class="stat-label"><span class="strat-badge copy">COPY</span></span><span class="stat-value" id="copy-pnl">$0</span></div>
    <div class="stat"><span class="stat-label">Trades / Win Rate</span><span class="stat-value" id="copy-stats">0 / 0%</span></div>
    <div class="stat"><span class="stat-label"><span class="strat-badge scalper">SCALPER</span></span><span class="stat-value" id="scalper-pnl">$0</span></div>
    <div class="stat"><span class="stat-label">Trades / Win Rate</span><span class="stat-value" id="scalper-stats">0 / 0%</span></div>
  </div>

  <!-- Chain Status -->
  <div class="card">
    <h2>🔗 Chain Status</h2>
    <div id="chain-status">
      <div class="stat"><span class="stat-label"><span class="chain-badge sol">SOLANA</span></span><span class="stat-value" id="sol-pnl">$0</span></div>
      <div class="stat"><span class="stat-label">Capital</span><span class="stat-value" id="sol-capital">$0</span></div>
      <div class="stat"><span class="stat-label"><span class="chain-badge base">BASE</span></span><span class="stat-value" id="base-pnl">$0</span></div>
      <div class="stat"><span class="stat-label">Capital</span><span class="stat-value" id="base-capital">$0</span></div>
      <div class="stat"><span class="stat-label"><span class="chain-badge bnb">BNB</span></span><span class="stat-value" id="bnb-pnl">$0</span></div>
      <div class="stat"><span class="stat-label">Capital</span><span class="stat-value" id="bnb-capital">$0</span></div>
    </div>
  </div>

  <!-- Security Stats -->
  <div class="card">
    <h2>🔒 Security Gate</h2>
    <div class="stat"><span class="stat-label">Total Checks</span><span class="stat-value" id="sec-total">0</span></div>
    <div class="stat"><span class="stat-label">Blocked (Honeypot/Tax)</span><span class="stat-value red" id="sec-blocked">0</span></div>
    <div class="stat"><span class="stat-label">Block Rate</span><span class="stat-value yellow" id="sec-rate">0%</span></div>
    <div class="stat"><span class="stat-label">Cache Size</span><span class="stat-value" id="sec-cache">0</span></div>
    <div class="stat"><span class="stat-label">Price Feed Ticks</span><span class="stat-value blue" id="feed-ticks">0</span></div>
    <div class="stat"><span class="stat-label">WebSocket %</span><span class="stat-value green" id="feed-ws">0%</span></div>
  </div>

  <!-- Open Positions -->
  <div class="card">
    <h2>📈 Open Positions</h2>
    <div id="positions">
      <div style="color:#8b949e;text-align:center;padding:20px">No open positions</div>
    </div>
  </div>

  <!-- Wallet Leaderboard -->
  <div class="card">
    <h2>👛 Wallet Leaderboard</h2>
    <div id="wallets">
      <div style="color:#8b949e;text-align:center;padding:20px">No wallets configured</div>
    </div>
  </div>

  <!-- Recent Trades -->
  <div class="card">
    <h2>🔄 Recent Trades</h2>
    <div id="recent-trades">
      <div style="color:#8b949e;text-align:center;padding:20px">No trades yet</div>
    </div>
  </div>

  <!-- Live Alerts -->
  <div class="card">
    <h2>📡 Live Alerts</h2>
    <div id="alerts">
      <div class="alert-item" style="color:#8b949e">Waiting for alerts...</div>
    </div>
  </div>

</div>
<div class="refresh-info">Auto-refreshes every 5 seconds</div>

<script>
  let alertLog = [];

  async function refresh() {
    try {
      const r = await fetch('/api/stats');
      const data = await r.json();
      updateDashboard(data);
      document.getElementById('status-text').textContent =
        'Live — ' + new Date().toLocaleTimeString();
    } catch(e) {
      document.getElementById('status-text').textContent = 'Reconnecting...';
    }
  }

  function fmt(v, prefix='$') {
    const n = parseFloat(v) || 0;
    const cls = n >= 0 ? 'green' : 'red';
    const sign = n >= 0 ? '+' : '';
    return `<span class="${cls}">${prefix}${sign}${n.toFixed(2)}</span>`;
  }

  function updateDashboard(d) {
    const overall = d.overall || {};
    const pnl = overall.total_pnl || 0;
    document.getElementById('overall-pnl').innerHTML = fmt(pnl);
    document.getElementById('overall-pnl').className = 'big-stat ' + (pnl >= 0 ? 'green' : 'red');
    document.getElementById('total-trades').textContent = overall.total_trades || 0;
    document.getElementById('win-rate').textContent = (overall.win_rate || 0).toFixed(1) + '%';
    document.getElementById('best-trade').innerHTML = fmt(overall.best_trade || 0);
    document.getElementById('worst-trade').innerHTML = fmt(overall.worst_trade || 0);
    document.getElementById('daily-pnl').innerHTML = fmt(d.daily_pnl || 0);

    const strategies = d.strategies || {};
    ['scanner','copy','scalper'].forEach(s => {
      const st = strategies[s] || {};
      document.getElementById(s+'-pnl').innerHTML = fmt(st.total_pnl || 0);
      document.getElementById(s+'-stats').textContent =
        (st.total_trades||0) + ' / ' + (st.win_rate||0).toFixed(1) + '%';
    });

    const chains = d.chains || {};
    ['sol','base','bnb'].forEach(c => {
      const ch = chains[c] || {};
      document.getElementById(c+'-pnl').innerHTML = fmt(ch.pnl || 0);
      document.getElementById(c+'-capital').textContent = '$' + (ch.capital || 0).toFixed(0);
    });

    const sec = d.security || {};
    document.getElementById('sec-total').textContent = sec.total_checks || 0;
    document.getElementById('sec-blocked').textContent = sec.blocked || 0;
    document.getElementById('sec-rate').textContent = (sec.block_rate || 0).toFixed(1) + '%';
    document.getElementById('sec-cache').textContent = sec.cache_size || 0;

    const feed = d.price_feed || {};
    document.getElementById('feed-ticks').textContent = feed.total_ticks || 0;
    document.getElementById('feed-ws').textContent = (feed.websocket_pct || 0).toFixed(1) + '%';

    // Positions
    const positions = d.positions || [];
    const posEl = document.getElementById('positions');
    if (positions.length === 0) {
      posEl.innerHTML = '<div style="color:#8b949e;text-align:center;padding:20px">No open positions</div>';
    } else {
      posEl.innerHTML = positions.map(p => {
        const chainCls = p.chain === 'solana' ? 'sol' : p.chain === 'base' ? 'base' : 'bnb';
        return `<div class="trade-row">
          <span><span class="chain-badge ${chainCls}">${p.chain.toUpperCase()}</span> $${p.symbol}</span>
          <span>${fmt(p.pnl_usd)} (${p.multiplier.toFixed(2)}x)</span>
        </div>`;
      }).join('');
    }

    // Wallets
    const wallets = d.wallets || [];
    const walletEl = document.getElementById('wallets');
    if (wallets.length === 0) {
      walletEl.innerHTML = '<div style="color:#8b949e;text-align:center;padding:20px">No wallets configured</div>';
    } else {
      walletEl.innerHTML = wallets.map(w => {
        const statusColor = w.status === 'active' ? 'green' : w.status === 'paused' ? 'yellow' : 'red';
        const fillColor = w.score >= 70 ? '#2ea043' : w.score >= 50 ? '#d29922' : '#f85149';
        return `<div class="wallet-row">
          <span class="${statusColor}">${w.label}</span>
          <span>${w.win_rate.toFixed(1)}% WR
            <span class="score-bar"><span class="score-fill" style="width:${w.score}%;background:${fillColor}"></span></span>
          </span>
        </div>`;
      }).join('');
    }

    // Recent Trades
    const trades = d.recent_trades || [];
    const tradeEl = document.getElementById('recent-trades');
    if (trades.length === 0) {
      tradeEl.innerHTML = '<div style="color:#8b949e;text-align:center;padding:20px">No trades yet</div>';
    } else {
      tradeEl.innerHTML = trades.slice(0,8).map(t => {
        const stratCls = t.strategy || 'scanner';
        return `<div class="trade-row">
          <span><span class="strat-badge ${stratCls}">${stratCls.toUpperCase()}</span> ${t.reason.slice(0,25)}</span>
          <span>${fmt(t.pnl)}</span>
        </div>`;
      }).join('');
    }

    // Alerts
    if (d.new_alerts && d.new_alerts.length > 0) {
      d.new_alerts.forEach(a => {
        alertLog.unshift({ msg: a, time: new Date().toLocaleTimeString() });
      });
      alertLog = alertLog.slice(0, 50);
      document.getElementById('alerts').innerHTML = alertLog.map(a =>
        `<div class="alert-item"><span class="alert-time">${a.time}</span> ${a.msg}</div>`
      ).join('');
    }
  }

  refresh();
  setInterval(refresh, 5000);
</script>
</body>
</html>
"""


class WebDashboard:
    """
    Lightweight aiohttp web server serving real-time bot stats.
    Runs on port 8080 by default.
    """

    def __init__(self, port: int = 8080):
        self.port = port
        self.app = web.Application()
        self._stats_providers = []
        self._alert_buffer = []
        self._start_time = datetime.now(timezone.utc)

        self.app.router.add_get("/", self._handle_index)
        self.app.router.add_get("/api/stats", self._handle_stats)

    def register_provider(self, provider):
        """Register an object that provides stats via get_stats()."""
        self._stats_providers.append(provider)

    def add_alert(self, message: str):
        """Add a message to the live alert feed."""
        self._alert_buffer.append(message)
        if len(self._alert_buffer) > 100:
            self._alert_buffer = self._alert_buffer[-100:]

    async def run(self):
        """Start the web server."""
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.port)
        await site.start()
        logger.info(
            f"[Dashboard] 🌐 Web dashboard running at "
            f"http://localhost:{self.port}"
        )

    async def _handle_index(self, request):
        return web.Response(text=HTML_DASHBOARD, content_type="text/html")

    async def _handle_stats(self, request):
        """Collect stats from all providers and return as JSON."""
        stats = await self._build_stats()
        return web.Response(
            text=json.dumps(stats),
            content_type="application/json"
        )

    async def _build_stats(self) -> dict:
        """Aggregate stats from all registered providers."""
        uptime = datetime.now(timezone.utc) - self._start_time
        hours = int(uptime.total_seconds() // 3600)
        minutes = int((uptime.total_seconds() % 3600) // 60)

        new_alerts = self._alert_buffer.copy()
        self._alert_buffer.clear()

        base_stats = {
            "uptime": f"{hours}h {minutes}m",
            "new_alerts": new_alerts,
            "overall": {},
            "strategies": {
                "scanner": {},
                "copy": {},
                "scalper": {}
            },
            "chains": {
                "sol": {"pnl": 0, "capital": 0},
                "base": {"pnl": 0, "capital": 0},
                "bnb": {"pnl": 0, "capital": 0}
            },
            "security": {},
            "price_feed": {},
            "positions": [],
            "wallets": [],
            "recent_trades": [],
            "daily_pnl": 0
        }

        for provider in self._stats_providers:
            try:
                provider_stats = provider.get_dashboard_stats()
                self._merge_stats(base_stats, provider_stats)
            except Exception as e:
                logger.debug(f"[Dashboard] Stats provider error: {e}")

        return base_stats

    def _merge_stats(self, base: dict, update: dict):
        """Deep merge update into base."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._merge_stats(base[key], value)
            else:
                base[key] = value
