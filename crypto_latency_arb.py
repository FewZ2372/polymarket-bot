"""
Crypto Latency Arbitrage Bot

Aprovecha el delay entre exchanges de crypto (Binance, Coinbase) y Polymarket
para tradear mercados de predicción sobre precios de crypto.

Estrategia:
1. Monitorea precios de BTC/ETH en Binance via WebSocket (latencia ~10-50ms)
2. Busca mercados en Polymarket tipo "Bitcoin above $X by date Y"
3. Cuando el precio real cruza el threshold, el mercado debería resolver YES/NO
4. Si Polymarket no refleja esto inmediatamente, hay oportunidad de arbitraje

Ejemplo:
- Mercado: "Bitcoin above $100,000 on Feb 10?"
- Precio actual de BTC en Binance: $100,050
- Precio YES en Polymarket: 0.65 (debería ser ~0.95+ porque ya cruzó)
- OPORTUNIDAD: Comprar YES barato, esperar resolución
"""

import asyncio
import json
import re
import time
from typing import Dict, List, Any, Optional, Callable, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

from logger import log
from config import config
from api.polymarket_api import PolymarketAPI, get_polymarket_api

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    log.warning("websockets not installed. Run: pip install websockets")


# ============================================================================
# CONFIGURACIÓN
# ============================================================================

@dataclass
class ArbConfig:
    """Configuración del bot de arbitraje."""
    # Crypto symbols to monitor
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    
    # Minimum price discrepancy to trigger (e.g., 0.15 = 15 cents difference)
    min_edge: float = 0.10
    
    # Maximum age of price data to consider valid (seconds)
    max_price_age_seconds: float = 2.0
    
    # How often to scan Polymarket for crypto markets (seconds)
    market_scan_interval: int = 60
    
    # Minimum liquidity in market to consider trading
    min_liquidity: float = 1000.0
    
    # Price buffer - how much above/below threshold to consider "safe"
    # e.g., if threshold is $100k and buffer is 0.005, we need $100,500 to be confident
    price_buffer_pct: float = 0.005
    
    # Auto-trade settings
    auto_trade: bool = False
    max_trade_amount: float = 10.0
    dry_run: bool = True


# ============================================================================
# CRYPTO PRICE FEED (BINANCE WEBSOCKET)
# ============================================================================

@dataclass
class CryptoPrice:
    """Real-time crypto price data."""
    symbol: str
    price: float
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "binance"
    
    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.timestamp).total_seconds()
    
    @property
    def is_fresh(self) -> bool:
        return self.age_seconds < 2.0


