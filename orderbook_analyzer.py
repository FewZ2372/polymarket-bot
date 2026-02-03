"""
Order Book Analyzer - Analyzes market depth and detects manipulation patterns.
Identifies buy/sell walls, spoofing, and market imbalances.
"""
import requests
import json
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict

from logger import log


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""
    price: float
    size: float
    side: str  # 'bid' or 'ask'
    order_count: int = 1


@dataclass
class OrderBook:
    """Full order book snapshot."""
    market_id: str
    market_title: str
    timestamp: datetime
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    
    @property
    def best_bid(self) -> float:
        return self.bids[0].price if self.bids else 0
    
    @property
    def best_ask(self) -> float:
        return self.asks[0].price if self.asks else 1
    
    @property
    def mid_price(self) -> float:
        return (self.best_bid + self.best_ask) / 2
    
    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid
    
    @property
    def spread_pct(self) -> float:
        return (self.spread / self.mid_price) * 100 if self.mid_price > 0 else 0
    
    def total_bid_size(self, levels: int = None) -> float:
        bids = self.bids[:levels] if levels else self.bids
        return sum(b.size for b in bids)
    
    def total_ask_size(self, levels: int = None) -> float:
        asks = self.asks[:levels] if levels else self.asks
        return sum(a.size for a in asks)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_id': self.market_id,
            'market_title': self.market_title[:50],
            'timestamp': self.timestamp.isoformat(),
            'best_bid': self.best_bid,
            'best_ask': self.best_ask,
            'mid_price': round(self.mid_price, 4),
            'spread': round(self.spread, 4),
            'spread_pct': round(self.spread_pct, 2),
            'bid_depth': self.total_bid_size(5),
            'ask_depth': self.total_ask_size(5),
            'bid_levels': len(self.bids),
            'ask_levels': len(self.asks),
        }


@dataclass
class WallDetection:
    """A detected buy or sell wall."""
    market_id: str
    market_title: str
    wall_type: str  # 'BUY_WALL' or 'SELL_WALL'
    price: float
    size: float
    size_vs_avg: float  # How many times larger than average
    depth_pct: float  # % of total depth at this level
    significance: str  # 'HIGH', 'MEDIUM', 'LOW'
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:40],
            'wall_type': self.wall_type,
            'price': self.price,
            'size': round(self.size, 2),
            'size_vs_avg': round(self.size_vs_avg, 1),
            'depth_pct': round(self.depth_pct, 1),
            'significance': self.significance,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class MarketImbalance:
    """Order book imbalance signal."""
    market_id: str
    market_title: str
    bid_size: float
    ask_size: float
    imbalance_ratio: float  # bid/ask ratio
    direction: str  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    strength: str  # 'STRONG', 'MODERATE', 'WEAK'
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:40],
            'bid_size': round(self.bid_size, 2),
            'ask_size': round(self.ask_size, 2),
            'imbalance_ratio': round(self.imbalance_ratio, 2),
            'direction': self.direction,
            'strength': self.strength,
            'timestamp': self.timestamp.isoformat(),
        }


