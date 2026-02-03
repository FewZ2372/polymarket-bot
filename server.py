"""
HTTP server for healthchecks at Fly.io and simple dashboard.
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from typing import Dict, List, Any, Optional
from datetime import datetime
import asyncio

from config import config
from logger import log


# Global state for dashboard
class BotState:
    """Holds the current state of the bot for the dashboard."""
    def __init__(self):
        self.started_at: datetime = datetime.now()
        self.last_scan: Optional[datetime] = None
        self.scan_count: int = 0
        self.opportunities_found: int = 0
        self.trades_executed: int = 0
        self.alerts_sent: int = 0
        self.last_opportunities: List[Dict[str, Any]] = []
        self.daily_pnl: float = 0.0
        self.is_running: bool = True
        self.errors: List[Dict[str, Any]] = []
    
    def record_scan(self, opportunities: List[Dict[str, Any]]):
        self.last_scan = datetime.now()
        self.scan_count += 1
        self.opportunities_found += len([o for o in opportunities if o.get('score', 0) >= 80])
        self.last_opportunities = opportunities[:10]
    
    def record_trade(self, profit: float = 0):
        self.trades_executed += 1
        self.daily_pnl += profit
    
    def record_alert(self):
        self.alerts_sent += 1
    
    def record_error(self, error: str, module: str):
        self.errors.append({
            "timestamp": datetime.now().isoformat(),
            "error": error,
            "module": module
        })
        # Keep only last 50 errors
        self.errors = self.errors[-50:]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "uptime_seconds": (datetime.now() - self.started_at).total_seconds(),
            "last_scan": self.last_scan.isoformat() if self.last_scan else None,
            "scan_count": self.scan_count,
            "opportunities_found": self.opportunities_found,
            "trades_executed": self.trades_executed,
            "alerts_sent": self.alerts_sent,
            "daily_pnl": self.daily_pnl,
            "is_running": self.is_running,
            "config": {
                "auto_trade_enabled": config.trading.auto_trade_enabled,
                "dry_run": config.trading.dry_run,
                "scan_interval": config.scan_interval,
                "min_score": config.trading.min_score_to_trade,
            }
        }


# Global state instance
bot_state = BotState()

# FastAPI app
app = FastAPI(
    title="Polymarket Bot",
    description="Arbitrage detection and auto-trading bot for prediction markets",
    version="2.0.0"
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Fly.io."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/status")
async def get_status():
    """Get current bot status."""
    return JSONResponse(content=bot_state.to_dict())


@app.get("/opportunities")
async def get_opportunities():
    """Get latest detected opportunities."""
    return JSONResponse(content={
        "last_scan": bot_state.last_scan.isoformat() if bot_state.last_scan else None,
        "opportunities": bot_state.last_opportunities
    })


@app.get("/errors")
async def get_errors():
    """Get recent errors."""
    return JSONResponse(content={"errors": bot_state.errors})


@app.get("/simulation")
async def get_simulation():
    """Get simulation statistics and trades."""
    try:
        from simulation_tracker import simulation_tracker
        stats = simulation_tracker.get_stats()
        open_trades = [
            {
                'id': t.id,
                'market': t.market,
                'entry_price': t.entry_price,
                'amount': t.amount_usd,
                'score': t.score,
                'timestamp': t.timestamp,
            }
            for t in simulation_tracker.get_open_trades()
        ]
        all_trades = [
            {
                'id': t.id,
                'market': t.market[:50],
                'entry_price': t.entry_price,
                'amount': t.amount_usd,
                'pnl': t.pnl_usd,
                'status': t.status,
                'timestamp': t.timestamp,
            }
            for t in simulation_tracker.trades[-20:]
        ]
        return JSONResponse(content={
            'stats': stats,
            'open_trades': open_trades,
            'recent_trades': all_trades,
            'total_trades': len(simulation_tracker.trades),
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.post("/simulation/reset")
async def reset_simulation():
    """Reset simulation data - clears all trades."""
    try:
        from simulation_tracker import simulation_tracker
        simulation_tracker.trades = []
        simulation_tracker.save()
        log.info("[API] Simulation data reset")
        return JSONResponse(content={'success': True, 'message': 'Simulation reset'})
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.post("/simulation/validate")
async def validate_simulation():
    """Validate and fix corrupted simulation data."""
    try:
        from simulation_tracker import simulation_tracker
        
        fixed_count = 0
        removed_count = 0
        trades_to_keep = []
        
        for trade in simulation_tracker.trades:
            # Check for corrupted data: unrealistic shares (entry price too low for outcome)
            # If buying NO at <0.30 or YES at <0.03, it's likely corrupted
            is_corrupted = False
            
            if trade.outcome == "NO" and trade.entry_price < 0.30:
                # NO should typically be >= 0.50 or so for most Resolution Arb trades
                is_corrupted = True
                log.warning(f"[VALIDATE] Removing corrupted NO trade: {trade.market[:40]} @ {trade.entry_price:.4f}")
            elif trade.outcome == "YES" and trade.entry_price > 0.97:
                # YES at >97% leaves almost no profit room
                is_corrupted = True
                log.warning(f"[VALIDATE] Removing unlikely YES trade: {trade.market[:40]} @ {trade.entry_price:.4f}")
            
            # Check for unrealistic P&L (>200% profit per trade is suspicious)
            if trade.pnl_pct > 200 or trade.pnl_pct < -100:
                is_corrupted = True
                log.warning(f"[VALIDATE] Removing trade with unrealistic P&L: {trade.market[:40]} | P&L: {trade.pnl_pct:.1f}%")
            
            if is_corrupted:
                removed_count += 1
            else:
                trades_to_keep.append(trade)
        
        simulation_tracker.trades = trades_to_keep
        simulation_tracker.save()
        
        return JSONResponse(content={
            'success': True,
            'removed': removed_count,
            'remaining': len(trades_to_keep),
            'message': f'Removed {removed_count} corrupted trades'
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/arbitrage")
async def get_arbitrage():
    """Get cross-platform arbitrage opportunities."""
    try:
        from platforms import multi_scanner
        opportunities = multi_scanner.find_arbitrage_opportunities(min_spread=0.02)
        summary = multi_scanner.get_platform_summary()
        return JSONResponse(content={
            'opportunities': [
                {
                    'title': o['title'][:60],
                    'spread_pct': round(o['spread_pct'], 2),
                    'buy_on': o['buy_on'],
                    'buy_price': o['buy_price'],
                    'sell_on': o['sell_on'],
                    'sell_price': o['sell_price'],
                }
                for o in opportunities[:10]
            ],
            'platform_summary': summary,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/momentum")
async def get_momentum():
    """Get momentum signals."""
    try:
        from momentum_tracker import momentum_tracker
        signals = momentum_tracker.get_recent_signals(limit=10)
        stats = momentum_tracker.get_stats()
        return JSONResponse(content={
            'signals': [s.to_dict() for s in signals],
            'stats': stats,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/whales")
async def get_whales():
    """Get whale tracking data."""
    try:
        from whale_tracker import whale_tracker
        from onchain_tracker import onchain_tracker
        
        top_picks = whale_tracker.get_top_whale_picks(limit=10)
        stats = whale_tracker.get_stats()
        
        # Add on-chain data
        onchain_stats = onchain_tracker.get_stats()
        copy_signals = onchain_tracker.get_copy_trade_signals(min_profit=10000)
        active_whales = onchain_tracker.get_active_whales(hours=24)
        recent_txs = onchain_tracker.get_recent_transactions(limit=10)
        
        return JSONResponse(content={
            'top_whales': top_picks,
            'stats': stats,
            'onchain_stats': onchain_stats,
            'copy_trade_signals': copy_signals[:5],
            'active_whales_24h': active_whales[:5],
            'recent_transactions': recent_txs,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/events")
async def get_events():
    """Get upcoming events."""
    try:
        from events_tracker import events_tracker
        events = events_tracker.get_high_impact_events(min_importance=6)
        stats = events_tracker.get_stats()
        return JSONResponse(content={
            'events': [e.to_dict() for e in events[:10]],
            'stats': stats,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/social")
async def get_social():
    """Get social sentiment data."""
    try:
        from social_sentiment import social_analyzer
        trending = social_analyzer.get_trending_topics(min_buzz=30)
        stats = social_analyzer.get_stats()
        return JSONResponse(content={
            'trending': [t.to_dict() for t in trending[:10]],
            'stats': stats,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/orderbook")
async def get_orderbook_analysis():
    """Get order book analysis and signals."""
    try:
        from orderbook_analyzer import orderbook_analyzer
        
        stats = orderbook_analyzer.get_stats()
        recent_walls = orderbook_analyzer.get_recent_walls(10)
        recent_imbalances = orderbook_analyzer.get_recent_imbalances(10)
        
        return JSONResponse(content={
            'stats': stats,
            'recent_walls': recent_walls,
            'recent_imbalances': recent_imbalances,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/backtest")
async def get_backtest():
    """Get backtest results from simulation data."""
    try:
        from backtester import backtester
        result = backtester.backtest_from_simulation()
        
        if result:
            return JSONResponse(content=result.to_dict())
        else:
            return JSONResponse(content={'error': 'No simulation data available'})
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/realtime")
async def get_realtime_status():
    """Get real-time feed status."""
    try:
        from realtime_feed import realtime_feed
        stats = realtime_feed.get_stats()
        momentum = realtime_feed.get_momentum_signals(min_momentum=0.02)
        return JSONResponse(content={
            'stats': stats,
            'momentum_signals': momentum[:10],
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/risk")
async def get_risk_status():
    """Get risk management status."""
    try:
        from risk_manager import risk_manager
        stats = risk_manager.get_stats()
        return JSONResponse(content=stats)
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/swing")
async def get_swing_signals():
    """Get current swing trading signals."""
    try:
        from trade_resolver import trade_resolver
        from simulation_tracker import simulation_tracker
        
        signals = trade_resolver.get_swing_signals(simulation_tracker)
        return JSONResponse(content={
            'signals': [
                {
                    'trade_id': s.trade_id,
                    'market': s.market[:50],
                    'entry_price': s.entry_price,
                    'current_price': s.current_price,
                    'profit_pct': round(s.profit_pct * 100, 2),
                    'action': s.action,
                    'reason': s.reason,
                }
                for s in signals
            ],
            'total_open_trades': len(simulation_tracker.get_open_trades()),
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/multi-arbitrage")
async def get_multi_outcome_arbitrage():
    """Get all arbitrage opportunities (multi-outcome, resolution, time decay, correlated)."""
    try:
        from arbitrage_scanner import arbitrage_scanner
        
        # Trigger full scan
        summary = arbitrage_scanner.scan_all()
        stats = arbitrage_scanner.get_stats()
        best_opps = arbitrage_scanner.get_best_opportunities(limit=15)
        
        return JSONResponse(content={
            'stats': stats,
            'summary': summary,
            'multi_outcome_opportunities': [o.to_dict() for o in arbitrage_scanner._opportunities[:5]],
            'resolution_opportunities': [o.to_dict() for o in arbitrage_scanner._resolution_opps[:5]],
            'time_decay_opportunities': [o.to_dict() for o in arbitrage_scanner._time_decay_opps[:5]],
            'correlated_pairs': [o.to_dict() for o in arbitrage_scanner._correlated_pairs[:5]],
            'best_opportunities': best_opps,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/signals")
async def get_signals():
    """Get insider activity and sports mispricing signals."""
    try:
        from advanced_strategies import advanced_scanner
        
        # Trigger scan
        advanced_scanner.scan_insider_activity()
        advanced_scanner.scan_sports_mispricing()
        
        # Get data from advanced_scanner
        insider_data = advanced_scanner.get_dashboard_data().get('insider', [])
        sports_data = advanced_scanner.get_dashboard_data().get('sports', [])
        
        return JSONResponse(content={
            'stats': {
                'insider_signals': len(advanced_scanner.insider_signals),
                'sports_mispricings': len(advanced_scanner.sports_mispricings),
            },
            'insider_signals': insider_data,
            'sports_mispricings': sports_data,
        })
    except Exception as e:
        return JSONResponse(content={'error': str(e)}, status_code=500)


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Simple HTML dashboard."""
    status = bot_state.to_dict()
    
    # Get simulation stats
    try:
        from simulation_tracker import simulation_tracker
        sim_stats = simulation_tracker.get_stats()
        sim_trades = simulation_tracker.get_open_trades()
    except:
        sim_stats = {'total_pnl': 0, 'total_invested': 0, 'win_rate': 0, 'total_trades': 0}
        sim_trades = []
    
    # Get arbitrage opportunities
    try:
        from arbitrage_scanner import arbitrage_scanner
        arb_stats = arbitrage_scanner.get_stats()
        arb_opps = arbitrage_scanner._opportunities[:3]
        resolution_opps = arbitrage_scanner._resolution_opps[:3]
        time_decay_opps = arbitrage_scanner._time_decay_opps[:3]
        correlated_pairs = arbitrage_scanner._correlated_pairs[:3]
    except:
        arb_stats = {'multi_outcome_opportunities': 0, 'resolution_opportunities': 0, 
                    'time_decay_opportunities': 0, 'correlated_pairs': 0}
        arb_opps = []
        resolution_opps = []
        time_decay_opps = []
        correlated_pairs = []
    
    # Get signal detection data from advanced_strategies
    try:
        from advanced_strategies import advanced_scanner
        # Run a quick scan if needed
        if not advanced_scanner.insider_signals:
            advanced_scanner.scan_insider_activity()
        if not advanced_scanner.sports_mispricings:
            advanced_scanner.scan_sports_mispricing()
        
        insider_signals = advanced_scanner.insider_signals[:5]
        sports_mispricings = advanced_scanner.sports_mispricings[:5]
        signal_stats = {
            'insider_signals': len(advanced_scanner.insider_signals),
            'sports_mispricings': len(advanced_scanner.sports_mispricings)
        }
    except Exception as e:
        log.debug(f"Signal detection error: {e}")
        signal_stats = {'insider_signals': 0, 'sports_mispricings': 0}
        insider_signals = []
        sports_mispricings = []
    
    opportunities_html = ""
    for opp in bot_state.last_opportunities[:5]:
        opportunities_html += f"""
        <tr>
            <td>{opp.get('question', 'N/A')[:50]}...</td>
            <td>{opp.get('score', 0)}</td>
            <td>${opp.get('yes', 0):.4f}</td>
            <td>{opp.get('spread', 0)*100:.1f}%</td>
        </tr>
        """
    
    # Build arbitrage opportunities HTML
    arb_html = ""
    for arb in arb_opps:
        arb_html += f"""
        <tr>
            <td>{arb.market_title[:40]}...</td>
            <td>{arb.arbitrage_type}</td>
            <td>{arb.total_price:.1%}</td>
            <td style="color: #3fb950">{arb.profit_pct:.2f}%</td>
            <td>{len(arb.outcomes)}</td>
        </tr>
        """
    
    resolution_html = ""
    for res in resolution_opps:
        resolution_html += f"""
        <tr>
            <td>{res.market_title[:40]}...</td>
            <td>{res.current_price:.2%} → {res.expected_price:.2%}</td>
            <td style="color: #3fb950">{res.profit_pct:.1f}%</td>
            <td>{res.resolution_status}</td>
        </tr>
        """
    
    time_decay_html = ""
    for td in time_decay_opps:
        risk_color = '#3fb950' if td.risk_level == 'LOW' else '#f0883e' if td.risk_level == 'MEDIUM' else '#f85149'
        time_decay_html += f"""
        <tr>
            <td>{td.market_title[:40]}...</td>
            <td>{td.current_price:.2%}</td>
            <td>{td.days_to_expiry:.1f}d</td>
            <td style="color: #58a6ff">{td.daily_theta:.2f}%</td>
            <td style="color: {risk_color}">{td.risk_level}</td>
        </tr>
        """
    
    correlated_html = ""
    for pair in correlated_pairs:
        correlated_html += f"""
        <tr>
            <td>{pair.market_a_title[:25]}...</td>
            <td>{pair.market_a_price:.2%}</td>
            <td>{pair.market_b_title[:25]}...</td>
            <td>{pair.market_b_price:.2%}</td>
            <td style="color: #a371f7">{pair.mispricing_pct:.1f}%</td>
        </tr>
        """
    
    # Build insider signals HTML (from advanced_strategies)
    insider_html = ""
    for sig in insider_signals:
        action_color = '#3fb950' if sig.suggested_action == 'BUY' else '#f85149' if sig.suggested_action == 'SELL' else '#8b949e'
        insider_html += f"""
        <tr>
            <td>{sig.market_title[:35]}...</td>
            <td>{sig.signal_type}</td>
            <td>{sig.volume_ratio:.1f}x</td>
            <td>{sig.price_change*100:+.1f}%</td>
            <td style="color: {action_color}">{sig.suggested_action}</td>
        </tr>
        """
    
    # Build sports mispricing HTML (from advanced_strategies)
    sports_html = ""
    for mp in sports_mispricings:
        sports_html += f"""
        <tr>
            <td>{mp.league}</td>
            <td style="color: #f85149">{mp.team_overvalued[:15]}</td>
            <td style="color: #3fb950">{mp.team_undervalued[:15]}</td>
            <td>{mp.edge_pct:.1f}%</td>
            <td>{mp.bias_type}</td>
        </tr>
        """
    
    sim_trades_html = ""
    for trade in sim_trades[-5:]:
        sim_trades_html += f"""
        <tr>
            <td>{trade.market[:40]}...</td>
            <td>${trade.entry_price:.4f}</td>
            <td>${trade.amount_usd:.2f}</td>
            <td>{trade.timestamp[:16]}</td>
        </tr>
        """
    
    # Calculate daily ROI - only meaningful after 1+ hours
    uptime_hours = status.get('uptime_seconds', 0) / 3600
    total_roi = sim_stats.get('pnl_pct', 0)
    
    # Only calculate daily ROI if we have at least 1 hour of data
    if uptime_hours >= 1.0:
        uptime_days = uptime_hours / 24
        daily_roi = total_roi / uptime_days
        daily_roi_display = f"{daily_roi:+.2f}%"
    else:
        daily_roi = 0
        minutes_left = int((1.0 - uptime_hours) * 60)
        daily_roi_display = f"~{minutes_left}m"  # Show time until we have enough data
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Polymarket Bot Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                   background: #0d1117; color: #c9d1d9; padding: 20px; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            h1 {{ color: #58a6ff; }}
            .card {{ background: #161b22; border-radius: 8px; padding: 20px; margin: 10px 0; 
                    border: 1px solid #30363d; }}
            .stat {{ display: inline-block; margin: 10px 20px; text-align: center; }}
            .stat-value {{ font-size: 2em; color: #58a6ff; }}
            .stat-label {{ color: #8b949e; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #30363d; }}
            th {{ color: #58a6ff; }}
            .status-badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.9em; }}
            .status-running {{ background: #238636; }}
            .status-stopped {{ background: #da3633; }}
            .dry-run {{ background: #9e6a03; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Polymarket Bot Dashboard</h1>
            
            <div class="card">
                <span class="status-badge {'status-running' if status['is_running'] else 'status-stopped'}">
                    {'🟢 Running' if status['is_running'] else '🔴 Stopped'}
                </span>
                {'<span class="status-badge dry-run">🧪 Dry Run Mode</span>' if status['config']['dry_run'] else ''}
                {'<span class="status-badge status-running">⚡ Auto-Trade ON</span>' if status['config']['auto_trade_enabled'] else ''}
                
                <div style="margin-top: 20px;">
                    <div class="stat">
                        <div class="stat-value">{status['scan_count']}</div>
                        <div class="stat-label">Scans</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{status['opportunities_found']}</div>
                        <div class="stat-label">Opportunities</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{status['trades_executed']}</div>
                        <div class="stat-label">Trades</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{status['alerts_sent']}</div>
                        <div class="stat-label">Alerts</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">${status['daily_pnl']:.2f}</div>
                        <div class="stat-label">Daily P&L</div>
                    </div>
                </div>
            </div>
            
            <div class="card">
                <h2>📊 Latest Opportunities</h2>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Score</th>
                        <th>Price</th>
                        <th>Spread</th>
                    </tr>
                    {opportunities_html if opportunities_html else '<tr><td colspan="4">No opportunities yet</td></tr>'}
                </table>
            </div>
            
            <div class="card">
                <h2>🎯 Arbitrage Opportunities</h2>
                <div style="display: flex; gap: 10px; margin-bottom: 15px; flex-wrap: wrap;">
                    <div class="stat">
                        <div class="stat-value" style="color: #f0883e">{arb_stats.get('multi_outcome_opportunities', 0)}</div>
                        <div class="stat-label">Multi-Outcome</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: #a371f7">{arb_stats.get('resolution_opportunities', 0)}</div>
                        <div class="stat-label">Resolution</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: #58a6ff">{arb_stats.get('time_decay_opportunities', 0)}</div>
                        <div class="stat-label">Time Decay</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: #3fb950">{arb_stats.get('correlated_pairs', 0)}</div>
                        <div class="stat-label">Correlated</div>
                    </div>
                </div>
                
                <h3>⏰ Time Decay (Theta Positive)</h3>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Price</th>
                        <th>Expires</th>
                        <th>θ/day</th>
                        <th>Risk</th>
                    </tr>
                    {time_decay_html if time_decay_html else '<tr><td colspan="5">Scanning...</td></tr>'}
                </table>
                
                <h3 style="margin-top: 20px;">📈 Resolution Arbitrage</h3>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Price Change</th>
                        <th>Profit</th>
                        <th>Status</th>
                    </tr>
                    {resolution_html if resolution_html else '<tr><td colspan="4">Scanning...</td></tr>'}
                </table>
                
                <h3 style="margin-top: 20px;">🔗 Correlated Markets (Mispriced Pairs)</h3>
                <table>
                    <tr>
                        <th>Market A</th>
                        <th>Price</th>
                        <th>Market B</th>
                        <th>Price</th>
                        <th>Gap</th>
                    </tr>
                    {correlated_html if correlated_html else '<tr><td colspan="5">Scanning...</td></tr>'}
                </table>
                
                <h3 style="margin-top: 20px;">💰 Multi-Outcome Arbitrage</h3>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Type</th>
                        <th>Sum</th>
                        <th>Profit</th>
                        <th>Outcomes</th>
                    </tr>
                    {arb_html if arb_html else '<tr><td colspan="5">Scanning...</td></tr>'}
                </table>
            </div>
            
            <div class="card">
                <h2>🔍 Signal Detection</h2>
                <div style="display: flex; gap: 10px; margin-bottom: 15px;">
                    <div class="stat">
                        <div class="stat-value" style="color: #f0883e">{signal_stats.get('insider_signals', 0)}</div>
                        <div class="stat-label">Insider Signals</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: #58a6ff">{signal_stats.get('sports_mispricings', 0)}</div>
                        <div class="stat-label">Sports Edge</div>
                    </div>
                </div>
                
                <h3>🕵️ Insider Activity (Unusual Volume)</h3>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Signal</th>
                        <th>Volume</th>
                        <th>Δ Price</th>
                        <th>Action</th>
                    </tr>
                    {insider_html if insider_html else '<tr><td colspan="5">Scanning...</td></tr>'}
                </table>
                
                <h3 style="margin-top: 20px;">🏀 Sports Mispricing (Fan Bias)</h3>
                <table>
                    <tr>
                        <th>League</th>
                        <th>Overvalued</th>
                        <th>Undervalued</th>
                        <th>Edge</th>
                        <th>Bias</th>
                    </tr>
                    {sports_html if sports_html else '<tr><td colspan="5">Scanning...</td></tr>'}
                </table>
            </div>
            
            <div class="card">
                <h2>🧪 Simulation Results</h2>
                <div style="margin: 15px 0;">
                    <div class="stat">
                        <div class="stat-value">${sim_stats.get('total_invested', 0):.2f}</div>
                        <div class="stat-label">Invested</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: {'#3fb950' if sim_stats.get('realized_pnl', 0) >= 0 else '#f85149'}">
                            ${sim_stats.get('realized_pnl', 0):+.2f}
                        </div>
                        <div class="stat-label">Realized P&L</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: #8b949e">
                            ${sim_stats.get('unrealized_pnl', 0):+.2f}
                        </div>
                        <div class="stat-label">Unrealized</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: {'#3fb950' if sim_stats.get('pnl_pct', 0) >= 0 else '#f85149'}">
                            {sim_stats.get('pnl_pct', 0):+.1f}%
                        </div>
                        <div class="stat-label">Total ROI</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value" style="color: {'#58a6ff' if uptime_hours >= 1 else '#8b949e'}">
                            {daily_roi_display}
                        </div>
                        <div class="stat-label">Daily ROI</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{sim_stats.get('total_trades', 0)}</div>
                        <div class="stat-label">Total</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{sim_stats.get('open_positions', 0)}</div>
                        <div class="stat-label">Open</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{sim_stats.get('closed_trades', 0)}</div>
                        <div class="stat-label">Closed</div>
                    </div>
                    <div class="stat">
                        <div class="stat-value">{sim_stats.get('win_rate', 0):.0f}%</div>
                        <div class="stat-label">Win Rate</div>
                    </div>
                </div>
                
                <h3 style="margin-top: 20px;">Open Positions</h3>
                <table>
                    <tr>
                        <th>Market</th>
                        <th>Entry</th>
                        <th>Amount</th>
                        <th>Time</th>
                    </tr>
                    {sim_trades_html if sim_trades_html else '<tr><td colspan="4">No open positions</td></tr>'}
                </table>
            </div>
            
            <div class="card">
                <h2>⚙️ Configuration</h2>
                <p>Min Score to Trade: <strong>{status['config']['min_score']}</strong></p>
                <p>Scan Interval: <strong>{status['config']['scan_interval']}s</strong></p>
                <p>Last Scan: <strong>{status['last_scan'] or 'Never'}</strong></p>
            </div>
        </div>
    </body>
    </html>
    """
    return html


async def run_server():
    """Run the HTTP server."""
    import uvicorn
    config_server = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level="warning"
    )
    server = uvicorn.Server(config_server)
    await server.serve()
