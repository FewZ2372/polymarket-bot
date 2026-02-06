"""
UP/DOWN 15-MINUTE ARBITRAGE BOT - REALISTIC VERSION
====================================================
Simula condiciones reales de trading en Polymarket:
- Taker fees reales de Polymarket (variable según precio)
- Slippage simulado basado en order book
- Delay de ejecución (1-3 segundos)
- Gas fees de Polygon (~$0.02 por tx)
- Liquidez limitada

Basado en documentación oficial: https://docs.polymarket.com/
"""

import asyncio
import json
import os
import random
import requests
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger("realistic_bot")


# ============================================================================
# CONFIGURACIÓN REALISTA
# ============================================================================

@dataclass
class RealisticConfig:
    """Configuración con parámetros realistas de Polymarket."""
    
    # Capital
    initial_capital: float = 150.0
    position_size: float = 25.0  # Tamaño más pequeño para $150 de capital
    max_positions: int = 5
    
    # Trading
    min_mispricing: float = 0.12  # Más alto para cubrir costos
    take_profit_pct: float = 0.20  # 20% para cubrir slippage
    stop_loss_pct: float = 0.15
    
    # Fees REALES de Polymarket (mercados 15-min crypto)
    # Fórmula: fee = shares * price * 0.25 * (price * (1 - price))^2
    # Máximo ~1.56% a precio $0.50
    use_real_fees: bool = True
    
    # Gas fees de Polygon (muy bajos)
    polygon_gas_per_tx: float = 0.02  # ~$0.02 por transacción
    
    # Slippage simulado
    slippage_base_pct: float = 0.015  # 1.5% base
    slippage_random_pct: float = 0.015  # +/- 1.5% random
    
    # Delay de ejecución (segundos)
    execution_delay_min: float = 1.0
    execution_delay_max: float = 3.0
    
    # Liquidez: probabilidad de que no haya suficiente liquidez
    liquidity_fail_pct: float = 0.10  # 10% de trades fallan por liquidez
    
    # Símbolos
    symbols: List[str] = field(default_factory=lambda: ['BTC', 'ETH', 'SOL', 'XRP'])


# ============================================================================
# POLYMARKET FEE CALCULATOR
# ============================================================================

def calculate_polymarket_fee(shares: float, price: float) -> float:
    """
    Calcula el taker fee de Polymarket para mercados de 15-min crypto.
    
    Fórmula oficial: fee = shares * price * 0.25 * (price * (1 - price))^2
    
    Ejemplos (100 shares):
    - Price $0.10: fee $0.02 (0.20%)
    - Price $0.30: fee $0.33 (1.10%)
    - Price $0.50: fee $0.78 (1.56%) <- máximo
    - Price $0.70: fee $0.77 (1.10%)
    - Price $0.90: fee $0.18 (0.20%)
    """
    if price <= 0 or price >= 1:
        return 0.0
    
    fee = shares * price * 0.25 * (price * (1 - price)) ** 2
    return round(fee, 4)


def calculate_effective_fee_rate(price: float) -> float:
    """Calcula el fee rate efectivo para un precio dado."""
    if price <= 0 or price >= 1:
        return 0.0
    return 0.25 * (price * (1 - price)) ** 2


# ============================================================================
# SLIPPAGE SIMULATOR
# ============================================================================

class SlippageSimulator:
    """Simula slippage realista basado en condiciones de mercado."""
    
    def __init__(self, config: RealisticConfig):
        self.config = config
    
    def calculate_slippage(self, price: float, size_usd: float, is_buy: bool) -> float:
        """
        Calcula el slippage para una orden.
        
        Returns:
            float: Precio ajustado después del slippage
        """
        # Base slippage
        base = self.config.slippage_base_pct
        
        # Random component
        random_component = random.uniform(-self.config.slippage_random_pct, 
                                          self.config.slippage_random_pct)
        
        # Size impact: trades más grandes tienen más slippage
        size_factor = 1 + (size_usd / 100) * 0.5  # +50% slippage por cada $100
        
        # Precio impact: precios extremos tienen más slippage (menos liquidez)
        if price < 0.20 or price > 0.80:
            price_factor = 1.5  # 50% más slippage en extremos
        else:
            price_factor = 1.0
        
        total_slippage = (base + random_component) * size_factor * price_factor
        
        # Aplicar slippage (compra = precio sube, venta = precio baja)
        if is_buy:
            adjusted_price = price * (1 + total_slippage)
        else:
            adjusted_price = price * (1 - total_slippage)
        
        # Limitar entre 0.01 y 0.99
        return max(0.01, min(0.99, adjusted_price))
    
    def should_fail_liquidity(self, price: float) -> bool:
        """Determina si el trade falla por falta de liquidez."""
        # Más probable fallar en precios extremos
        if price < 0.15 or price > 0.85:
            fail_chance = self.config.liquidity_fail_pct * 2
        else:
            fail_chance = self.config.liquidity_fail_pct
        
        return random.random() < fail_chance


