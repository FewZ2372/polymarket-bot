"""
REAL Latency Arbitrage Bot

Este bot hace arbitraje de latencia REAL:
1. Detecta cuando BTC/ETH cruza un threshold de precio
2. Compra inmediatamente en Polymarket antes de que ajuste
3. Vende cuando el mercado se ajusta

El edge viene de la VELOCIDAD, no de calcular fair values.
"""

import asyncio
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from logger import log
from crypto_latency_arb import BinanceFeed, PolymarketCryptoScanner, CryptoMarket, CryptoPrice, MarketType


# ============================================================================
# CONFIGURATION
# ============================================================================

@dataclass
class LatencyArbConfig:
    """Configuración del bot de latencia."""
    # Capital
    initial_capital: float = 100.0
    position_size: float = 10.0
    max_positions: int = 5
    
    # Timing
    min_hold_seconds: int = 10       # Hold mínimo antes de vender
    max_hold_seconds: int = 300      # Forzar venta después de 5 min
    
    # Thresholds
    min_price_move_pct: float = 0.001  # 0.1% movimiento mínimo para considerar cruce
    min_market_mispricing: float = 0.10  # El mercado debe estar 10%+ mal priceado
    
    # Take Profit / Stop Loss
    take_profit_pct: float = 0.20    # Vender cuando ganamos 20%+
    stop_loss_pct: float = 0.15      # Cortar si perdemos 15%
    
    # Fees (Polymarket real)
    winner_fee: float = 0.02
    gas_per_trade: float = 0.25
    
    # State
    state_file: str = "latency_arb_state.json"


# ============================================================================
# THRESHOLD TRACKER
# ============================================================================

@dataclass
class ThresholdCross:
    """Representa un cruce de threshold detectado."""
    market: CryptoMarket
    cross_direction: str  # "up" or "down"
    price_before: float
    price_after: float
    threshold: float
    detected_at: datetime
    market_price_at_detection: float  # Precio YES/NO en Polymarket al detectar
    expected_price: float  # Precio que DEBERÍA tener después del cruce