class BinanceFeed:
    """
    Ultra-fast WebSocket feed from Binance.
    
    Uses the combined stream endpoint for multiple symbols.
    Latency: typically 10-50ms from trade execution to our callback.
    """
    
    WS_URL = "wss://stream.binance.com:9443/ws"
    
    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or ["btcusdt", "ethusdt"]
        self._ws = None
        self._running = False
        self._prices: Dict[str, CryptoPrice] = {}
        self._callbacks: List[Callable[[CryptoPrice], None]] = []
        self._last_message_time: Optional[datetime] = None
        self._message_count = 0
        self._last_message_time = 0
        self._reconnect_delay = 1
    
    @property
    def stream_url(self) -> str:
        """Build combined stream URL for all symbols."""
        streams = "/".join([f"{s.lower()}@trade" for s in self.symbols])
        return f"{self.WS_URL}/{streams}"
    
    async def connect(self) -> bool:
        """Establish WebSocket connection to Binance."""
        if not WEBSOCKETS_AVAILABLE:
            log.error("websockets library not available")
            return False
        
        try:
            log.info(f"[BinanceFeed] Connecting to {self.stream_url}")
            self._ws = await websockets.connect(
                self.stream_url,
                ping_interval=20,
                ping_timeout=10,
            )
            self._running = True
            log.info(f"[BinanceFeed] Connected! Monitoring {self.symbols}")
            return True
            
        except Exception as e:
            log.error(f"[BinanceFeed] Connection failed: {e}")
            return False
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        log.info("[BinanceFeed] Disconnected")
    
    def add_callback(self, callback: Callable[[CryptoPrice], None]):
        """Add a callback for price updates."""
        self._callbacks.append(callback)
    
    def get_price(self, symbol: str) -> Optional[CryptoPrice]:
        """Get latest price for a symbol."""
        return self._prices.get(symbol.upper())
    
    def get_all_prices(self) -> Dict[str, CryptoPrice]:
        """Get all current prices."""
        return self._prices.copy()
    
    async def _process_message(self, message: str):
        """Process incoming trade message from Binance."""
        self._last_message_time = datetime.now()
        try:
            data = json.loads(message)
            
            # Trade message format
            if data.get('e') == 'trade':
                symbol = data.get('s', '').upper()  # e.g., "BTCUSDT"
                price = float(data.get('p', 0))     # Trade price
                
                if symbol and price > 0:
                    crypto_price = CryptoPrice(
                        symbol=symbol,
                        price=price,
                        timestamp=datetime.now(),
                        source="binance"
                    )
                    
                    self._prices[symbol] = crypto_price
                    self._message_count += 1
                    self._last_message_time = time.time()
                    
                    # Notify callbacks
                    for callback in self._callbacks:
                        try:
                            callback(crypto_price)
                        except Exception as e:
                            log.debug(f"[BinanceFeed] Callback error: {e}")
            
        except json.JSONDecodeError:
            log.debug(f"[BinanceFeed] Invalid JSON: {message[:100]}")
        except Exception as e:
            log.debug(f"[BinanceFeed] Error processing message: {e}")
    
    async def listen(self):
        """Main listening loop."""
        while self._running:
            try:
                if not self._ws:
                    connected = await self.connect()
                    if not connected:
                        await asyncio.sleep(self._reconnect_delay)
                        self._reconnect_delay = min(30, self._reconnect_delay * 2)
                        continue
                    self._reconnect_delay = 1
                
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=30
                )
                await self._process_message(message)
                
            except asyncio.TimeoutError:
                log.debug("[BinanceFeed] No message in 30s, checking connection...")
                if self._ws:
                    try:
                        await self._ws.ping()
                    except Exception:
                        self._ws = None
                        
            except websockets.exceptions.ConnectionClosed:
                log.warning("[BinanceFeed] Connection closed, reconnecting...")
                self._ws = None
                await asyncio.sleep(self._reconnect_delay)
                
            except Exception as e:
                log.error(f"[BinanceFeed] Error: {e}")
                self._ws = None
                await asyncio.sleep(self._reconnect_delay)
    
    def is_connected(self) -> bool:
        """Check if connected and receiving data."""
        if self._ws is None or not self._running:
            return False
        if self._last_message_time is None:
            return False
        # Consider connected if we received a message in the last 10 seconds
        age = (datetime.now() - self._last_message_time).total_seconds()
        return age < 10
    
    def get_stats(self) -> Dict[str, Any]:
        """Get feed statistics."""
        return {
            'connected': self.is_connected(),
            'running': self._running,
            'symbols': self.symbols,
            'message_count': self._message_count,
            'prices': {s: p.price for s, p in self._prices.items()},
            'last_update_age': time.time() - self._last_message_time if self._last_message_time else None,
        }


# ============================================================================
# POLYMARKET CRYPTO MARKET SCANNER
# ============================================================================

class MarketType(Enum):
    """Type of crypto price market."""
    ABOVE = "above"     # "BTC above $X"
    BELOW = "below"     # "BTC below $X"
    BETWEEN = "between" # "BTC between $X and $Y"
    EXACT = "exact"     # "BTC at exactly $X"
    UNKNOWN = "unknown"


