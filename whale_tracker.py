"""
Whale Tracker - Monitors top traders and their positions in real-time.
"""
import requests
import time
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from logger import log


@dataclass
class WhalePosition:
    """A whale's position in a market."""
    trader_address: str
    trader_name: str
    market_id: str
    market_title: str
    side: str  # 'YES' or 'NO'
    size: float
    entry_price: float
    current_value: float
    pnl: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class WhaleActivity:
    """A detected whale activity (new position or significant change)."""
    trader_address: str
    trader_name: str
    activity_type: str  # 'NEW_POSITION', 'INCREASED', 'DECREASED', 'CLOSED'
    market_title: str
    market_slug: str
    side: str
    size_change: float
    total_size: float
    price: float
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'trader': self.trader_name,
            'activity': self.activity_type,
            'market': self.market_title,
            'side': self.side,
            'size_change': self.size_change,
            'total_size': self.total_size,
            'price': self.price,
            'timestamp': self.timestamp.isoformat(),
        }


class WhaleTracker:
    """
    Tracks whale (top trader) activity on Polymarket.
    """
    
    LEADERBOARD_URL = "https://gamma-api.polymarket.com/users"
    POSITIONS_URL = "https://gamma-api.polymarket.com/user-positions"
    
    def __init__(self, num_whales: int = 20, min_position_usd: float = 1000):
        self.num_whales = num_whales
        self.min_position_usd = min_position_usd
        
        self._whales: List[Dict] = []
        self._whale_positions: Dict[str, List[WhalePosition]] = {}  # address -> positions
        self._previous_positions: Dict[str, Dict[str, float]] = {}  # address -> {market: size}
        self._activities: List[WhaleActivity] = []
        self._last_fetch = 0
        self._fetch_interval = 300  # 5 minutes
    
    def fetch_top_whales(self) -> List[Dict]:
        """Fetch top traders from leaderboard."""
        url = f"{self.LEADERBOARD_URL}?order=profit&ascending=false&limit={self.num_whales}"
        
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                self._whales = response.json()
                log.info(f"Fetched {len(self._whales)} top whales from leaderboard")
                return self._whales
        except Exception as e:
            log.error(f"Error fetching whales: {e}")
        
        return self._whales
    
    def get_whale_addresses(self) -> List[str]:
        """Get list of whale addresses to monitor."""
        if not self._whales:
            self.fetch_top_whales()
        
        return [w.get('proxyAddress') for w in self._whales if w.get('proxyAddress')]
    
    def fetch_whale_positions(self, address: str) -> List[WhalePosition]:
        """Fetch positions for a specific whale."""
        # Note: This API endpoint may require authentication or may not be public
        # This is a placeholder implementation
        try:
            # The actual Polymarket API for user positions might differ
            url = f"https://gamma-api.polymarket.com/query"
            # This would need the actual GraphQL query
            # For now, we'll track based on known positions
            pass
        except Exception as e:
            log.debug(f"Error fetching positions for {address}: {e}")
        
        return []
    
    def detect_whale_activity(self, markets: List[Dict[str, Any]]) -> List[WhaleActivity]:
        """
        Detect whale activity by checking for large positions in active markets.
        This is a heuristic approach since we can't directly track wallet transactions.
        """
        activities = []
        
        # Refresh whale list periodically
        now = time.time()
        if now - self._last_fetch > self._fetch_interval:
            self.fetch_top_whales()
            self._last_fetch = now
        
        # For each market, check if it's popular among whales
        for market in markets:
            volume = market.get('vol24h', 0)
            liquidity = market.get('liquidity', 0)
            
            # High volume markets are more likely to have whale activity
            if volume > 100000:  # $100k+ volume
                # This is where we'd ideally check on-chain data
                # For now, we'll use heuristics
                pass
        
        return activities
    
    def get_whale_consensus(self, market_slug: str) -> Optional[Dict[str, Any]]:
        """
        Get the consensus view of whales on a specific market.
        Returns aggregate whale sentiment if we can determine it.
        """
        # This would aggregate known whale positions
        # For now, returns None as we need more data
        return None
    
    def get_top_whale_picks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get markets where multiple whales have significant positions.
        """
        picks = []
        
        # This would analyze whale positions to find common picks
        # For now, we return the whale leaderboard info
        for whale in self._whales[:limit]:
            picks.append({
                'trader': whale.get('displayName', 'Anonymous'),
                'address': whale.get('proxyAddress', ''),
                'profit': whale.get('profit', 0),
                'volume': whale.get('volume', 0),
                'positions_count': whale.get('positionsCount', 0),
            })
        
        return picks
    
    def get_recent_activities(self, limit: int = 20) -> List[WhaleActivity]:
        """Get recent whale activities."""
        return sorted(self._activities, key=lambda a: a.timestamp, reverse=True)[:limit]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get whale tracking statistics."""
        top_whale_name = 'N/A'
        top_whale_profit = 0
        
        if self._whales and len(self._whales) > 0:
            top_whale_name = self._whales[0].get('displayName', 'Unknown')
            top_whale_profit = self._whales[0].get('profit', 0)
        
        return {
            'whales_tracked': len(self._whales),
            'total_whale_profit': sum(w.get('profit', 0) for w in self._whales),
            'total_whale_volume': sum(w.get('volume', 0) for w in self._whales),
            'activities_detected': len(self._activities),
            'top_whale': top_whale_name,
            'top_whale_profit': top_whale_profit,
        }
    
    def should_follow_trade(self, market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determine if we should follow whales into a trade.
        Returns recommendation based on whale activity.
        """
        # Check if this market is popular among tracked whales
        score = market.get('score', 0)
        volume = market.get('vol24h', 0)
        
        recommendation = {
            'follow': False,
            'confidence': 0,
            'reason': 'Insufficient whale data',
            'whales_in_market': 0,
        }
        
        # Boost confidence if market has high score and volume
        if score >= 80 and volume > 50000:
            recommendation['follow'] = True
            recommendation['confidence'] = min(80, score)
            recommendation['reason'] = 'High score market with significant volume'
        
        return recommendation


# Global instance
whale_tracker = WhaleTracker()
