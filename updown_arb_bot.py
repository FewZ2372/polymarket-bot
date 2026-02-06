"""
Up/Down 15-Minute Arbitrage Bot

Arbitraje en mercados de 15 minutos "Up or Down" de Polymarket.
Estos mercados resuelven basándose en si el precio subió o bajó en 15 minutos.

Estrategia:
1. Buscar mercados activos de 15 minutos (BTC, ETH, SOL, etc.)
2. Obtener precio de inicio del período
3. Comparar con precio actual de Binance
4. Si el mercado está mal priceado, comprar
"""

import asyncio
import json
import re
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from logger import log


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class UpDownConfig:
    """Configuración del bot."""
    initial_capital: float = 1000.0
    position_size: float = 100.0
    max_positions: int = 10
    
    # Mispricing mínimo para tradear
    min_mispricing: float = 0.10  # 10%
    
    # Take Profit / Stop Loss
    take_profit_pct: float = 0.15   # 15% ganancia
    stop_loss_pct: float = 0.10     # 10% pérdida
    
    # Timing
    min_time_remaining_seconds: int = 60   # Mínimo 1 minuto para que resuelva
    max_time_remaining_seconds: int = 840  # Máximo 14 minutos (casi todo el período)
    
    # Fees
    winner_fee: float = 0.02
    gas_per_trade: float = 0.25
    
    # Cryptos a monitorear (solo las que tienen series de 15m activas)
    symbols: List[str] = field(default_factory=lambda: ['BTC', 'ETH', 'SOL', 'XRP'])


# ============================================================================
# BINANCE PRICES (Simple REST API)
# ============================================================================

