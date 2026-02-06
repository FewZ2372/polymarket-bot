"""
Crypto Arbitrage Dashboard

Dashboard web para monitorear el simulador de arbitraje en tiempo real.
Corre en http://localhost:8081
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

from logger import log
from crypto_arb_realistic import RealisticSimulator, RealisticConfig, RealisticPortfolio
from crypto_latency_arb import ArbConfig

# ============================================================================
# GLOBAL STATE
# ============================================================================

app = FastAPI(title="Crypto Arbitrage Dashboard - REALISTIC")

# Global simulator reference
simulator: Optional[RealisticSimulator] = None
simulator_task: Optional[asyncio.Task] = None


# ============================================================================
# DASHBOARD HTML
# ============================================================================

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crypto Arb - Realistic (TP/SL)</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        
        h1 {
            text-align: center;
            margin-bottom: 20px;
            color: #00d4ff;
            font-size: 2em;
        }
        
        .status-bar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: rgba(255,255,255,0.05);
            padding: 10px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        
        .status-indicator {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        
        .status-dot.running { background: #00ff88; }
        .status-dot.stopped { background: #ff4444; }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        
        .card {
            background: rgba(255,255,255,0.05);
            border-radius: 15px;
            padding: 20px;
            border: 1px solid rgba(255,255,255,0.1);
        }
        
        .card h2 {
            color: #00d4ff;
            margin-bottom: 15px;
            font-size: 1.2em;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            padding-bottom: 10px;
        }
        
        .metric {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }
        
        .metric:last-child {
            border-bottom: none;
        }
        
        .metric-label {
            color: #888;
        }
        
        .metric-value {
            font-weight: bold;
            font-family: 'Courier New', monospace;
        }
        
        .metric-value.positive { color: #00ff88; }
        .metric-value.negative { color: #ff4444; }
        .metric-value.neutral { color: #ffaa00; }
        
        .big-number {
            font-size: 2.5em;
            font-weight: bold;
            text-align: center;
            margin: 20px 0;
        }
        
        .big-number.positive { color: #00ff88; }
        .big-number.negative { color: #ff4444; }
        
        .positions-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        
        .positions-table th,
        .positions-table td {
            padding: 10px;
            text-align: left;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }
        
        .positions-table th {
            color: #888;
            font-weight: normal;
            font-size: 0.9em;
        }
        
        .positions-table tr:hover {
            background: rgba(255,255,255,0.05);
        }
        
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.85em;
            font-weight: bold;
        }
        
        .badge.yes { background: #00aa55; }
        .badge.no { background: #aa5500; }
        .badge.win { background: #00ff88; color: #000; }
        .badge.loss { background: #ff4444; }
        
        .crypto-prices {
            display: flex;
            gap: 20px;
            justify-content: center;
            flex-wrap: wrap;
        }
        
        .crypto-price {
            background: rgba(0,212,255,0.1);
            padding: 15px 25px;
            border-radius: 10px;
            text-align: center;
        }
        
        .crypto-price .symbol {
            font-size: 0.9em;
            color: #888;
        }
        
        .crypto-price .price {
            font-size: 1.5em;
            font-weight: bold;
            color: #00d4ff;
        }
        
        .refresh-info {
            text-align: center;
            color: #666;
            font-size: 0.85em;
            margin-top: 20px;
        }
        
        .history-item {
            padding: 10px;
            margin: 5px 0;
            background: rgba(255,255,255,0.03);
            border-radius: 8px;
            font-size: 0.9em;
        }
        
        .history-item.win {
            border-left: 3px solid #00ff88;
        }
        
        .history-item.loss {
            border-left: 3px solid #ff4444;
        }
        
        .no-data {
            text-align: center;
            color: #666;
            padding: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Crypto Arbitrage - REALISTIC (Take Profit / Stop Loss)</h1>
        
        <div class="status-bar">
            <div class="status-indicator">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">Connecting...</span>
            </div>
            <div id="runtime">Runtime: --:--:--</div>
            <div id="lastUpdate">Last update: --</div>
        </div>
        
        <div class="crypto-prices" id="cryptoPrices">
            <div class="crypto-price">
                <div class="symbol">BTC</div>
                <div class="price" id="btcPrice">$--</div>
            </div>
            <div class="crypto-price">
                <div class="symbol">ETH</div>
                <div class="price" id="ethPrice">$--</div>
            </div>
        </div>
        
        <div class="grid" style="margin-top: 20px;">
            <div class="card">
                <h2>Portfolio</h2>
                <div class="big-number" id="totalValue">$--</div>
                <div class="metric">
                    <span class="metric-label">Return</span>
                    <span class="metric-value" id="totalReturn">--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Initial Capital</span>
                    <span class="metric-value" id="initialCapital">$--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Cash Available</span>
                    <span class="metric-value" id="cashAvailable">$--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Exposure</span>
                    <span class="metric-value" id="exposure">$--</span>
                </div>
            </div>
            
            <div class="card">
                <h2>Performance</h2>
                <div class="big-number" id="pnl">$--</div>
                <div class="metric">
                    <span class="metric-label">Total Trades</span>
                    <span class="metric-value" id="totalTrades">--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Win Rate</span>
                    <span class="metric-value" id="winRate">--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Wins / Losses</span>
                    <span class="metric-value" id="winLoss">-- / --</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Best Trade</span>
                    <span class="metric-value positive" id="bestTrade">$--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Worst Trade</span>
                    <span class="metric-value negative" id="worstTrade">$--</span>
                </div>
            </div>
            
            <div class="card">
                <h2>Market Data</h2>
                <div class="metric">
                    <span class="metric-label">Markets Monitored</span>
                    <span class="metric-value" id="marketsMonitored">--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Opportunities Detected</span>
                    <span class="metric-value" id="opportunitiesDetected">--</span>
                </div>
                <div class="metric">
                    <span class="metric-label">Binance Status</span>
                    <span class="metric-value" id="binanceStatus">--</span>
                </div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card" style="grid-column: span 2;">
                <h2>Open Positions (<span id="openCount">0</span>)</h2>
                <table class="positions-table" id="positionsTable">
                    <thead>
                        <tr>
                            <th>ID</th>
                            <th>Market</th>
                            <th>Side</th>
                            <th>Entry</th>
                            <th>Size</th>
                            <th>Crypto</th>
                            <th>Threshold</th>
                            <th>Age</th>
                        </tr>
                    </thead>
                    <tbody id="positionsBody">
                        <tr><td colspan="8" class="no-data">No open positions</td></tr>
                    </tbody>
                </table>
            </div>
            
            <div class="card">
                <h2>Recent Trades</h2>
                <div id="recentTrades">
                    <div class="no-data">No trades yet</div>
                </div>
            </div>
        </div>
        
        <div class="refresh-info">
            Auto-refresh every 2 seconds | Press F5 to force refresh
        </div>
    </div>
    
    <script>
        function formatMoney(value) {
            if (value === null || value === undefined) return '$--';
            const sign = value >= 0 ? '' : '-';
            return sign + '$' + Math.abs(value).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
        
        function formatPercent(value) {
            if (value === null || value === undefined) return '--';
            const sign = value >= 0 ? '+' : '';
            return sign + value.toFixed(1) + '%';
        }
        
        function formatPrice(value) {
            if (value === null || value === undefined) return '$--';
            return '$' + value.toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});
        }
        
        function timeSince(dateStr) {
            if (!dateStr) return '--';
            const date = new Date(dateStr);
            const seconds = Math.floor((new Date() - date) / 1000);
            if (seconds < 60) return seconds + 's ago';
            if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
            return Math.floor(seconds / 3600) + 'h ago';
        }
        
        async function updateDashboard() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                // Status
                const statusDot = document.getElementById('statusDot');
                const statusText = document.getElementById('statusText');
                
                if (data.running) {
                    statusDot.className = 'status-dot running';
                    statusText.textContent = 'Running';
                } else {
                    statusDot.className = 'status-dot stopped';
                    statusText.textContent = 'Stopped';
                }
                
                document.getElementById('runtime').textContent = 'Runtime: ' + (data.stats?.runtime_formatted || '--');
                document.getElementById('lastUpdate').textContent = 'Updated: ' + new Date().toLocaleTimeString();
                
                // Crypto prices
                if (data.prices) {
                    document.getElementById('btcPrice').textContent = formatPrice(data.prices.BTCUSDT);
                    document.getElementById('ethPrice').textContent = formatPrice(data.prices.ETHUSDT);
                }
                
                // Portfolio
                const stats = data.stats || {};
                
                const totalValue = document.getElementById('totalValue');
                totalValue.textContent = formatMoney(stats.total_value);
                totalValue.className = 'big-number ' + (stats.total_return_pct >= 0 ? 'positive' : 'negative');
                
                const totalReturn = document.getElementById('totalReturn');
                totalReturn.textContent = formatPercent(stats.total_return_pct);
                totalReturn.className = 'metric-value ' + (stats.total_return_pct >= 0 ? 'positive' : 'negative');
                
                document.getElementById('initialCapital').textContent = formatMoney(stats.initial_capital);
                document.getElementById('cashAvailable').textContent = formatMoney(stats.current_cash);
                document.getElementById('exposure').textContent = formatMoney(stats.total_exposure);
                
                // Performance
                const pnl = document.getElementById('pnl');
                pnl.textContent = formatMoney(stats.total_pnl);
                pnl.className = 'big-number ' + (stats.total_pnl >= 0 ? 'positive' : 'negative');
                
                document.getElementById('totalTrades').textContent = stats.trade_count || 0;
                document.getElementById('winRate').textContent = (stats.win_rate || 0).toFixed(0) + '%';
                document.getElementById('winLoss').textContent = (stats.wins || 0) + ' / ' + (stats.losses || 0);
                document.getElementById('bestTrade').textContent = formatMoney(stats.largest_win);
                document.getElementById('worstTrade').textContent = formatMoney(stats.largest_loss);
                
                // Market data
                document.getElementById('marketsMonitored').textContent = data.markets_count || 0;
                const analyzed = data.opportunities_analyzed || 0;
                const withEdge = data.opportunities_with_edge || 0;
                const efficiency = analyzed > 0 ? (withEdge / analyzed * 100).toFixed(2) : 0;
                document.getElementById('opportunitiesDetected').textContent = `${withEdge.toLocaleString()} / ${analyzed.toLocaleString()} (${efficiency}%)`;
                document.getElementById('binanceStatus').textContent = data.binance_connected ? 'Connected' : 'Disconnected';
                
                // Open positions
                document.getElementById('openCount').textContent = (data.positions || []).length;
                const tbody = document.getElementById('positionsBody');
                
                if (data.positions && data.positions.length > 0) {
                    tbody.innerHTML = data.positions.map(pos => `
                        <tr>
                            <td>${pos.id}</td>
                            <td title="${pos.market_question}">${pos.market_question.substring(0, 35)}...</td>
                            <td><span class="badge ${pos.side.toLowerCase()}">${pos.side}</span></td>
                            <td>${pos.entry_price.toFixed(4)}</td>
                            <td>${formatMoney(pos.size)}</td>
                            <td>${pos.crypto_symbol}</td>
                            <td>${formatPrice(pos.threshold_price)}</td>
                            <td>${timeSince(pos.opened_at)}</td>
                        </tr>
                    `).join('');
                } else {
                    tbody.innerHTML = '<tr><td colspan="8" class="no-data">No open positions</td></tr>';
                }
                
                // Recent trades
                const tradesDiv = document.getElementById('recentTrades');
                if (data.recent_trades && data.recent_trades.length > 0) {
                    tradesDiv.innerHTML = data.recent_trades.slice(0, 10).map(trade => `
                        <div class="history-item ${trade.pnl >= 0 ? 'win' : 'loss'}">
                            <strong>${trade.id}</strong> - ${trade.side} ${trade.crypto_symbol} $${trade.threshold_price.toLocaleString()}
                            <br>
                            <small>P&L: ${formatMoney(trade.pnl)} (${formatPercent(trade.pnl_pct * 100)}) - ${trade.resolution_reason}</small>
                        </div>
                    `).join('');
                } else {
                    tradesDiv.innerHTML = '<div class="no-data">No trades yet</div>';
                }
                
            } catch (error) {
                console.error('Error updating dashboard:', error);
                document.getElementById('statusDot').className = 'status-dot stopped';
                document.getElementById('statusText').textContent = 'Connection Error';
            }
        }
        
        // Update every 2 seconds
        setInterval(updateDashboard, 2000);
        updateDashboard();
    </script>
</body>
</html>
"""


# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the dashboard HTML."""
    return DASHBOARD_HTML


@app.get("/api/status")
async def get_status():
    """Get current simulator status."""
    global simulator
    
    if simulator is None:
        return {
            "running": False,
            "message": "Simulator not started"
        }
    
    # Get stats
    stats = simulator.portfolio.get_stats()
    
    # Get prices
    prices = {}
    for symbol, price in simulator.binance_feed.get_all_prices().items():
        prices[symbol] = price.price
    
    # Get positions
    positions = [pos.to_dict() for pos in simulator.portfolio.open_positions]
    
    # Get recent closed trades
    recent_trades = [pos.to_dict() for pos in simulator.portfolio.closed_positions[-20:]]
    recent_trades.reverse()  # Most recent first
    
    return {
        "running": simulator._running,
        "stats": stats,
        "prices": prices,
        "positions": positions,
        "recent_trades": recent_trades,
        "markets_count": len(simulator._crypto_markets),
        "opportunities_analyzed": simulator._opportunities_analyzed,
        "opportunities_with_edge": simulator._opportunities_with_edge,
        "binance_connected": simulator.binance_feed.is_connected(),
    }


@app.post("/api/start")
async def start_simulator():
    """Start the simulator."""
    global simulator, simulator_task
    
    if simulator is not None and simulator._running:
        return {"status": "already_running"}
    
    # Create REALISTIC simulator with take profit/stop loss
    config = RealisticConfig(
        initial_capital=100.0,
        max_position_size=10.0,
        max_open_positions=5,
        min_edge_after_fees=0.05,
        min_confidence=0.70,
        take_profit_pct=0.50,     # Take profit at 50% of expected edge
        stop_loss_pct=0.30,       # Stop loss at 30% loss
        min_hold_seconds=60,      # Hold at least 1 minute
        state_file='crypto_arb_realistic_state.json',
    )
    
    arb_config = ArbConfig(
        symbols=['BTCUSDT', 'ETHUSDT'],
        min_edge=0.05,
        min_liquidity=500,
    )
    
    simulator = RealisticSimulator(config)
    
    # Start in background
    simulator_task = asyncio.create_task(simulator.start())
    
    return {"status": "started"}