# ============================================================================
# BINANCE PRICES (igual que el bot original)
# ============================================================================

class BinancePrices:
    """Obtiene precios de Binance via REST API."""
    
    BASE_URL = "https://api.binance.com/api/v3"
    
    def __init__(self, symbols: List[str]):
        self.symbols = [f"{s}USDT" for s in symbols]
        self._prices: Dict[str, float] = {}
        self._last_update: Optional[datetime] = None
        self._historical_cache: Dict[str, float] = {}
    
    def update(self) -> Dict[str, float]:
        try:
            for symbol in self.symbols:
                url = f"{self.BASE_URL}/ticker/price?symbol={symbol}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    self._prices[symbol] = float(data['price'])
            self._last_update = datetime.now()
        except Exception as e:
            log.error(f"[Binance] Error: {e}")
        return self._prices
    
    def get_price(self, symbol: str) -> Optional[float]:
        if symbol not in self._prices:
            symbol = f"{symbol}USDT"
        return self._prices.get(symbol)
    
    def get_historical_price(self, symbol: str, timestamp: datetime) -> Optional[float]:
        import calendar
        
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        cache_key = f"{symbol}_{timestamp.strftime('%Y%m%d%H%M')}"
        if cache_key in self._historical_cache:
            return self._historical_cache[cache_key]
        
        try:
            ts_ms = int(calendar.timegm(timestamp.timetuple()) * 1000)
            url = f"{self.BASE_URL}/klines?symbol={symbol}&interval=1m&startTime={ts_ms}&limit=1"
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    price = float(data[0][1])
                    self._historical_cache[cache_key] = price
                    return price
        except Exception as e:
            log.warning(f"[Binance] Error getting historical: {e}")
        
        return None
    
    def get_trend(self, symbol: str = "BTC", lookback_minutes: int = 15) -> Tuple[str, float]:
        import calendar
        
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        current_price = self._prices.get(symbol)
        if not current_price:
            return "NEUTRAL", 0.0
        
        past_time = datetime.utcnow() - timedelta(minutes=lookback_minutes)
        
        try:
            ts_ms = int(calendar.timegm(past_time.timetuple()) * 1000)
            url = f"{self.BASE_URL}/klines?symbol={symbol}&interval=1m&startTime={ts_ms}&limit=1"
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    past_price = float(data[0][1])
                    change_pct = (current_price - past_price) / past_price * 100
                    
                    if change_pct <= -0.3:
                        return "BEARISH", change_pct
                    elif change_pct >= 0.3:
                        return "BULLISH", change_pct
                    else:
                        return "NEUTRAL", change_pct
        except Exception as e:
            log.warning(f"[Binance] Error getting trend: {e}")
        
        return "NEUTRAL", 0.0
    
    def get_all(self) -> Dict[str, float]:
        return self._prices.copy()


# ============================================================================
# MARKET SCANNER (simplificado del original)
# ============================================================================

@dataclass
class UpDownMarket:
    market_id: str
    crypto_symbol: str
    question: str
    end_time: datetime
    start_time: datetime
    up_price: float
    down_price: float
    
    @property
    def time_remaining_seconds(self) -> float:
        return max(0, (self.end_time - datetime.utcnow()).total_seconds())
    
    @property
    def time_elapsed_seconds(self) -> float:
        return max(0, (datetime.utcnow() - self.start_time).total_seconds())
    
    def to_dict(self) -> dict:
        return {
            'market_id': self.market_id,
            'crypto': self.crypto_symbol,
            'question': self.question,
            'up_price': self.up_price,
            'down_price': self.down_price,
            'time_remaining': f"{int(self.time_remaining_seconds)}s",
        }


