"""
Mega Bot - Sistema unificado de trading con todas las estrategias.

Integra:
- Detección de oportunidades (arbitraje, time decay, momentum, etc.)
- Gestión de posiciones (entrada, take profit, stop loss)
- Simulación de trades
- Dashboard web
"""

import sys
import asyncio
import time
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, '.')

from api.polymarket_api import PolymarketAPI, get_polymarket_api
from api.kalshi_api import KalshiAPI, get_kalshi_api
from models.market import Market
from models.opportunity import Opportunity, OpportunityType
from opportunity_detector import OpportunityDetector
from logger import log


# ============== CONFIGURACIÓN ==============

@dataclass
class TradingConfig:
    """Configuración de trading - V2 MEJORADO con diversificación."""
    # Montos
    trade_amount: float = 2.0
    max_daily_exposure: float = 200.0  # Aumentado para más diversificación
    max_position_per_market: float = 10.0
    
    # Take profit dinámico según entry price
    take_profit_low: float = 0.40      # Entry < 0.30 -> +40%
    take_profit_medium: float = 0.20   # Entry 0.30-0.70 -> +20% 
    take_profit_high: float = 0.10     # Entry > 0.70 -> +10%
    
    # Take profit decreciente por tiempo
    tp_decay_enabled: bool = True
    tp_decay_hours: List[int] = field(default_factory=lambda: [6, 12, 24, 36])
    tp_decay_targets: List[float] = field(default_factory=lambda: [0.12, 0.08, 0.05, 0.03])
    
    # MEJORA: Stop loss HABILITADO (-25%)
    stop_loss_enabled: bool = True
    stop_loss_pct: float = -0.25  # Cortar pérdidas a -25%
    
    # MEJORA: Filtros MÁS LAXOS para encontrar más mercados únicos
    min_score: int = 30               # Más bajo para más oportunidades
    min_expected_profit: float = 1.5  # Más bajo
    min_confidence: int = 40          # Más permisivo
    max_days_to_resolution: int = 30  # 1 mes max
    
    # MEJORA: LÍMITE de trades por mercado para DIVERSIFICAR
    max_trades_per_market: int = 2    # Máximo 2 trades por mercado!
    
    # MEJORA: Límite de capital por mercado (diversificación)
    max_capital_per_market_pct: float = 0.15  # Max 15% del capital en un mercado
    
    # MEJORA: Filtro de duración mínima para momentum
    min_hours_for_momentum: float = 24.0  # No momentum en eventos <24h
    
    # Scan
    scan_interval: int = 180


# ============== POSICIÓN ==============

@dataclass
class Position:
    """Representa una posición abierta."""
    id: str
    market_id: str
    market_question: str
    side: str  # "YES" o "NO"
    entry_price: float
    amount: float
    shares: float
    current_price: float
    entry_time: datetime
    opportunity_type: str
    status: str = "OPEN"  # OPEN, CLOSED, RESOLVED
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def unrealized_pnl(self) -> float:
        """P&L no realizado."""
        if self.status != "OPEN":
            return 0
        price_change = self.current_price - self.entry_price
        if self.side == "NO":
            price_change = -price_change
        return self.shares * price_change
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """P&L % no realizado."""
        if self.entry_price == 0:
            return 0
        # Tanto YES como NO: ganancia cuando current_price > entry_price
        # (compramos a X, ahora vale Y, ganancia = Y - X)
        return (self.current_price - self.entry_price) / self.entry_price
    
    @property
    def hours_open(self) -> float:
        """Horas desde la entrada."""
        delta = datetime.now() - self.entry_time
        return delta.total_seconds() / 3600
    
    def get_target_take_profit(self, config: TradingConfig) -> float:
        """Obtiene el take profit objetivo según entry price y tiempo."""
        # Base take profit según entry price
        if self.entry_price < 0.30:
            base_tp = config.take_profit_low
        elif self.entry_price <= 0.70:
            base_tp = config.take_profit_medium
        else:
            base_tp = config.take_profit_high
        
        # Aplicar decay por tiempo si está habilitado
        if config.tp_decay_enabled:
            hours = self.hours_open
            for i, hour_threshold in enumerate(config.tp_decay_hours):
                if hours >= hour_threshold:
                    base_tp = min(base_tp, config.tp_decay_targets[i])
        
        return base_tp
    
    def should_take_profit(self, config: TradingConfig) -> bool:
        """Verifica si se debe tomar profit."""
        target_tp = self.get_target_take_profit(config)
        return self.unrealized_pnl_pct >= target_tp
    
    def should_stop_loss(self, config: TradingConfig) -> bool:
        """Verifica si se debe cortar pérdida."""
        if not config.stop_loss_enabled:
            return False
        return self.unrealized_pnl_pct <= config.stop_loss_pct
    
    def to_dict(self) -> Dict:
        """Convierte a diccionario."""
        return {
            'id': self.id,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'side': self.side,
            'entry_price': self.entry_price,
            'amount': self.amount,
            'shares': self.shares,
            'current_price': self.current_price,
            'entry_time': self.entry_time.isoformat(),
            'opportunity_type': self.opportunity_type,
            'status': self.status,
            'exit_price': self.exit_price,
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'pnl': self.pnl,
            'unrealized_pnl': self.unrealized_pnl,
            'hours_open': self.hours_open,
            'extra_data': self.extra_data,
        }