class ThresholdTracker:
    """
    Trackea precios y detecta cruces de threshold.
    
    Un cruce ocurre cuando:
    - Precio anterior < threshold Y precio actual >= threshold (cruce UP)
    - Precio anterior >= threshold Y precio actual < threshold (cruce DOWN)
    """
    
    def __init__(self, config: LatencyArbConfig):
        self.config = config
        self._last_prices: Dict[str, float] = {}  # symbol -> last price
        self._markets_by_threshold: Dict[str, List[CryptoMarket]] = {}  # symbol -> markets
    
    def update_markets(self, markets: List[CryptoMarket]):
        """Actualiza la lista de mercados a monitorear."""
        self._markets_by_threshold.clear()
        
        for market in markets:
            symbol = market.crypto_symbol
            if symbol not in self._markets_by_threshold:
                self._markets_by_threshold[symbol] = []
            self._markets_by_threshold[symbol].append(market)
        
        # Ordenar por threshold para eficiencia
        for symbol in self._markets_by_threshold:
            self._markets_by_threshold[symbol].sort(key=lambda m: m.threshold_price)
    
    def check_crosses(self, price: CryptoPrice) -> List[ThresholdCross]:
        """
        Verifica si el nuevo precio cruza algún threshold.
        Retorna lista de cruces detectados.
        """
        symbol = price.symbol.replace('USDT', '')
        current_price = price.price
        
        # Obtener precio anterior
        last_price = self._last_prices.get(symbol)
        self._last_prices[symbol] = current_price
        
        if last_price is None:
            return []  # Primera lectura, no podemos detectar cruce
        
        # Verificar si hubo movimiento significativo
        price_change_pct = abs(current_price - last_price) / last_price
        if price_change_pct < self.config.min_price_move_pct:
            return []  # Movimiento muy pequeño
        
        crosses = []
        markets = self._markets_by_threshold.get(symbol, [])
        
        for market in markets:
            threshold = market.threshold_price
            cross = self._detect_cross(market, last_price, current_price, threshold)
            if cross:
                crosses.append(cross)
        
        return crosses
    
    def _detect_cross(
        self, 
        market: CryptoMarket, 
        price_before: float, 
        price_after: float, 
        threshold: float
    ) -> Optional[ThresholdCross]:
        """Detecta si hubo cruce de threshold."""
        
        cross_direction = None
        expected_yes_price = None
        
        if market.market_type == MarketType.ABOVE:
            # Mercado "above threshold"
            if price_before < threshold and price_after >= threshold:
                # Cruzó hacia ARRIBA -> YES debería valer ~1
                cross_direction = "up"
                expected_yes_price = 0.95
            elif price_before >= threshold and price_after < threshold:
                # Cruzó hacia ABAJO -> YES debería valer ~0
                cross_direction = "down"
                expected_yes_price = 0.05
                
        elif market.market_type == MarketType.BELOW:
            # Mercado "below threshold"
            if price_before >= threshold and price_after < threshold:
                # Cruzó hacia ABAJO -> YES debería valer ~1
                cross_direction = "down"
                expected_yes_price = 0.95
            elif price_before < threshold and price_after >= threshold:
                # Cruzó hacia ARRIBA -> YES debería valer ~0
                cross_direction = "up"
                expected_yes_price = 0.05
        
        if cross_direction is None:
            return None
        
        # Verificar que el mercado está mal priceado
        current_yes = market.yes_price
        mispricing = abs(expected_yes_price - current_yes)
        
        if mispricing < self.config.min_market_mispricing:
            return None  # Mercado ya ajustó o casi
        
        return ThresholdCross(
            market=market,
            cross_direction=cross_direction,
            price_before=price_before,
            price_after=price_after,
            threshold=threshold,
            detected_at=datetime.now(),
            market_price_at_detection=current_yes,
            expected_price=expected_yes_price,
        )


# ============================================================================
# POSITION
# ============================================================================

@dataclass
class LatencyPosition:
    """Posición de arbitraje de latencia."""
    id: str
    market_id: str
    market_question: str
    crypto_symbol: str
    threshold: float
    market_type: MarketType
    
    # Trade info
    side: str  # YES or NO
    entry_price: float
    shares: float
    size: float
    
    # Cross info
    cross_direction: str
    crypto_price_at_entry: float
    expected_exit_price: float
    
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
        return {
            'id': self.id,
            'market_question': self.market_question[:50],
            'side': self.side,
            'entry_price': self.entry_price,
            'shares': self.shares,
            'expected_exit': self.expected_exit_price,
            'age_seconds': self.age_seconds,
            'exit_price': self.exit_price,
            'pnl': self.pnl,
            'exit_reason': self.exit_reason,
        }


# ============================================================================
# PORTFOLIO
# ============================================================================