@dataclass
class CryptoMarket:
    """A Polymarket market about crypto prices."""
    market_id: str
    question: str
    slug: str
    
    # Parsed info
    crypto_symbol: str        # "BTC", "ETH"
    market_type: MarketType   # above, below, etc.
    threshold_price: float    # The price threshold (e.g., 100000)
    threshold_price_upper: Optional[float] = None  # For "between" markets
    
    # Current market state
    yes_price: float = 0.0
    no_price: float = 0.0
    liquidity: float = 0.0
    volume_24h: float = 0.0
    
    # Dates
    end_date: Optional[datetime] = None
    
    # Token IDs for trading
    token_ids: List[str] = field(default_factory=list)
    condition_id: str = ""
    
    # Raw data
    raw_data: Dict = field(default_factory=dict)
    
    def should_be_yes(self, current_price: float, buffer_pct: float = 0.005) -> Optional[bool]:
        """
        Given current crypto price, determine what the market should resolve to.
        
        Returns:
            True if should be YES, False if should be NO, None if uncertain
        """
        if self.market_type == MarketType.ABOVE:
            # "BTC above $100k" → YES if price > 100k
            buffer = self.threshold_price * buffer_pct
            if current_price > self.threshold_price + buffer:
                return True
            elif current_price < self.threshold_price - buffer:
                return False
            return None  # Too close to threshold
            
        elif self.market_type == MarketType.BELOW:
            # "BTC below $100k" → YES if price < 100k
            buffer = self.threshold_price * buffer_pct
            if current_price < self.threshold_price - buffer:
                return True
            elif current_price > self.threshold_price + buffer:
                return False
            return None
            
        elif self.market_type == MarketType.BETWEEN:
            if self.threshold_price_upper is None:
                return None
            lower_buffer = self.threshold_price * buffer_pct
            upper_buffer = self.threshold_price_upper * buffer_pct
            if (current_price > self.threshold_price + lower_buffer and 
                current_price < self.threshold_price_upper - upper_buffer):
                return True
            elif (current_price < self.threshold_price - lower_buffer or
                  current_price > self.threshold_price_upper + upper_buffer):
                return False
            return None
        
        return None
    
    def get_edge(self, current_price: float, buffer_pct: float = 0.005) -> Optional[Tuple[str, float]]:
        """
        Calculate the edge (mispricing) based on current crypto price.
        
        Returns:
            Tuple of (action, edge) or None if no edge.
            action: "BUY_YES" or "BUY_NO"
            edge: Expected profit per dollar (e.g., 0.30 = 30 cents profit on $1 bet)
        """
        should_yes = self.should_be_yes(current_price, buffer_pct)
        
        if should_yes is None:
            return None
        
        if should_yes:
            # Market should resolve YES
            # If YES price is low, there's an edge
            fair_yes = 0.95  # Assume 95% certainty when price clearly crossed
            if self.yes_price < fair_yes:
                edge = fair_yes - self.yes_price
                return ("BUY_YES", edge)
        else:
            # Market should resolve NO
            fair_no = 0.95
            if self.no_price < fair_no:
                edge = fair_no - self.no_price
                return ("BUY_NO", edge)
        
        return None