# ============== MEGA BOT ==============

class MegaBot:
    """
    Bot de trading unificado.
    """
    
    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        
        # APIs
        self.pm_api = get_polymarket_api()
        self.kalshi_api = get_kalshi_api()
        
        # Detector de oportunidades
        self.detector = OpportunityDetector()
        
        # Estado
        self.positions: List[Position] = []
        self.closed_positions: List[Position] = []
        self.trade_counter = 0
        
        # Stats - SIEMPRE empezar desde cero
        self.stats = {
            'total_scans': 0,
            'total_opportunities': 0,
            'total_trades': 0,
            'total_invested': 0.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'start_time': datetime.now(),  # SIEMPRE establecer a ahora cuando se crea el bot
        }
        
        # NO cargar estado previo - empezar desde cero
        # self._load_state(load_positions=False)  # Comentado para empezar siempre desde cero
        
        log.info(f"[MegaBot-V2] Starting fresh - start_time: {self.stats['start_time'].isoformat()}")
    
    def _load_state(self, load_positions: bool = False):
        """Carga estado previo."""
        state_file = Path('mega_bot_v2_state.json')
        if state_file.exists() and load_positions:
            try:
                with open(state_file, 'r') as f:
                    data = json.load(f)
                
                # Solo cargar posiciones si se solicita explícitamente
                if load_positions:
                    for p in data.get('positions', []):
                        pos = Position(
                            id=p['id'],
                            market_id=p['market_id'],
                            market_question=p['market_question'],
                            side=p['side'],
                            entry_price=p['entry_price'],
                            amount=p['amount'],
                            shares=p['shares'],
                            current_price=p['current_price'],
                            entry_time=datetime.fromisoformat(p['entry_time']),
                            opportunity_type=p['opportunity_type'],
                            status=p['status'],
                            extra_data=p.get('extra_data', {}),
                        )
                        self.positions.append(pos)
                    log.info(f"[MegaBot-V2] Loaded {len(self.positions)} positions from state")
                else:
                    log.info(f"[MegaBot-V2] State file exists but not loading (fresh start)")
            except Exception as e:
                log.error(f"[MegaBot-V2] Error loading state: {e}")
        elif state_file.exists():
            log.info(f"[MegaBot-V2] State file exists but starting fresh (no positions loaded)")
    
    def _save_state(self):
        """Guarda estado."""
        data = {
            'positions': [p.to_dict() for p in self.positions],
            'closed_positions': [p.to_dict() for p in self.closed_positions[-100:]],  # Últimos 100
            'stats': {
                'total_scans': self.stats['total_scans'],
                'total_opportunities': self.stats['total_opportunities'],
                'total_trades': self.stats['total_trades'],
                'total_invested': self.stats['total_invested'],
                'realized_pnl': self.stats['realized_pnl'],
                'wins': self.stats['wins'],
                'losses': self.stats['losses'],
            },
            # NO guardar start_time - siempre empezar desde cero cuando se carga
            # 'start_time': self.stats['start_time'].isoformat(),
            'trade_counter': self.trade_counter,
            'last_updated': datetime.now().isoformat(),
        }
        
        with open('mega_bot_v2_state.json', 'w') as f:
            json.dump(data, f, indent=2)
    
    async def scan(self) -> List[Opportunity]:
        """Ejecuta un escaneo."""
        self.stats['total_scans'] += 1
        
        log.info(f"\n{'='*60}")
        log.info(f"[MegaBot-V2] SCAN #{self.stats['total_scans']} - {datetime.now().strftime('%H:%M:%S')}")
        log.info(f"{'='*60}")
        
        # 1. Obtener datos
        log.info("[MegaBot-V2] Fetching market data...")
        raw_markets = self.pm_api.get_markets(limit=200)
        markets = [Market.from_api_response(m) for m in raw_markets]
        events = self.pm_api.get_events(limit=50)
        kalshi_markets = self.kalshi_api.get_markets_for_arbitrage()
        
        log.info(f"  - {len(markets)} markets, {len(events)} events, {len(kalshi_markets)} Kalshi")
        
        # 2. Detectar oportunidades
        opportunities = self.detector.scan_all(
            markets=markets,
            events=events,
            kalshi_markets=kalshi_markets,
        )
        
        self.stats['total_opportunities'] += len(opportunities)
        
        # 3. Filtrar por config
        filtered = self._filter_opportunities(opportunities, markets)
        log.info(f"[MegaBot-V2] {len(filtered)} opportunities after filtering")
        
        # 4. Actualizar precios de posiciones abiertas (usando también eventos para multi-outcome)
        await self._update_positions(markets, events)
        
        # 5. Verificar exits (take profit, stop loss, resolved)
        log.info(f"[MegaBot-V2] Checking exits for {len(self.positions)} open positions...")
        exits = self._check_exits()
        if exits:
            log.info(f"[MegaBot-V2] Found {len(exits)} positions to close")
        for pos in exits:
            # Usar la razón correcta guardada en _check_exits
            reason = getattr(self, '_exit_reasons', {}).get(pos.id, "UNKNOWN")
            self._close_position(pos, pos.current_price, reason)
        
        # 6. Ejecutar trades
        new_trades = self._execute_trades(filtered)
        
        # 7. Guardar estado
        self._save_state()
        
        # 8. Mostrar resumen
        self._print_summary()
        
        return filtered
    
    def _filter_opportunities(self, opportunities: List[Opportunity], markets: List[Market]) -> List[Opportunity]:
        """Filtra oportunidades - V2 MEJORADO con diversificación y filtros más laxos."""
        filtered = []
        
        # Crear diccionario de mercados por ID para búsqueda rápida
        markets_by_id = {m.id: m for m in markets}
        
        # Calcular capital actual por mercado para diversificación
        capital_by_market = {}
        for p in self.positions:
            capital_by_market[p.market_id] = capital_by_market.get(p.market_id, 0) + p.amount
        
        for opp in opportunities:
            # V2 FILTRO: NO multi-outcome arbitrage (precios promediados no funcionan)
            if opp.type == OpportunityType.MULTI_OUTCOME_ARB:
                log.debug(f"[V2-FILTER] Skipping MULTI_ARB: {opp.market_question[:30]}...")
                continue
            
            # V2 FILTRO: Acepta BUY_YES y BUY_NO
            if opp.action.value not in ['buy_yes', 'buy_no']:
                log.debug(f"[V2-FILTER] Skipping action ({opp.action.value}): {opp.market_question[:30]}...")
                continue
            
            # Filtro: score/confidence (más laxo)
            if opp.confidence < self.config.min_confidence:
                continue
            
            # Filtro: profit esperado (más laxo)
            if opp.expected_profit < self.config.min_expected_profit:
                continue
            
            # MEJORA: Máximo trades por mercado (DIVERSIFICACIÓN)
            market_trades = sum(1 for p in self.positions if p.market_id == opp.market_id)
            if market_trades >= self.config.max_trades_per_market:
                log.debug(f"[V2-FILTER] Max trades ({market_trades}) reached for: {opp.market_question[:30]}...")
                continue
            
            # MEJORA: Límite de capital por mercado (15% max)
            current_market_capital = capital_by_market.get(opp.market_id, 0)
            max_capital = self.config.max_daily_exposure * self.config.max_capital_per_market_pct
            if current_market_capital >= max_capital:
                log.debug(f"[V2-FILTER] Max capital (${current_market_capital:.0f}) reached for: {opp.market_question[:30]}...")
                continue
            
            # Filtro: exposure diario
            if self.stats['total_invested'] >= self.config.max_daily_exposure:
                continue
            
            # Calcular precio de entrada
            if opp.action.value == 'buy_yes':
                entry_price = opp.current_yes_price or 0
            else:  # buy_no
                yes_price = opp.current_yes_price or 0
                entry_price = (1.0 - yes_price) if yes_price > 0 else (opp.current_no_price or 0)
            
            # MEJORA: Rango de precio AMPLIADO: 3c-65c (más oportunidades)
            if entry_price > 0.65:
                continue
            if entry_price < 0.03 and entry_price > 0:
                continue
            
            # Calcular días a resolución
            days_to_resolution = opp.days_to_resolution
            hours_to_resolution = None
            if days_to_resolution is None and opp.market_id:
                matching_market = markets_by_id.get(opp.market_id)
                if matching_market and matching_market.end_date:
                    delta = matching_market.end_date - datetime.now()
                    days_to_resolution = delta.total_seconds() / 86400
                    hours_to_resolution = delta.total_seconds() / 3600
            elif days_to_resolution is not None:
                hours_to_resolution = days_to_resolution * 24
            
            # Filtro: días a resolución (más laxo: 30 días)
            if days_to_resolution is not None and days_to_resolution > self.config.max_days_to_resolution:
                continue
            
            # MEJORA: Filtrar momentum en eventos MUY CORTOS (<24h)
            # Los eventos que resuelven en horas son básicamente apuestas binarias
            is_momentum = opp.type.value in ['momentum_long', 'momentum_short', 'contrarian']
            if is_momentum and hours_to_resolution is not None:
                if hours_to_resolution < self.config.min_hours_for_momentum:
                    log.debug(f"[V2-FILTER] Momentum skip (only {hours_to_resolution:.0f}h left): {opp.market_question[:30]}...")
                    continue
            
            # MEJORA: Priorizar time_decay sobre momentum (bonus de ordenamiento)
            # Esto se hace añadiendo un score_bonus que se usará para ordenar
            opp._score_bonus = 0
            if opp.type.value in ['time_decay', 'improbable_expiring']:
                opp._score_bonus = 20  # Priorizar time decay
            elif opp.type.value == 'near_certain':
                opp._score_bonus = 10  # También priorizar near_certain
            
            filtered.append(opp)
        
        # MEJORA: Ordenar por score + bonus (priorizar time_decay)
        filtered.sort(key=lambda x: (getattr(x, '_score_bonus', 0) + (x.confidence or 0)), reverse=True)
        
        return filtered
    
    async def _update_positions(self, markets: List[Market], events: List[Dict] = None):
        """Actualiza precios de posiciones abiertas."""
        import json
        
        # Crear diccionario de precios por ID y también por condition_id
        market_prices = {}
        for m in markets:
            market_prices[m.id] = (m.yes_price, m.no_price)
            if m.condition_id:
                market_prices[m.condition_id] = (m.yes_price, m.no_price)
        
        # Para multi-outcome, crear diccionario de eventos por ID
        event_markets_by_event_id = {}
        if events:
            for event in events:
                event_id = str(event.get('id') or event.get('event_id', ''))
                if event_id and 'markets' in event:
                    event_markets_by_event_id[event_id] = event['markets']
        
        multi_outcome_count = sum(1 for p in self.positions if p.extra_data.get('is_multi_outcome'))
        log.info(f"[MegaBot-V2] Updating {len(self.positions)} positions ({multi_outcome_count} multi-outcome), found {len(event_markets_by_event_id)} events")
        
        updated_count = 0
        for pos in self.positions:
            old_price = pos.current_price
            
            # Para multi-outcome arbitrage, buscar precios desde los eventos
            if pos.extra_data.get('is_multi_outcome'):
                # Intentar obtener precios desde el evento actual
                event_id = str(pos.market_id)
                total_price = 0
                valid_prices = 0
                
                if event_id in event_markets_by_event_id:
                    log.info(f"[MegaBot-V2] Found event {event_id} for position {pos.id} ({pos.market_question[:40]}...)")
                    event_markets = event_markets_by_event_id[event_id]
                    log.info(f"[MegaBot-V2] Event has {len(event_markets)} markets")
                    for m in event_markets:
                        # Extraer precio YES del mercado del evento
                        price = 0
                        if 'outcomePrices' in m:
                            prices_str = m.get('outcomePrices', '[]')
                            try:
                                prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                                if prices and len(prices) > 0:
                                    price = float(prices[0])
                            except Exception as e:
                                log.debug(f"[MegaBot-V2] Error parsing outcomePrices: {e}")
                                pass
                        elif 'yes_price' in m:
                            price = float(m.get('yes_price', 0))
                        elif 'price' in m:
                            # Fallback: usar 'price' directamente
                            price = float(m.get('price', 0))
                        
                        if 0 < price < 1:
                            if pos.side == "YES":
                                total_price += price
                            else:
                                total_price += (1.0 - price)
                            valid_prices += 1
                            log.debug(f"[MegaBot-V2] Added price {price:.4f} from market {m.get('id', 'unknown')} (side: {pos.side})")
                        else:
                            log.debug(f"[MegaBot-V2] Invalid price {price} from market {m.get('id', 'unknown')}")
                    
                    log.info(f"[MegaBot-V2] Extracted {valid_prices} valid prices from event {event_id}")
                
                # Si no encontramos en eventos, intentar con IDs guardados
                if valid_prices == 0:
                    individual_ids = pos.extra_data.get('individual_market_ids', [])
                    condition_ids = pos.extra_data.get('individual_condition_ids', [])
                    
                    for market_id in individual_ids + condition_ids:
                        if market_id in market_prices:
                            yes_price, no_price = market_prices[market_id]
                            if pos.side == "YES":
                                total_price += yes_price
                            else:
                                total_price += no_price
                            valid_prices += 1
                
                if valid_prices > 0:
                    new_price = total_price / valid_prices
                    # Siempre actualizar el precio, incluso si el cambio es pequeño
                    # Esto asegura que el P&L se calcule correctamente
                    if abs(new_price - pos.current_price) > 0.0001:  # Reducido umbral a 0.01%
                        old_price = pos.current_price
                        pos.current_price = new_price  # Actualizar primero
                        # Ahora calcular P&L con el precio actualizado
                        log.info(f"[MegaBot-V2] [OK] Updating multi-outcome price: {pos.market_question[:40]}... "
                                f"{old_price:.4f} -> {new_price:.4f} ({valid_prices} markets) | "
                                f"P&L: ${pos.unrealized_pnl:.2f} ({pos.unrealized_pnl_pct:+.2%})")
                        updated_count += 1
                    else:
                        log.debug(f"[MegaBot-V2] Price unchanged for {pos.market_question[:40]}... "
                                 f"({pos.current_price:.4f} ≈ {new_price:.4f}, diff: {abs(new_price - pos.current_price):.6f})")
                else:
                    # No encontramos precios - esto es un problema
                    log.warning(f"[MegaBot-V2] [X] Could not find prices for multi-outcome position: "
                               f"{pos.market_question[:40]}... (event_id: {event_id}, tried {len(pos.extra_data.get('individual_market_ids', []))} market IDs)")
                # Si no encontramos precios, mantener el precio actual
            elif pos.market_id in market_prices:
                yes_price, no_price = market_prices[pos.market_id]
                pos.current_price = yes_price if pos.side == "YES" else no_price
                if old_price != pos.current_price:
                    updated_count += 1
        
        if updated_count > 0:
            log.info(f"[MegaBot-V2] Updated prices for {updated_count} positions")
    
    def _check_exits(self) -> List[Position]:
        """Verifica qué posiciones deben cerrarse - con STOP LOSS activo."""
        exits = []
        exit_reasons = {}  # Guardar razón de cierre
        
        for i, pos in enumerate(self.positions):
            if pos.status != "OPEN":
                continue
            
            # Calcular valores para debugging
            target_tp = pos.get_target_take_profit(self.config)
            current_pnl_pct = pos.unrealized_pnl_pct
            
            # Log para debugging (siempre mostrar las primeras 3 posiciones)
            if i < 3:
                sl_status = f"SL: {self.config.stop_loss_pct:+.0%}" if self.config.stop_loss_enabled else "SL: OFF"
                log.info(f"[MegaBot-V2] Checking exit #{i+1}: {pos.market_question[:40]}... | "
                        f"P&L: {current_pnl_pct:+.2%} | TP: {target_tp:+.2%} | {sl_status} | "
                        f"Entry: ${pos.entry_price:.4f} | Current: ${pos.current_price:.4f} | "
                        f"Hours: {pos.hours_open:.1f}h")
            
            # Take profit
            if pos.should_take_profit(self.config):
                log.info(f"[MegaBot-V2] [OK] TAKE PROFIT: {pos.market_question[:40]}... "
                        f"(P&L: {current_pnl_pct:+.1%} >= TP: {target_tp:+.1%})")
                exits.append(pos)
                exit_reasons[pos.id] = "TAKE_PROFIT"
                continue
            
            # MEJORA: Stop loss ACTIVO (-25%)
            if pos.should_stop_loss(self.config):
                log.info(f"[MegaBot-V2] [X] STOP LOSS: {pos.market_question[:40]}... "
                        f"(P&L: {current_pnl_pct:+.1%} <= SL: {self.config.stop_loss_pct:+.1%})")
                exits.append(pos)
                exit_reasons[pos.id] = "STOP_LOSS"
                continue
            
            # MEJORA: Cerrar posiciones de mercados ya resueltos (precio ~0 o ~1)
            if pos.current_price <= 0.01 or pos.current_price >= 0.99:
                reason = "RESOLVED_WIN" if current_pnl_pct > 0 else "RESOLVED_LOSS"
                log.info(f"[MegaBot-V2] [!] MARKET RESOLVED: {pos.market_question[:40]}... "
                        f"(Price: ${pos.current_price:.4f}, P&L: {current_pnl_pct:+.1%})")
                exits.append(pos)
                exit_reasons[pos.id] = reason
        
        # Guardar razones para usar en _close_position
        self._exit_reasons = exit_reasons
        
        return exits
    
    def _close_position(self, pos: Position, exit_price: float, reason: str):
        """Cierra una posición."""
        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.status = "CLOSED"
        
        # Calcular P&L
        price_change = exit_price - pos.entry_price
        if pos.side == "NO":
            price_change = -price_change
        pos.pnl = pos.shares * price_change
        
        # Actualizar stats
        self.stats['realized_pnl'] += pos.pnl
        if pos.pnl > 0:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
        
        # Mover a closed
        self.positions.remove(pos)
        self.closed_positions.append(pos)
        
        log.info(f"[MegaBot-V2] CLOSED: {reason} | PnL: ${pos.pnl:.2f} | "
                f"{pos.market_question[:40]}...")
    
    def _execute_trades(self, opportunities: List[Opportunity]) -> List[Position]:
        """Ejecuta trades en oportunidades."""
        new_trades = []
        
        for opp in opportunities[:5]:  # Máximo 5 trades por scan
            # Verificar exposure
            if self.stats['total_invested'] >= self.config.max_daily_exposure:
                break
            
            # Crear posición
            self.trade_counter += 1
            
            side = "YES"
            if opp.action.value in ['buy_no', 'buy_all_no']:
                side = "NO"
            
            # Obtener precio de entrada
            if side == "YES":
                entry_price = opp.current_yes_price
            else:
                # Para NO, usar current_no_price o calcularlo
                entry_price = opp.current_no_price
                if entry_price is None or entry_price <= 0:
                    # Calcular NO price desde YES price
                    entry_price = 1.0 - opp.current_yes_price if opp.current_yes_price > 0 else 0.5
            
            # Validar precio
            if entry_price is None or entry_price <= 0 or entry_price >= 1.0:
                log.warning(f"[MegaBot-V2] Invalid entry price {entry_price} for {opp.market_question[:40]}...")
                continue
            
            shares = self.config.trade_amount / entry_price if entry_price > 0 else 0
            
            # Para multi-outcome arbitrage, guardar IDs de mercados individuales
            extra_data = {}
            if opp.type.value == 'multi_outcome_arb' and opp.markets:
                # Extraer IDs de mercados individuales
                # Guardar tanto 'id' como 'conditionId' para máxima compatibilidad
                market_ids = []
                condition_ids = []
                for m in opp.markets:
                    # Guardar ambos: id y conditionId
                    market_id = m.get('id') or m.get('market_id')
                    condition_id = m.get('conditionId') or m.get('condition_id')
                    
                    if market_id:
                        market_ids.append(str(market_id))
                    if condition_id:
                        condition_ids.append(str(condition_id))
                
                if market_ids or condition_ids:
                    extra_data['individual_market_ids'] = market_ids  # IDs principales
                    extra_data['individual_condition_ids'] = condition_ids  # Condition IDs como backup
                    extra_data['is_multi_outcome'] = True
                    log.info(f"[MegaBot-V2] Multi-outcome position: {len(market_ids)} market IDs, {len(condition_ids)} condition IDs")
                else:
                    log.warning(f"[MegaBot-V2] No individual market IDs found for multi-outcome arb")
            
            pos = Position(
                id=f"trade_{self.trade_counter}",
                market_id=opp.market_id,
                market_question=opp.market_question,
                side=side,
                entry_price=entry_price,
                amount=self.config.trade_amount,
                shares=shares,
                current_price=entry_price,
                entry_time=datetime.now(),
                opportunity_type=opp.type.value,
            )
            # Guardar extra_data si existe
            if extra_data:
                pos.extra_data = extra_data
            
            self.positions.append(pos)
            new_trades.append(pos)
            
            # Actualizar stats
            self.stats['total_trades'] += 1
            self.stats['total_invested'] += self.config.trade_amount
            
            log.info(
                f"[MegaBot-V2] NEW TRADE: {opp.type.value} | {side} @ ${entry_price:.2f} | "
                f"{opp.market_question[:40]}..."
            )
        
        return new_trades
    
    def _print_summary(self):
        """Muestra resumen con métricas de DIVERSIFICACIÓN."""
        # Calcular unrealized
        unrealized = sum(p.unrealized_pnl for p in self.positions)
        self.stats['unrealized_pnl'] = unrealized
        
        total_pnl = self.stats['realized_pnl'] + unrealized
        win_rate = self.stats['wins'] / max(1, self.stats['wins'] + self.stats['losses']) * 100
        
        # Calcular tiempo corriendo
        runtime = datetime.now() - self.stats['start_time']
        hours = runtime.total_seconds() / 3600
        daily_roi = (total_pnl / max(1, self.stats['total_invested'])) * 100 if hours > 0 else 0
        
        # MEJORA: Calcular métricas de diversificación
        unique_markets = len(set(p.market_id for p in self.positions))
        avg_trades_per_market = len(self.positions) / max(1, unique_markets)
        
        # Capital por mercado
        capital_by_market = {}
        for p in self.positions:
            capital_by_market[p.market_id] = capital_by_market.get(p.market_id, 0) + p.amount
        max_concentration = max(capital_by_market.values()) if capital_by_market else 0
        max_concentration_pct = (max_concentration / max(1, self.stats['total_invested'])) * 100
        
        log.info(f"\n{'='*60}")
        log.info("MEGA BOT V2 STATUS (DIVERSIFIED)")
        log.info(f"{'='*60}")
        log.info(f"Runtime: {hours:.1f}h | Scans: {self.stats['total_scans']}")
        log.info(f"Positions: {len(self.positions)} open | {len(self.closed_positions)} closed")
        log.info(f"Unique Markets: {unique_markets} | Avg trades/market: {avg_trades_per_market:.1f}")
        log.info(f"Max Concentration: ${max_concentration:.0f} ({max_concentration_pct:.0f}%)")
        log.info(f"{'='*60}")
        log.info(f"Invested: ${self.stats['total_invested']:.2f}")
        log.info(f"Realized P&L: ${self.stats['realized_pnl']:.2f}")
        log.info(f"Unrealized P&L: ${unrealized:.2f}")
        log.info(f"Total P&L: ${total_pnl:.2f} ({daily_roi:.1f}% ROI)")
        log.info(f"Win Rate: {win_rate:.1f}% ({self.stats['wins']}W / {self.stats['losses']}L)")
        log.info(f"Stop Loss: {'ON (-25%)' if self.config.stop_loss_enabled else 'OFF'}")
        log.info(f"{'='*60}\n")
    
    def get_dashboard_data(self) -> Dict:
        """Retorna datos para el dashboard."""
        unrealized = sum(p.unrealized_pnl for p in self.positions)
        total_pnl = self.stats['realized_pnl'] + unrealized
        
        runtime = datetime.now() - self.stats['start_time']
        hours = runtime.total_seconds() / 3600
        days = hours / 24
        
        # Calcular ROI total
        total_roi = (total_pnl / max(1, self.stats['total_invested'])) * 100
        
        # Calcular ROI diario (extrapolado)
        daily_roi = (total_roi / max(days, 0.01)) if days > 0 else 0
        
        return {
            'status': 'running',
            'runtime_hours': hours,
            'runtime_days': days,
            'total_scans': self.stats['total_scans'],
            'total_opportunities': self.stats['total_opportunities'],
            'total_trades': self.stats['total_trades'],
            'total_invested': self.stats['total_invested'],
            'realized_pnl': self.stats['realized_pnl'],
            'unrealized_pnl': unrealized,
            'total_pnl': total_pnl,
            'total_roi': total_roi,
            'daily_roi': daily_roi,
            'wins': self.stats['wins'],
            'losses': self.stats['losses'],
            'win_rate': self.stats['wins'] / max(1, self.stats['wins'] + self.stats['losses']) * 100,
            'open_positions': len(self.positions),
            'closed_positions': len(self.closed_positions),
            'positions': [p.to_dict() for p in self.positions],
            'recent_closed': [p.to_dict() for p in self.closed_positions[-10:]],
            'config': asdict(self.config),
        }
    
    async def run(self, max_scans: int = None):
        """Ejecuta el bot continuamente."""
        log.info(f"\n{'#'*60}")
        log.info("MEGA BOT STARTING")
        log.info(f"Trade amount: ${self.config.trade_amount}")
        log.info(f"Max daily exposure: ${self.config.max_daily_exposure}")
        log.info(f"Scan interval: {self.config.scan_interval}s")
        log.info(f"{'#'*60}\n")
        
        scan_count = 0
        
        try:
            while max_scans is None or scan_count < max_scans:
                await self.scan()
                scan_count += 1
                
                if max_scans is None or scan_count < max_scans:
                    log.info(f"Next scan in {self.config.scan_interval}s...")
                    await asyncio.sleep(self.config.scan_interval)
                    
        except KeyboardInterrupt:
            log.info("\n[MegaBot-V2] Stopped by user")
        
        self._save_state()
        self._print_summary()


