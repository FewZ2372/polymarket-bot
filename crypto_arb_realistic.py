"""
Crypto Arbitrage - REALISTIC Simulator

Simulación realista para evaluar si vale la pena invertir dinero real.

CAMBIOS vs versión anterior:
1. Fees reales de Polymarket (~1.5% por trade)
2. Slippage basado en liquidez
3. Resolución en fecha real del mercado (no instantánea)
4. Cálculo de "fair value" considerando tiempo y volatilidad
5. Solo tradea cuando hay edge REAL

Este simulador es PESIMISTA a propósito - si muestra ganancias, 
probablemente sean alcanzables en la realidad.
"""

import asyncio
import json
import math
import random
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

from logger import log
from crypto_latency_arb import (
    BinanceFeed, PolymarketCryptoScanner, CryptoMarket, CryptoPrice, 
    MarketType, ArbConfig
)


# ============================================================================
# REALISTIC CONFIGURATION
# ============================================================================

@dataclass
class RealisticConfig:
    """Configuración realista para simulación."""
    # Capital
    initial_capital: float = 100.0
    
    # Position sizing
    max_position_size: float = 10.0
    min_position_size: float = 2.0
    max_open_positions: int = 5
    max_exposure_pct: float = 0.50
    
    # FEES (Polymarket REAL fees - International/Polygon)
    # 2% fee ONLY on winning trades, nothing on losing trades
    winner_fee: float = 0.02     # 2% on winning trades only
    gas_fee_per_trade: float = 0.25  # ~$0.25 gas per transaction
    
    # SLIPPAGE model
    base_slippage: float = 0.005  # 0.5% base slippage
    size_impact: float = 0.001    # Additional 0.1% per $1 traded
    
    # Edge requirements (STRICT)
    min_edge_after_fees: float = 0.05  # Need 5% edge AFTER fees to trade
    min_confidence: float = 0.80       # 80% confidence minimum
    
    # Volatility assumption for fair value calc
    btc_daily_volatility: float = 0.03   # 3% daily volatility
    eth_daily_volatility: float = 0.04   # 4% daily volatility
    
    # Resolution timing
    # Markets resolve at their end_date, not immediately
    # For simulation, we can accelerate time
    time_acceleration: float = 1.0  # 1.0 = real time, 24.0 = 1 day per hour
    
    # Cooldowns
    trade_cooldown_seconds: int = 300  # 5 minutes between trades on same market
    
    # TAKE PROFIT / STOP LOSS
    # Para arbitraje de latencia, queremos capturar ganancias rápido
    take_profit_pct: float = 0.50      # Vender cuando capturamos 50% del edge esperado
    stop_loss_pct: float = 0.30        # Cortar pérdidas si perdemos 30% de la posición
    min_hold_seconds: int = 60         # Mantener al menos 1 minuto antes de TP/SL
    
    # State
    state_file: str = "crypto_arb_realistic_state.json"


# ============================================================================
# FAIR VALUE CALCULATOR
# ============================================================================