class PolymarketCryptoScanner:
    """
    Scans Polymarket for crypto price prediction markets.
    
    Looks for markets like:
    - "Will Bitcoin be above $X on date Y?"
    - "Bitcoin price prediction"
    - "ETH above $X"
    """
    
    # Patterns to identify crypto price markets
    CRYPTO_PATTERNS = [
        # "Bitcoin above $100,000"
        (r'(bitcoin|btc)\s+(?:be\s+)?(?:above|over|higher than|exceed)\s*\$?([\d,]+)', 'BTC', MarketType.ABOVE),
        # "Bitcoin below $90,000"
        (r'(bitcoin|btc)\s+(?:be\s+)?(?:below|under|lower than|fall below)\s*\$?([\d,]+)', 'BTC', MarketType.BELOW),
        # "Will Bitcoin hit $100k"
        (r'(bitcoin|btc)\s+(?:hit|reach|touch)\s*\$?([\d,]+)', 'BTC', MarketType.ABOVE),
        # "Bitcoin dip to $60,000" - means it will go DOWN to that level
        (r'(bitcoin|btc)\s+(?:dip|drop|fall|crash)\s+(?:to|below)\s*\$?([\d,]+)', 'BTC', MarketType.BELOW),
        # Ethereum variants
        (r'(ethereum|eth)\s+(?:be\s+)?(?:above|over|higher than|exceed)\s*\$?([\d,]+)', 'ETH', MarketType.ABOVE),
        (r'(ethereum|eth)\s+(?:be\s+)?(?:below|under|lower than|fall below)\s*\$?([\d,]+)', 'ETH', MarketType.BELOW),
        (r'(ethereum|eth)\s+(?:hit|reach|touch)\s*\$?([\d,]+)', 'ETH', MarketType.ABOVE),
        (r'(ethereum|eth)\s+(?:dip|drop|fall|crash)\s+(?:to|below)\s*\$?([\d,]+)', 'ETH', MarketType.BELOW),
        # Generic "price above" patterns
        (r'(btc|bitcoin)\s+.*?\$?([\d,]+)k?\s*(?:by|before|on|in)', 'BTC', MarketType.ABOVE),
    ]
    
    def __init__(self, api: PolymarketAPI = None):
        self.api = api or get_polymarket_api()
        self._crypto_markets: Dict[str, CryptoMarket] = {}
        self._last_scan: Optional[datetime] = None
    
    def _parse_price(self, price_str: str) -> float:
        """Parse price string like '100,000' or '100k' to float."""
        price_str = price_str.replace(',', '').lower()
        
        if 'k' in price_str:
            return float(price_str.replace('k', '')) * 1000
        elif 'm' in price_str:
            return float(price_str.replace('m', '')) * 1_000_000
        
        return float(price_str)
    
    def _parse_market(self, raw: Dict[str, Any]) -> Optional[CryptoMarket]:
        """Try to parse a market as a crypto price market."""
        question = raw.get('question', '').lower()
        
        for pattern, symbol, market_type in self.CRYPTO_PATTERNS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                try:
                    price_str = match.group(2)
                    threshold_price = self._parse_price(price_str)
                    
                    # Parse market prices
                    prices_str = raw.get('outcomePrices', '[]')
                    try:
                        prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                        yes_price = float(prices[0]) if len(prices) > 0 else 0.0
                        no_price = float(prices[1]) if len(prices) > 1 else 1.0 - yes_price
                    except:
                        yes_price = 0.0
                        no_price = 0.0
                    
                    # Parse end date
                    end_date = None
                    if raw.get('endDate'):
                        try:
                            end_str = raw['endDate'].replace('Z', '+00:00')
                            end_date = datetime.fromisoformat(end_str)
                            if end_date.tzinfo:
                                end_date = end_date.replace(tzinfo=None)
                        except:
                            pass
                    
                    return CryptoMarket(
                        market_id=raw.get('id', ''),
                        question=raw.get('question', ''),
                        slug=raw.get('slug', ''),
                        crypto_symbol=symbol,
                        market_type=market_type,
                        threshold_price=threshold_price,
                        yes_price=yes_price,
                        no_price=no_price,
                        liquidity=float(raw.get('liquidity', 0) or 0),
                        volume_24h=float(raw.get('volume24hr', 0) or 0),
                        end_date=end_date,
                        token_ids=raw.get('clobTokenIds', []),
                        condition_id=raw.get('conditionId', ''),
                        raw_data=raw,
                    )
                    
                except (ValueError, IndexError) as e:
                    log.debug(f"[CryptoScanner] Failed to parse market: {e}")
                    continue
        
        return None
    
    def scan_markets(self, min_liquidity: float = 1000.0) -> List[CryptoMarket]:
        """
        Scan Polymarket for crypto price markets.
        
        Args:
            min_liquidity: Minimum liquidity to consider
        
        Returns:
            List of CryptoMarket objects
        """
        log.info("[CryptoScanner] Scanning for crypto price markets...")
        
        # Get all active markets
        raw_markets = self.api.get_markets(limit=500, active_only=True, use_cache=False)
        
        crypto_markets = []
        
        for raw in raw_markets:
            # Quick filter - must contain crypto keywords
            question = raw.get('question', '').lower()
            if not any(kw in question for kw in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto']):
                continue
            
            # Skip low liquidity
            liquidity = float(raw.get('liquidity', 0) or 0)
            if liquidity < min_liquidity:
                continue
            
            # Try to parse as crypto market
            market = self._parse_market(raw)
            if market:
                crypto_markets.append(market)
                self._crypto_markets[market.market_id] = market
        
        self._last_scan = datetime.now()
        
        log.info(f"[CryptoScanner] Found {len(crypto_markets)} crypto price markets")
        
        for m in crypto_markets[:5]:  # Log first 5
            log.info(f"  - {m.crypto_symbol} {m.market_type.value} ${m.threshold_price:,.0f} | YES: {m.yes_price:.2f} | Liq: ${m.liquidity:,.0f}")
        
        return crypto_markets
    
    def get_market(self, market_id: str) -> Optional[CryptoMarket]:
        """Get a cached crypto market by ID."""
        return self._crypto_markets.get(market_id)
    
    def get_all_markets(self) -> List[CryptoMarket]:
        """Get all cached crypto markets."""
        return list(self._crypto_markets.values())
    
    def get_markets_for_symbol(self, symbol: str) -> List[CryptoMarket]:
        """Get markets for a specific crypto symbol."""
        symbol = symbol.upper().replace('USDT', '')
        return [m for m in self._crypto_markets.values() if m.crypto_symbol == symbol]


# ============================================================================
# LATENCY ARBITRAGE DETECTOR
# ============================================================================

@dataclass
class ArbOpportunity:
    """A detected arbitrage opportunity."""
    market: CryptoMarket
    crypto_price: CryptoPrice
    action: str              # "BUY_YES" or "BUY_NO"
    edge: float              # Expected profit per dollar
    confidence: float        # 0-1 confidence in the signal
    timestamp: datetime = field(default_factory=datetime.now)
    
    @property
    def expected_return_pct(self) -> float:
        """Expected return as percentage."""
        return self.edge * 100
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_id': self.market.market_id,
            'question': self.market.question,
            'crypto': self.market.crypto_symbol,
            'crypto_price': self.crypto_price.price,
            'threshold': self.market.threshold_price,
            'market_type': self.market.market_type.value,
            'action': self.action,
            'current_price': self.market.yes_price if 'YES' in self.action else self.market.no_price,
            'edge': self.edge,
            'expected_return_pct': self.expected_return_pct,
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat(),
        }