class UpDownScanner:
    """Scanner de mercados Up/Down."""
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    SERIES_SLUGS = {
        'BTC': 'btc-up-or-down-15m',
        'ETH': 'eth-up-or-down-15m',
        'SOL': 'sol-up-or-down-15m',
        'XRP': 'xrp-up-or-down-15m',
    }
    
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
    
    def scan(self) -> List[UpDownMarket]:
        markets = []
        
        for symbol in self.symbols:
            slug = self.SERIES_SLUGS.get(symbol)
            if not slug:
                continue
            
            try:
                url = f"{self.GAMMA_API}/series?slug={slug}"
                resp = requests.get(url, timeout=(5, 10))
                
                if resp.status_code != 200:
                    continue
                
                data = resp.json()
                events = data.get('events', [])
                
                for event in events[:5]:
                    event_slug = event.get('slug', '')
                    if not event_slug:
                        continue
                    
                    detail_url = f"{self.GAMMA_API}/events?slug={event_slug}"
                    detail_resp = requests.get(detail_url, timeout=(5, 8))
                    
                    if detail_resp.status_code != 200:
                        continue
                    
                    event_data = detail_resp.json()
                    if isinstance(event_data, list) and len(event_data) > 0:
                        event_data = event_data[0]
                    
                    market = self._parse_event(event_data, symbol)
                    if market and 60 < market.time_remaining_seconds < 840:
                        markets.append(market)
                        log.info(f"[Scanner] {symbol}: Found 1 tradeable markets")
                        break
                        
            except Exception as e:
                log.debug(f"[Scanner] Error scanning {symbol}: {e}")
        
        return markets
    
    def _parse_event(self, event: dict, symbol: str) -> Optional[UpDownMarket]:
        try:
            end_str = event.get('endDate', '')
            if not end_str:
                return None
            
            end_time = datetime.fromisoformat(end_str.replace('Z', ''))
            start_time = end_time - timedelta(minutes=15)
            
            prices_str = event.get('outcomePrices', '')
            if not prices_str:
                return None
            
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            up_price = float(prices[0]) if len(prices) > 0 else 0.5
            down_price = float(prices[1]) if len(prices) > 1 else 0.5
            
            markets = event.get('markets', [])
            market_id = markets[0].get('id', '') if markets else event.get('id', '')
            
            return UpDownMarket(
                market_id=str(market_id),
                crypto_symbol=symbol,
                question=event.get('title', ''),
                end_time=end_time,
                start_time=start_time,
                up_price=up_price,
                down_price=down_price,
            )
        except Exception as e:
            log.debug(f"[Scanner] Parse error: {e}")
            return None


# ============================================================================
# POSITION (con fees realistas)
# ============================================================================

@dataclass
class RealisticPosition:
    id: str
    market_id: str
    crypto_symbol: str
    side: str  # "UP" or "DOWN"
    
    # Precios
    intended_entry_price: float  # Precio que queríamos
    actual_entry_price: float    # Precio real con slippage
    
    shares: float
    size: float  # USDC invertido
    
    # Fees
    entry_fee: float  # Polymarket taker fee
    entry_gas: float  # Polygon gas
    entry_slippage_cost: float  # Costo del slippage
    
    # Context
    crypto_price_at_entry: float
    start_price_estimate: float
    market_end_time: datetime
    opened_at: datetime
    
    # Exit
    exit_price: Optional[float] = None
    actual_exit_price: Optional[float] = None
    exit_fee: float = 0.0
    exit_gas: float = 0.0
    exit_slippage_cost: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    closed_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'crypto': self.crypto_symbol,
            'side': self.side,
            'intended_price': self.intended_entry_price,
            'actual_price': self.actual_entry_price,
            'shares': self.shares,
            'size': self.size,
            'entry_fee': self.entry_fee,
            'slippage': self.entry_slippage_cost,
            'pnl': self.pnl,
            'exit_reason': self.exit_reason,
        }


# ============================================================================
# PORTFOLIO REALISTA
# ============================================================================

