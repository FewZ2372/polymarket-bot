"""
Real-time data feed using WebSockets.
Connects to Polymarket's WebSocket API for live price updates.
"""
import asyncio
import json
import time
from typing import Dict, List, Any, Optional, Callable, Set
from datetime import datetime
from dataclasses import dataclass, field
from collections import defaultdict

from logger import log

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    log.warning("websockets not installed. Real-time feed disabled.")


@dataclass
class PriceUpdate:
    """A real-time price update."""
    market_id: str
    token_id: str
    price: float
    side: str  # 'bid' or 'ask'
    size: float
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class MarketSnapshot:
    """Current state of a market."""
    market_id: str
    best_bid: float = 0
    best_ask: float = 0
    mid_price: float = 0
    spread: float = 0
    last_update: datetime = field(default_factory=datetime.now)
    price_history: List[float] = field(default_factory=list)
    
    def update(self, bid: float = None, ask: float = None):
        """Update the snapshot with new prices."""
        if bid is not None:
            self.best_bid = bid
        if ask is not None:
            self.best_ask = ask
        
        if self.best_bid > 0 and self.best_ask > 0:
            self.mid_price = (self.best_bid + self.best_ask) / 2
            self.spread = self.best_ask - self.best_bid
        
        self.last_update = datetime.now()
        
        # Keep price history for momentum detection
        if self.mid_price > 0:
            self.price_history.append(self.mid_price)
            # Keep last 100 prices
            self.price_history = self.price_history[-100:]
    
    def get_momentum(self, periods: int = 10) -> float:
        """Calculate price momentum over last N updates."""
        if len(self.price_history) < periods + 1:
            return 0
        
        old_price = self.price_history[-periods-1]
        current = self.price_history[-1]
        
        if old_price == 0:
            return 0
        
        return (current - old_price) / old_price