class FairValueCalculator:
    """
    Calcula el "fair value" de un mercado de predicción crypto.
    
    Usa el modelo de Black-Scholes simplificado para estimar la probabilidad
    de que el precio cruce un threshold antes de la fecha de resolución.
    """
    
    def __init__(self, config: RealisticConfig):
        self.config = config
    
    def calculate_probability(
        self,
        current_price: float,
        threshold: float,
        market_type: MarketType,
        days_to_resolution: float,
        volatility: float
    ) -> float:
        """
        Calcula la probabilidad de que el precio cruce el threshold.
        
        Usa una aproximación del modelo de Black-Scholes.
        """
        if days_to_resolution <= 0:
            # Ya pasó la fecha de resolución
            if market_type == MarketType.ABOVE:
                return 1.0 if current_price > threshold else 0.0
            else:  # BELOW
                return 1.0 if current_price < threshold else 0.0
        
        # Calcular distancia al threshold en términos de volatilidad
        price_ratio = current_price / threshold
        log_ratio = math.log(price_ratio)
        
        # Volatilidad ajustada por tiempo (sqrt of time)
        vol_adjusted = volatility * math.sqrt(days_to_resolution)
        
        if vol_adjusted == 0:
            vol_adjusted = 0.01  # Evitar división por cero
        
        # Z-score (simplificado)
        z_score = log_ratio / vol_adjusted
        
        # Aproximación de la CDF normal
        # P(price > threshold) aproximado
        def norm_cdf(x):
            """Aproximación de la CDF normal estándar."""
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))
        
        if market_type == MarketType.ABOVE:
            # Probabilidad de que suba por encima del threshold
            # Si estamos por debajo, necesitamos subir
            prob = norm_cdf(z_score)
        else:  # BELOW
            # Probabilidad de que baje por debajo del threshold
            prob = 1 - norm_cdf(z_score)
        
        # Ajustar por el hecho de que puede tocar el threshold en cualquier momento
        # (barrier option adjustment - simplificado)
        touch_adjustment = 1.2 if abs(z_score) < 1 else 1.0
        prob = min(0.95, prob * touch_adjustment)
        prob = max(0.05, prob)
        
        return prob
    
    def get_fair_price(
        self,
        market: CryptoMarket,
        current_crypto_price: float,
        for_yes: bool = True
    ) -> Tuple[float, float]:
        """
        Calcula el fair price de YES y la probabilidad.
        
        Returns:
            Tuple de (fair_yes_price, probability)
        """
        # Determinar días hasta resolución
        if market.end_date:
            days_to_resolution = (market.end_date - datetime.now()).total_seconds() / 86400
        else:
            days_to_resolution = 30  # Asumir 30 días si no hay fecha
        
        days_to_resolution = max(0, days_to_resolution)
        
        # Determinar volatilidad
        if market.crypto_symbol == "BTC":
            volatility = self.config.btc_daily_volatility
        else:
            volatility = self.config.eth_daily_volatility
        
        # Calcular probabilidad
        prob = self.calculate_probability(
            current_crypto_price,
            market.threshold_price,
            market.market_type,
            days_to_resolution,
            volatility
        )
        
        # Fair price = probabilidad
        fair_yes = prob
        
        return fair_yes, prob


# ============================================================================
# REALISTIC POSITION
# ============================================================================

@dataclass
class RealisticPosition:
    """Posición con tracking realista."""
    id: str
    market_id: str
    market_question: str
    crypto_symbol: str
    threshold_price: float
    market_type: MarketType
    
    # Position
    side: str  # YES or NO
    entry_price: float
    size_before_fees: float
    fees_paid: float
    size_after_fees: float  # Actual investment after fees
    shares: float
    
    # Entry context
    crypto_price_at_entry: float
    fair_value_at_entry: float
    edge_at_entry: float
    
    # Market resolution
    market_end_date: Optional[datetime]
    
    # Timestamps
    opened_at: datetime
    closed_at: Optional[datetime] = None
    
    # Resolution
    exit_price: Optional[float] = None
    exit_fees: float = 0.0
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    
    status: str = "open"  # open, won, lost
    resolution_reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'crypto_symbol': self.crypto_symbol,
            'threshold_price': self.threshold_price,
            'market_type': self.market_type.value,
            'side': self.side,
            'entry_price': self.entry_price,
            'size_before_fees': self.size_before_fees,
            'fees_paid': self.fees_paid,
            'size_after_fees': self.size_after_fees,
            'shares': self.shares,
            'crypto_price_at_entry': self.crypto_price_at_entry,
            'fair_value_at_entry': self.fair_value_at_entry,
            'edge_at_entry': self.edge_at_entry,
            'market_end_date': self.market_end_date.isoformat() if self.market_end_date else None,
            'opened_at': self.opened_at.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'exit_price': self.exit_price,
            'exit_fees': self.exit_fees,
            'gross_pnl': self.gross_pnl,
            'net_pnl': self.net_pnl,
            'status': self.status,
            'resolution_reason': self.resolution_reason,
        }


# ============================================================================
# REALISTIC PORTFOLIO
# ============================================================================