class LatencyArbDetector:
    """
    Detects arbitrage opportunities between crypto exchanges and Polymarket.
    
    The key insight: When BTC crosses a threshold (e.g., $100k), Polymarket markets
    about "BTC above $100k" should immediately reflect this. If there's a delay,
    we can profit by being first.
    """
    
    def __init__(self, config: ArbConfig = None):
        self.config = config or ArbConfig()
        self._opportunities: List[ArbOpportunity] = []
        self._callbacks: List[Callable[[ArbOpportunity], None]] = []
        
    def add_callback(self, callback: Callable[[ArbOpportunity], None]):
        """Add callback for when opportunity is detected."""
        self._callbacks.append(callback)
    
    def check_opportunity(
        self, 
        market: CryptoMarket, 
        crypto_price: CryptoPrice
    ) -> Optional[ArbOpportunity]:
        """
        Check if there's an arbitrage opportunity for a given market.
        
        Args:
            market: The Polymarket market
            crypto_price: Current crypto price from exchange
        
        Returns:
            ArbOpportunity if found, None otherwise
        """
        # Verify price is fresh
        if not crypto_price.is_fresh:
            return None
        
        # Check if symbols match
        market_symbol = market.crypto_symbol
        price_symbol = crypto_price.symbol.upper().replace('USDT', '')
        
        if market_symbol != price_symbol:
            return None
        
        # Calculate edge
        edge_result = market.get_edge(
            crypto_price.price, 
            buffer_pct=self.config.price_buffer_pct
        )
        
        if edge_result is None:
            return None
        
        action, edge = edge_result
        
        # Check minimum edge
        if edge < self.config.min_edge:
            return None
        
        # Calculate confidence based on how far past threshold we are
        price_diff_pct = abs(crypto_price.price - market.threshold_price) / market.threshold_price
        confidence = min(1.0, price_diff_pct * 10)  # 10% diff = 100% confidence
        
        opportunity = ArbOpportunity(
            market=market,
            crypto_price=crypto_price,
            action=action,
            edge=edge,
            confidence=confidence,
        )
        
        self._opportunities.append(opportunity)
        
        # Notify callbacks
        for callback in self._callbacks:
            try:
                callback(opportunity)
            except Exception as e:
                log.error(f"[ArbDetector] Callback error: {e}")
        
        return opportunity
    
    def scan_all_markets(
        self, 
        markets: List[CryptoMarket], 
        prices: Dict[str, CryptoPrice]
    ) -> List[ArbOpportunity]:
        """
        Scan all markets against current prices.
        
        Args:
            markets: List of crypto markets
            prices: Dict of symbol -> CryptoPrice
        
        Returns:
            List of detected opportunities
        """
        opportunities = []
        
        for market in markets:
            # Get relevant price
            symbol_with_usdt = f"{market.crypto_symbol}USDT"
            crypto_price = prices.get(symbol_with_usdt)
            
            if not crypto_price:
                continue
            
            opp = self.check_opportunity(market, crypto_price)
            if opp:
                opportunities.append(opp)
        
        return opportunities
    
    def get_recent_opportunities(self, max_age_seconds: float = 60) -> List[ArbOpportunity]:
        """Get opportunities detected in the last N seconds."""
        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        return [o for o in self._opportunities if o.timestamp > cutoff]
    
    def clear_old_opportunities(self, max_age_seconds: float = 300):
        """Remove opportunities older than N seconds."""
        cutoff = datetime.now() - timedelta(seconds=max_age_seconds)
        self._opportunities = [o for o in self._opportunities if o.timestamp > cutoff]