class RealisticPortfolio:
    """Portfolio con simulación realista de costos."""
    
    STATE_FILE = "updown_realistic_state.json"
    
    def __init__(self, config: RealisticConfig, load_saved: bool = True):
        self.config = config
        self.slippage = SlippageSimulator(config)
        
        self.initial_capital = config.initial_capital
        self.cash = config.initial_capital
        
        self.positions: Dict[str, RealisticPosition] = {}
        self.closed_positions: List[RealisticPosition] = []
        
        self.trade_count = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        
        # Tracking de costos
        self.total_fees = 0.0
        self.total_slippage_cost = 0.0
        self.total_gas = 0.0
        self.failed_trades = 0  # Trades que fallaron por liquidez
        
        self.started_at = datetime.now()
        
        if load_saved:
            self.load_state()
    
    @property
    def total_value(self) -> float:
        exposure = sum(p.size for p in self.positions.values())
        return self.cash + exposure
    
    @property
    def total_costs(self) -> float:
        return self.total_fees + self.total_slippage_cost + self.total_gas
    
    def can_open(self) -> Tuple[bool, str]:
        if len(self.positions) >= self.config.max_positions:
            return False, "Max positions"
        if self.cash < self.config.position_size + 1:  # +$1 buffer
            return False, "Insufficient cash"
        return True, "OK"
    
    def open_position(
        self, 
        market: UpDownMarket, 
        side: str, 
        entry_price: float,
        crypto_price: float,
        start_price_estimate: float
    ) -> Optional[RealisticPosition]:
        """Abre posición con simulación realista de costos."""
        
        can, reason = self.can_open()
        if not can:
            log.warning(f"[Portfolio] Cannot open: {reason}")
            return None
        
        # Simular delay de ejecución
        delay = random.uniform(self.config.execution_delay_min, 
                               self.config.execution_delay_max)
        
        # Simular fallo por liquidez
        if self.slippage.should_fail_liquidity(entry_price):
            self.failed_trades += 1
            log.warning(f"[Portfolio] Trade FAILED: No liquidity at ${entry_price:.3f}")
            return None
        
        # Calcular slippage (compramos, precio sube)
        actual_entry = self.slippage.calculate_slippage(entry_price, 
                                                         self.config.position_size, 
                                                         is_buy=True)
        slippage_cost = (actual_entry - entry_price) * (self.config.position_size / actual_entry)
        
        # Calcular shares con precio real
        size = self.config.position_size
        shares = size / actual_entry
        
        # Calcular fee de Polymarket
        entry_fee = calculate_polymarket_fee(shares, actual_entry)
        
        # Gas de Polygon
        gas = self.config.polygon_gas_per_tx
        
        self.trade_count += 1
        position = RealisticPosition(
            id=f"R-{self.trade_count:04d}",
            market_id=market.market_id,
            crypto_symbol=market.crypto_symbol,
            side=side,
            intended_entry_price=entry_price,
            actual_entry_price=actual_entry,
            shares=shares,
            size=size,
            entry_fee=entry_fee,
            entry_gas=gas,
            entry_slippage_cost=slippage_cost,
            crypto_price_at_entry=crypto_price,
            start_price_estimate=start_price_estimate,
            market_end_time=market.end_time,
            opened_at=datetime.now(),
        )
        
        # Actualizar estado
        self.cash -= size
        self.total_fees += entry_fee
        self.total_slippage_cost += slippage_cost
        self.total_gas += gas
        
        self.positions[position.id] = position
        self.save_state()
        
        return position
    
    def close_position(self, position_id: str, exit_price: float, reason: str) -> Optional[float]:
        """Cierra posición con costos realistas."""
        
        if position_id not in self.positions:
            return None
        
        position = self.positions[position_id]
        
        # Para resolución ($0 o $1), no hay slippage
        if exit_price in [0.0, 1.0]:
            actual_exit = exit_price
            slippage_cost = 0.0
        else:
            # Simular slippage (vendemos, precio baja)
            actual_exit = self.slippage.calculate_slippage(exit_price, 
                                                            position.shares * exit_price, 
                                                            is_buy=False)
            slippage_cost = (exit_price - actual_exit) * position.shares
        
        # Calcular exit fee
        exit_value = position.shares * actual_exit
        exit_fee = calculate_polymarket_fee(position.shares, actual_exit) if exit_value > 0 else 0
        gas = self.config.polygon_gas_per_tx
        
        # Calcular PnL
        gross_value = exit_value
        total_costs = (position.entry_fee + position.entry_gas + position.entry_slippage_cost +
                      exit_fee + gas + slippage_cost)
        
        net_pnl = gross_value - position.size - total_costs
        
        # Actualizar posición
        position.exit_price = exit_price
        position.actual_exit_price = actual_exit
        position.exit_fee = exit_fee
        position.exit_gas = gas
        position.exit_slippage_cost = slippage_cost
        position.exit_reason = reason
        position.pnl = net_pnl
        position.closed_at = datetime.now()
        
        # Actualizar portfolio
        self.cash += gross_value
        self.total_fees += exit_fee
        self.total_slippage_cost += slippage_cost
        self.total_gas += gas
        self.total_pnl += net_pnl
        
        if net_pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        self.closed_positions.append(position)
        del self.positions[position_id]
        
        self.save_state()
        return net_pnl
    
    def get_stats(self) -> dict:
        runtime = (datetime.now() - self.started_at).total_seconds()
        hours = int(runtime // 3600)
        minutes = int((runtime % 3600) // 60)
        seconds = int(runtime % 60)
        
        total_trades = self.wins + self.losses
        win_rate = (self.wins / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'runtime': f"{hours}:{minutes:02d}:{seconds:02d}",
            'initial': self.initial_capital,
            'current': self.total_value,
            'cash': self.cash,
            'pnl': self.total_pnl,
            'pnl_pct': (self.total_value - self.initial_capital) / self.initial_capital * 100,
            'fees': self.total_fees,
            'slippage': self.total_slippage_cost,
            'gas': self.total_gas,
            'total_costs': self.total_costs,
            'trades': self.trade_count,
            'open': len(self.positions),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'failed_trades': self.failed_trades,
        }
    
    def save_state(self):
        """Guarda estado a disco."""
        def serialize_pos(p):
            return {
                'id': p.id,
                'market_id': p.market_id,
                'crypto_symbol': p.crypto_symbol,
                'side': p.side,
                'intended_entry_price': p.intended_entry_price,
                'actual_entry_price': p.actual_entry_price,
                'shares': p.shares,
                'size': p.size,
                'entry_fee': p.entry_fee,
                'entry_slippage_cost': p.entry_slippage_cost,
                'pnl': p.pnl,
                'exit_reason': p.exit_reason,
            }
        
        state = {
            'version': 1,
            'saved_at': datetime.now().isoformat(),
            'initial_capital': self.initial_capital,
            'cash': self.cash,
            'trade_count': self.trade_count,
            'wins': self.wins,
            'losses': self.losses,
            'total_pnl': self.total_pnl,
            'total_fees': self.total_fees,
            'total_slippage_cost': self.total_slippage_cost,
            'total_gas': self.total_gas,
            'failed_trades': self.failed_trades,
            'started_at': self.started_at.isoformat(),
            'positions': {},
            'closed_positions': [serialize_pos(p) for p in self.closed_positions[-50:]],
        }
        
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.error(f"[Portfolio] Save error: {e}")
    
    def load_state(self) -> bool:
        """Carga estado desde disco."""
        if not os.path.exists(self.STATE_FILE):
            log.info("[Portfolio] No saved state, starting fresh")
            return False
        
        try:
            with open(self.STATE_FILE, 'r') as f:
                state = json.load(f)
            
            self.initial_capital = state['initial_capital']
            self.cash = state['cash']
            self.trade_count = state['trade_count']
            self.wins = state['wins']
            self.losses = state['losses']
            self.total_pnl = state['total_pnl']
            self.total_fees = state.get('total_fees', 0)
            self.total_slippage_cost = state.get('total_slippage_cost', 0)
            self.total_gas = state.get('total_gas', 0)
            self.failed_trades = state.get('failed_trades', 0)
            self.started_at = datetime.fromisoformat(state['started_at'])
            
            log.info(f"[Portfolio] State restored: ${self.total_pnl:+.2f} PnL, {self.trade_count} trades")
            return True
        except Exception as e:
            log.error(f"[Portfolio] Load error: {e}")
            return False


# ============================================================================
# MAIN BOT
# ============================================================================

class RealisticArbBot:
    """Bot de arbitraje con simulación realista."""
    
    def __init__(self, config: RealisticConfig = None):
        self.config = config or RealisticConfig()
        
        self.binance = BinancePrices(self.config.symbols)
        self.scanner = UpDownScanner(self.config.symbols)
        self.portfolio = RealisticPortfolio(self.config)
        
        self._running = False
        self._markets: List[UpDownMarket] = []
        self._start_prices: Dict[str, Dict[datetime, float]] = {}
        
        self._current_trend = "NEUTRAL"
        self._trend_change_pct = 0.0
        self._last_trend_update = None
        
        self._opportunities_found = 0
    
    def _update_trend(self):
        now = datetime.utcnow()
        if self._last_trend_update and (now - self._last_trend_update).total_seconds() < 30:
            return
        
        self._current_trend, self._trend_change_pct = self.binance.get_trend("BTC", 15)
        self._last_trend_update = now
    
    def _get_start_price(self, market: UpDownMarket) -> Optional[float]:
        symbol = market.crypto_symbol
        if symbol in self._start_prices:
            if market.start_time in self._start_prices[symbol]:
                return self._start_prices[symbol][market.start_time]
        
        start_price = self.binance.get_historical_price(symbol, market.start_time)
        if start_price:
            if symbol not in self._start_prices:
                self._start_prices[symbol] = {}
            self._start_prices[symbol][market.start_time] = start_price
        return start_price
    
    def _calculate_fair_prices(self, market, current_price, start_price):
        import math
        
        price_change_pct = (current_price - start_price) / start_price
        time_elapsed = market.time_elapsed_seconds
        time_remaining = market.time_remaining_seconds
        total_time = time_elapsed + time_remaining
        
        if total_time <= 0:
            return 0.5, 0.5
        
        time_factor = time_elapsed / total_time
        volatility_factor = 2.0
        
        z_score = price_change_pct * 100 * volatility_factor * math.sqrt(time_factor + 0.1)
        fair_up = 1 / (1 + math.exp(-z_score))
        fair_up = max(0.05, min(0.95, fair_up))
        fair_down = 1 - fair_up
        
        return fair_up, fair_down
    
    def _analyze_market(self, market: UpDownMarket):
        time_remaining = market.time_remaining_seconds
        if time_remaining < 60 or time_remaining > 840:
            return
        
        current_price = self.binance.get_price(market.crypto_symbol)
        if not current_price:
            return
        
        start_price = self._get_start_price(market)
        if not start_price:
            return
        
        fair_up, fair_down = self._calculate_fair_prices(market, current_price, start_price)
        
        up_mispricing = fair_up - market.up_price
        down_mispricing = fair_down - market.down_price
        
        side = None
        entry_price = 0
        
        if up_mispricing > self.config.min_mispricing and market.up_price < 0.85:
            side = "UP"
            entry_price = market.up_price
        elif down_mispricing > self.config.min_mispricing and market.down_price < 0.85:
            side = "DOWN"
            entry_price = market.down_price
        
        # Trend filter
        if side:
            if self._current_trend == "BEARISH" and side == "UP":
                return
            elif self._current_trend == "BULLISH" and side == "DOWN":
                return
        
        if not side:
            return
        
        self._opportunities_found += 1
        
        # Check if already in market
        for pos in self.portfolio.positions.values():
            if pos.market_id == market.market_id:
                return
        
        # Calculate expected costs for logging
        expected_fee_rate = calculate_effective_fee_rate(entry_price)
        
        log.info(f"""
+====================================================================+
| [OPPORTUNITY] {market.crypto_symbol} {side} - REALISTIC
+--------------------------------------------------------------------+
|  Entry: ${entry_price:.3f} (+ ~{self.config.slippage_base_pct*100:.1f}% slippage expected)
|  Fee rate: {expected_fee_rate*100:.2f}%
|  Time remaining: {time_remaining:.0f}s
+====================================================================+
""")
        
        position = self.portfolio.open_position(
            market=market,
            side=side,
            entry_price=entry_price,
            crypto_price=current_price,
            start_price_estimate=start_price,
        )
        
        if position:
            log.info(f"[OPENED] {position.id}: {side} @ ${position.actual_entry_price:.3f} "
                    f"(intended ${entry_price:.3f}, slippage ${position.entry_slippage_cost:.2f})")
    
    def _check_exits(self):
        for pos_id, position in list(self.portfolio.positions.items()):
            market = None
            for m in self._markets:
                if m.market_id == position.market_id:
                    market = m
                    break
            
            if not market:
                # Mercado resolvió
                current_price = self.binance.get_price(position.crypto_symbol)
                if current_price:
                    is_up = current_price >= position.start_price_estimate
                    won = (position.side == "UP" and is_up) or (position.side == "DOWN" and not is_up)
                    exit_price = 1.00 if won else 0.00
                    reason = "RESOLVED: " + ("WIN" if won else "LOSS")
                    
                    pnl = self.portfolio.close_position(pos_id, exit_price, reason)
                    if pnl is not None:
                        log.info(f"[{reason}] {position.id} | PnL ${pnl:+.2f}")
                continue
            
            # Check TP/SL
            if position.side == "UP":
                current_market_price = market.up_price
            else:
                current_market_price = market.down_price
            
            current_value = position.shares * current_market_price
            unrealized_pnl_pct = (current_value - position.size) / position.size
            
            exit_reason = None
            
            if unrealized_pnl_pct >= self.config.take_profit_pct:
                exit_reason = f"TAKE PROFIT (+{unrealized_pnl_pct*100:.1f}%)"
            elif unrealized_pnl_pct <= -self.config.stop_loss_pct:
                exit_reason = f"STOP LOSS ({unrealized_pnl_pct*100:.1f}%)"
            elif market.time_remaining_seconds < 30:
                exit_reason = "PRE-RESOLUTION"
            
            if exit_reason:
                pnl = self.portfolio.close_position(pos_id, current_market_price, exit_reason)
                if pnl is not None:
                    log.info(f"[CLOSED] {position.id}: {exit_reason} | PnL ${pnl:+.2f}")
    
    async def _main_loop(self):
        while self._running:
            try:
                self.binance.update()
                self._update_trend()
                self._markets = self.scanner.scan()
                
                for market in self._markets:
                    self._analyze_market(market)
                
                self._check_exits()
            except Exception as e:
                log.error(f"[MainLoop] Error: {e}")
            
            await asyncio.sleep(5)
    
    async def _status_loop(self):
        while self._running:
            await asyncio.sleep(15)
            
            stats = self.portfolio.get_stats()
            prices = self.binance.get_all()
            
            price_str = " | ".join([f"{s.replace('USDT','')}: ${p:,.0f}" for s, p in prices.items()])
            trend_icon = {"BULLISH": "[^]", "BEARISH": "[v]", "NEUTRAL": "[-]"}.get(self._current_trend, "[-]")
            
            log.info(f"""
+====================================================================+
| [REALISTIC BOT] {stats['runtime']}
+--------------------------------------------------------------------+
| PRICES: {price_str}
| TREND: {self._current_trend} {trend_icon} (BTC 15m: {self._trend_change_pct:+.2f}%)
|
| PORTFOLIO (Capital: ${self.config.initial_capital})
|   Value: ${stats['current']:.2f} ({stats['pnl_pct']:+.1f}%)
|   Cash: ${stats['cash']:.2f}
|   P&L: ${stats['pnl']:+.2f}
|
| COSTS BREAKDOWN
|   Fees: ${stats['fees']:.2f}
|   Slippage: ${stats['slippage']:.2f}
|   Gas: ${stats['gas']:.2f}
|   TOTAL COSTS: ${stats['total_costs']:.2f}
|
| TRADES
|   Total: {stats['trades']} | Open: {stats['open']} | Failed: {stats['failed_trades']}
|   Wins: {stats['wins']} | Losses: {stats['losses']}
|   Win Rate: {stats['win_rate']:.0f}%
+====================================================================+
""")
    
    async def _watchdog_loop(self):
        while self._running:
            try:
                await asyncio.sleep(300)
                self.portfolio.save_state()
                log.debug("[Watchdog] State saved")
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[Watchdog] Error: {e}")
    
    async def start(self):
        log.info(f"""
+====================================================================+
|     REALISTIC UP/DOWN ARBITRAGE BOT                               |
+====================================================================+
|  Capital: ${self.config.initial_capital:.2f}
|  Position size: ${self.config.position_size:.2f}
|  
|  REALISTIC SETTINGS:
|  - Slippage: {self.config.slippage_base_pct*100:.1f}% +/- {self.config.slippage_random_pct*100:.1f}%
|  - Execution delay: {self.config.execution_delay_min}-{self.config.execution_delay_max}s
|  - Liquidity fail rate: {self.config.liquidity_fail_pct*100:.0f}%
|  - Polygon gas: ${self.config.polygon_gas_per_tx:.2f}/tx
|  - Polymarket fees: Variable (max 1.56%)
+====================================================================+
""")
        
        self._running = True
        self.binance.update()
        self._markets = self.scanner.scan()
        
        tasks = [
            asyncio.create_task(self._main_loop()),
            asyncio.create_task(self._status_loop()),
            asyncio.create_task(self._watchdog_loop()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self.portfolio.save_state()
    
    def stop(self):
        self._running = False
        stats = self.portfolio.get_stats()
        log.info(f"""
+====================================================================+
|     BOT STOPPED                                                    |
+====================================================================+
|  Final Value: ${stats['current']:.2f} ({stats['pnl_pct']:+.1f}%)
|  Total P&L: ${stats['pnl']:+.2f}
|  Total Costs: ${stats['total_costs']:.2f}
+====================================================================+
""")


# ============================================================================
# DASHBOARD
# ============================================================================

def run_with_dashboard(port: int = 8090):
    """Corre el bot con dashboard web."""
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    
    app = FastAPI(title="Realistic Up/Down Bot")
    bot = None
    
    DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Realistic Up/Down Bot</title>
    <style>
        body { font-family: monospace; background: #0a0a1a; color: #eee; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #ff6b6b; text-align: center; }
        .subtitle { color: #888; text-align: center; margin-bottom: 20px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 15px; }
        .card { background: #151530; padding: 15px; border-radius: 10px; border: 1px solid #333; }
        .card h3 { color: #888; margin-bottom: 10px; font-size: 12px; text-transform: uppercase; }
        .stat { margin: 8px 0; }
        .stat-value { font-size: 24px; color: #ff6b6b; }
        .stat-label { color: #666; font-size: 11px; }
        .positive { color: #00ff88; }
        .negative { color: #ff4466; }
        .cost { color: #ffaa00; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 11px; }
        th { color: #666; text-align: left; padding: 6px; border-bottom: 1px solid #333; }
        td { padding: 6px; border-bottom: 1px solid #222; }
        .warning { background: #332200; border-color: #664400; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Realistic Up/Down Bot</h1>
        <p class="subtitle">Simulacion con slippage, fees reales y delays</p>
        
        <div class="grid">
            <div class="card">
                <h3>Portfolio</h3>
                <div class="stat">
                    <div class="stat-value" id="value">$0.00</div>
                    <div class="stat-label">Total Value (Capital: $150)</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="pnl">$0.00</div>
                    <div class="stat-label">P&L</div>
                </div>
            </div>
            
            <div class="card warning">
                <h3>Costs (Realistic)</h3>
                <div class="stat">
                    <div class="stat-value cost" id="fees">$0.00</div>
                    <div class="stat-label">Polymarket Fees</div>
                </div>
                <div class="stat">
                    <div class="stat-value cost" id="slippage">$0.00</div>
                    <div class="stat-label">Slippage Cost</div>
                </div>
                <div class="stat">
                    <div class="stat-value cost" id="totalCosts">$0.00</div>
                    <div class="stat-label">TOTAL COSTS</div>
                </div>
            </div>
            
            <div class="card">
                <h3>Performance</h3>
                <div class="stat">
                    <div class="stat-value" id="winrate">0%</div>
                    <div class="stat-label">Win Rate</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="trades">0</div>
                    <div class="stat-label">Trades (Failed: <span id="failed">0</span>)</div>
                </div>
            </div>
            
            <div class="card">
                <h3>Trend</h3>
                <div class="stat">
                    <div class="stat-value" id="trend">-</div>
                    <div class="stat-label">BTC 15m Trend</div>
                </div>
            </div>
        </div>
        
        <div class="card" style="margin-top: 15px;">
            <h3>Recent Trades</h3>
            <table>
                <thead>
                    <tr><th>ID</th><th>Crypto</th><th>Side</th><th>Intended</th><th>Actual</th><th>Slippage</th><th>P&L</th><th>Reason</th></tr>
                </thead>
                <tbody id="trades_table"></tbody>
            </table>
        </div>
    </div>
    
    <script>
        async function update() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                document.getElementById('value').textContent = '$' + (data.stats?.current || 0).toFixed(2);
                const pnl = data.stats?.pnl || 0;
                document.getElementById('pnl').innerHTML = '<span class="' + (pnl >= 0 ? 'positive' : 'negative') + '">$' + pnl.toFixed(2) + '</span>';
                
                document.getElementById('fees').textContent = '$' + (data.stats?.fees || 0).toFixed(2);
                document.getElementById('slippage').textContent = '$' + (data.stats?.slippage || 0).toFixed(2);
                document.getElementById('totalCosts').textContent = '$' + (data.stats?.total_costs || 0).toFixed(2);
                
                document.getElementById('winrate').textContent = (data.stats?.win_rate || 0).toFixed(0) + '%';
                document.getElementById('trades').textContent = data.stats?.trades || 0;
                document.getElementById('failed').textContent = data.stats?.failed_trades || 0;
                
                const trend = data.trend?.direction || 'NEUTRAL';
                const trendEl = document.getElementById('trend');
                trendEl.textContent = trend + ' (' + (data.trend?.change_pct || 0).toFixed(2) + '%)';
                trendEl.className = 'stat-value ' + (trend === 'BULLISH' ? 'positive' : trend === 'BEARISH' ? 'negative' : '');
                
                document.getElementById('trades_table').innerHTML = (data.closed || []).slice(0, 15).map(t => 
                    `<tr>
                        <td>${t.id}</td>
                        <td>${t.crypto}</td>
                        <td>${t.side}</td>
                        <td>$${(t.intended_price || 0).toFixed(3)}</td>
                        <td>$${(t.actual_price || 0).toFixed(3)}</td>
                        <td class="cost">$${(t.slippage || 0).toFixed(2)}</td>
                        <td class="${t.pnl >= 0 ? 'positive' : 'negative'}">$${(t.pnl || 0).toFixed(2)}</td>
                        <td>${t.exit_reason || ''}</td>
                    </tr>`
                ).join('') || '<tr><td colspan="8">No trades yet</td></tr>';
                
            } catch(e) { console.error(e); }
        }
        
        setInterval(update, 2000);
        update();
    </script>
</body>
</html>
"""
    
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML
    
    @app.get("/api/status")
    async def status():
        if not bot:
            return {"running": False}
        
        return {
            "running": bot._running,
            "stats": bot.portfolio.get_stats(),
            "trend": {
                "direction": bot._current_trend,
                "change_pct": bot._trend_change_pct,
            },
            "closed": [p.to_dict() for p in reversed(bot.portfolio.closed_positions[-20:])],
        }
    
    async def main():
        nonlocal bot
        
        config = RealisticConfig(
            initial_capital=150.0,
            position_size=25.0,
            max_positions=5,
            min_mispricing=0.12,
            take_profit_pct=0.20,
            stop_loss_pct=0.15,
        )
        bot = RealisticArbBot(config)
        
        bot_task = asyncio.create_task(bot.start())
        
        server_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
        server = uvicorn.Server(server_config)
        
        log.info("="*60)
        log.info(f"REALISTIC DASHBOARD: http://localhost:{port}")
        log.info("="*60)
        
        try:
            await server.serve()
        except KeyboardInterrupt:
            pass
        finally:
            bot.stop()
            bot_task.cancel()
    
    asyncio.run(main())


if __name__ == "__main__":
    run_with_dashboard(port=8090)