class RealisticPortfolio:
    """Portfolio con tracking realista de fees y P&L."""
    
    def __init__(self, config: RealisticConfig):
        self.config = config
        self.initial_capital = config.initial_capital
        self.cash = config.initial_capital
        self.positions: Dict[str, RealisticPosition] = {}
        self.closed_positions: List[RealisticPosition] = []
        
        self.trade_count = 0
        self.total_fees_paid = 0.0
        self.gross_pnl = 0.0
        self.net_pnl = 0.0
        self.wins = 0
        self.losses = 0
        
        self.started_at = datetime.now()
    
    @property
    def open_positions(self) -> List[RealisticPosition]:
        return list(self.positions.values())
    
    @property
    def total_exposure(self) -> float:
        return sum(p.size_after_fees for p in self.positions.values())
    
    @property
    def total_value(self) -> float:
        return self.cash + self.total_exposure
    
    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0
        return (self.total_value - self.initial_capital) / self.initial_capital * 100
    
    def calculate_slippage(self, size: float, liquidity: float) -> float:
        """Calcula slippage basado en tamaño vs liquidez."""
        base = self.config.base_slippage
        
        # Impact adicional basado en tamaño relativo a liquidez
        if liquidity > 0:
            size_ratio = size / liquidity
            impact = size_ratio * 0.1  # 10% del ratio como slippage adicional
        else:
            impact = 0.05  # 5% si no hay liquidez info
        
        return base + impact
    
    def calculate_entry_fees(self, size: float) -> float:
        """Calcula fees de entrada (solo gas)."""
        return self.config.gas_fee_per_trade
    
    def calculate_exit_fees(self, gross_payout: float, is_winner: bool) -> float:
        """Calcula fees de salida (2% solo si ganamos + gas)."""
        gas = self.config.gas_fee_per_trade
        if is_winner:
            return gross_payout * self.config.winner_fee + gas
        return gas  # Solo gas si perdemos
    
    def can_open_position(self, size: float) -> Tuple[bool, str]:
        """Verifica si podemos abrir posición."""
        if len(self.positions) >= self.config.max_open_positions:
            return False, f"Max positions ({self.config.max_open_positions})"
        
        entry_fees = self.calculate_entry_fees(size)
        total_needed = size + entry_fees
        
        if self.cash < total_needed:
            return False, f"Insufficient cash (need ${total_needed:.2f}, have ${self.cash:.2f})"
        
        exposure_after = (self.total_exposure + size) / self.initial_capital
        if exposure_after > self.config.max_exposure_pct:
            return False, f"Would exceed exposure limit"
        
        return True, "OK"
    
    def open_position(
        self,
        market: CryptoMarket,
        crypto_price: CryptoPrice,
        side: str,
        edge: float,
        fair_value: float
    ) -> Optional[RealisticPosition]:
        """Abre una posición con fees y slippage realistas."""
        
        # Calcular tamaño
        size = min(self.config.max_position_size, self.cash * 0.15)
        size = max(self.config.min_position_size, size)
        
        can_open, reason = self.can_open_position(size)
        if not can_open:
            return None
        
        # Precio base
        if side == "YES":
            base_price = market.yes_price
        else:
            base_price = market.no_price
        
        # Filtrar precios extremos
        if base_price < 0.05 or base_price > 0.95:
            return None
        
        # Aplicar slippage
        slippage = self.calculate_slippage(size, market.liquidity)
        entry_price = base_price * (1 + slippage)
        entry_price = min(0.95, max(0.05, entry_price))
        
        # Calcular fees de entrada (solo gas)
        entry_fees = self.calculate_entry_fees(size)
        size_after_fees = size - entry_fees
        
        # Calcular shares
        shares = size_after_fees / entry_price
        
        # Crear posición
        position = RealisticPosition(
            id=f"R-{self.trade_count + 1:04d}",
            market_id=market.market_id,
            market_question=market.question,
            crypto_symbol=market.crypto_symbol,
            threshold_price=market.threshold_price,
            market_type=market.market_type,
            side=side,
            entry_price=entry_price,
            size_before_fees=size,
            fees_paid=entry_fees,  # Solo gas en entrada
            size_after_fees=size_after_fees,
            shares=shares,
            crypto_price_at_entry=crypto_price.price,
            fair_value_at_entry=fair_value,
            edge_at_entry=edge,
            market_end_date=market.end_date,
            opened_at=datetime.now(),
        )
        
        # Actualizar portfolio
        self.cash -= (size + entry_fees)  # Size + gas
        self.positions[position.id] = position
        self.trade_count += 1
        self.total_fees_paid += entry_fees
        
        return position
    
    def close_position(self, position_id: str, exit_price: float, won: bool, reason: str):
        """Cierra una posición."""
        if position_id not in self.positions:
            return
        
        position = self.positions[position_id]
        
        # Calcular valor de salida
        gross_exit_value = position.shares * exit_price
        
        # Fees de salida (2% SOLO si ganamos + gas siempre)
        exit_fees = self.calculate_exit_fees(gross_exit_value, won)
        net_exit_value = gross_exit_value - exit_fees
        
        # P&L
        position.exit_price = exit_price
        position.exit_fees = exit_fees
        position.gross_pnl = gross_exit_value - position.size_after_fees
        position.net_pnl = net_exit_value - position.size_after_fees
        position.closed_at = datetime.now()
        position.status = "won" if won else "lost"
        position.resolution_reason = reason
        
        # Actualizar stats
        self.total_fees_paid += exit_fees
        self.gross_pnl += position.gross_pnl
        self.net_pnl += position.net_pnl
        
        if won:
            self.wins += 1
        else:
            self.losses += 1
        
        # Devolver capital
        self.cash += net_exit_value
        
        # Mover a cerradas
        self.closed_positions.append(position)
        del self.positions[position_id]
    
    def get_stats(self) -> Dict[str, Any]:
        runtime = (datetime.now() - self.started_at).total_seconds()
        win_rate = (self.wins / (self.wins + self.losses) * 100) if (self.wins + self.losses) > 0 else 0
        
        return {
            'runtime_seconds': runtime,
            'runtime_formatted': str(timedelta(seconds=int(runtime))),
            'initial_capital': self.initial_capital,
            'current_cash': self.cash,
            'total_exposure': self.total_exposure,
            'total_value': self.total_value,
            'total_return_pct': self.total_return_pct,
            'gross_pnl': self.gross_pnl,
            'net_pnl': self.net_pnl,
            'total_fees_paid': self.total_fees_paid,
            'trade_count': self.trade_count,
            'open_positions': len(self.positions),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
        }
    
    def save_state(self, filepath: str):
        data = {
            'cash': self.cash,
            'trade_count': self.trade_count,
            'total_fees_paid': self.total_fees_paid,
            'gross_pnl': self.gross_pnl,
            'net_pnl': self.net_pnl,
            'wins': self.wins,
            'losses': self.losses,
            'started_at': self.started_at.isoformat(),
            'positions': {k: v.to_dict() for k, v in self.positions.items()},
            'closed_positions': [p.to_dict() for p in self.closed_positions[-50:]],
        }
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)