class LatencyPortfolio:
    """Portfolio para arbitraje de latencia."""
    
    def __init__(self, config: LatencyArbConfig):
        self.config = config
        self.initial_capital = config.initial_capital
        self.cash = config.initial_capital
        
        self.positions: Dict[str, LatencyPosition] = {}
        self.closed_positions: List[LatencyPosition] = []
        
        self.trade_count = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.total_fees = 0.0
        
        self.started_at = datetime.now()
    
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
    
    def open_position(self, cross: ThresholdCross) -> Optional[LatencyPosition]:
        """Abre posición basada en cruce detectado."""
        can, reason = self.can_open()
        if not can:
            return None
        
        market = cross.market
        
        # Determinar lado a comprar
        if cross.expected_price > 0.5:
            # YES debería subir -> comprar YES
            side = "YES"
            entry_price = market.yes_price
        else:
            # YES debería bajar -> comprar NO
            side = "NO"
            entry_price = market.no_price
        
        # Filtrar precios extremos
        if entry_price < 0.02 or entry_price > 0.98:
            return None
        
        # Calcular shares
        size = self.config.position_size
        gas = self.config.gas_per_trade
        effective_size = size - gas
        shares = effective_size / entry_price
        
        # Crear posición
        self.trade_count += 1
        position = LatencyPosition(
            id=f"LAT-{self.trade_count:04d}",
            market_id=market.market_id,
            market_question=market.question,
            crypto_symbol=market.crypto_symbol,
            threshold=market.threshold_price,
            market_type=market.market_type,
            side=side,
            entry_price=entry_price,
            shares=shares,
            size=size,
            cross_direction=cross.cross_direction,
            crypto_price_at_entry=cross.price_after,
            expected_exit_price=cross.expected_price,
            opened_at=datetime.now(),
        )
        
        self.cash -= size
        self.total_fees += gas
        self.positions[position.id] = position
        
        return position
    
    def close_position(self, position_id: str, exit_price: float, reason: str):
        """Cierra una posición."""
        if position_id not in self.positions:
            return
        
        position = self.positions[position_id]
        
        # Calcular P&L
        exit_value = position.shares * exit_price
        gas = self.config.gas_per_trade
        
        # Fee solo si ganamos
        gross_pnl = exit_value - (position.size - self.config.gas_per_trade)
        if gross_pnl > 0:
            fee = exit_value * self.config.winner_fee
        else:
            fee = 0
        
        net_pnl = gross_pnl - fee - gas
        
        position.exit_price = exit_price
        position.exit_reason = reason
        position.pnl = net_pnl
        position.closed_at = datetime.now()
        
        # Update stats
        self.cash += exit_value - fee - gas
        self.total_fees += fee + gas
        self.total_pnl += net_pnl
        
        if net_pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        self.closed_positions.append(position)
        del self.positions[position_id]
    
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


# ============================================================================
# MAIN BOT
# ============================================================================

