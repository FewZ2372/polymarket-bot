"""
Trade Resolver - Automatically resolves trades and calculates P&L.
Also handles swing trading (exit positions for profit before resolution).
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
    
    # Dynamic swing trading thresholds based on entry price
    # Low entry = more upside potential, hold longer
    # High entry = less upside, take profit quickly
    THRESHOLDS = {
        'low': {      # Entry < 0.30
            'take_profit': 0.50,  # +50%
            'stop_loss': -0.20,   # -20%
        },
        'medium': {   # Entry 0.30 - 0.70
            'take_profit': 0.20,  # +20%
            'stop_loss': -0.15,   # -15%
        },
        'high': {     # Entry > 0.70
            'take_profit': 0.08,  # +8%
            'stop_loss': -0.10,   # -10%
        }
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
        try:
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
    
    def check_swing_opportunity(self, trade) -> SwingOpportunity:
        """
        Check if we should exit a trade early for profit/loss.
        
        Dynamic swing trading logic based on entry price:
        - Low entry (<0.30): Hold longer, target +50%, stop at -20%
        - Medium entry (0.30-0.70): Target +20%, stop at -15%
        - High entry (>0.70): Quick exit, target +8%, stop at -10%
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
        
        # Get dynamic thresholds based on entry price
        thresholds = self._get_thresholds(entry_price)
        take_profit = thresholds['take_profit']
        stop_loss = thresholds['stop_loss']
        
        action = "HOLD"
        price_tier = "low" if entry_price < 0.30 else ("high" if entry_price > 0.70 else "medium")
        reason = f"Holding ({price_tier} entry, target: +{take_profit*100:.0f}%)"
        
        # Take profit
        if profit_pct >= take_profit:
            action = "SELL"
            reason = f"Take profit ({price_tier}): +{profit_pct*100:.1f}% (target was +{take_profit*100:.0f}%)"
        
        # Stop loss
        elif profit_pct <= stop_loss:
            action = "SELL"
            reason = f"Stop loss ({price_tier}): {profit_pct*100:.1f}% (limit was {stop_loss*100:.0f}%)"
        
        # Near-certain outcome - always exit
        elif current_price >= 0.98:
            action = "SELL"
            reason = f"Near-certain YES: price at {current_price:.2f}, locking in gains"
        
        elif current_price <= 0.02:
            action = "SELL"
            reason = f"Near-certain NO: price at {current_price:.2f}, cutting losses"
        
        # Trailing stop for big winners: if we WERE up 30%+ but now dropped below 15%
        # This needs historical tracking - for now, use price movement as proxy
        elif entry_price < 0.30 and profit_pct >= 0.15 and profit_pct < 0.25:
            # We're up decent but not at target - check if we should protect gains
            # If price dropped significantly from what could have been higher
            potential_high = entry_price * 1.30  # What +30% would have been
            if current_price < potential_high * 0.85:  # Dropped 15% from potential high
                action = "SELL"
                reason = f"Trailing stop: protecting +{profit_pct*100:.1f}% gain (could drop further)"
        
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