# ============================================================================
# REALISTIC SIMULATOR
# ============================================================================

class RealisticSimulator:
    """
    Simulador realista de arbitraje crypto.
    
    Diferencias clave:
    1. Calcula fair value usando volatilidad y tiempo
    2. Solo tradea cuando hay edge REAL después de fees
    3. Resuelve posiciones basado en fecha del mercado
    4. Incluye fees y slippage realistas
    """
    
    def __init__(self, config: RealisticConfig = None):
        self.config = config or RealisticConfig()
        
        self.binance_feed = BinanceFeed(symbols=['BTCUSDT', 'ETHUSDT'])
        self.poly_scanner = PolymarketCryptoScanner()
        self.fair_value_calc = FairValueCalculator(self.config)
        self.portfolio = RealisticPortfolio(self.config)
        
        self._running = False
        self._crypto_markets: List[CryptoMarket] = []
        self._last_trade_time: Dict[str, datetime] = {}
        self._opportunities_analyzed = 0
        self._opportunities_with_edge = 0
        
        self.binance_feed.add_callback(self._on_price_update)
    
    def _on_price_update(self, price: CryptoPrice):
        """Analiza oportunidades en cada update de precio."""
        symbol = price.symbol.replace('USDT', '')
        relevant_markets = [m for m in self._crypto_markets if m.crypto_symbol == symbol]
        
        for market in relevant_markets:
            self._analyze_opportunity(market, price)
    
    def _analyze_opportunity(self, market: CryptoMarket, crypto_price: CryptoPrice):
        """Analiza si hay oportunidad real de arbitraje."""
        self._opportunities_analyzed += 1
        
        # Cooldown check
        if market.market_id in self._last_trade_time:
            elapsed = (datetime.now() - self._last_trade_time[market.market_id]).total_seconds()
            if elapsed < self.config.trade_cooldown_seconds:
                return
        
        # Ya tenemos posición?
        for pos in self.portfolio.open_positions:
            if pos.market_id == market.market_id:
                return
        
        # Calcular fair value
        fair_yes, prob = self.fair_value_calc.get_fair_price(market, crypto_price.price)
        fair_no = 1 - fair_yes
        
        # Comparar con precios de mercado
        market_yes = market.yes_price
        market_no = market.no_price
        
        # Buscar edge
        # Si fair_yes > market_yes, el mercado subestima YES → comprar YES
        # Si fair_no > market_no, el mercado subestima NO → comprar NO
        
        edge_yes = fair_yes - market_yes
        edge_no = fair_no - market_no
        
        # Considerar fees realistas
        # Solo 2% si ganamos, pero asumimos que vamos a ganar para calcular edge
        # Más gas (~$0.50 round trip en $10 = ~5%)
        estimated_fee_impact = self.config.winner_fee + (self.config.gas_fee_per_trade * 2 / 10)  # ~7% para $10 trade
        
        best_side = None
        best_edge = 0
        
        if edge_yes > estimated_fee_impact + self.config.min_edge_after_fees:
            if market_yes >= 0.05 and market_yes <= 0.95:
                best_side = "YES"
                best_edge = edge_yes - estimated_fee_impact
        
        if edge_no > estimated_fee_impact + self.config.min_edge_after_fees:
            if market_no >= 0.05 and market_no <= 0.95:
                if edge_no - estimated_fee_impact > best_edge:
                    best_side = "NO"
                    best_edge = edge_no - estimated_fee_impact
        
        if best_side is None:
            return
        
        # Confidence check
        confidence = min(1.0, best_edge * 5)  # Higher edge = higher confidence
        if confidence < self.config.min_confidence:
            return
        
        self._opportunities_with_edge += 1
        
        # Record trade time
        self._last_trade_time[market.market_id] = datetime.now()
        
        # Open position
        fair_value = fair_yes if best_side == "YES" else fair_no
        position = self.portfolio.open_position(
            market, crypto_price, best_side, best_edge, fair_value
        )
        
        if position:
            log.info(f"""
+====================================================================+
| [REALISTIC] NEW POSITION
+--------------------------------------------------------------------+
|  ID: {position.id}
|  Market: {market.question[:50]}...
|  End Date: {market.end_date.strftime('%Y-%m-%d') if market.end_date else 'Unknown'}
|  
|  Side: {position.side}
|  Entry: {position.entry_price:.4f} (market: {market_yes if best_side == 'YES' else market_no:.4f})
|  Fair Value: {fair_value:.4f}
|  
|  Size: ${position.size_before_fees:.2f}
|  Fees: ${position.fees_paid:.2f}
|  Net Investment: ${position.size_after_fees:.2f}
|  
|  Edge (after fees): {best_edge:.1%}
|  Crypto: {crypto_price.symbol} @ ${crypto_price.price:,.2f}
+====================================================================+
""")
    
    def _check_take_profit_stop_loss(self):
        """
        Verifica Take Profit y Stop Loss basado en el fair value actual.
        
        En arbitraje de latencia, la idea es:
        - Compramos cuando el mercado está mal priceado vs fair value
        - El mercado eventualmente se corrige
        - Cuando capturamos parte del edge, vendemos (take profit)
        - Si el mercado se mueve en contra, cortamos (stop loss)
        """
        prices = self.binance_feed.get_all_prices()
        
        for pos_id, position in list(self.portfolio.positions.items()):
            # Verificar tiempo mínimo de hold
            age_seconds = (datetime.now() - position.opened_at).total_seconds()
            if age_seconds < self.config.min_hold_seconds:
                continue
            
            # Obtener precio crypto actual
            symbol = f"{position.crypto_symbol}USDT"
            current_price = prices.get(symbol)
            if not current_price:
                continue
            
            # Encontrar el mercado correspondiente para obtener precio actual
            market = None
            for m in self._crypto_markets:
                if m.market_id == position.market_id:
                    market = m
                    break
            
            if not market:
                continue
            
            # Calcular fair value actual con el nuevo precio crypto
            fair_yes, _ = self.fair_value_calc.calculate_fair_price(market, current_price.price)
            
            # Determinar precio actual de la posición
            if position.side == "YES":
                current_market_price = market.yes_price
                fair_value = fair_yes
            else:
                current_market_price = market.no_price
                fair_value = 1.0 - fair_yes
            
            # Calcular P&L actual (sin cerrar)
            current_value = position.shares * current_market_price
            unrealized_pnl = current_value - position.size_after_fees
            unrealized_pnl_pct = unrealized_pnl / position.size_after_fees if position.size_after_fees > 0 else 0
            
            # Edge original esperado
            expected_edge = position.edge_at_entry
            
            # ==================== TAKE PROFIT ====================
            # Si el precio del mercado se movió hacia el fair value
            # y capturamos un % del edge esperado, tomamos ganancia
            
            price_moved_toward_fair = False
            if position.side == "YES":
                # Compramos YES esperando que suba hacia fair value
                price_moved_toward_fair = current_market_price > position.entry_price
            else:
                # Compramos NO esperando que suba hacia fair value
                price_moved_toward_fair = current_market_price > position.entry_price
            
            if price_moved_toward_fair and unrealized_pnl_pct >= self.config.take_profit_pct * expected_edge:
                # Tomar ganancia!
                exit_price = current_market_price * (1 - self.config.base_slippage)  # Slippage de venta
                won = unrealized_pnl > 0
                reason = f"TAKE PROFIT: {unrealized_pnl_pct*100:+.1f}% (edge captured: {unrealized_pnl_pct/expected_edge*100:.0f}%)"
                
                self.portfolio.close_position(pos_id, exit_price, won, reason)
                
                log.info(f"""
+====================================================================+
| [TP] TAKE PROFIT TRIGGERED
+--------------------------------------------------------------------+
|  ID: {position.id}
|  {position.side} @ {position.entry_price:.4f} -> {exit_price:.4f}
|  
|  Unrealized P&L: ${unrealized_pnl:+.2f} ({unrealized_pnl_pct*100:+.1f}%)
|  Edge at Entry: {expected_edge*100:.1f}%
|  Edge Captured: {unrealized_pnl_pct/expected_edge*100:.0f}%
|  
|  Net P&L: ${position.net_pnl:+.2f}
+====================================================================+
""")
                continue
            
            # ==================== STOP LOSS ====================
            # Si perdemos más del % configurado, cortamos pérdidas
            
            if unrealized_pnl_pct <= -self.config.stop_loss_pct:
                # Cortar pérdidas
                exit_price = current_market_price * (1 - self.config.base_slippage)  # Slippage de venta
                won = False
                reason = f"STOP LOSS: {unrealized_pnl_pct*100:+.1f}%"
                
                self.portfolio.close_position(pos_id, exit_price, won, reason)
                
                log.info(f"""
+====================================================================+
| [SL] STOP LOSS TRIGGERED
+--------------------------------------------------------------------+
|  ID: {position.id}
|  {position.side} @ {position.entry_price:.4f} -> {exit_price:.4f}
|  
|  Loss: ${unrealized_pnl:+.2f} ({unrealized_pnl_pct*100:+.1f}%)
|  
|  Net P&L: ${position.net_pnl:+.2f}
+====================================================================+
""")
                continue
    
    def _check_resolutions(self):
        """Verifica si alguna posición debe resolverse (fecha de mercado)."""
        prices = self.binance_feed.get_all_prices()
        
        for pos_id, position in list(self.portfolio.positions.items()):
            # Verificar si llegó la fecha de resolución
            if position.market_end_date:
                # Aplicar aceleración de tiempo
                simulated_now = datetime.now()
                if self.config.time_acceleration > 1:
                    elapsed = (datetime.now() - position.opened_at).total_seconds()
                    simulated_elapsed = elapsed * self.config.time_acceleration
                    simulated_now = position.opened_at + timedelta(seconds=simulated_elapsed)
                
                if simulated_now < position.market_end_date:
                    continue  # Aún no es hora de resolver
            else:
                # Sin fecha de fin, resolver después de 1 hora simulada
                min_age = 3600 / self.config.time_acceleration
                age = (datetime.now() - position.opened_at).total_seconds()
                if age < min_age:
                    continue
            
            # Obtener precio actual
            symbol = f"{position.crypto_symbol}USDT"
            current_price = prices.get(symbol)
            if not current_price:
                continue
            
            # Determinar resultado
            crypto_price = current_price.price
            threshold = position.threshold_price
            
            if position.market_type == MarketType.ABOVE:
                actual_yes = crypto_price > threshold
            else:  # BELOW
                actual_yes = crypto_price < threshold
            
            # Determinar si ganamos
            if position.side == "YES":
                won = actual_yes
            else:
                won = not actual_yes
            
            # Precio de salida realista
            if won:
                # Ganamos: el mercado resuelve a ~95-99 cents
                exit_price = 0.95 + random.uniform(0, 0.04)
            else:
                # Perdimos: el mercado resuelve a ~1-5 cents
                exit_price = 0.01 + random.uniform(0, 0.04)
            
            reason = f"{position.crypto_symbol} @ ${crypto_price:,.2f} vs ${threshold:,.0f}"
            self.portfolio.close_position(pos_id, exit_price, won, reason)
            
            result = "[WIN]" if won else "[LOSS]"
            log.info(f"""
{result} POSITION RESOLVED
   ID: {position.id}
   {position.side} @ {position.entry_price:.4f} -> {exit_price:.4f}
   Gross P&L: ${position.gross_pnl:+.2f}
   Fees: ${position.fees_paid + position.exit_fees:.2f}
   Net P&L: ${position.net_pnl:+.2f}
   Reason: {reason}
""")
    
    async def _status_loop(self):
        """Status periódico."""
        while self._running:
            await asyncio.sleep(15)
            
            prices = self.binance_feed.get_all_prices()
            stats = self.portfolio.get_stats()
            
            price_strs = [f"{s.replace('USDT', '')}: ${p.price:,.0f}" for s, p in prices.items()]
            
            efficiency = (self._opportunities_with_edge / self._opportunities_analyzed * 100) if self._opportunities_analyzed > 0 else 0
            
            log.info(f"""
+====================================================================+
| [REALISTIC] STATUS - {stats['runtime_formatted']}
+--------------------------------------------------------------------+
| PORTFOLIO
|   Initial: ${stats['initial_capital']:.2f}
|   Current: ${stats['total_value']:.2f} ({stats['total_return_pct']:+.1f}%)
|   Cash: ${stats['current_cash']:.2f} | Exposure: ${stats['total_exposure']:.2f}
|
| P&L BREAKDOWN
|   Gross P&L: ${stats['gross_pnl']:+.2f}
|   Total Fees: ${stats['total_fees_paid']:.2f}
|   Net P&L: ${stats['net_pnl']:+.2f}
|
| TRADES
|   Total: {stats['trade_count']} | Open: {stats['open_positions']}
|   Win Rate: {stats['win_rate']:.0f}% ({stats['wins']}W / {stats['losses']}L)
|
| EFFICIENCY
|   Opportunities Analyzed: {self._opportunities_analyzed:,}
|   With Real Edge: {self._opportunities_with_edge} ({efficiency:.2f}%)
|
| MARKETS: {', '.join(price_strs)}
+====================================================================+
""")
            
            self.portfolio.save_state(self.config.state_file)
    
    async def _resolution_loop(self):
        """Loop de resolución y Take Profit/Stop Loss."""
        while self._running:
            await asyncio.sleep(5)  # Verificar cada 5 segundos para TP/SL
            self._check_take_profit_stop_loss()
            self._check_resolutions()
    
    async def _market_scan_loop(self):
        """Escanea mercados periódicamente."""
        while self._running:
            try:
                self._crypto_markets = self.poly_scanner.scan_markets(min_liquidity=1000)
            except Exception as e:
                log.error(f"Market scan error: {e}")
            await asyncio.sleep(60)
    
    async def start(self):
        """Inicia el simulador."""
        log.info(f"""
+====================================================================+
|     REALISTIC CRYPTO ARBITRAGE SIMULATOR                          |
+====================================================================+
|  This simulation includes:
|  - Real fees: {self.config.winner_fee:.0%} on WINNING trades only
|  - Gas: ${self.config.gas_fee_per_trade:.2f} per trade
|  - Slippage based on liquidity
|  - Fair value calculation (volatility model)
|  - TAKE PROFIT: {self.config.take_profit_pct:.0%} of expected edge
|  - STOP LOSS: {self.config.stop_loss_pct:.0%} max loss per position
|  
|  Capital: ${self.config.initial_capital:.2f}
|  Min Edge (after fees): {self.config.min_edge_after_fees:.0%}
|  Min Hold Time: {self.config.min_hold_seconds}s
|  
|  If this shows profit, it's likely achievable in reality.
+====================================================================+
""")
        
        self._running = True
        
        # Initial scan
        self._crypto_markets = self.poly_scanner.scan_markets(min_liquidity=1000)
        
        # Connect Binance
        await self.binance_feed.connect()
        
        # Run with auto-reconnect for Binance
        while self._running:
            try:
                tasks = [
                    asyncio.create_task(self.binance_feed.listen()),
                    asyncio.create_task(self._market_scan_loop()),
                    asyncio.create_task(self._status_loop()),
                    asyncio.create_task(self._resolution_loop()),
                ]
                
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[Simulator] Connection error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
                try:
                    await self.binance_feed.connect()
                except:
                    pass
        
        await self.stop()
    
    async def stop(self):
        """Detiene el simulador."""
        self._running = False
        await self.binance_feed.disconnect()
        self.portfolio.save_state(self.config.state_file)
        
        stats = self.portfolio.get_stats()
        log.info(f"""
+====================================================================+
|     SIMULATION ENDED                                              |
+====================================================================+
|  Runtime: {stats['runtime_formatted']}
|  
|  RESULTS:
|    Initial: ${stats['initial_capital']:.2f}
|    Final: ${stats['total_value']:.2f}
|    Return: {stats['total_return_pct']:+.1f}%
|  
|  P&L:
|    Gross: ${stats['gross_pnl']:+.2f}
|    Fees Paid: ${stats['total_fees_paid']:.2f}
|    Net: ${stats['net_pnl']:+.2f}
|  
|  Trades: {stats['trade_count']}
|  Win Rate: {stats['win_rate']:.0f}%
+====================================================================+
""")


# ============================================================================
# MAIN
# ============================================================================

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Realistic Crypto Arbitrage Simulator')
    parser.add_argument('--capital', type=float, default=100.0)
    parser.add_argument('--time-accel', type=float, default=60.0,
                        help='Time acceleration (60 = 1 minute = 1 hour)')
    parser.add_argument('--min-edge', type=float, default=0.05,
                        help='Minimum edge after fees (default: 5%%)')
    parser.add_argument('--reset', action='store_true')
    
    args = parser.parse_args()
    
    if args.reset:
        Path('crypto_arb_realistic_state.json').unlink(missing_ok=True)
    
    config = RealisticConfig(
        initial_capital=args.capital,
        min_edge_after_fees=args.min_edge,
        time_acceleration=args.time_accel,
    )
    
    simulator = RealisticSimulator(config)
    
    try:
        await simulator.start()
    except KeyboardInterrupt:
        pass
    finally:
        await simulator.stop()


if __name__ == "__main__":
    asyncio.run(main())