# ============================================================================
# MAIN BOT ORCHESTRATOR
# ============================================================================

class CryptoArbBot:
    """
    Main bot that orchestrates the crypto latency arbitrage strategy.
    
    Flow:
    1. Connect to Binance WebSocket for live crypto prices
    2. Periodically scan Polymarket for crypto price markets
    3. On each price update, check for arbitrage opportunities
    4. When opportunity found, execute trade (or alert in dry-run mode)
    """
    
    def __init__(self, arb_config: ArbConfig = None):
        self.arb_config = arb_config or ArbConfig()
        
        # Components
        self.binance_feed = BinanceFeed(symbols=self.arb_config.symbols)
        self.poly_scanner = PolymarketCryptoScanner()
        self.arb_detector = LatencyArbDetector(self.arb_config)
        
        # State
        self._running = False
        self._crypto_markets: List[CryptoMarket] = []
        self._last_market_scan: Optional[datetime] = None
        self._opportunities_found = 0
        self._trades_executed = 0
        
        # Setup callbacks
        self.binance_feed.add_callback(self._on_price_update)
        self.arb_detector.add_callback(self._on_opportunity)
        
        # Import trader if available
        self._trader = None
        if self.arb_config.auto_trade:
            try:
                from trader import trader
                self._trader = trader
            except ImportError:
                log.warning("[CryptoArbBot] trader not available, dry-run only")
    
    def _on_price_update(self, price: CryptoPrice):
        """Called on each price update from Binance."""
        # Quick check against all markets
        symbol = price.symbol.replace('USDT', '')
        relevant_markets = [m for m in self._crypto_markets if m.crypto_symbol == symbol]
        
        for market in relevant_markets:
            self.arb_detector.check_opportunity(market, price)
    
    def _on_opportunity(self, opp: ArbOpportunity):
        """Called when an arbitrage opportunity is detected."""
        self._opportunities_found += 1
        
        # Format alert
        direction = "📈" if "YES" in opp.action else "📉"
        log.info(f"""
╔══════════════════════════════════════════════════════════════════╗
║  {direction} ARBITRAGE OPPORTUNITY DETECTED!
╠══════════════════════════════════════════════════════════════════╣
║  Market: {opp.market.question[:55]}...
║  Crypto: {opp.market.crypto_symbol} @ ${opp.crypto_price.price:,.2f}
║  Threshold: ${opp.market.threshold_price:,.0f} ({opp.market.market_type.value})
║  
║  Action: {opp.action}
║  Current Price: {opp.market.yes_price if 'YES' in opp.action else opp.market.no_price:.4f}
║  Expected Edge: {opp.edge:.2%} ({opp.expected_return_pct:.1f}% return)
║  Confidence: {opp.confidence:.1%}
║  
║  Liquidity: ${opp.market.liquidity:,.0f}
║  Price Age: {opp.crypto_price.age_seconds*1000:.0f}ms
╚══════════════════════════════════════════════════════════════════╝
""")
        
        # Execute trade if enabled
        if self.arb_config.auto_trade and not self.arb_config.dry_run:
            self._execute_trade(opp)
        else:
            log.info(f"[DRY RUN] Would execute: {opp.action} on {opp.market.market_id}")
    
    def _execute_trade(self, opp: ArbOpportunity):
        """Execute a trade for the opportunity."""
        if not self._trader or not self._trader.is_ready:
            log.warning("[CryptoArbBot] Trader not ready, skipping execution")
            return
        
        try:
            # Build opportunity dict for trader
            outcome = "YES" if "YES" in opp.action else "NO"
            trade_opp = {
                'id': opp.market.market_id,
                'question': opp.market.question,
                'yes': opp.market.yes_price,
                'no': opp.market.no_price,
                'score': int(opp.confidence * 100),
                'suggested_amount': min(self.arb_config.max_trade_amount, opp.market.liquidity * 0.01),
                'token_id': opp.market.token_ids[0] if opp.market.token_ids else None,
                'condition_id': opp.market.condition_id,
                'type': 'CRYPTO_ARB',
            }
            
            result = self._trader.execute_trade(trade_opp)
            
            if result.success:
                self._trades_executed += 1
                log.info(f"[CryptoArbBot] Trade executed: {result.order_id}")
            else:
                log.error(f"[CryptoArbBot] Trade failed: {result.error}")
                
        except Exception as e:
            log.error(f"[CryptoArbBot] Trade execution error: {e}")
    
    async def _market_scan_loop(self):
        """Periodically scan Polymarket for crypto markets."""
        while self._running:
            try:
                self._crypto_markets = self.poly_scanner.scan_markets(
                    min_liquidity=self.arb_config.min_liquidity
                )
                self._last_market_scan = datetime.now()
                
                log.info(f"[CryptoArbBot] Updated market list: {len(self._crypto_markets)} markets")
                
            except Exception as e:
                log.error(f"[CryptoArbBot] Market scan error: {e}")
            
            await asyncio.sleep(self.arb_config.market_scan_interval)
    
    async def start(self):
        """Start the bot."""
        log.info("""
╔══════════════════════════════════════════════════════════════════╗
║           CRYPTO LATENCY ARBITRAGE BOT STARTING                 ║
╠══════════════════════════════════════════════════════════════════╣
║  Strategy: Monitor crypto prices from Binance, detect when      ║
║  Polymarket crypto markets are mispriced, and trade the edge.   ║
║                                                                  ║
║  Symbols: {symbols}
║  Min Edge: {edge:.1%}
║  Auto Trade: {auto}
║  Dry Run: {dry}
╚══════════════════════════════════════════════════════════════════╝
""".format(
            symbols=', '.join(self.arb_config.symbols),
            edge=self.arb_config.min_edge,
            auto=self.arb_config.auto_trade,
            dry=self.arb_config.dry_run
        ))
        
        self._running = True
        
        # Initial market scan
        self._crypto_markets = self.poly_scanner.scan_markets(
            min_liquidity=self.arb_config.min_liquidity
        )
        
        if not self._crypto_markets:
            log.warning("[CryptoArbBot] No crypto markets found! Will retry periodically.")
        
        # Start tasks
        tasks = [
            asyncio.create_task(self.binance_feed.listen()),
            asyncio.create_task(self._market_scan_loop()),
            asyncio.create_task(self._status_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("[CryptoArbBot] Shutting down...")
        finally:
            await self.stop()
    
    async def _status_loop(self):
        """Print periodic status updates."""
        while self._running:
            await asyncio.sleep(30)  # Every 30 seconds
            
            prices = self.binance_feed.get_all_prices()
            price_str = ', '.join([f"{s}: ${p.price:,.2f}" for s, p in prices.items()])
            
            recent_opps = self.arb_detector.get_recent_opportunities(60)
            
            log.info(f"[STATUS] Prices: {price_str} | Markets: {len(self._crypto_markets)} | Recent Opps: {len(recent_opps)} | Total Found: {self._opportunities_found}")
    
    async def stop(self):
        """Stop the bot."""
        self._running = False
        await self.binance_feed.disconnect()
        log.info("[CryptoArbBot] Stopped")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current bot statistics."""
        return {
            'running': self._running,
            'binance': self.binance_feed.get_stats(),
            'crypto_markets': len(self._crypto_markets),
            'opportunities_found': self._opportunities_found,
            'trades_executed': self._trades_executed,
            'last_market_scan': self._last_market_scan.isoformat() if self._last_market_scan else None,
            'config': {
                'symbols': self.arb_config.symbols,
                'min_edge': self.arb_config.min_edge,
                'auto_trade': self.arb_config.auto_trade,
                'dry_run': self.arb_config.dry_run,
            }
        }


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Crypto Latency Arbitrage Bot')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'],
                        help='Crypto symbols to monitor (e.g., BTCUSDT ETHUSDT)')
    parser.add_argument('--min-edge', type=float, default=0.10,
                        help='Minimum edge to trigger (default: 0.10 = 10%%)')
    parser.add_argument('--min-liquidity', type=float, default=1000,
                        help='Minimum market liquidity (default: $1000)')
    parser.add_argument('--auto-trade', action='store_true',
                        help='Enable automatic trading')
    parser.add_argument('--live', action='store_true',
                        help='Disable dry-run mode (REAL TRADES)')
    parser.add_argument('--max-trade', type=float, default=10.0,
                        help='Maximum trade amount in USDC (default: $10)')
    
    args = parser.parse_args()
    
    # Build config
    arb_config = ArbConfig(
        symbols=args.symbols,
        min_edge=args.min_edge,
        min_liquidity=args.min_liquidity,
        auto_trade=args.auto_trade,
        dry_run=not args.live,
        max_trade_amount=args.max_trade,
    )
    
    # Safety warning
    if args.live:
        log.warning("⚠️  LIVE TRADING MODE ENABLED - REAL MONEY AT RISK ⚠️")
        log.warning("Press Ctrl+C within 5 seconds to abort...")
        await asyncio.sleep(5)
    
    # Start bot
    bot = CryptoArbBot(arb_config)
    
    try:
        await bot.start()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
