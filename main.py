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
from arbitrage_scanner import arbitrage_scanner
from signal_detector import signal_detector
from market_health import market_health


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
                limit=75,  # Analyze top 75 markets (fetches 200 from API)
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
            
            # 3. Check for cross-platform arbitrage (REAL arbitrage)
            # Only trade when Polymarket has the LOWEST price (we can actually buy there)
            try:
                # Get tradeable opportunities where Polymarket is cheapest
                arb_trades = multi_scanner.get_polymarket_arbitrage_trades(
                    min_spread=0.05,  # Minimum 5% spread for real arb
                    min_confidence=75
                )
                
                if arb_trades:
                    log.info(f"[CROSS-PLATFORM ARB] {len(arb_trades)} opportunities where Polymarket is cheapest")
                    
                    # Execute trades on Polymarket
                    if can_trade and config.trading.auto_trade_enabled:
                        for arb_opp in arb_trades[:3]:  # Max 3 arb trades per cycle
                            # Format for trader
                            arb_market = {
                                'question': arb_opp['question'],
                                'slug': arb_opp['slug'],
                                'yes': arb_opp['yes'],
                                'no': arb_opp['no'],
                                'score': arb_opp['score'],
                                'spread': arb_opp['spread'],
                                'category': 'cross_platform_arb',
                                'suggested_side': 'YES',
                                'strategy': 'CROSS_PLATFORM_ARB',
                                'reason': arb_opp['reason'],
                            }
                            
                            try:
                                trade_result = trader.execute_trade(arb_market)
                                if trade_result and trade_result.success:
                                    bot_state.record_trade()
                                    log.info(f"[ARB TRADE] Cross-platform: BUY YES | {arb_opp['question'][:35]} | "
                                            f"PM: {arb_opp['yes']:.2%} vs {arb_opp['reference_platform']}: {arb_opp['reference_price']:.2%}")
                            except Exception as te:
                                log.debug(f"Error executing cross-platform arb: {te}")
                    else:
                        for opp in arb_trades[:3]:
                            log.info(f"  {opp['question'][:40]} | {opp['spread_pct']:.1f}% spread vs {opp['reference_platform']}")
            except Exception as e:
                log.debug(f"Error in cross-platform arbitrage scan: {e}")
            
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
            
            # 4.6 Scan for all arbitrage opportunities
            try:
                arb_summary = arbitrage_scanner.scan_all()
                
                if arb_summary['total'] > 0:
                    log.info(f"[ARBITRAGE SCAN] Found {arb_summary['total']} total opportunities:")
                    log.info(f"  Multi-Outcome: {arb_summary['multi_outcome']} | "
                            f"Resolution: {arb_summary['resolution']} | "
                            f"Time Decay: {arb_summary['time_decay']} | "
                            f"Correlated: {arb_summary['correlated']}")
                    
                    # Log best opportunities
                    for best in arb_summary['best_opportunities'][:3]:
                        opp_type = best['type']
                        profit = best['profit_pct']
                        conf = best['confidence']
                        name = best['data'].get('market', '')[:35]
                        
                        if opp_type == 'TIME_DECAY':
                            daily = best.get('daily_return', 0)
                            log.info(f"  [{opp_type}] {name} | {profit:.1f}% total ({daily:.2f}%/day) | Conf: {conf}%")
                        else:
                            log.info(f"  [{opp_type}] {name} | {profit:.1f}% profit | Conf: {conf}%")
                    
                    # AUTO-TRADE: DISABLED - "Resolution Arbitrage" is NOT real arbitrage
                    # It's speculation disguised as arbitrage and loses money
                    if False and config.trading.auto_trade_enabled:
                        for res_opp in arbitrage_scanner._resolution_opps[:5]:
                            # Only trade if confidence >= 75 and profit >= 5%
                            if res_opp.confidence >= 75 and res_opp.profit_pct >= 5.0:
                                # Determine the correct outcome to buy
                                # expected_price > 0.5 means YES will win → buy YES
                                # expected_price <= 0.5 means NO will win → buy NO
                                should_buy_yes = res_opp.expected_price > 0.5
                                
                                # Convert resolution opportunity to market dict format
                                arb_market = {
                                    'question': res_opp.market_title,
                                    'slug': res_opp.market_slug,
                                    'condition_id': res_opp.market_id,
                                    'token_id': res_opp.token_id,
                                    'yes': res_opp.current_price,  # Always pass actual YES price
                                    'no': 1 - res_opp.current_price,  # NO = 1 - YES
                                    'score': 90 + int(res_opp.profit_pct),  # High score for arb
                                    'spread': res_opp.profit_pct / 100,
                                    'category': 'arbitrage',
                                    'end_date': res_opp.end_date,
                                    'forced_outcome': 'YES' if should_buy_yes else 'NO',  # Force correct outcome
                                }
                                
                                try:
                                    trade_result = trader.execute_trade(arb_market)
                                    if trade_result and trade_result.success:
                                        bot_state.record_trade()
                                        outcome = 'YES' if should_buy_yes else 'NO'
                                        log.info(f"[ARB TRADE] Resolution arb: BUY {outcome} | {res_opp.market_title[:35]} | "
                                                f"Profit: {res_opp.profit_pct:.1f}%")
                                except Exception as te:
                                    log.debug(f"Error executing arb trade: {te}")
            except Exception as e:
                log.debug(f"Error in arbitrage scan: {e}")
            
            # 4.7 Detect insider activity and sports mispricing
            try:
                signal_summary = signal_detector.scan_all()
                
                if signal_summary['total_signals'] > 0:
                    log.info(f"[SIGNAL DETECTION] Found {signal_summary['total_signals']} signals:")
                    log.info(f"  Insider: {signal_summary['insider_signals']} | "
                            f"Sports: {signal_summary['sports_mispricings']}")
                    
                    # Log actionable signals
                    actionable = signal_detector.get_actionable_signals(min_confidence=65)
                    for sig in actionable[:3]:
                        sig_type = sig['type']
                        action = sig['action']
                        conf = sig['confidence']
                        data = sig['data']
                        
                        if sig_type == 'INSIDER':
                            log.info(f"  [INSIDER] {action}: {data['market'][:35]} | "
                                    f"Vol: {data['volume_spike']}x | Conf: {conf}%")
                        else:
                            log.info(f"  [SPORTS] {data['sport']}: Bet {data['undervalued']} vs {data['overvalued']} | "
                                    f"Edge: {data['edge']:.1f}% | Conf: {conf}%")
                    
                    # AUTO-TRADE: Execute trades on high-confidence INSIDER ACCUMULATION signals
                    if can_trade and config.trading.auto_trade_enabled:
                        insider_signals = signal_detector._insider_signals
                        
                        # Words that indicate meme/absurd markets - skip these
                        MEME_KEYWORDS = [
                            'jesus', 'christ', 'god', 'alien', 'ufo', 'zombie', 'vampire',
                            'bigfoot', 'loch ness', 'flat earth', 'illuminati', 'reptilian',
                            'time travel', 'teleport', 'immortal', 'resurrect', 'rapture',
                            'gta vi', 'gta 6', 'before gta',  # GTA meme markets
                        ]
                        
                        for ins_sig in insider_signals[:2]:  # Max 2 insider trades per cycle
                            # Only trade ACCUMULATION signals (someone quietly buying)
                            if ins_sig.signal_type != 'ACCUMULATION':
                                continue
                            
                            # Skip meme/absurd markets
                            title_lower = ins_sig.market_title.lower()
                            if any(kw in title_lower for kw in MEME_KEYWORDS):
                                log.debug(f"Skipping meme market: {ins_sig.market_title[:40]}")
                                continue
                            
                            # Require high confidence
                            if ins_sig.confidence < 75:
                                continue
                            
                            # Require reasonable price range (15% - 50%) - tighter range
                            if ins_sig.current_price < 0.15 or ins_sig.current_price > 0.50:
                                continue
                            
                            # Require significant volume spike (at least 4x for insider)
                            if ins_sig.volume_spike_ratio < 4.0:
                                continue
                            
                            # Require minimum absolute volume ($10k+)
                            if ins_sig.volume_24h < 10000:
                                continue
                            
                            # Build market dict for trader
                            insider_market = {
                                'question': ins_sig.market_title,
                                'slug': ins_sig.market_slug,
                                'yes': ins_sig.current_price,
                                'no': 1 - ins_sig.current_price,
                                'score': 85 + int(ins_sig.volume_spike_ratio),  # Base 85 + volume bonus
                                'spread': 0.02,
                                'category': 'insider_signal',
                                'suggested_side': 'YES',  # Accumulation = expect price to go up
                                'strategy': 'INSIDER_ACCUMULATION',
                                'volume_spike': ins_sig.volume_spike_ratio,
                            }
                            
                            try:
                                trade_result = trader.execute_trade(insider_market)
                                if trade_result and trade_result.success:
                                    bot_state.record_trade()
                                    log.info(f"[INSIDER TRADE] Accumulation: BUY YES | {ins_sig.market_title[:35]} | "
                                            f"Vol: {ins_sig.volume_spike_ratio:.1f}x | Price: {ins_sig.current_price:.0%}")
                            except Exception as te:
                                log.debug(f"Error executing insider trade: {te}")
            except Exception as e:
                log.debug(f"Error in signal detection: {e}")
            
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
                        # Mark as SWING trade - we exit by price (time-decay TP), not resolution
                        # This bypasses expiry filter since we'll be out before then
                        if 'strategy' not in market:
                            market['strategy'] = 'SWING'
                        
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
                    # Use actual simulation balance, not theoretical starting_balance
                    sim_stats = simulation_tracker.get_stats()
                    current_balance = sim_stats.get('total_invested', 0) + sim_stats.get('total_pnl', 0)
                    
                    # Sync peak_balance with simulation reality (first time or reset)
                    if risk_manager.state.peak_balance > current_balance * 2:
                        log.info(f"[RISK] Syncing peak to simulation balance: {current_balance:.2f}")
                        risk_manager.state.peak_balance = current_balance
                        risk_manager.state.current_drawdown_pct = 0.0
                        risk_manager.state.is_trading_allowed = True
                        risk_manager.state.pause_reason = None
                    
                    risk_manager.update_balance(current_balance)
                    risk_manager.record_daily_pnl(
                        pnl=sim_stats.get('realized_pnl', 0),
                        pnl_pct=sim_stats.get('pnl_pct', 0) / 100,
                        trades=sim_stats.get('total_trades', 0),
                        wins=sim_stats.get('wins', 0),
                        losses=sim_stats.get('losses', 0),
                    )
                except Exception as e:
                    log.debug(f"Error in trade resolution: {e}")
            
            # 8. Update Market Health Monitor
            try:
                # Convert trades to dict format for health monitor
                trades_for_health = [
                    {
                        'timestamp': t.timestamp,
                        'pnl_pct': t.pnl_pct,
                        'status': t.status,
                        'spread': getattr(t, 'spread', 0),
                        'exit_time': getattr(t, 'exit_time', None),
                    }
                    for t in simulation_tracker.trades
                ]
                
                # Calculate health metrics
                health_metrics = market_health.calculate_metrics(trades_for_health)
                
                # Log health status (avoid emoji for Windows encoding)
                status_symbols = {"HEALTHY": "[OK]", "CAUTION": "[!]", "WARNING": "[!!]", "CRITICAL": "[X]"}
                symbol = status_symbols.get(health_metrics.status, "[?]")
                log.info(f"[MARKET HEALTH] {symbol} {health_metrics.status} (Score: {health_metrics.health_score}/100)")
                log.info(f"  ROI: {health_metrics.avg_roi_pct:+.1f}% | Win Rate: {health_metrics.win_rate*100:.0f}% | "
                        f"Trades: {health_metrics.trades_analyzed}")
                
                # Log adjustments if not healthy
                adj = market_health.get_adjustments()
                if health_metrics.status != "HEALTHY":
                    log.warning(f"[HEALTH ADJ] Position size: {adj.position_size_multiplier:.0%} | "
                               f"Min score: {adj.min_score_threshold} | Max trades: {adj.max_concurrent_trades}")
                    log.warning(f"[HEALTH] {adj.reason}")
                
                # Check if we should alert
                should_alert, alert_msg = market_health.should_alert()
                if should_alert and alert_msg:
                    alerter.send_raw_message(alert_msg)
                    
            except Exception as e:
                log.debug(f"Error updating market health: {e}")
            
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