class RealLatencyArbBot:
    """
    Bot de arbitraje de latencia REAL.
    
    Estrategia:
    1. Monitorea precios de Binance en tiempo real
    2. Detecta cuando precio cruza un threshold de Polymarket
    3. Compra inmediatamente (antes de que Polymarket ajuste)
    4. Vende cuando el mercado ajusta o por TP/SL
    """
    
    def __init__(self, config: LatencyArbConfig = None):
        self.config = config or LatencyArbConfig()
        
        self.binance = BinanceFeed(symbols=['BTCUSDT', 'ETHUSDT'])
        self.scanner = PolymarketCryptoScanner()
        self.tracker = ThresholdTracker(self.config)
        self.portfolio = LatencyPortfolio(self.config)
        
        self._running = False
        self._markets: List[CryptoMarket] = []
        
        # Stats
        self._crosses_detected = 0
        self._trades_attempted = 0
        
        # Register callback
        self.binance.add_callback(self._on_price_update)
    
    def _on_price_update(self, price: CryptoPrice):
        """Callback cuando llega nuevo precio de Binance."""
        # Detectar cruces
        crosses = self.tracker.check_crosses(price)
        
        for cross in crosses:
            self._crosses_detected += 1
            self._handle_cross(cross, price)
        
        # Verificar posiciones abiertas
        self._check_exits(price)
    
    def _handle_cross(self, cross: ThresholdCross, price: CryptoPrice):
        """Maneja un cruce de threshold detectado."""
        market = cross.market
        
        # Log del cruce
        log.info(f"""
+====================================================================+
| [CROSS DETECTED] {cross.cross_direction.upper()}
+--------------------------------------------------------------------+
|  {market.crypto_symbol}: ${cross.price_before:,.2f} -> ${cross.price_after:,.2f}
|  Threshold: ${cross.threshold:,.0f} ({market.market_type.value})
|  
|  Market: {market.question[:50]}...
|  Current YES price: ${cross.market_price_at_detection:.2f}
|  Expected YES price: ${cross.expected_price:.2f}
|  Mispricing: {abs(cross.expected_price - cross.market_price_at_detection)*100:.1f}%
+====================================================================+
""")
        
        # Intentar abrir posición
        self._trades_attempted += 1
        position = self.portfolio.open_position(cross)
        
        if position:
            log.info(f"""
+====================================================================+
| [POSITION OPENED] {position.id}
+--------------------------------------------------------------------+
|  Side: {position.side}
|  Entry: ${position.entry_price:.4f}
|  Size: ${position.size:.2f} ({position.shares:.2f} shares)
|  
|  Expected exit: ${position.expected_exit_price:.2f}
|  Potential profit: {(position.expected_exit_price - position.entry_price) / position.entry_price * 100:.1f}%
+====================================================================+
""")
    
    def _check_exits(self, price: CryptoPrice):
        """Verifica si alguna posición debe cerrarse."""
        symbol = price.symbol.replace('USDT', '')
        
        for pos_id, position in list(self.portfolio.positions.items()):
            if position.crypto_symbol != symbol:
                continue
            
            # Buscar mercado actual
            market = None
            for m in self._markets:
                if m.market_id == position.market_id:
                    market = m
                    break
            
            if not market:
                continue
            
            # Precio actual
            if position.side == "YES":
                current_price = market.yes_price
            else:
                current_price = market.no_price
            
            # Calcular P&L no realizado
            current_value = position.shares * current_price
            cost = position.size - self.config.gas_per_trade
            unrealized_pnl_pct = (current_value - cost) / cost
            
            exit_reason = None
            
            # Take Profit
            if unrealized_pnl_pct >= self.config.take_profit_pct:
                exit_reason = f"TAKE PROFIT (+{unrealized_pnl_pct*100:.1f}%)"
            
            # Stop Loss
            elif unrealized_pnl_pct <= -self.config.stop_loss_pct:
                exit_reason = f"STOP LOSS ({unrealized_pnl_pct*100:.1f}%)"
            
            # Max hold time
            elif position.age_seconds >= self.config.max_hold_seconds:
                exit_reason = f"MAX HOLD TIME ({position.age_seconds:.0f}s)"
            
            # Min hold time check
            if exit_reason and position.age_seconds < self.config.min_hold_seconds:
                continue  # Esperar más
            
            if exit_reason:
                self.portfolio.close_position(pos_id, current_price, exit_reason)
                
                log.info(f"""
+====================================================================+
| [POSITION CLOSED] {position.id}
+--------------------------------------------------------------------+
|  {position.side}: ${position.entry_price:.4f} -> ${current_price:.4f}
|  P&L: ${position.pnl:+.2f}
|  Reason: {exit_reason}
|  Hold time: {position.age_seconds:.0f}s
+====================================================================+
""")
    
    async def _scan_markets_loop(self):
        """Escanea mercados periódicamente."""
        while self._running:
            try:
                self._markets = self.scanner.scan_markets(min_liquidity=1000)
                self.tracker.update_markets(self._markets)
                
                # Contar mercados cerca del precio actual
                prices = self.binance.get_all_prices()
                close_markets = 0
                for m in self._markets:
                    symbol = f"{m.crypto_symbol}USDT"
                    if symbol in prices:
                        current = prices[symbol].price
                        distance_pct = abs(current - m.threshold_price) / current
                        if distance_pct < 0.02:  # Dentro de 2%
                            close_markets += 1
                
                log.info(f"[Scanner] {len(self._markets)} markets | {close_markets} within 2% of threshold")
                
            except Exception as e:
                log.error(f"[Scanner] Error: {e}")
            
            await asyncio.sleep(30)
    
    async def _status_loop(self):
        """Muestra status periódico."""
        while self._running:
            await asyncio.sleep(10)
            
            stats = self.portfolio.get_stats()
            prices = self.binance.get_all_prices()
            price_str = " | ".join([f"{s.replace('USDT','')}: ${p.price:,.0f}" for s, p in prices.items()])
            
            log.info(f"""
+====================================================================+
| [LATENCY ARB] {stats['runtime']}
+--------------------------------------------------------------------+
| PRICES: {price_str}
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
|   Crosses detected: {self._crosses_detected}
|   Trades attempted: {self._trades_attempted}
|   Markets monitored: {len(self._markets)}
+====================================================================+
""")
    
    async def start(self):
        """Inicia el bot."""
        log.info(f"""
+====================================================================+
|     REAL LATENCY ARBITRAGE BOT                                     |
+====================================================================+
|  This bot detects THRESHOLD CROSSES:
|  - When BTC/ETH crosses a Polymarket threshold price
|  - Buys immediately before market adjusts
|  - Sells when market catches up (or TP/SL)
|
|  Capital: ${self.config.initial_capital:.2f}
|  Position size: ${self.config.position_size:.2f}
|  Take Profit: {self.config.take_profit_pct:.0%}
|  Stop Loss: {self.config.stop_loss_pct:.0%}
|  Min mispricing: {self.config.min_market_mispricing:.0%}
+====================================================================+
""")
        
        self._running = True
        
        # Scan inicial
        self._markets = self.scanner.scan_markets(min_liquidity=1000)
        self.tracker.update_markets(self._markets)
        log.info(f"[Init] Loaded {len(self._markets)} crypto markets")
        
        # Conectar Binance
        await self.binance.connect()
        
        # Run tasks
        tasks = [
            asyncio.create_task(self.binance.listen()),
            asyncio.create_task(self._scan_markets_loop()),
            asyncio.create_task(self._status_loop()),
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
        await self.binance.disconnect()
        
        stats = self.portfolio.get_stats()
        log.info(f"""
+====================================================================+
|     BOT STOPPED                                                    |
+====================================================================+
|  Runtime: {stats['runtime']}
|  Final Value: ${stats['current']:.2f} ({stats['pnl_pct']:+.1f}%)
|  Total P&L: ${stats['pnl']:+.2f}
|  Crosses detected: {self._crosses_detected}
+====================================================================+
""")


# ============================================================================
# DASHBOARD
# ============================================================================

async def run_with_dashboard():
    """Corre el bot con dashboard web."""
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn
    
    app = FastAPI(title="Real Latency Arb")
    bot: Optional[RealLatencyArbBot] = None
    
    DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Real Latency Arbitrage</title>
    <style>
        body { font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { color: #00ff88; }
        .card { background: #16213e; padding: 15px; margin: 10px 0; border-radius: 8px; }
        .stat { display: inline-block; margin: 10px 20px; }
        .stat-value { font-size: 24px; color: #00ff88; }
        .stat-label { color: #888; font-size: 12px; }
        .positive { color: #00ff88; }
        .negative { color: #ff4444; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #333; }
        .status { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }
        .status.on { background: #00ff88; }
        .status.off { background: #ff4444; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Real Latency Arbitrage Bot</h1>
        
        <div class="card">
            <div class="stat">
                <div class="stat-value" id="status">--</div>
                <div class="stat-label">Status</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="runtime">--</div>
                <div class="stat-label">Runtime</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="btc">--</div>
                <div class="stat-label">BTC</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="eth">--</div>
                <div class="stat-label">ETH</div>
            </div>
        </div>
        
        <div class="card">
            <div class="stat">
                <div class="stat-value" id="value">--</div>
                <div class="stat-label">Portfolio Value</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="pnl">--</div>
                <div class="stat-label">P&L</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="winrate">--</div>
                <div class="stat-label">Win Rate</div>
            </div>
            <div class="stat">
                <div class="stat-value" id="crosses">--</div>
                <div class="stat-label">Crosses Detected</div>
            </div>
        </div>
        
        <div class="card">
            <h3>Open Positions</h3>
            <table>
                <thead><tr><th>ID</th><th>Market</th><th>Side</th><th>Entry</th><th>Expected</th><th>Age</th></tr></thead>
                <tbody id="positions"></tbody>
            </table>
        </div>
        
        <div class="card">
            <h3>Recent Trades</h3>
            <table>
                <thead><tr><th>ID</th><th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr></thead>
                <tbody id="trades"></tbody>
            </table>
        </div>
    </div>
    
    <script>
        async function update() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                document.getElementById('status').innerHTML = 
                    data.running ? '<span class="status on"></span>Running' : '<span class="status off"></span>Stopped';
                document.getElementById('runtime').textContent = data.stats?.runtime || '--';
                document.getElementById('btc').textContent = data.prices?.BTCUSDT ? '$' + data.prices.BTCUSDT.toLocaleString() : '--';
                document.getElementById('eth').textContent = data.prices?.ETHUSDT ? '$' + data.prices.ETHUSDT.toLocaleString() : '--';
                
                document.getElementById('value').textContent = '$' + (data.stats?.current || 0).toFixed(2);
                const pnl = data.stats?.pnl || 0;
                document.getElementById('pnl').innerHTML = '<span class="' + (pnl >= 0 ? 'positive' : 'negative') + '">$' + pnl.toFixed(2) + '</span>';
                document.getElementById('winrate').textContent = (data.stats?.win_rate || 0).toFixed(0) + '%';
                document.getElementById('crosses').textContent = data.crosses || 0;
                
                // Positions
                const posBody = document.getElementById('positions');
                posBody.innerHTML = (data.positions || []).map(p => 
                    `<tr><td>${p.id}</td><td>${p.market_question}</td><td>${p.side}</td><td>$${p.entry_price.toFixed(2)}</td><td>$${p.expected_exit.toFixed(2)}</td><td>${p.age_seconds.toFixed(0)}s</td></tr>`
                ).join('') || '<tr><td colspan="6">No open positions</td></tr>';
                
                // Trades
                const tradeBody = document.getElementById('trades');
                tradeBody.innerHTML = (data.trades || []).slice(0, 10).map(t => 
                    `<tr><td>${t.id}</td><td>${t.market_question}</td><td>${t.side}</td><td>$${t.entry_price.toFixed(2)}</td><td>$${(t.exit_price || 0).toFixed(2)}</td><td class="${t.pnl >= 0 ? 'positive' : 'negative'}">$${t.pnl.toFixed(2)}</td><td>${t.exit_reason}</td></tr>`
                ).join('') || '<tr><td colspan="7">No trades yet</td></tr>';
                
            } catch(e) { console.error(e); }
        }
        
        setInterval(update, 1000);
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
        
        prices = {}
        for s, p in bot.binance.get_all_prices().items():
            prices[s] = p.price
        
        return {
            "running": bot._running,
            "stats": bot.portfolio.get_stats(),
            "prices": prices,
            "crosses": bot._crosses_detected,
            "positions": [p.to_dict() for p in bot.portfolio.positions.values()],
            "trades": [p.to_dict() for p in reversed(bot.portfolio.closed_positions[-20:])],
        }
    
    # Start bot
    config = LatencyArbConfig(
        initial_capital=1000.0,
        position_size=100.0,      # $100 por trade
        max_positions=10,         # 10 posiciones = $1000 total
        take_profit_pct=0.20,
        stop_loss_pct=0.15,
        min_market_mispricing=0.10,
    )
    bot = RealLatencyArbBot(config)
    
    # Run both
    bot_task = asyncio.create_task(bot.start())
    
    server_config = uvicorn.Config(app, host="0.0.0.0", port=8088, log_level="warning")
    server = uvicorn.Server(server_config)
    
    log.info("="*60)
    log.info("DASHBOARD: http://localhost:8088")
    log.info("="*60)
    
    try:
        await server.serve()
    finally:
        await bot.stop()


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    asyncio.run(run_with_dashboard())