# ============== SERVIDOR WEB ==============

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import threading

app = FastAPI()
bot: Optional[MegaBot] = None


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Dashboard HTML."""
    global bot
    if bot is None:
        return "<h1>Bot not started</h1>"
    
    data = bot.get_dashboard_data()
    
    # Generar HTML de posiciones
    positions_html = ""
    for p in data['positions'][:20]:
        pnl_class = "positive" if p['unrealized_pnl'] > 0 else "negative"
        positions_html += f"""
        <tr>
            <td>{p['market_question'][:50]}...</td>
            <td>{p['side']}</td>
            <td>${p['entry_price']:.2f}</td>
            <td>${p['current_price']:.2f}</td>
            <td class="{pnl_class}">${p['unrealized_pnl']:.2f}</td>
            <td>{p['hours_open']:.1f}h</td>
        </tr>
        """
    
    pnl_class = "positive" if data['total_pnl'] > 0 else "negative"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mega Bot Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            h1 {{ color: #00d4ff; }}
            .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }}
            .stat {{ background: #16213e; padding: 15px; border-radius: 8px; text-align: center; }}
            .stat-value {{ font-size: 24px; font-weight: bold; color: #00d4ff; }}
            .stat-label {{ font-size: 12px; color: #888; }}
            .positive {{ color: #00ff88; }}
            .negative {{ color: #ff4444; }}
            table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #333; }}
            th {{ background: #16213e; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 Mega Bot Dashboard</h1>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">{data['runtime_hours']:.1f}h</div>
                    <div class="stat-label">Runtime</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['total_scans']}</div>
                    <div class="stat-label">Scans</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['total_trades']}</div>
                    <div class="stat-label">Trades</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['win_rate']:.0f}%</div>
                    <div class="stat-label">Win Rate</div>
                </div>
            </div>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-value">${data['total_invested']:.2f}</div>
                    <div class="stat-label">Invested</div>
                </div>
                <div class="stat">
                    <div class="stat-value {pnl_class}">${data['realized_pnl']:.2f}</div>
                    <div class="stat-label">Realized P&L</div>
                </div>
                <div class="stat">
                    <div class="stat-value">${data['unrealized_pnl']:.2f}</div>
                    <div class="stat-label">Unrealized P&L</div>
                </div>
                <div class="stat">
                    <div class="stat-value {pnl_class}">${data['total_pnl']:.2f}</div>
                    <div class="stat-label">Total P&L</div>
                </div>
            </div>
            
            <div class="stats">
                <div class="stat">
                    <div class="stat-value {pnl_class}">{data['total_roi']:.1f}%</div>
                    <div class="stat-label">Total ROI</div>
                </div>
                <div class="stat">
                    <div class="stat-value {pnl_class}">{data['daily_roi']:.1f}%</div>
                    <div class="stat-label">Daily ROI</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['open_positions']}</div>
                    <div class="stat-label">Open Positions</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['closed_positions']}</div>
                    <div class="stat-label">Closed Positions</div>
                </div>
            </div>
            
            <h2>Open Positions ({data['open_positions']})</h2>
            <table>
                <tr>
                    <th>Market</th>
                    <th>Side</th>
                    <th>Entry</th>
                    <th>Current</th>
                    <th>P&L</th>
                    <th>Time</th>
                </tr>
                {positions_html}
            </table>
            
            <p style="color: #666; font-size: 12px;">
                Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            </p>
        </div>
    </body>
    </html>
    """
    return html