class BinancePrices:
    """Obtiene precios de Binance via REST API."""
    
    BASE_URL = "https://api.binance.com/api/v3"
    
    def __init__(self, symbols: List[str]):
        self.symbols = [f"{s}USDT" for s in symbols]
        self._prices: Dict[str, float] = {}
        self._last_update: Optional[datetime] = None
        self._historical_cache: Dict[str, float] = {}  # Cache de precios históricos
    
    def update(self) -> Dict[str, float]:
        """Actualiza todos los precios."""
        try:
            for symbol in self.symbols:
                url = f"{self.BASE_URL}/ticker/price?symbol={symbol}"
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    self._prices[symbol] = float(data['price'])
            self._last_update = datetime.now()
        except Exception as e:
            log.error(f"[Binance] Error fetching prices: {e}")
        
        return self._prices
    
    def get_price(self, symbol: str) -> Optional[float]:
        """Obtiene precio de un símbolo."""
        if symbol not in self._prices:
            symbol = f"{symbol}USDT"
        return self._prices.get(symbol)
    
    def get_historical_price(self, symbol: str, timestamp: datetime) -> Optional[float]:
        """
        Obtiene el precio histórico de Binance al momento específico.
        Usa la API de klines para obtener el precio de apertura del minuto.
        
        IMPORTANTE: timestamp debe ser un datetime naive en UTC.
        """
        import calendar
        
        # Asegurar que el símbolo tenga el sufijo USDT
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        # Cache key incluye símbolo y timestamp redondeado al minuto
        cache_key = f"{symbol}_{timestamp.strftime('%Y%m%d%H%M')}"
        if cache_key in self._historical_cache:
            return self._historical_cache[cache_key]
        
        try:
            # Convertir datetime UTC a timestamp en milisegundos
            # Usar calendar.timegm() que interpreta correctamente como UTC
            # (a diferencia de timestamp() que asume localtime)
            ts_ms = int(calendar.timegm(timestamp.timetuple()) * 1000)
            
            url = f"{self.BASE_URL}/klines?symbol={symbol}&interval=1m&startTime={ts_ms}&limit=1"
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    # data[0][1] es el precio de apertura (open)
                    price = float(data[0][1])
                    self._historical_cache[cache_key] = price
                    return price
        except Exception as e:
            log.warning(f"[Binance] Error getting historical price for {symbol}: {e}")
        
        return None
    
    def get_all(self) -> Dict[str, float]:
        return self._prices.copy()
    
    def get_trend(self, symbol: str = "BTC", lookback_minutes: int = 15) -> Tuple[str, float]:
        """
        Detecta la tendencia del mercado basado en el cambio de precio.
        
        Returns:
            Tuple[str, float]: ("BULLISH" | "BEARISH" | "NEUTRAL", cambio_porcentual)
        """
        import calendar
        
        if not symbol.endswith('USDT'):
            symbol = f"{symbol}USDT"
        
        current_price = self._prices.get(symbol)
        if not current_price:
            return "NEUTRAL", 0.0
        
        # Obtener precio de hace X minutos
        past_time = datetime.utcnow() - timedelta(minutes=lookback_minutes)
        
        try:
            ts_ms = int(calendar.timegm(past_time.timetuple()) * 1000)
            url = f"{self.BASE_URL}/klines?symbol={symbol}&interval=1m&startTime={ts_ms}&limit=1"
            resp = requests.get(url, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    past_price = float(data[0][1])  # Open price
                    
                    change_pct = (current_price - past_price) / past_price * 100
                    
                    # Umbrales para determinar tendencia
                    if change_pct <= -0.3:  # Bajó más de 0.3%
                        return "BEARISH", change_pct
                    elif change_pct >= 0.3:  # Subió más de 0.3%
                        return "BULLISH", change_pct
                    else:
                        return "NEUTRAL", change_pct
        except Exception as e:
            log.warning(f"[Binance] Error getting trend: {e}")
        
        return "NEUTRAL", 0.0


# ============================================================================
# POLYMARKET UP/DOWN MARKETS
# ============================================================================

@dataclass
class UpDownMarket:
    """Mercado de Up/Down de 15 minutos."""
    market_id: str
    question: str
    crypto_symbol: str  # BTC, ETH, etc.
    
    # Timing
    start_time: datetime
    end_time: datetime
    
    # Prices
    up_price: float
    down_price: float
    
    # Liquidity
    liquidity: float
    
    # Token IDs for trading
    up_token_id: str
    down_token_id: str
    
    @property
    def time_remaining_seconds(self) -> float:
        # end_time está en UTC, usar utcnow para comparar
        return max(0, (self.end_time - datetime.utcnow()).total_seconds())
    
    @property
    def time_elapsed_seconds(self) -> float:
        # start_time está en UTC, usar utcnow para comparar
        return max(0, (datetime.utcnow() - self.start_time).total_seconds())
    
    @property
    def period_duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()
    
    def to_dict(self) -> dict:
        return {
            'market_id': self.market_id,
            'question': self.question[:40],
            'crypto': self.crypto_symbol,
            'up_price': self.up_price,
            'down_price': self.down_price,
            'time_remaining': f"{self.time_remaining_seconds:.0f}s",
            'liquidity': self.liquidity,
        }


class UpDownScanner:
    """Escanea Polymarket por mercados de Up/Down usando el endpoint /series."""
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    # Series de Up/Down por símbolo (verificado que funcionan)
    SERIES_SLUGS = {
        'BTC': 'btc-up-or-down-15m',   # 97 active
        'ETH': 'eth-up-or-down-15m',   # 96 active
        'SOL': 'sol-up-or-down-15m',   # 96 active
        'XRP': 'xrp-up-or-down-15m',   # 97 active
        # DOGE no tiene serie de 15m activa
    }
    
    def __init__(self, symbols: List[str] = None):
        self.symbols = symbols or ['BTC', 'ETH', 'SOL']
        self._markets_cache: List[UpDownMarket] = []
        self._last_scan: Optional[datetime] = None
    
    def scan(self) -> List[UpDownMarket]:
        """
        Escanea por mercados activos de Up/Down.
        
        Estrategia:
        1. Obtener lista de eventos de /series (sin precios)
        2. Filtrar por tiempo restante (2-15 min)
        3. Obtener detalles completos de /events?slug=X
        """
        markets = []
        now = datetime.utcnow()
        
        try:
            for symbol in self.symbols:
                series_slug = self.SERIES_SLUGS.get(symbol)
                if not series_slug:
                    continue
                
                # Paso 1: Obtener lista de eventos de la serie
                url = f"{self.GAMMA_API}/series?slug={series_slug}"
                resp = requests.get(url, timeout=(5, 10))  # (connect, read) timeout
                
                if resp.status_code != 200:
                    continue
                
                series_list = resp.json()
                if not series_list or len(series_list) == 0:
                    continue
                
                series = series_list[0]
                events_basic = series.get('events', [])
                
                # Paso 2: Filtrar eventos por tiempo
                relevant_slugs = []
                for event in events_basic:
                    if event.get('closed', False):
                        continue
                    
                    # Parsear endDate para filtrar
                    end_str = event.get('endDate', '')
                    if not end_str:
                        continue
                    
                    try:
                        end_str = end_str.replace('Z', '+00:00')
                        end_time = datetime.fromisoformat(end_str).replace(tzinfo=None)
                    except:
                        continue
                    
                    time_remaining = (end_time - now).total_seconds()
                    
                    # Solo mercados con 2-15 minutos restantes
                    if 120 <= time_remaining <= 900:
                        relevant_slugs.append((event.get('slug'), symbol, end_time))
                
                # Paso 3: Obtener detalles completos para eventos relevantes
                for event_slug, sym, end_time in relevant_slugs:
                    try:
                        detail_url = f"{self.GAMMA_API}/events?slug={event_slug}"
                        detail_resp = requests.get(detail_url, timeout=(5, 8))  # (connect, read)
                        
                        if detail_resp.status_code == 200:
                            full_events = detail_resp.json()
                            if full_events and len(full_events) > 0:
                                market = self._parse_full_event(full_events[0], sym)
                                if market:
                                    markets.append(market)
                    except Exception as e:
                        log.warning(f"[Scanner] Error getting details for {event_slug}: {e}")
                
                log.info(f"[Scanner] {symbol}: Found {len([m for m in markets if m.crypto_symbol == symbol])} tradeable markets")
            
            self._markets_cache = markets
            self._last_scan = datetime.now()
            
        except Exception as e:
            log.error(f"[Scanner] Error: {e}")
            import traceback
            traceback.print_exc()
            return self._markets_cache
        
        return markets
    
    def _parse_full_event(self, event: dict, crypto_symbol: str) -> Optional[UpDownMarket]:
        """Parsea un evento completo con markets y precios."""
        try:
            title = event.get('title', '')
            
            # Obtener mercado principal
            markets_data = event.get('markets', [])
            if not markets_data:
                return None
            
            market_data = markets_data[0]
            
            # Verificar que esté activo
            if market_data.get('closed', False):
                return None
            
            # Parsear precios - viene como string JSON
            outcome_prices_str = market_data.get('outcomePrices', '[]')
            if isinstance(outcome_prices_str, str):
                outcome_prices = json.loads(outcome_prices_str)
            else:
                outcome_prices = outcome_prices_str
                
            if len(outcome_prices) < 2:
                return None
            
            up_price = float(outcome_prices[0])
            down_price = float(outcome_prices[1])
            
            # Parsear token IDs
            token_ids_str = market_data.get('clobTokenIds', '[]')
            if isinstance(token_ids_str, str):
                token_ids = json.loads(token_ids_str)
            else:
                token_ids = token_ids_str
                
            if len(token_ids) < 2:
                token_ids = ['', '']
            
            # Parsear tiempos
            # IMPORTANTE: Para mercados Up/Down de 15 minutos, el startDate de la API
            # es la fecha de CREACIÓN del evento, NO el inicio del período de trading.
            # El inicio del período es siempre endDate - 15 minutos.
            end_time_str = event.get('endDate') or market_data.get('endDate')
            
            if not end_time_str:
                return None
            
            def parse_time(s):
                if not s:
                    return None
                s = s.replace('Z', '+00:00')
                try:
                    return datetime.fromisoformat(s).replace(tzinfo=None)
                except:
                    try:
                        return datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
                    except:
                        return None
            
            end_time = parse_time(end_time_str)
            
            if not end_time:
                return None
            
            # Para mercados de 15 minutos, el inicio es SIEMPRE end_time - 15 minutos
            start_time = end_time - timedelta(minutes=15)
            
            # Verificar que el mercado está activo
            now = datetime.utcnow()
            if now > end_time:
                return None
            
            return UpDownMarket(
                market_id=market_data.get('id', ''),
                question=market_data.get('question', title),
                crypto_symbol=crypto_symbol,
                start_time=start_time,
                end_time=end_time,
                up_price=up_price,
                down_price=down_price,
                liquidity=float(market_data.get('liquidityNum', 0) or market_data.get('liquidity', 0) or 0),
                up_token_id=token_ids[0],
                down_token_id=token_ids[1],
            )
            
        except Exception as e:
            log.warning(f"[Scanner] Parse error for {event.get('title', 'unknown')}: {e}")
            return None
    
# ============================================================================
# POSITION
# ============================================================================

@dataclass
class UpDownPosition:
    """Posición en mercado Up/Down."""
    id: str
    market_id: str
    crypto_symbol: str
    
    side: str  # "UP" or "DOWN"
    entry_price: float
    shares: float
    size: float
    
    # Context
    crypto_price_at_entry: float
    start_price_estimate: float  # Precio estimado al inicio del período
    market_end_time: datetime
    
    # Timing
    opened_at: datetime
    closed_at: Optional[datetime] = None
    
    # Exit
    exit_price: Optional[float] = None
    exit_reason: str = ""
    pnl: float = 0.0
    
    @property
    def age_seconds(self) -> float:
        return (datetime.now() - self.opened_at).total_seconds()
    
    def to_dict(self) -> dict:
        time_to_resolution = max(0, (self.market_end_time - datetime.now()).total_seconds())
        return {
            'id': self.id,
            'crypto': self.crypto_symbol,
            'side': self.side,
            'entry_price': self.entry_price,
            'shares': self.shares,
            'time_to_resolution': f"{time_to_resolution:.0f}s",
            'pnl': self.pnl,
            'exit_reason': self.exit_reason,
        }


# ============================================================================
# PORTFOLIO
# ============================================================================

class UpDownPortfolio:
    """Portfolio para Up/Down trading."""
    
    def __init__(self, config: UpDownConfig, load_saved: bool = True):
        self.config = config
        self.initial_capital = config.initial_capital
        self.cash = config.initial_capital
        
        self.positions: Dict[str, UpDownPosition] = {}
        self.closed_positions: List[UpDownPosition] = []
        
        self.trade_count = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.total_fees = 0.0
        
        self.started_at = datetime.now()
        
        # Intentar cargar estado guardado
        if load_saved:
            self.load_state()
    
    @property
    def total_value(self) -> float:
        exposure = sum(p.size for p in self.positions.values())
        return self.cash + exposure
    
    def can_open(self) -> Tuple[bool, str]:
        if len(self.positions) >= self.config.max_positions:
            return False, "Max positions"
        if self.cash < self.config.position_size + self.config.gas_per_trade:
            return False, "Insufficient cash"
        return True, "OK"
    
    def open_position(
        self, 
        market: UpDownMarket, 
        side: str, 
        entry_price: float,
        crypto_price: float,
        start_price_estimate: float
    ) -> Optional[UpDownPosition]:
        """Abre posición."""
        can, reason = self.can_open()
        if not can:
            log.warning(f"[Portfolio] Cannot open: {reason}")
            return None
        
        # Calcular shares
        size = self.config.position_size
        gas = self.config.gas_per_trade
        effective_size = size - gas
        shares = effective_size / entry_price
        
        self.trade_count += 1
        position = UpDownPosition(
            id=f"UD-{self.trade_count:04d}",
            market_id=market.market_id,
            crypto_symbol=market.crypto_symbol,
            side=side,
            entry_price=entry_price,
            shares=shares,
            size=size,
            crypto_price_at_entry=crypto_price,
            start_price_estimate=start_price_estimate,
            market_end_time=market.end_time,
            opened_at=datetime.now(),
        )
        
        self.cash -= size
        self.total_fees += gas
        self.positions[position.id] = position
        
        # Guardar estado
        self.save_state()
        
        return position
    
    def close_position(self, position_id: str, exit_price: float, reason: str):
        """Cierra posición."""
        if position_id not in self.positions:
            return
        
        position = self.positions[position_id]
        
        exit_value = position.shares * exit_price
        gas = self.config.gas_per_trade
        
        gross_pnl = exit_value - (position.size - self.config.gas_per_trade)
        fee = exit_value * self.config.winner_fee if gross_pnl > 0 else 0
        
        net_pnl = gross_pnl - fee - gas
        
        position.exit_price = exit_price
        position.exit_reason = reason
        position.pnl = net_pnl
        position.closed_at = datetime.now()
        
        self.cash += exit_value - fee - gas
        self.total_fees += fee + gas
        self.total_pnl += net_pnl
        
        if net_pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        self.closed_positions.append(position)
        del self.positions[position_id]
        
        # Guardar estado
        self.save_state()
    
    def get_stats(self) -> dict:
        runtime = (datetime.now() - self.started_at).total_seconds()
        hours = int(runtime // 3600)
        minutes = int((runtime % 3600) // 60)
        seconds = int(runtime % 60)
        
        win_rate = (self.wins / (self.wins + self.losses) * 100) if (self.wins + self.losses) > 0 else 0
        
        return {
            'runtime': f"{hours}:{minutes:02d}:{seconds:02d}",
            'initial': self.initial_capital,
            'current': self.total_value,
            'cash': self.cash,
            'pnl': self.total_pnl,
            'pnl_pct': (self.total_value - self.initial_capital) / self.initial_capital * 100,
            'fees': self.total_fees,
            'trades': self.trade_count,
            'open': len(self.positions),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
        }
    
    # ========================================================================
    # PERSISTENCIA
    # ========================================================================
    
    STATE_FILE = "updown_portfolio_state.json"
    
    def save_state(self):
        """Guarda el estado del portfolio a disco."""
        import json
        
        def serialize_position(p: UpDownPosition) -> dict:
            return {
                'id': p.id,
                'market_id': p.market_id,
                'crypto_symbol': p.crypto_symbol,
                'side': p.side,
                'entry_price': p.entry_price,
                'shares': p.shares,
                'size': p.size,
                'crypto_price_at_entry': p.crypto_price_at_entry,
                'start_price_estimate': p.start_price_estimate,
                'market_end_time': p.market_end_time.isoformat() if p.market_end_time else None,
                'opened_at': p.opened_at.isoformat() if p.opened_at else None,
                'exit_price': p.exit_price,
                'exit_reason': p.exit_reason,
                'pnl': p.pnl,
                'closed_at': p.closed_at.isoformat() if p.closed_at else None,
            }
        
        state = {
            'version': 2,
            'saved_at': datetime.now().isoformat(),
            'initial_capital': self.initial_capital,
            'cash': self.cash,
            'trade_count': self.trade_count,
            'wins': self.wins,
            'losses': self.losses,
            'total_pnl': self.total_pnl,
            'total_fees': self.total_fees,
            'started_at': self.started_at.isoformat(),
            'positions': {pid: serialize_position(p) for pid, p in self.positions.items()},
            'closed_positions': [serialize_position(p) for p in self.closed_positions[-100:]],  # Últimos 100
        }
        
        try:
            with open(self.STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
            log.debug(f"[Portfolio] State saved to {self.STATE_FILE}")
        except Exception as e:
            log.error(f"[Portfolio] Failed to save state: {e}")
    
    def load_state(self) -> bool:
        """Carga el estado del portfolio desde disco. Retorna True si cargó exitosamente."""
        import json
        import os
        
        if not os.path.exists(self.STATE_FILE):
            log.info("[Portfolio] No saved state found, starting fresh")
            return False
        
        def deserialize_position(data: dict) -> UpDownPosition:
            return UpDownPosition(
                id=data['id'],
                market_id=data['market_id'],
                crypto_symbol=data['crypto_symbol'],
                side=data['side'],
                entry_price=data['entry_price'],
                shares=data['shares'],
                size=data['size'],
                crypto_price_at_entry=data['crypto_price_at_entry'],
                start_price_estimate=data['start_price_estimate'],
                market_end_time=datetime.fromisoformat(data['market_end_time']) if data.get('market_end_time') else None,
                opened_at=datetime.fromisoformat(data['opened_at']) if data.get('opened_at') else None,
                exit_price=data.get('exit_price'),
                exit_reason=data.get('exit_reason'),
                pnl=data.get('pnl', 0.0),
                closed_at=datetime.fromisoformat(data['closed_at']) if data.get('closed_at') else None,
            )
        
        try:
            with open(self.STATE_FILE, 'r') as f:
                state = json.load(f)
            
            # Verificar versión
            if state.get('version', 1) < 2:
                log.warning("[Portfolio] Old state version, starting fresh")
                return False
            
            # Restaurar estado
            self.initial_capital = state['initial_capital']
            self.cash = state['cash']
            self.trade_count = state['trade_count']
            self.wins = state['wins']
            self.losses = state['losses']
            self.total_pnl = state['total_pnl']
            self.total_fees = state['total_fees']
            self.started_at = datetime.fromisoformat(state['started_at'])
            
            # Restaurar posiciones abiertas
            self.positions = {}
            for pid, pdata in state.get('positions', {}).items():
                pos = deserialize_position(pdata)
                # Verificar que la posición no haya expirado (mercado ya cerró)
                if pos.market_end_time and datetime.utcnow() < pos.market_end_time:
                    self.positions[pid] = pos
                else:
                    log.info(f"[Portfolio] Skipping expired position {pid}")
            
            # Restaurar historial de trades cerrados
            self.closed_positions = [deserialize_position(p) for p in state.get('closed_positions', [])]
            
            log.info(f"[Portfolio] State restored: ${self.total_pnl:+.2f} P&L, {self.trade_count} trades, {len(self.positions)} open positions")
            return True
            
        except Exception as e:
            log.error(f"[Portfolio] Failed to load state: {e}")
            return False


# ============================================================================
# MAIN BOT
# ============================================================================

class UpDownArbBot:
    """
    Bot de arbitraje para mercados Up/Down de 15 minutos.
    
    Estrategia:
    1. Escanea mercados activos de Up/Down (BTC, ETH, SOL, etc.)
    2. Para cada mercado, estima el precio de inicio del período
    3. Compara con precio actual de Binance
    4. Si BTC subió y "Up" está barato, comprar Up
    5. Si BTC bajó y "Down" está barato, comprar Down
    """
    
    def __init__(self, config: UpDownConfig = None):
        self.config = config or UpDownConfig()
        
        self.binance = BinancePrices(self.config.symbols)
        self.scanner = UpDownScanner(self.config.symbols)
        self.portfolio = UpDownPortfolio(self.config)
        
        self._running = False
        self._markets: List[UpDownMarket] = []
        
        # Tracking de precios de inicio (para estimar)
        self._start_prices: Dict[str, Dict[datetime, float]] = {}  # symbol -> {start_time -> price}
        
        # Stats
        self._opportunities_found = 0
        self._trades_attempted = 0
        
        # Trend tracking
        self._current_trend = "NEUTRAL"
        self._trend_change_pct = 0.0
        self._last_trend_update = None
        self._last_trend_change_time = None  # Cuándo cambió la tendencia
        self._trend_cooldown_seconds = 180   # 3 minutos de cooldown después de cambio
    
    def _update_trend(self):
        """Actualiza el detector de tendencia cada 30 segundos."""
        now = datetime.utcnow()
        
        # Solo actualizar cada 30 segundos
        if self._last_trend_update and (now - self._last_trend_update).total_seconds() < 30:
            return
        
        old_trend = self._current_trend
        self._current_trend, self._trend_change_pct = self.binance.get_trend("BTC", lookback_minutes=15)
        self._last_trend_update = now
        
        # Detectar cambio de tendencia
        if old_trend != self._current_trend and old_trend != "NEUTRAL":
            self._last_trend_change_time = now
            log.info(f"[TREND CHANGE] {old_trend} -> {self._current_trend} (BTC {self._trend_change_pct:+.2f}%) | Cooldown {self._trend_cooldown_seconds}s activado")
        
        log.debug(f"[Trend] BTC 15m: {self._current_trend} ({self._trend_change_pct:+.2f}%)")
    
    def _is_in_trend_cooldown(self) -> bool:
        """Verifica si estamos en cooldown después de un cambio de tendencia."""
        if not self._last_trend_change_time:
            return False
        
        elapsed = (datetime.utcnow() - self._last_trend_change_time).total_seconds()
        return elapsed < self._trend_cooldown_seconds
    
    def _get_start_price(self, market: UpDownMarket) -> Optional[float]:
        """
        Obtiene el precio REAL al inicio del período de 15 minutos.
        Usa la API de klines de Binance para obtener el precio histórico.
        """
        symbol = market.crypto_symbol
        
        # Verificar cache primero
        if symbol in self._start_prices:
            if market.start_time in self._start_prices[symbol]:
                return self._start_prices[symbol][market.start_time]
        
        # Obtener precio histórico de Binance
        start_price = self.binance.get_historical_price(symbol, market.start_time)
        
        if start_price:
            # Guardar en cache
            if symbol not in self._start_prices:
                self._start_prices[symbol] = {}
            self._start_prices[symbol][market.start_time] = start_price
            return start_price
        
        return None
    
    def _calculate_fair_prices(self, market: UpDownMarket, current_price: float, start_price: float) -> Tuple[float, float]:
        """
        Calcula los precios "fair" de Up y Down basado en el movimiento actual.
        
        Si el precio ya subió significativamente, Up debería valer más.
        Si el precio ya bajó significativamente, Down debería valer más.
        """
        time_remaining = market.time_remaining_seconds
        total_time = market.period_duration_seconds
        time_elapsed = market.time_elapsed_seconds
        
        if total_time <= 0:
            return 0.5, 0.5
        
        # Cambio porcentual
        if start_price <= 0:
            return 0.5, 0.5
        
        price_change_pct = (current_price - start_price) / start_price
        
        # Factor de tiempo: mientras más tiempo pasó, más "seguro" es el resultado
        time_factor = min(1.0, time_elapsed / total_time)
        
        # Calcular probabilidad de Up
        # Si el precio subió mucho y queda poco tiempo, Up es muy probable
        # Si el precio bajó mucho y queda poco tiempo, Down es muy probable
        
        # Modelo simple: logistic basado en cambio de precio
        # +1% de cambio con tiempo = ~60% probabilidad
        # +2% de cambio con tiempo = ~75% probabilidad
        # etc.
        
        import math
        
        # Ajustar por tiempo (cambios son más significativos con menos tiempo)
        adjusted_change = price_change_pct * (1 + time_factor * 2)
        
        # Sigmoid para convertir a probabilidad
        # Escala: ±3% = ~95% probabilidad
        k = 50  # Sensibilidad
        fair_up = 1 / (1 + math.exp(-k * adjusted_change))
        fair_down = 1 - fair_up
        
        return fair_up, fair_down
    
    def _analyze_market(self, market: UpDownMarket):
        """Analiza un mercado y decide si tradear."""
        # Verificar tiempo restante
        time_remaining = market.time_remaining_seconds
        if time_remaining < self.config.min_time_remaining_seconds:
            return  # Muy poco tiempo
        if time_remaining > self.config.max_time_remaining_seconds:
            return  # Muy temprano
        
        # Obtener precio actual
        current_price = self.binance.get_price(market.crypto_symbol)
        if not current_price:
            return
        
        # Obtener precio REAL al inicio del período (de Binance historical API)
        start_price = self._get_start_price(market)
        if not start_price:
            log.warning(f"[Analyze] Could not get start price for {market.crypto_symbol}")
            return
        
        # Calcular precios fair
        fair_up, fair_down = self._calculate_fair_prices(market, current_price, start_price)
        
        # Verificar mispricing
        up_mispricing = fair_up - market.up_price
        down_mispricing = fair_down - market.down_price
        
        # Determinar si hay oportunidad
        side = None
        entry_price = 0
        mispricing = 0
        
        if up_mispricing > self.config.min_mispricing and market.up_price < 0.90:
            side = "UP"
            entry_price = market.up_price
            mispricing = up_mispricing
        elif down_mispricing > self.config.min_mispricing and market.down_price < 0.90:
            side = "DOWN"
            entry_price = market.down_price
            mispricing = down_mispricing
        
        # =================================================================
        # TREND COOLDOWN: Esperar después de cambio de tendencia
        # =================================================================
        if side and self._is_in_trend_cooldown():
            elapsed = (datetime.utcnow() - self._last_trend_change_time).total_seconds()
            remaining = self._trend_cooldown_seconds - elapsed
            log.debug(f"[TrendCooldown] En cooldown, esperando {remaining:.0f}s más antes de tradear")
            return  # No tradear durante cooldown
        
        # =================================================================
        # TREND FILTER: Solo tradear en la dirección de la tendencia
        # =================================================================
        if side:
            # BEARISH: Solo permitir DOWN (mercado cayendo -> DOWN gana)
            if self._current_trend == "BEARISH" and side == "UP":
                log.debug(f"[TrendFilter] Skipping UP in BEARISH market ({self._trend_change_pct:+.2f}%)")
                side = None  # Bloquear la entrada UP
            # BULLISH: Solo permitir UP (mercado subiendo -> UP gana)  
            elif self._current_trend == "BULLISH" and side == "DOWN":
                log.debug(f"[TrendFilter] Skipping DOWN in BULLISH market ({self._trend_change_pct:+.2f}%)")
                side = None  # Bloquear la entrada DOWN
            # NEUTRAL: Permitir ambos
        
        if not side:
            # Log para debugging - ver por qué no hay oportunidad
            if self._opportunities_found == 0:  # Solo logear una vez para no spamear
                log.info(f"[Debug] {market.crypto_symbol}: Start=${start_price:,.2f}, Current=${current_price:,.2f}, Change={(current_price-start_price)/start_price*100:+.2f}%")
                log.info(f"[Debug] Market: Up=${market.up_price:.2f}, Down=${market.down_price:.2f} | Fair: Up=${fair_up:.2f}, Down=${fair_down:.2f}")
                log.info(f"[Debug] Mispricing: Up={up_mispricing*100:+.1f}%, Down={down_mispricing*100:+.1f}% (need >{self.config.min_mispricing*100}%)")
            return
        
        self._opportunities_found += 1
        
        # Ya tenemos posición en este mercado?
        for pos in self.portfolio.positions.values():
            if pos.market_id == market.market_id:
                return  # Ya estamos en este mercado
        
        price_change = (current_price - start_price) / start_price * 100
        
        log.info(f"""
+====================================================================+
| [OPPORTUNITY] {market.crypto_symbol} Up/Down
+--------------------------------------------------------------------+
|  Market: {market.question[:50]}...
|  Time remaining: {time_remaining:.0f}s
|  
|  {market.crypto_symbol} Price: ${current_price:,.2f}
|  Start estimate: ${start_price:,.2f} ({price_change:+.2f}%)
|  
|  Market prices: Up ${market.up_price:.2f} | Down ${market.down_price:.2f}
|  Fair prices:   Up ${fair_up:.2f} | Down ${fair_down:.2f}
|  
|  Action: BUY {side} @ ${entry_price:.2f}
|  Mispricing: {mispricing*100:.1f}%
+====================================================================+
""")
        
        # Abrir posición
        self._trades_attempted += 1
        position = self.portfolio.open_position(
            market=market,
            side=side,
            entry_price=entry_price,
            crypto_price=current_price,
            start_price_estimate=start_price,
        )
        
        if position:
            log.info(f"[OPENED] {position.id}: {side} @ ${entry_price:.2f} ({position.shares:.2f} shares)")
    
    def _check_exits(self):
        """Verifica posiciones para cerrar."""
        for pos_id, position in list(self.portfolio.positions.items()):
            # Buscar mercado actual
            market = None
            for m in self._markets:
                if m.market_id == position.market_id:
                    market = m
                    break
            
            if not market:
                # Mercado ya no está activo (probablemente resolvió)
                # Obtener precio actual para determinar resultado
                current_price = self.binance.get_price(position.crypto_symbol)
                if current_price:
                    # Usar el start_price_estimate que guardamos (precio al inicio del período)
                    # Para determinar si el precio subió o bajó
                    is_up = current_price >= position.start_price_estimate
                    won = (position.side == "UP" and is_up) or (position.side == "DOWN" and not is_up)
                    
                    # En Polymarket, si ganas recibes $1.00 por share, si pierdes $0.00
                    exit_price = 1.00 if won else 0.00
                    reason = "RESOLVED: " + ("WIN" if won else "LOSS")
                    
                    self.portfolio.close_position(pos_id, exit_price, reason)
                    
                    price_change = (current_price - position.start_price_estimate) / position.start_price_estimate * 100
                    log.info(f"[{reason}] {position.id}: {position.crypto_symbol} moved {price_change:+.2f}% | P&L ${position.pnl:+.2f}")
                continue
            
            # Obtener precio actual del mercado
            if position.side == "UP":
                current_market_price = market.up_price
            else:
                current_market_price = market.down_price
            
            # Calcular P&L no realizado
            current_value = position.shares * current_market_price
            cost = position.size - self.config.gas_per_trade
            unrealized_pnl_pct = (current_value - cost) / cost if cost > 0 else 0
            
            exit_reason = None
            
            # Take Profit
            if unrealized_pnl_pct >= self.config.take_profit_pct:
                exit_reason = f"TAKE PROFIT (+{unrealized_pnl_pct*100:.1f}%)"
            
            # Stop Loss basado en P&L
            elif unrealized_pnl_pct <= -self.config.stop_loss_pct:
                exit_reason = f"STOP LOSS ({unrealized_pnl_pct*100:.1f}%)"
            
            # ============================================================
            # PROBABILITY STOP LOSS: Salir si es muy probable que perdamos
            # ============================================================
            else:
                current_crypto_price = self.binance.get_price(position.crypto_symbol)
                if current_crypto_price and position.start_price_estimate:
                    price_change_pct = (current_crypto_price - position.start_price_estimate) / position.start_price_estimate * 100
                    
                    # Si compramos UP pero el precio bajó mucho -> muy probable perder
                    # Si compramos DOWN pero el precio subió mucho -> muy probable perder
                    is_losing_position = (
                        (position.side == "UP" and price_change_pct < -0.5) or
                        (position.side == "DOWN" and price_change_pct > 0.5)
                    )
                    
                    # Solo activar si queda poco tiempo (menos de 3 min) y estamos perdiendo
                    time_pressure = market.time_remaining_seconds < 180
                    
                    # O si el movimiento es muy extremo (>1%), salir aunque quede tiempo
                    extreme_move = abs(price_change_pct) > 1.0
                    
                    if is_losing_position and (time_pressure or extreme_move):
                        direction = "caido" if position.side == "UP" else "subido"
                        exit_reason = f"PROB STOP LOSS ({position.crypto_symbol} {direction} {price_change_pct:+.2f}%)"
            
            # Muy poco tiempo restante - cerrar antes de resolución
            if not exit_reason and market.time_remaining_seconds < 30:
                exit_reason = "PRE-RESOLUTION EXIT"
            
            if exit_reason:
                self.portfolio.close_position(pos_id, current_market_price, exit_reason)
                log.info(f"[CLOSED] {position.id}: {exit_reason} | P&L ${position.pnl:+.2f}")
    
    async def _main_loop(self):
        """Loop principal."""
        import traceback
        consecutive_errors = 0
        max_consecutive_errors = 10
        
        while self._running:
            try:
                # Actualizar precios
                self.binance.update()
                
                # Actualizar tendencia
                self._update_trend()
                
                # Escanear mercados
                self._markets = self.scanner.scan()
                
                # Analizar cada mercado
                for market in self._markets:
                    self._analyze_market(market)
                
                # Verificar exits
                self._check_exits()
                
                # Reset error counter on success
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                log.error(f"[MainLoop] Error ({consecutive_errors}/{max_consecutive_errors}): {e}")
                log.error(f"[MainLoop] Traceback: {traceback.format_exc()}")
                
                # Si hay muchos errores consecutivos, guardar estado y reintentar
                if consecutive_errors >= max_consecutive_errors:
                    log.error("[MainLoop] Demasiados errores consecutivos, guardando estado...")
                    self.portfolio.save_state()
                    consecutive_errors = 0
                    await asyncio.sleep(30)  # Esperar más tiempo antes de reintentar
                    continue
            
            await asyncio.sleep(5)  # Cada 5 segundos
    
    async def _status_loop(self):
        """Status periódico."""
        while self._running:
            await asyncio.sleep(15)
            
            stats = self.portfolio.get_stats()
            prices = self.binance.get_all()
            
            price_str = " | ".join([f"{s.replace('USDT','')}: ${p:,.0f}" for s, p in prices.items()])
            
            active_markets = [m for m in self._markets if m.time_remaining_seconds > 60]
            
            # Indicador visual de tendencia
            trend_emoji = {"BULLISH": "[^]", "BEARISH": "[v]", "NEUTRAL": "[-]"}.get(self._current_trend, "[-]")
            
            # Cooldown status
            cooldown_str = ""
            if self._is_in_trend_cooldown():
                elapsed = (datetime.utcnow() - self._last_trend_change_time).total_seconds()
                remaining = self._trend_cooldown_seconds - elapsed
                cooldown_str = f" [COOLDOWN {remaining:.0f}s]"
            
            log.info(f"""
+====================================================================+
| [UP/DOWN ARB] {stats['runtime']}
+--------------------------------------------------------------------+
| PRICES: {price_str}
| TREND: {self._current_trend} {trend_emoji} (BTC 15m: {self._trend_change_pct:+.2f}%){cooldown_str}
|
| MARKETS: {len(active_markets)} active Up/Down markets
|
| PORTFOLIO
|   Value: ${stats['current']:.2f} ({stats['pnl_pct']:+.1f}%)
|   Cash: ${stats['cash']:.2f}
|   P&L: ${stats['pnl']:+.2f}
|
| TRADES
|   Total: {stats['trades']} | Open: {stats['open']}
|   Wins: {stats['wins']} | Losses: {stats['losses']}
|   Win Rate: {stats['win_rate']:.0f}%
|
| DETECTION
|   Opportunities: {self._opportunities_found}
|   Trades: {self._trades_attempted}
+====================================================================+
""")
    
    async def _watchdog_loop(self):
        """
        Watchdog que guarda el estado periódicamente y detecta problemas.
        Esto asegura que no perdemos datos si el proceso muere inesperadamente.
        """
        save_interval = 300  # Guardar cada 5 minutos
        
        while self._running:
            try:
                await asyncio.sleep(save_interval)
                
                # Guardar estado periódicamente
                self.portfolio.save_state()
                log.debug(f"[Watchdog] Estado guardado automáticamente")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"[Watchdog] Error: {e}")
    
    async def start(self):
        """Inicia el bot."""
        log.info(f"""
+====================================================================+
|     UP/DOWN 15-MINUTE ARBITRAGE BOT                                |
+====================================================================+
|  Trades on 15-minute Up/Down markets
|  
|  Symbols: {', '.join(self.config.symbols)}
|  Capital: ${self.config.initial_capital:.2f}
|  Position size: ${self.config.position_size:.2f}
|  Min mispricing: {self.config.min_mispricing:.0%}
|  Take Profit: {self.config.take_profit_pct:.0%}
|  Stop Loss: {self.config.stop_loss_pct:.0%}
+====================================================================+
""")
        
        self._running = True
        
        # Initial scan
        self.binance.update()
        self._markets = self.scanner.scan()
        log.info(f"[Init] Found {len(self._markets)} Up/Down markets")
        
        for m in self._markets[:5]:
            log.info(f"  - {m.crypto_symbol}: Up ${m.up_price:.2f} | Down ${m.down_price:.2f} | {m.time_remaining_seconds:.0f}s remaining")
        
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
            await self.stop()
    
    async def stop(self):
        """Detiene el bot."""
        self._running = False
        
        stats = self.portfolio.get_stats()
        log.info(f"""
+====================================================================+
|     BOT STOPPED                                                    |
+====================================================================+
|  Runtime: {stats['runtime']}
|  Final Value: ${stats['current']:.2f} ({stats['pnl_pct']:+.1f}%)
|  Total P&L: ${stats['pnl']:+.2f}
|  Opportunities: {self._opportunities_found}
+====================================================================+
""")


# ============================================================================
# DASHBOARD
# ============================================================================

async def run_with_dashboard():
    """Corre el bot con dashboard."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    import uvicorn
    
    app = FastAPI(title="Up/Down Arb Bot")
    bot: Optional[UpDownArbBot] = None
    
    DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Up/Down 15m Arbitrage</title>
    <style>
        body { font-family: monospace; background: #0a0a1a; color: #eee; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: #00ffcc; text-align: center; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }
        .card { background: #151530; padding: 15px; border-radius: 10px; border: 1px solid #333; }
        .card h3 { color: #888; margin-bottom: 10px; font-size: 12px; text-transform: uppercase; }
        .stat { margin: 8px 0; }
        .stat-value { font-size: 28px; color: #00ffcc; }
        .stat-label { color: #666; font-size: 11px; }
        .positive { color: #00ff88; }
        .negative { color: #ff4466; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
        th { color: #666; text-align: left; padding: 8px; border-bottom: 1px solid #333; }
        td { padding: 8px; border-bottom: 1px solid #222; }
        .market-card { background: #1a1a3a; padding: 10px; border-radius: 8px; margin: 5px 0; }
        .up { color: #00ff88; }
        .down { color: #ff4466; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Up/Down 15-Minute Arbitrage</h1>
        
        <div class="grid">
            <div class="card">
                <h3>Portfolio</h3>
                <div class="stat">
                    <div class="stat-value" id="value">$0.00</div>
                    <div class="stat-label">Total Value</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="pnl">$0.00</div>
                    <div class="stat-label">P&L</div>
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
                    <div class="stat-label">Total Trades</div>
                </div>
            </div>
            
            <div class="card">
                <h3>Prices</h3>
                <div id="prices" style="font-size: 14px;"></div>
            </div>
            
            <div class="card">
                <h3>Trend Filter</h3>
                <div class="stat">
                    <div class="stat-value" id="trend">-</div>
                    <div class="stat-label">BTC 15m Trend</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="trendChange">0%</div>
                    <div class="stat-label">Price Change</div>
                </div>
            </div>
            
            <div class="card">
                <h3>Detection</h3>
                <div class="stat">
                    <div class="stat-value" id="opportunities">0</div>
                    <div class="stat-label">Opportunities Found</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="markets">0</div>
                    <div class="stat-label">Active Markets</div>
                </div>
            </div>
        </div>
        
        <div class="card" style="margin-top: 15px;">
            <h3>Active Markets</h3>
            <div id="activeMarkets"></div>
        </div>
        
        <div class="card" style="margin-top: 15px;">
            <h3>Open Positions</h3>
            <table>
                <thead><tr><th>ID</th><th>Crypto</th><th>Side</th><th>Entry</th><th>Time Left</th></tr></thead>
                <tbody id="positions"></tbody>
            </table>
        </div>
        
        <div class="card" style="margin-top: 15px;">
            <h3>Recent Trades</h3>
            <table>
                <thead><tr><th>ID</th><th>Crypto</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr></thead>
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
                document.getElementById('winrate').textContent = (data.stats?.win_rate || 0).toFixed(0) + '%';
                document.getElementById('trades').textContent = data.stats?.trades || 0;
                document.getElementById('opportunities').textContent = data.opportunities || 0;
                document.getElementById('markets').textContent = (data.markets || []).length;
                
                // Prices
                const pricesDiv = document.getElementById('prices');
                pricesDiv.innerHTML = Object.entries(data.prices || {}).map(([s, p]) => 
                    `<div>${s.replace('USDT','')}: <strong>$${p.toLocaleString()}</strong></div>`
                ).join('');
                
                // Trend
                const trend = data.trend?.direction || 'NEUTRAL';
                const trendChange = data.trend?.change_pct || 0;
                const trendEl = document.getElementById('trend');
                trendEl.textContent = trend;
                trendEl.className = 'stat-value ' + (trend === 'BULLISH' ? 'positive' : trend === 'BEARISH' ? 'negative' : '');
                const trendChangeEl = document.getElementById('trendChange');
                trendChangeEl.textContent = trendChange.toFixed(2) + '%';
                trendChangeEl.className = 'stat-value ' + (trendChange >= 0 ? 'positive' : 'negative');
                
                // Active markets
                const marketsDiv = document.getElementById('activeMarkets');
                marketsDiv.innerHTML = (data.markets || []).slice(0, 6).map(m => 
                    `<div class="market-card">
                        <strong>${m.crypto}</strong> | 
                        <span class="up">Up $${m.up_price.toFixed(2)}</span> | 
                        <span class="down">Down $${m.down_price.toFixed(2)}</span> |
                        ${m.time_remaining} left
                    </div>`
                ).join('') || '<div>No active markets</div>';
                
                // Positions
                document.getElementById('positions').innerHTML = (data.positions || []).map(p => 
                    `<tr><td>${p.id}</td><td>${p.crypto}</td><td class="${p.side.toLowerCase()}">${p.side}</td><td>$${p.entry_price.toFixed(2)}</td><td>${p.time_to_resolution}</td></tr>`
                ).join('') || '<tr><td colspan="5">No open positions</td></tr>';
                
                // Trades
                document.getElementById('trades_table').innerHTML = (data.closed || []).slice(0, 10).map(t => 
                    `<tr><td>${t.id}</td><td>${t.crypto}</td><td class="${t.side.toLowerCase()}">${t.side}</td><td>$${t.entry_price.toFixed(2)}</td><td>$${(t.exit_price || 0).toFixed(2)}</td><td class="${t.pnl >= 0 ? 'positive' : 'negative'}">$${t.pnl.toFixed(2)}</td><td>${t.exit_reason}</td></tr>`
                ).join('') || '<tr><td colspan="7">No trades yet</td></tr>';
                
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
            "prices": bot.binance.get_all(),
            "markets": [m.to_dict() for m in bot._markets if m.time_remaining_seconds > 60],
            "positions": [p.to_dict() for p in bot.portfolio.positions.values()],
            "closed": [p.to_dict() for p in reversed(bot.portfolio.closed_positions[-20:])],
            "opportunities": bot._opportunities_found,
            "trend": {
                "direction": bot._current_trend,
                "change_pct": bot._trend_change_pct,
            }
        }
    
    # Create bot
    config = UpDownConfig(
        initial_capital=1000.0,
        position_size=100.0,
        max_positions=10,
        min_mispricing=0.10,
        take_profit_pct=0.15,
        stop_loss_pct=0.10,
        symbols=['BTC', 'ETH', 'SOL', 'XRP'],
    )
    bot = UpDownArbBot(config)
    
    # Run
    bot_task = asyncio.create_task(bot.start())
    
    server_config = uvicorn.Config(app, host="0.0.0.0", port=8089, log_level="warning")
    server = uvicorn.Server(server_config)
    
    log.info("="*60)
    log.info("DASHBOARD: http://localhost:8089")
    log.info("="*60)
    
    try:
        await server.serve()
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(run_with_dashboard())