@app.post("/api/stop")
async def stop_simulator():
    """Stop the simulator."""
    global simulator, simulator_task
    
    if simulator is None:
        return {"status": "not_running"}
    
    await simulator.stop()
    
    if simulator_task:
        simulator_task.cancel()
        try:
            await simulator_task
        except asyncio.CancelledError:
            pass
    
    return {"status": "stopped"}


# ============================================================================
# MAIN
# ============================================================================

async def run_with_dashboard():
    """Run REALISTIC simulator with dashboard."""
    global simulator, simulator_task
    
    # Create REALISTIC simulator with take profit/stop loss
    config = RealisticConfig(
        initial_capital=100.0,
        max_position_size=10.0,
        max_open_positions=5,
        min_edge_after_fees=0.05,    # 5% edge after fees
        min_confidence=0.70,
        take_profit_pct=0.50,        # Take profit at 50% of expected edge
        stop_loss_pct=0.30,          # Stop loss at 30% loss
        min_hold_seconds=60,         # Hold at least 1 minute
        time_acceleration=1.0,       # Real time (cambiar a mayor para testing)
        state_file='crypto_arb_realistic_state.json',
    )
    
    arb_config = ArbConfig(
        symbols=['BTCUSDT', 'ETHUSDT'],
        min_edge=0.05,
        min_liquidity=500,
    )
    
    simulator = RealisticSimulator(config)
    
    # Start simulator in background
    simulator_task = asyncio.create_task(simulator.start())
    
    # Run web server
    config = uvicorn.Config(app, host="0.0.0.0", port=8087, log_level="warning")
    server = uvicorn.Server(config)
    
    log.info("="*60)
    log.info("DASHBOARD: http://localhost:8087")
    log.info("="*60)
    
    try:
        await server.serve()
    finally:
        await simulator.stop()


if __name__ == "__main__":
    asyncio.run(run_with_dashboard())
