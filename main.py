"""
Polymarket Trading Bot - Main Entry Point

This bot scans Polymarket for arbitrage opportunities by:
1. Comparing prices with Kalshi
2. Analyzing news sentiment
3. Tracking whale/insider activity
4. Optionally executing trades automatically

Author: Refactored with Claude
Version: 2.0.0
"""
import asyncio
import signal
import sys
from datetime import datetime
from typing import Optional

from config import config
from logger import log
from scanner import get_top_markets
from sentiment_analyzer import SentimentAnalyzer
from insider_tracker import InsiderTracker
from trader import trader, TradeResult
from alerter import alerter
from server import app, bot_state, run_server

# New modules
from platforms import multi_scanner
from momentum_tracker import momentum_tracker
from whale_tracker import whale_tracker
from events_tracker import events_tracker
from social_sentiment import social_analyzer
from trade_resolver import trade_resolver
from simulation_tracker import simulation_tracker
from risk_manager import risk_manager
from onchain_tracker import onchain_tracker
from orderbook_analyzer import orderbook_analyzer


class PolymarketBot:
    """
    Main bot orchestrator that coordinates all modules.
    """
    
    def __init__(self):
        self.sentiment_analyzer = SentimentAnalyzer()
        self.insider_tracker = InsiderTracker()
        self.running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._server_task: Optional[asyncio.Task] = None
    
    async def scan_cycle(self):
        """
        Execute one scan cycle: fetch markets, analyze, and act.
        """
        log.info("Starting scan cycle...")
        
        try:
            # 0. Check if trading is allowed (drawdown protection)
            can_trade, pause_reason = risk_manager.can_trade()
            if not can_trade:
                log.warning(f"[RISK] Trading paused: {pause_reason}")
                # Still scan for monitoring, but don't trade
            
            # 1. Fetch and analyze markets from Polymarket
            markets = get_top_markets(
                limit=30,  # Get more, we'll filter
                sentiment_analyzer=self.sentiment_analyzer
            )
            
            if not markets:
                log.warning("No markets returned from scanner")
                return
            
            # 1.5 Apply quality filters
            markets = risk_manager.get_filtered_markets(markets)
            
            # Update dashboard state
            bot_state.record_scan(markets)
            
            log.info(f"Analyzed {len(markets)} markets")
            
            # 2. Run momentum tracking on all markets
            momentum_signals = momentum_tracker.bulk_analyze(markets)
            if momentum_signals:
                log.info(f"Detected {len(momentum_signals)} momentum signals")
            
            # 3. Check for cross-platform arbitrage
            try:
                arbitrage_opps = multi_scanner.find_arbitrage_opportunities(min_spread=0.03)
                if arbitrage_opps:
                    log.info(f"Found {len(arbitrage_opps)} cross-platform arbitrage opportunities")
                    for opp in arbitrage_opps[:3]:
                        log.info(f"  [{opp['spread_pct']:.1f}%] {opp['title'][:40]} | Buy {opp['buy_on']} @ {opp['buy_price']:.2f}, Sell {opp['sell_on']} @ {opp['sell_price']:.2f}")
            except Exception as e:
                log.debug(f"Error in arbitrage scan: {e}")
            
            # 4. Check for event-driven opportunities
            try:
                events_tracker.fetch_events_from_news()
                event_alerts = events_tracker.check_for_alerts(markets)
                if event_alerts:
                    log.info(f"Found {len(event_alerts)} event-related alerts")
            except Exception as e:
                log.debug(f"Error in events scan: {e}")
            
            # 4.5 Analyze order books for top markets
            try:
                ob_signals = orderbook_analyzer.scan_markets(markets[:5])
                if ob_signals:
                    log.info(f"Order book signals for {len(ob_signals)} markets")
                    for sig in ob_signals[:3]:
                        signal = sig.get('signal', {})
                        if signal.get('action') not in ['HOLD', 'AVOID']:
                            log.info(f"  [ORDERBOOK] {sig['market'][:30]} | {signal['action']} (conf: {signal['confidence']}%)")
            except Exception as e:
                log.debug(f"Error in orderbook analysis: {e}")
            
            # 5. Process high-score opportunities
            for market in markets:
                score = market.get('score', 0)
                
                # Boost score based on other signals
                if any(s.market_title == market.get('question') for s in momentum_signals):
                    score += 10
                    market['has_momentum'] = True
                
                if score >= config.trading.min_score_to_trade:
                    log.info(f"Opportunity detected: Score={score} | {market['question'][:50]}")
                    
                    # Enrich with social sentiment
                    try:
                        social_signal = social_analyzer.analyze_market(market)
                        market['social_sentiment'] = social_signal.sentiment_score
                        market['social_buzz'] = social_signal.buzz_score
                    except Exception as e:
                        log.debug(f"Social analysis error: {e}")
                    
                    # Attempt trade if enabled
                    trade_result: Optional[TradeResult] = None
                    
                    if config.trading.auto_trade_enabled:
                        trade_result = trader.execute_trade(market)
                        
                        if trade_result and trade_result.success:
                            bot_state.record_trade()
                            side_str = trade_result.side.value if trade_result.side else "UNKNOWN"
                            log.info(f"Trade executed: {side_str} ${trade_result.amount:.2f}")
                    
                    # Send alert for high-value opportunities
                    if score >= 80 or (trade_result and trade_result.success):
                        if alerter.alert_opportunity(market, trade_result):
                            bot_state.record_alert()
            
            # 6. Update whale tracking (leaderboard + on-chain)
            whale_tracker.fetch_top_whales()
            onchain_tracker.fetch_top_whales()
            
            # Check for new whale transactions on-chain
            try:
                new_txs = onchain_tracker.check_all_whale_activity()
                if new_txs:
                    log.info(f"Detected {len(new_txs)} new whale transactions on-chain")
                    for tx in new_txs[:3]:
                        log.info(f"  [ON-CHAIN] {tx.whale_name}: {tx.action} ${tx.amount_usd:.0f}")
                
                # Get copy trade signals
                whale_signals = onchain_tracker.get_copy_trade_signals(min_profit=10000)
                if whale_signals:
                    log.info(f"Found {len(whale_signals)} whale copy-trade signals")
                    for signal in whale_signals[:3]:
                        urgency_tag = f"[{signal['urgency']}]" if signal['urgency'] == 'HIGH' else ""
                        log.info(f"  [WHALE]{urgency_tag} {signal['whale']}: {signal['action']} (conf: {signal['confidence']:.0f}%)")
            except Exception as e:
                log.debug(f"Error getting whale signals: {e}")
            
            # 7. Check for trade resolutions and swing exits (in dry run mode)
            if config.trading.dry_run:
                try:
                    resolution_results = trade_resolver.resolve_trades(simulation_tracker)
                    
                    if resolution_results['resolved']:
                        log.info(f"Resolved {len(resolution_results['resolved'])} trades")
                    if resolution_results['swing_exits']:
                        log.info(f"Swing exited {len(resolution_results['swing_exits'])} trades")
                    
                    # Update risk manager with current P&L
                    sim_stats = simulation_tracker.get_stats()
                    risk_manager.update_balance(
                        sim_stats.get('total_invested', 0) + sim_stats.get('total_pnl', 0)
                    )
                    risk_manager.record_daily_pnl(
                        pnl=sim_stats.get('realized_pnl', 0),
                        pnl_pct=sim_stats.get('pnl_pct', 0) / 100,
                        trades=sim_stats.get('total_trades', 0),
                        wins=sim_stats.get('wins', 0),
                        losses=sim_stats.get('losses', 0),
                    )
                except Exception as e:
                    log.debug(f"Error in trade resolution: {e}")
            
            log.info("Scan cycle completed")
            
        except Exception as e:
            log.error(f"Error in scan cycle: {e}")
            bot_state.record_error(str(e), "scan_cycle")
    
    async def scan_loop(self):
        """
        Main scanning loop that runs continuously.
        """
        # Initial insider fetch
        self.insider_tracker.fetch_top_traders()
        
        while self.running:
            try:
                await self.scan_cycle()
                
                log.info(f"Next scan in {config.scan_interval} seconds...")
                await asyncio.sleep(config.scan_interval)
                
            except asyncio.CancelledError:
                log.info("Scan loop cancelled")
                break
            except Exception as e:
                log.error(f"Error in scan loop: {e}")
                bot_state.record_error(str(e), "scan_loop")
                await asyncio.sleep(60)  # Wait before retry
    
    async def start(self):
        """
        Start the bot with all components.
        """
        self.running = True
        bot_state.is_running = True
        
        log.info("=" * 60)
        log.info("POLYMARKET TRADING BOT v2.0")
        log.info("=" * 60)
        log.info(f"Auto-trade: {'ENABLED' if config.trading.auto_trade_enabled else 'DISABLED'}")
        log.info(f"Dry run: {'YES' if config.trading.dry_run else 'NO'}")
        log.info(f"Min score to trade: {config.trading.min_score_to_trade}")
        log.info(f"Scan interval: {config.scan_interval}s")
        log.info(f"Trader ready: {'YES' if trader.is_ready else 'NO'}")
        log.info("=" * 60)
        
        # Start HTTP server and scan loop concurrently
        self._server_task = asyncio.create_task(run_server())
        self._scan_task = asyncio.create_task(self.scan_loop())
        
        # Wait for both tasks
        try:
            await asyncio.gather(self._server_task, self._scan_task)
        except asyncio.CancelledError:
            log.info("Bot tasks cancelled")
    
    async def stop(self):
        """
        Gracefully stop the bot.
        """
        log.info("Stopping bot...")
        self.running = False
        bot_state.is_running = False
        
        if self._scan_task:
            self._scan_task.cancel()
        if self._server_task:
            self._server_task.cancel()
        
        log.info("Bot stopped")


# Global bot instance
bot = PolymarketBot()


def handle_shutdown(signum, frame):
    """Handle shutdown signals."""
    log.info(f"Received signal {signum}, initiating shutdown...")
    asyncio.create_task(bot.stop())


async def main():
    """Main entry point."""
    # Setup signal handlers
    if sys.platform != 'win32':
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received")
        await bot.stop()
    except Exception as e:
        log.error(f"Fatal error: {e}")
        await bot.stop()
        raise


if __name__ == "__main__":
    asyncio.run(main())