class RealtimeFeed:
    """
    Manages WebSocket connections for real-time market data.
    """
    
    # Polymarket WebSocket endpoints
    WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    def __init__(self):
        self._ws = None
        self._running = False
        self._subscribed_markets: Set[str] = set()
        self._market_snapshots: Dict[str, MarketSnapshot] = {}
        self._callbacks: List[Callable[[PriceUpdate], None]] = []
        self._reconnect_delay = 5
        self._last_message_time = 0
    
    async def connect(self):
        """Establish WebSocket connection."""
        if not WEBSOCKETS_AVAILABLE:
            log.warning("WebSockets not available, using polling mode")
            return False
        
        try:
            self._ws = await websockets.connect(
                self.WS_URL,
                ping_interval=30,
                ping_timeout=10,
            )
            self._running = True
            log.info("WebSocket connected to Polymarket")
            return True
            
        except Exception as e:
            log.error(f"WebSocket connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("WebSocket disconnected")
    
    async def subscribe(self, market_ids: List[str]):
        """Subscribe to market updates."""
        if not self._ws:
            return
        
        for market_id in market_ids:
            if market_id in self._subscribed_markets:
                continue
            
            try:
                subscribe_msg = {
                    "type": "subscribe",
                    "channel": "market",
                    "market": market_id,
                }
                await self._ws.send(json.dumps(subscribe_msg))
                self._subscribed_markets.add(market_id)
                
                # Initialize snapshot
                if market_id not in self._market_snapshots:
                    self._market_snapshots[market_id] = MarketSnapshot(market_id=market_id)
                
            except Exception as e:
                log.error(f"Failed to subscribe to {market_id}: {e}")
        
        log.info(f"Subscribed to {len(self._subscribed_markets)} markets")
    
    async def unsubscribe(self, market_ids: List[str]):
        """Unsubscribe from market updates."""
        if not self._ws:
            return
        
        for market_id in market_ids:
            if market_id not in self._subscribed_markets:
                continue
            
            try:
                unsubscribe_msg = {
                    "type": "unsubscribe",
                    "channel": "market",
                    "market": market_id,
                }
                await self._ws.send(json.dumps(unsubscribe_msg))
                self._subscribed_markets.discard(market_id)
                
            except Exception as e:
                log.error(f"Failed to unsubscribe from {market_id}: {e}")
    
    def add_callback(self, callback: Callable[[PriceUpdate], None]):
        """Add a callback for price updates."""
        self._callbacks.append(callback)
    
    def remove_callback(self, callback: Callable[[PriceUpdate], None]):
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message."""
        try:
            data = json.loads(message)
            msg_type = data.get('type', '')
            
            if msg_type == 'book':
                # Order book update
                market_id = data.get('market', '')
                bids = data.get('bids', [])
                asks = data.get('asks', [])
                
                if market_id in self._market_snapshots:
                    snapshot = self._market_snapshots[market_id]
                    
                    # Get best bid/ask
                    best_bid = float(bids[0]['price']) if bids else 0
                    best_ask = float(asks[0]['price']) if asks else 0
                    
                    snapshot.update(bid=best_bid, ask=best_ask)
                    
                    # Create price update
                    update = PriceUpdate(
                        market_id=market_id,
                        token_id=data.get('asset_id', ''),
                        price=snapshot.mid_price,
                        side='mid',
                        size=0,
                    )
                    
                    # Notify callbacks
                    for callback in self._callbacks:
                        try:
                            callback(update)
                        except Exception as e:
                            log.debug(f"Callback error: {e}")
            
            elif msg_type == 'trade':
                # Trade execution
                market_id = data.get('market', '')
                price = float(data.get('price', 0))
                size = float(data.get('size', 0))
                side = data.get('side', '')
                
                update = PriceUpdate(
                    market_id=market_id,
                    token_id=data.get('asset_id', ''),
                    price=price,
                    side=side,
                    size=size,
                )
                
                for callback in self._callbacks:
                    try:
                        callback(update)
                    except Exception as e:
                        log.debug(f"Callback error: {e}")
            
            self._last_message_time = time.time()
            
        except json.JSONDecodeError:
            log.debug(f"Invalid JSON: {message[:100]}")
        except Exception as e:
            log.debug(f"Error processing message: {e}")
    
    async def listen(self):
        """Main listening loop."""
        while self._running:
            try:
                if not self._ws:
                    connected = await self.connect()
                    if not connected:
                        await asyncio.sleep(self._reconnect_delay)
                        continue
                
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=60
                )
                await self._process_message(message)
                
            except asyncio.TimeoutError:
                # No message in 60s, send ping
                if self._ws:
                    try:
                        await self._ws.ping()
                    except Exception:
                        self._ws = None
                        
            except websockets.exceptions.ConnectionClosed:
                log.warning("WebSocket connection closed, reconnecting...")
                self._ws = None
                await asyncio.sleep(self._reconnect_delay)
                
            except Exception as e:
                log.error(f"WebSocket error: {e}")
                self._ws = None
                await asyncio.sleep(self._reconnect_delay)
    
    def get_snapshot(self, market_id: str) -> Optional[MarketSnapshot]:
        """Get current snapshot for a market."""
        return self._market_snapshots.get(market_id)
    
    def get_all_snapshots(self) -> Dict[str, MarketSnapshot]:
        """Get all market snapshots."""
        return self._market_snapshots.copy()
    
    def get_momentum_signals(self, min_momentum: float = 0.02) -> List[Dict[str, Any]]:
        """Get markets with significant momentum."""
        signals = []
        
        for market_id, snapshot in self._market_snapshots.items():
            momentum = snapshot.get_momentum(periods=10)
            
            if abs(momentum) >= min_momentum:
                signals.append({
                    'market_id': market_id,
                    'momentum': momentum,
                    'direction': 'UP' if momentum > 0 else 'DOWN',
                    'mid_price': snapshot.mid_price,
                    'spread': snapshot.spread,
                    'last_update': snapshot.last_update.isoformat(),
                })
        
        return sorted(signals, key=lambda x: abs(x['momentum']), reverse=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get feed statistics."""
        return {
            'connected': self._ws is not None,
            'running': self._running,
            'subscribed_markets': len(self._subscribed_markets),
            'total_snapshots': len(self._market_snapshots),
            'last_message_age': time.time() - self._last_message_time if self._last_message_time else None,
            'markets_with_data': len([s for s in self._market_snapshots.values() if s.mid_price > 0]),
        }


# Global instance
realtime_feed = RealtimeFeed()


async def start_realtime_feed(market_ids: List[str] = None):
    """Start the real-time feed as a background task."""
    if not WEBSOCKETS_AVAILABLE:
        log.warning("WebSockets not available")
        return
    
    await realtime_feed.connect()
    
    if market_ids:
        await realtime_feed.subscribe(market_ids)
    
    # Start listening in background
    asyncio.create_task(realtime_feed.listen())
    log.info("Real-time feed started")
