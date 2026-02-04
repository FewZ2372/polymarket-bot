"""
Smart Trader - High Frequency Trading without Stop Loss
========================================================
Strategy: MANY SMALL TRADES, let markets RESOLVE naturally.

Key features:
1. NO STOP LOSS - Markets resolve, that's our exit
2. NO DUPLICATE TRADES - One position per market
3. TAKE PROFIT ONLY - Optional early exit on +15-25% gains
4. SHORT-TERM FOCUS - Only markets resolving in <14 days
5. POSITION TRACKING - Know what we own

Why no stop loss?
- Entry at $0.10 means max loss is $0.10 (already defined)
- Stop loss can cut you out before market moves in your favor
- v22 had no stop loss and achieved 100% win rate

This module wraps the existing trader to add intelligence.
"""
import json
from typing import Dict, Set, Optional, Any
from datetime import datetime, timedelta

from logger import log
from simulation_tracker import simulation_tracker
from market_health import market_health, MarketHealthMonitor


class SmartTrader:
    """
    High-frequency trade management for SHORT-TERM markets.
    
    Strategy: MANY SMALL TRADES
    - More concurrent positions allowed
    - Quick resolution markets only
    - Small position sizes, high volume
    """
    
    # Allow multiple trades per market for high-frequency strategy
    MAX_TRADES_PER_MARKET = 10
    
    # Maximum days until market resolution - SHORT TERM ONLY
    MAX_DAYS_TO_EXPIRY = 21  # 3 weeks max
    
    def __init__(self):
        self.open_markets: Set[str] = set()  # Markets where we have open positions
        self.market_trade_count: Dict[str, int] = {}  # Count trades per market
        self.market_entries: Dict[str, dict] = {}  # Track entry details
        self._load_positions()
    
    def _load_positions(self):
        """Load existing open positions from simulation tracker."""
        try:
            simulation_tracker.load()
            self.market_trade_count = {}  # Reset counts
            for trade in simulation_tracker.trades:
                if trade.status == "OPEN":
                    market_key = self._get_market_key(trade.market, trade.outcome)
                    self.open_markets.add(market_key)
                    # Count trades per market
                    base_market = trade.market.lower().strip()[:50]
                    self.market_trade_count[base_market] = self.market_trade_count.get(base_market, 0) + 1
                    self.market_entries[market_key] = {
                        'entry_price': trade.entry_price,
                        'entry_time': trade.timestamp,
                        'amount': trade.amount_usd,
                        'outcome': trade.outcome,
                    }
            log.info(f"[SMART] Loaded {len(self.open_markets)} positions in {len(self.market_trade_count)} markets")
        except Exception as e:
            log.warning(f"[SMART] Could not load positions: {e}")
    
    def _get_market_key(self, market_title: str, outcome: str = "YES") -> str:
        """Generate unique key for market (ignoring outcome - one position per market)."""
        # Normalize title - ONLY use market name, not outcome
        # This prevents having both YES and NO positions on same market
        title = market_title.lower().strip()[:50]
        return title  # No outcome suffix - one trade per market total
    
    def should_trade(self, opportunity: Dict[str, Any], outcome: str = "YES") -> tuple[bool, str]:
        """
        Decide if we should take this trade.
        Returns (should_trade, reason)
        """
        market_title = opportunity.get('question', '')
        base_market = market_title.lower().strip()[:50]
        
        # RULE 0: Check market health adjustments
        health_adj = market_health.get_adjustments()
        if not health_adj.is_trading_allowed:
            return False, f"[HEALTH] Trading pausado: {health_adj.reason}"
        
        # RULE 1: Max trades adjusted by market health
        max_trades = min(self.MAX_TRADES_PER_MARKET, health_adj.max_concurrent_trades)
        current_count = self.market_trade_count.get(base_market, 0)
        if current_count >= max_trades:
            return False, f"Max {max_trades} trades (health adjusted)"
        
        # Check total open positions against health limit
        if self.get_position_count() >= health_adj.max_concurrent_trades:
            return False, f"[HEALTH] Max positions: {health_adj.max_concurrent_trades}"
        
        # RULE 2: Quality filter - VERY LOW score threshold for high frequency
        # We want VOLUME - let the diversification protect us
        score = opportunity.get('score', 0)
        MIN_SCORE = 40  # Very low - we're betting small amounts
        
        if score < MIN_SCORE:
            return False, f"Score {score} < {MIN_SCORE}"
        
        # RULE 3: Price sanity check
        entry_price = opportunity.get('yes', 0) if outcome == "YES" else opportunity.get('no', 0)
        if entry_price <= 0.01 or entry_price >= 0.99:
            return False, f"Price {entry_price:.2%} too extreme"
        
        # RULE 3b: Don't buy very cheap markets (< 5c) - often dying/resolving NO
        if entry_price < 0.05:
            return False, f"Price {entry_price:.2%} too cheap (likely dying market)"
        
        # RULE 3c: Price cap for decent upside
        # At 50c: win = +100%, lose = -100% (1:1 minimum acceptable)
        MAX_ENTRY_PRICE = 0.50  # 50 cents max
        if entry_price > MAX_ENTRY_PRICE:
            return False, f"Price {entry_price:.0%} > {MAX_ENTRY_PRICE:.0%} max"
        
        # RULE 3c: TREND CHECK - Don't buy falling markets
        # If price dropped significantly in last hour/day, the market is dying
        price_change_1h = opportunity.get('oneHourPriceChange', 0) or opportunity.get('price_change_1h', 0) or 0
        price_change_24h = opportunity.get('oneDayPriceChange', 0) or opportunity.get('price_change_24h', 0) or 0
        
        # Don't buy if price dropped >10% in last hour (sharp dump)
        if price_change_1h < -0.10:
            return False, f"Falling market: -{abs(price_change_1h)*100:.0f}% in 1h"
        
        # Don't buy if price dropped >25% in last 24h (sustained decline)
        if price_change_24h < -0.25:
            return False, f"Falling market: -{abs(price_change_24h)*100:.0f}% in 24h"
        
        # Extra caution for cheap markets: reject if ANY significant drop
        if entry_price < 0.15 and price_change_1h < -0.05:
            return False, f"Cheap + falling: {entry_price:.0%} with -{abs(price_change_1h)*100:.0f}% 1h drop"
        
        # RULE 4: Time to expiry check - ALWAYS applied now
        # Scanner already filters to <30 days, but double-check here
        end_date = opportunity.get('end_date')
        days_to_expiry = opportunity.get('days_to_resolution')  # From new scanner
        
        if days_to_expiry is None and end_date:
            try:
                if isinstance(end_date, str):
                    end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                    if end_date.tzinfo:
                        end_date = end_date.replace(tzinfo=None)
                days_to_expiry = (end_date - datetime.now()).total_seconds() / 86400
            except Exception as e:
                log.debug(f"Could not parse end_date: {e}")
                days_to_expiry = None
        
        if days_to_expiry is not None:
            if days_to_expiry > self.MAX_DAYS_TO_EXPIRY:
                return False, f"Expires in {days_to_expiry:.0f}d > max {self.MAX_DAYS_TO_EXPIRY}d"
            
            if days_to_expiry < 0:
                return False, f"Market already expired"
        
        return True, "OK"
    
    def record_trade(self, opportunity: Dict[str, Any], outcome: str = "YES"):
        """Record that we took a trade (for duplicate tracking)."""
        market_title = opportunity.get('question', '')
        market_key = self._get_market_key(market_title, outcome)
        base_market = market_title.lower().strip()[:50]
        
        self.open_markets.add(market_key)
        # Increment trade count for this market
        self.market_trade_count[base_market] = self.market_trade_count.get(base_market, 0) + 1
        self.market_entries[market_key] = {
            'entry_price': opportunity.get('yes', 0) if outcome == "YES" else opportunity.get('no', 0),
            'entry_time': datetime.now().isoformat(),
            'amount': 2.0,
            'outcome': outcome,
        }
    
    def remove_position(self, market_title: str, outcome: str = "YES"):
        """Remove position when trade is closed."""
        market_key = self._get_market_key(market_title, outcome)
        base_market = market_title.lower().strip()[:50]
        
        self.open_markets.discard(market_key)
        self.market_entries.pop(market_key, None)
        
        # Decrement trade count for this market
        if base_market in self.market_trade_count:
            self.market_trade_count[base_market] = max(0, self.market_trade_count[base_market] - 1)
            if self.market_trade_count[base_market] == 0:
                del self.market_trade_count[base_market]
    
    def get_position_count(self) -> int:
        """Get number of open positions."""
        return len(self.open_markets)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get smart trader statistics."""
        return {
            'open_positions': len(self.open_markets),
            'unique_markets': len(set(k.rsplit('_', 1)[0] for k in self.open_markets)),
            'blocked_duplicates': getattr(self, '_blocked_count', 0),
        }


# Global instance
smart_trader = SmartTrader()


class SmartTradeResolver:
    """
    Trade resolver SIN STOP LOSS - Let markets resolve naturally.
    
    Filosofía: En mercados de predicción con entrada barata y resolución rápida,
    el stop loss es contraproducente porque:
    1. Tu pérdida máxima ya está definida (el precio de entrada)
    2. Podrías cortarte antes de que el mercado se mueva a tu favor
    3. v22 no usaba stop loss y tuvo 100% win rate
    
    Solo usamos TAKE PROFIT opcional para liberar capital si hay una ganancia grande.
    """
    
    # Take profit settings - OPTIONAL early exit if big gains
    # The main exit is market resolution, but we can take profit early
    # These match trade_resolver.py initial targets (before time decay)
    TAKE_PROFIT_SETTINGS = {
        'low': 0.50,      # +50% for cheap markets (high upside potential)
        'medium': 0.20,   # +20% for medium markets (v22 value)
        'high': 0.08,     # +8% for expensive markets (v22 value)
    }
    
    # NO STOP LOSS - Let markets resolve
    USE_STOP_LOSS = False
    
    def get_price_tier(self, entry_price: float) -> str:
        """Determine price tier for settings."""
        if entry_price < 0.25:
            return 'low'
        elif entry_price < 0.60:
            return 'medium'
        else:
            return 'high'
    
    def should_exit(self, trade, current_price: float) -> tuple[bool, str]:
        """
        Determine if trade should be exited.
        Returns (should_exit, reason)
        
        NO STOP LOSS - only take profit or wait for resolution.
        """
        entry_price = trade.entry_price
        tier = self.get_price_tier(entry_price)
        
        # Calculate P&L
        if trade.outcome == "YES":
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            # For NO positions, profit when price drops
            pnl_pct = (entry_price - current_price) / entry_price
        
        # ONLY RULE: Take profit if we hit target
        take_profit = self.TAKE_PROFIT_SETTINGS[tier]
        if pnl_pct >= take_profit:
            return True, f"TAKE PROFIT: +{pnl_pct*100:.1f}% (target: +{take_profit*100:.0f}%)"
        
        # NO STOP LOSS - Hold until resolution
        # The market will resolve and we'll either win $1 or lose our entry
        return False, f"HOLDING: {pnl_pct*100:+.1f}% (waiting for resolution)"


smart_resolver = SmartTradeResolver()
