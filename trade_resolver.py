"""
Trade Resolver - Automatically resolves trades and calculates P&L.
Also handles swing trading (exit positions for profit before resolution).
VERSION: v22 (production working version)
"""
import requests
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass

from logger import log
from config import config


@dataclass
class MarketResolution:
    """Information about a resolved market."""
    slug: str
    question: str
    resolved: bool
    winning_outcome: Optional[str]  # "YES" or "NO" or None
    resolution_price: float  # 1.0 for YES win, 0.0 for NO win
    resolved_at: Optional[datetime]


@dataclass 
class SwingOpportunity:
    """An opportunity to exit a position for profit."""
    trade_id: str
    market: str
    entry_price: float
    current_price: float
    profit_pct: float
    action: str  # "SELL" or "HOLD"
    reason: str


class TradeResolver:
    """
    Handles automatic resolution of trades and swing trading exits.
    """
    
    POLYMARKET_API = "https://gamma-api.polymarket.com/markets"
    
    # Dynamic swing trading thresholds based on entry price AND time held
    # Take profit DECREASES over time to avoid stuck capital
    # Initial targets are optimistic, then we accept smaller gains
    THRESHOLDS = {
        'low': {      # Entry < 0.30
            'take_profit': 0.50,  # +50% initial target
            'stop_loss': -0.20,   # -20%
        },
        'medium': {   # Entry 0.30 - 0.70
            'take_profit': 0.25,  # +25% initial
            'stop_loss': -0.15,   # -15%
        },
        'high': {     # Entry > 0.70
            'take_profit': 0.10,  # +10% initial
            'stop_loss': -0.10,   # -10%
        }
    }
    
    # Time-based take profit decay (hours -> multiplier)
    # After X hours, accept lower take profit
    TIME_DECAY_SCHEDULE = {
        0: 1.0,      # 0-12h: 100% of target (e.g., +45%)
        12: 0.75,    # 12-24h: 75% of target (e.g., +33%)
        24: 0.50,    # 24-48h: 50% of target (e.g., +22%)
        48: 0.25,    # 48-72h: 25% of target (e.g., +11%)
        72: 0.10,    # >72h: 10% of target (e.g., +4.5%) - just get out
    }
    
    def _get_thresholds(self, entry_price: float) -> dict:
        """Get appropriate thresholds based on entry price."""
        if entry_price < 0.30:
            return self.THRESHOLDS['low']
        elif entry_price > 0.70:
            return self.THRESHOLDS['high']
        else:
            return self.THRESHOLDS['medium']
    
    def __init__(self):
        self._market_cache: Dict[str, Dict] = {}
        self._cache_time: float = 0
        self._cache_ttl = 60  # 1 minute
    
    def fetch_market_status(self, slug: str) -> Optional[Dict]:
        """Fetch current status of a market from Polymarket API."""
        import time
        try:
            # Check if cache needs to be cleared (older than TTL)
            now = time.time()
            if now - self._cache_time > self._cache_ttl:
                self._market_cache = {}
                self._cache_time = now
            
            # Try to get from cache first
            if slug in self._market_cache:
                return self._market_cache[slug]
            
            url = f"{self.POLYMARKET_API}?slug={slug}"
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                markets = response.json()
                if markets and len(markets) > 0:
                    market = markets[0]
                    self._market_cache[slug] = market
                    return market
            
            return None
            
        except Exception as e:
            log.debug(f"Error fetching market {slug}: {e}")
            return None
    
    def check_market_resolution(self, slug: str) -> MarketResolution:
        """Check if a market has been resolved."""
        market = self.fetch_market_status(slug)
        
        if not market:
            return MarketResolution(
                slug=slug,
                question="Unknown",
                resolved=False,
                winning_outcome=None,
                resolution_price=0,
                resolved_at=None
            )
        
        is_closed = market.get('closed', False)
        resolution = market.get('resolution', None)
        
        # Parse outcome prices to determine winner
        prices_str = market.get('outcomePrices', '[]')
        try:
            prices = json.loads(prices_str)
            yes_price = float(prices[0]) if prices else 0.5
        except:
            yes_price = 0.5
        
        winning_outcome = None
        resolution_price = yes_price
        
        if is_closed:
            # Market is resolved
            if resolution:
                winning_outcome = "YES" if resolution.upper() == "YES" else "NO"
                resolution_price = 1.0 if winning_outcome == "YES" else 0.0
            elif yes_price >= 0.99:
                winning_outcome = "YES"
                resolution_price = 1.0
            elif yes_price <= 0.01:
                winning_outcome = "NO"
                resolution_price = 0.0
        
        return MarketResolution(
            slug=slug,
            question=market.get('question', 'Unknown'),
            resolved=is_closed and winning_outcome is not None,
            winning_outcome=winning_outcome,
            resolution_price=resolution_price,
            resolved_at=datetime.now() if is_closed else None
        )
    
    def get_current_price(self, slug: str) -> Tuple[float, float]:
        """Get current YES and NO prices for a market."""
        market = self.fetch_market_status(slug)
        
        if not market:
            return 0.5, 0.5
        
        prices_str = market.get('outcomePrices', '[]')
        try:
            prices = json.loads(prices_str)
            yes_price = float(prices[0]) if prices else 0.5
            no_price = float(prices[1]) if len(prices) > 1 else 1 - yes_price
            return yes_price, no_price
        except:
            return 0.5, 0.5
    
    def _get_time_decay_multiplier(self, hours_held: float) -> float:
        """Get the take profit multiplier based on how long we've held the trade."""
        # Find the appropriate decay level
        for threshold_hours in sorted(self.TIME_DECAY_SCHEDULE.keys(), reverse=True):
            if hours_held >= threshold_hours:
                return self.TIME_DECAY_SCHEDULE[threshold_hours]
        return 1.0  # Default to full target
    
    def check_swing_opportunity(self, trade) -> SwingOpportunity:
        """
        Check if we should exit a trade early for profit/loss.
        
        TIME-DECAYING take profit:
        - 0-12h: 100% of target (e.g., +45% for low entry)
        - 12-24h: 75% of target (e.g., +33%)
        - 24-48h: 50% of target (e.g., +22%)
        - 48-72h: 25% of target (e.g., +11%)
        - >72h: 10% of target (e.g., +4.5%) - free up capital
        """
        slug = trade.market_slug
        entry_price = trade.entry_price
        
        current_yes, current_no = self.get_current_price(slug)
        
        # Get current price based on what we bought
        if trade.outcome == "YES":
            current_price = current_yes
        else:
            current_price = current_no
        
        # Calculate profit/loss
        if entry_price > 0:
            profit_pct = (current_price - entry_price) / entry_price
        else:
            profit_pct = 0
        
        # Calculate how long we've held this trade
        try:
            entry_time = datetime.fromisoformat(trade.timestamp.replace('Z', '+00:00'))
            hours_held = (datetime.now(entry_time.tzinfo) - entry_time).total_seconds() / 3600
        except:
            try:
                entry_time = datetime.fromisoformat(trade.timestamp)
                hours_held = (datetime.now() - entry_time).total_seconds() / 3600
            except:
                hours_held = 0
        
        # Get dynamic thresholds based on entry price
        thresholds = self._get_thresholds(entry_price)
        base_take_profit = thresholds['take_profit']
        # Note: stop_loss removed - we wait for resolution instead of cutting losses early
        
        # Apply time decay to take profit
        decay_multiplier = self._get_time_decay_multiplier(hours_held)
        take_profit = base_take_profit * decay_multiplier
        
        # Minimum take profit floor (don't go below +3%)
        take_profit = max(take_profit, 0.03)
        
        action = "HOLD"
        price_tier = "low" if entry_price < 0.30 else ("high" if entry_price > 0.70 else "medium")
        reason = f"Holding ({price_tier}, {hours_held:.0f}h, target: +{take_profit*100:.0f}%)"
        
        # Take profit (time-decayed)
        if profit_pct >= take_profit:
            action = "SELL"
            reason = f"Take profit ({price_tier}, {hours_held:.0f}h): +{profit_pct*100:.1f}% (target was +{take_profit*100:.0f}%)"
        
        # Near-certain YES - always exit (locking gains)
        elif current_price >= 0.98:
            action = "SELL"
            reason = f"Near-certain YES: price at {current_price:.2f}, locking in gains"
        
        elif current_price <= 0.02:
            # Near-certain NO - market is basically resolved, cut losses
            action = "SELL"
            reason = f"Near-certain NO: price at {current_price:.2f}, market resolving against us"
        
        # After 72h, exit if we have ANY profit
        elif hours_held > 72 and profit_pct > 0:
            action = "SELL"
            reason = f"Time exit ({hours_held:.0f}h): taking +{profit_pct*100:.1f}% to free capital"
        
        return SwingOpportunity(
            trade_id=trade.id,
            market=trade.market,
            entry_price=entry_price,
            current_price=current_price,
            profit_pct=profit_pct,
            action=action,
            reason=reason
        )
    
    def resolve_trades(self, simulation_tracker) -> Dict[str, Any]:
        """
        Check all open trades and resolve/exit as needed.
        Returns summary of actions taken.
        """
        results = {
            'resolved': [],
            'swing_exits': [],
            'errors': []
        }
        
        open_trades = simulation_tracker.get_open_trades()
        
        if not open_trades:
            return results
        
        log.info(f"Checking {len(open_trades)} open trades for resolution/swing...")
        
        for trade in open_trades:
            try:
                # First check if market is resolved
                resolution = self.check_market_resolution(trade.market_slug)
                
                if resolution.resolved:
                    # Market is resolved - calculate final P&L
                    trade.resolve(resolution.winning_outcome)
                    simulation_tracker.save()
                    
                    # Update smart_trader to free up the position slot
                    try:
                        from smart_trader import smart_trader
                        smart_trader.remove_position(trade.market, trade.outcome)
                    except:
                        pass
                    
                    results['resolved'].append({
                        'trade_id': trade.id,
                        'market': trade.market[:40],
                        'outcome': resolution.winning_outcome,
                        'pnl': trade.pnl_usd,
                        'status': trade.status
                    })
                    
                    log.info(f"[RESOLVED] {trade.market[:40]} | {trade.status} | P&L: ${trade.pnl_usd:+.2f}")
                    continue
                
                # Check for swing trading opportunity
                swing = self.check_swing_opportunity(trade)
                
                if swing.action == "SELL":
                    # Exit position early
                    trade.current_price = swing.current_price
                    trade.exit_price = swing.current_price
                    trade.pnl_usd = (swing.current_price - trade.entry_price) * trade.shares
                    trade.pnl_pct = swing.profit_pct * 100
                    trade.status = "EXITED"
                    trade.resolved = True
                    simulation_tracker.save()
                    
                    # Update smart_trader to free up the position slot
                    try:
                        from smart_trader import smart_trader
                        smart_trader.remove_position(trade.market, trade.outcome)
                    except:
                        pass
                    
                    results['swing_exits'].append({
                        'trade_id': trade.id,
                        'market': trade.market[:40],
                        'reason': swing.reason,
                        'entry': swing.entry_price,
                        'exit': swing.current_price,
                        'pnl': trade.pnl_usd
                    })
                    
                    log.info(f"[SWING EXIT] {trade.market[:40]} | {swing.reason} | P&L: ${trade.pnl_usd:+.2f}")
                else:
                    # Update current price for tracking
                    trade.calculate_pnl(swing.current_price)
                    
            except Exception as e:
                results['errors'].append({
                    'trade_id': trade.id,
                    'error': str(e)
                })
                log.debug(f"Error processing trade {trade.id}: {e}")
        
        # Save all updates
        simulation_tracker.save()
        
        return results
    
    def get_swing_signals(self, simulation_tracker) -> List[SwingOpportunity]:
        """Get all current swing trading signals for open trades."""
        signals = []
        
        for trade in simulation_tracker.get_open_trades():
            try:
                swing = self.check_swing_opportunity(trade)
                if swing.action == "SELL":
                    signals.append(swing)
            except Exception as e:
                log.debug(f"Error checking swing for {trade.id}: {e}")
        
        return signals


# Global instance
trade_resolver = TradeResolver()