@app.get("/api/stats")
async def api_stats():
    """API endpoint para stats."""
    global bot
    if bot is None:
        return {"error": "Bot not started"}
    return bot.get_dashboard_data()


def start_server(port: int = 8080):
    """Inicia servidor en thread separado."""
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ============== MAIN ==============

async def main():
    global bot
    
    import argparse
    parser = argparse.ArgumentParser(description='Mega Bot - Unified Trading Bot')
    parser.add_argument('--port', '-p', type=int, default=8080, help='Dashboard port')
    parser.add_argument('--interval', '-i', type=int, default=180, help='Scan interval (seconds)')
    parser.add_argument('--amount', '-a', type=float, default=2.0, help='Trade amount')
    parser.add_argument('--exposure', '-e', type=float, default=100.0, help='Max daily exposure')
    parser.add_argument('--no-server', action='store_true', help='Disable web server')
    
    args = parser.parse_args()
    
    # Crear config
    config = TradingConfig(
        trade_amount=args.amount,
        max_daily_exposure=args.exposure,
        scan_interval=args.interval,
    )
    
    # Crear bot
    bot = MegaBot(config)
    
    # Iniciar servidor web
    if not args.no_server:
        server_thread = threading.Thread(target=start_server, args=(args.port,), daemon=True)
        server_thread.start()
        log.info(f"[MegaBot-V2] Dashboard running at http://localhost:{args.port}")
    
    # Ejecutar bot
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