class OrderBookAnalyzer:
    """
    Analyzes order books for trading signals and manipulation detection.
    """
    
    POLYMARKET_CLOB = "https://clob.polymarket.com"
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    # Thresholds for detection
    WALL_SIZE_MULTIPLIER = 3.0  # Must be 3x avg to be a wall
    WALL_DEPTH_PCT = 20.0  # Must be >20% of total depth
    IMBALANCE_THRESHOLD = 2.0  # 2:1 ratio = imbalanced
    STRONG_IMBALANCE = 3.0  # 3:1 = strong imbalance
    
    def __init__(self):
        self._orderbook_cache: Dict[str, OrderBook] = {}
        self._cache_time: Dict[str, float] = {}
        self._cache_ttl = 30  # 30 seconds
        self._wall_history: List[WallDetection] = []
        self._imbalance_history: List[MarketImbalance] = []
    
    def fetch_orderbook(self, token_id: str, market_title: str = "") -> Optional[OrderBook]:
        """Fetch order book from Polymarket CLOB."""
        import time
        
        # Check cache
        cache_key = token_id
        if cache_key in self._cache_time:
            if time.time() - self._cache_time[cache_key] < self._cache_ttl:
                return self._orderbook_cache.get(cache_key)
        
        try:
            url = f"{self.POLYMARKET_CLOB}/book"
            params = {'token_id': token_id}
            
            response = requests.get(url, params=params, timeout=10)
            
            if response.status_code != 200:
                return None
            
            data = response.json()
            
            # Parse bids
            bids = []
            for bid in data.get('bids', []):
                bids.append(OrderBookLevel(
                    price=float(bid.get('price', 0)),
                    size=float(bid.get('size', 0)),
                    side='bid',
                ))
            
            # Parse asks
            asks = []
            for ask in data.get('asks', []):
                asks.append(OrderBookLevel(
                    price=float(ask.get('price', 0)),
                    size=float(ask.get('size', 0)),
                    side='ask',
                ))
            
            # Sort properly
            bids.sort(key=lambda x: x.price, reverse=True)  # Highest first
            asks.sort(key=lambda x: x.price)  # Lowest first
            
            orderbook = OrderBook(
                market_id=token_id,
                market_title=market_title,
                timestamp=datetime.now(),
                bids=bids,
                asks=asks,
            )
            
            # Cache it
            self._orderbook_cache[cache_key] = orderbook
            self._cache_time[cache_key] = time.time()
            
            return orderbook
            
        except Exception as e:
            log.debug(f"Error fetching orderbook: {e}")
            return None
    
    def detect_walls(self, orderbook: OrderBook) -> List[WallDetection]:
        """
        Detect buy and sell walls in the order book.
        
        A wall is a large order that could prevent price movement.
        """
        walls = []
        
        # Analyze bids for buy walls
        if orderbook.bids and len(orderbook.bids) > 0:
            avg_bid_size = sum(b.size for b in orderbook.bids) / len(orderbook.bids)
            total_bid_size = orderbook.total_bid_size()
            
            # Skip if average is zero (all empty orders)
            if avg_bid_size <= 0:
                avg_bid_size = 1  # Prevent division by zero
            
            for bid in orderbook.bids[:10]:  # Check top 10 levels
                if bid.size > avg_bid_size * self.WALL_SIZE_MULTIPLIER:
                    depth_pct = (bid.size / total_bid_size) * 100 if total_bid_size > 0 else 0
                    
                    if depth_pct >= self.WALL_DEPTH_PCT:
                        significance = 'HIGH' if depth_pct >= 30 else 'MEDIUM'
                        
                        walls.append(WallDetection(
                            market_id=orderbook.market_id,
                            market_title=orderbook.market_title,
                            wall_type='BUY_WALL',
                            price=bid.price,
                            size=bid.size,
                            size_vs_avg=bid.size / avg_bid_size,
                            depth_pct=depth_pct,
                            significance=significance,
                        ))
        
        # Analyze asks for sell walls
        if orderbook.asks and len(orderbook.asks) > 0:
            avg_ask_size = sum(a.size for a in orderbook.asks) / len(orderbook.asks)
            total_ask_size = orderbook.total_ask_size()
            
            # Skip if average is zero
            if avg_ask_size <= 0:
                avg_ask_size = 1  # Prevent division by zero
            
            for ask in orderbook.asks[:10]:
                if ask.size > avg_ask_size * self.WALL_SIZE_MULTIPLIER:
                    depth_pct = (ask.size / total_ask_size) * 100 if total_ask_size > 0 else 0
                    
                    if depth_pct >= self.WALL_DEPTH_PCT:
                        significance = 'HIGH' if depth_pct >= 30 else 'MEDIUM'
                        
                        walls.append(WallDetection(
                            market_id=orderbook.market_id,
                            market_title=orderbook.market_title,
                            wall_type='SELL_WALL',
                            price=ask.price,
                            size=ask.size,
                            size_vs_avg=ask.size / avg_ask_size,
                            depth_pct=depth_pct,
                            significance=significance,
                        ))
        
        # Update history
        self._wall_history.extend(walls)
        self._wall_history = self._wall_history[-100:]  # Keep last 100
        
        return walls
    
    def analyze_imbalance(self, orderbook: OrderBook, levels: int = 5) -> MarketImbalance:
        """
        Analyze order book imbalance.
        
        Imbalance indicates potential price direction.
        """
        bid_size = orderbook.total_bid_size(levels)
        ask_size = orderbook.total_ask_size(levels)
        
        # Calculate ratio (avoid infinity which breaks JSON serialization)
        if ask_size > 0:
            ratio = bid_size / ask_size
        else:
            ratio = 100.0 if bid_size > 0 else 1.0  # Cap at 100x instead of infinity
        
        # Determine direction
        if ratio >= self.STRONG_IMBALANCE:
            direction = 'BULLISH'
            strength = 'STRONG'
        elif ratio >= self.IMBALANCE_THRESHOLD:
            direction = 'BULLISH'
            strength = 'MODERATE'
        elif ratio <= 1 / self.STRONG_IMBALANCE:
            direction = 'BEARISH'
            strength = 'STRONG'
        elif ratio <= 1 / self.IMBALANCE_THRESHOLD:
            direction = 'BEARISH'
            strength = 'MODERATE'
        else:
            direction = 'NEUTRAL'
            strength = 'WEAK'
        
        imbalance = MarketImbalance(
            market_id=orderbook.market_id,
            market_title=orderbook.market_title,
            bid_size=bid_size,
            ask_size=ask_size,
            imbalance_ratio=ratio,
            direction=direction,
            strength=strength,
        )
        
        # Update history
        self._imbalance_history.append(imbalance)
        self._imbalance_history = self._imbalance_history[-100:]
        
        return imbalance
    
    def analyze_market(self, market: Dict[str, Any]) -> Dict[str, Any]:
        """
        Full order book analysis for a market.
        Returns trading signals based on order book state.
        """
        token_id = market.get('token_id')
        if not token_id:
            return {'error': 'No token_id available'}
        
        orderbook = self.fetch_orderbook(token_id, market.get('question', ''))
        if not orderbook:
            return {'error': 'Could not fetch orderbook'}
        
        # Detect walls
        walls = self.detect_walls(orderbook)
        
        # Analyze imbalance
        imbalance = self.analyze_imbalance(orderbook)
        
        # Generate trading signal
        signal = self._generate_signal(orderbook, walls, imbalance)
        
        return {
            'orderbook': orderbook.to_dict(),
            'walls': [w.to_dict() for w in walls],
            'imbalance': imbalance.to_dict(),
            'signal': signal,
        }
    
    def _generate_signal(
        self, 
        orderbook: OrderBook, 
        walls: List[WallDetection], 
        imbalance: MarketImbalance
    ) -> Dict[str, Any]:
        """Generate trading signal from order book analysis."""
        
        signal = {
            'action': 'HOLD',
            'confidence': 0,
            'reason': 'No clear signal',
        }
        
        # Check for manipulation signals (spoofing)
        buy_walls = [w for w in walls if w.wall_type == 'BUY_WALL']
        sell_walls = [w for w in walls if w.wall_type == 'SELL_WALL']
        
        # Strong sell wall with bullish imbalance = potential spoofing
        # (Someone placing fake sells to accumulate at lower price)
        if sell_walls and imbalance.direction == 'BULLISH' and imbalance.strength == 'STRONG':
            signal = {
                'action': 'CAUTION_BUY',
                'confidence': 60,
                'reason': 'Sell wall with bullish imbalance - potential accumulation',
            }
        
        # Strong buy wall with bearish imbalance = potential spoofing
        elif buy_walls and imbalance.direction == 'BEARISH' and imbalance.strength == 'STRONG':
            signal = {
                'action': 'CAUTION_SELL',
                'confidence': 60,
                'reason': 'Buy wall with bearish imbalance - potential distribution',
            }
        
        # Clean bullish signal
        elif imbalance.direction == 'BULLISH' and imbalance.strength == 'STRONG' and not sell_walls:
            signal = {
                'action': 'BUY',
                'confidence': 75,
                'reason': 'Strong bullish imbalance with no resistance',
            }
        
        # Clean bearish signal
        elif imbalance.direction == 'BEARISH' and imbalance.strength == 'STRONG' and not buy_walls:
            signal = {
                'action': 'SELL',
                'confidence': 75,
                'reason': 'Strong bearish imbalance with no support',
            }
        
        # Wide spread = low liquidity, avoid
        if orderbook.spread_pct > 5:
            signal = {
                'action': 'AVOID',
                'confidence': 80,
                'reason': f'Wide spread ({orderbook.spread_pct:.1f}%) indicates low liquidity',
            }
        
        return signal
    
    def scan_markets(self, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scan multiple markets for order book signals.
        """
        results = []
        
        for market in markets:
            token_id = market.get('token_id')
            if not token_id:
                continue
            
            try:
                analysis = self.analyze_market(market)
                if 'error' not in analysis:
                    signal = analysis.get('signal', {})
                    if signal.get('action') not in ['HOLD', None]:
                        results.append({
                            'market': market.get('question', '')[:50],
                            'slug': market.get('slug', ''),
                            **analysis,
                        })
            except Exception as e:
                log.debug(f"Error analyzing {market.get('slug', '')}: {e}")
        
        return results
    
    def get_recent_walls(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recently detected walls."""
        return [w.to_dict() for w in self._wall_history[-limit:]]
    
    def get_recent_imbalances(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent imbalance signals."""
        return [i.to_dict() for i in self._imbalance_history[-limit:]]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get analyzer statistics."""
        return {
            'cached_orderbooks': len(self._orderbook_cache),
            'walls_detected': len(self._wall_history),
            'imbalances_tracked': len(self._imbalance_history),
            'buy_walls': len([w for w in self._wall_history if w.wall_type == 'BUY_WALL']),
            'sell_walls': len([w for w in self._wall_history if w.wall_type == 'SELL_WALL']),
            'bullish_imbalances': len([i for i in self._imbalance_history if i.direction == 'BULLISH']),
            'bearish_imbalances': len([i for i in self._imbalance_history if i.direction == 'BEARISH']),
        }


# Global instance
orderbook_analyzer = OrderBookAnalyzer()
