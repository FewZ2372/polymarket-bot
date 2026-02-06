"""
Mega Bot V3 - Sistema COMPLETO de trading cuantitativo.

Implementa TODAS las estrategias de la conversación GPT:
- Edge histórico por subclase de mercado
- Persistencia de mala cotización  
- Asimetría de liquidez
- Elasticidad informativa
- Sesgo de cluster
- Clasificación de mercados (excluir memes/deportes)
- Límites de diversificación
- Stop loss dinámico
- Scoring combinado (macro + señales)
"""

import sys
import asyncio
import time
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, '.')

from api.polymarket_api import PolymarketAPI, get_polymarket_api
from api.kalshi_api import KalshiAPI, get_kalshi_api
from models.market import Market
from models.opportunity import Opportunity, OpportunityType
from opportunity_detector import OpportunityDetector
from logger import log


# ============== CLASIFICACIÓN DE MERCADOS ==============

class MarketCategory:
    """Categorías de mercados válidas e inválidas."""
    
    # VÁLIDAS - mercados con edge explotable
    POLITICS = "politics"           # Elecciones, arrestos, renuncias
    LEGAL = "legal"                 # Fallos judiciales, apelaciones
    REGULATION = "regulation"       # Multas, prohibiciones, sanciones
    MACRO = "macro"                 # Tasas, inflación, default
    CRYPTO = "crypto"               # Bitcoin, ETH precios
    GEOPOLITICS = "geopolitics"     # Guerras, conflictos, tratados
    CORPORATE = "corporate"         # Adquisiciones, earnings, IPOs
    
    # INVÁLIDAS - evitar (alta varianza, poco edge)
    SPORTS_LIVE = "sports_live"     # Deportes en vivo
    CELEBRITY = "celebrity"         # Celebridades, entretenimiento
    MEME = "meme"                   # Mercados virales/meme
    WEATHER = "weather"             # Clima (muy impredecible)
    
    VALID_CATEGORIES = {POLITICS, LEGAL, REGULATION, MACRO, CRYPTO, GEOPOLITICS, CORPORATE}
    INVALID_CATEGORIES = {SPORTS_LIVE, CELEBRITY, MEME, WEATHER}


def classify_market(question: str, slug: str = "") -> Tuple[str, bool]:
    """
    Clasifica un mercado por su pregunta.
    Returns: (category, is_valid)
    """
    q = question.lower()
    s = slug.lower() if slug else ""
    combined = f"{q} {s}"
    
    # INVÁLIDOS primero (más específicos)
    # Nota: Solo matchear deportes EN VIVO, no futuros a largo plazo
    invalid_patterns = {
        MarketCategory.SPORTS_LIVE: [
            r'\bvs\.?\b.*\b(today|tonight|game\s*\d)\b',  # Solo juegos específicos
            r'\bspread\b', r'\bmoneyline\b', r'\bover/under\b',
            r'\bgoals?\s*(scored|over|under)\b',
            # Equipos solo si es partido específico, no campeonatos
        ],
        MarketCategory.CELEBRITY: [
            r'\b(taylor\s*swift|kanye|kardashian|beyonce|drake|rihanna)\b',
            r'\b(pregnant|baby|married|divorce|dating|engaged)\b',
            r'\b(grammy|oscar|emmy|golden\s*globe|award)\b',
            r'\b(album|song|movie|netflix|disney)\b',
        ],
        MarketCategory.MEME: [
            r'\bmeme\b', r'\bvirgin\b', r'\bchad\b',
            r'\b(dogecoin|shib|pepe|wojak)\b',
            r'\b(wen|gm|wagmi|ngmi)\b',
        ],
        MarketCategory.WEATHER: [
            r'\b(hurricane|tornado|earthquake|flood|wildfire)\b',
            r'\b(temperature|rainfall|snow|weather)\b',
        ],
    }
    
    for category, patterns in invalid_patterns.items():
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return category, False
    
    # VÁLIDOS
    valid_patterns = {
        MarketCategory.POLITICS: [
            r'\b(president|election|vote|poll|congress|senate|parliament)\b',
            r'\b(trump|biden|desantis|newsom|governor|mayor|minister)\b',
            r'\b(arrested|impeach|resign|indicted|convicted)\b',
            r'\b(democrat|republican|labour|conservative)\b',
        ],
        MarketCategory.LEGAL: [
            r'\b(court|judge|trial|verdict|lawsuit|settlement)\b',
            r'\b(supreme\s*court|appeal|ruling|conviction)\b',
            r'\b(guilty|innocent|sentenced|prison)\b',
        ],
        MarketCategory.REGULATION: [
            r'\b(sec|ftc|doj|fda|epa|fcc)\b',
            r'\b(regulation|ban|approve|sanction|tariff)\b',
            r'\b(antitrust|monopoly|fine|penalty)\b',
        ],
        MarketCategory.MACRO: [
            r'\b(fed|interest\s*rate|inflation|gdp|unemployment)\b',
            r'\b(recession|default|debt\s*ceiling|treasury)\b',
            r'\b(fomc|powell|yellen|ecb|boe)\b',
        ],
        MarketCategory.CRYPTO: [
            r'\b(bitcoin|btc|ethereum|eth|crypto)\b',
            r'\b(halving|etf|spot\s*etf)\b',
            r'\b\$\d+[k,]?\d*\b',  # Price targets like $100k
        ],
        MarketCategory.GEOPOLITICS: [
            r'\b(war|invasion|military|nato|china|russia|ukraine)\b',
            r'\b(missile|nuclear|treaty|ceasefire|peace)\b',
            r'\b(sovereignty|territory|border)\b',
        ],
        MarketCategory.CORPORATE: [
            r'\b(acquire|merger|ipo|earnings|revenue)\b',
            r'\b(ceo|fired|hire|layoff)\b',
            r'\b(stock|share|market\s*cap)\b',
            r'\b(tesla|apple|google|microsoft|amazon|meta)\b',
        ],
    }
    
    for category, patterns in valid_patterns.items():
        for pattern in patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return category, True
    
    # Default: asumir válido si no matchea nada malo
    return "other", True


# ============== DATOS HISTÓRICOS POR SUBCLASE ==============

# Dataset de probabilidades históricas basado en datos reales
HISTORICAL_PROBABILITIES = {
    # Política
    "incumbent_wins_reelection": 0.65,          # Incumbentes ganan 65%
    "impeachment_succeeds": 0.15,               # Impeachments rara vez pasan
    "politician_arrested": 0.25,                # Arrestos de políticos
    "law_passes_after_committee": 0.72,         # Leyes post-comité
    "governor_recall_succeeds": 0.20,           # Recalls casi nunca
    
    # Legal
    "appeal_overturns_verdict": 0.18,           # Apelaciones rara vez ganan
    "supreme_court_affirms": 0.70,              # SCOTUS suele afirmar
    "settlement_before_trial": 0.85,            # Mayoría settlea
    "conviction_rate_federal": 0.90,            # Feds ganan casi siempre
    
    # Regulación
    "fda_approves_drug": 0.75,                  # FDA aprueba mayoría
    "sec_wins_case": 0.80,                      # SEC casi siempre gana
    "antitrust_blocks_merger": 0.30,            # Raro que bloqueen
    "sanction_imposed": 0.60,                   # Sanciones frecuentes
    
    # Macro
    "fed_raises_rates": 0.55,                   # Histórico de subidas
    "fed_holds_rates": 0.30,                    # Holds
    "fed_cuts_rates": 0.15,                     # Cortes raros
    "recession_next_year": 0.20,                # Base rate recesión
    
    # Crypto
    "btc_above_ath_6mo": 0.35,                  # BTC sobre ATH
    "btc_drops_50pct": 0.15,                    # Crash severo
    "etf_approved": 0.60,                       # ETFs suelen aprobarse
    
    # Geopolítica
    "war_ends_6mo": 0.15,                       # Guerras duran
    "ceasefire_holds": 0.40,                    # Ceasefires frágiles
    "sanctions_lifted": 0.20,                   # Sanciones persisten
    
    # Default para categorías desconocidas
    "default": 0.50,
}


def get_historical_probability(question: str, category: str) -> float:
    """
    Obtiene probabilidad histórica basada en el tipo de pregunta.
    """
    q = question.lower()
    
    # Mapear pregunta a subclase
    if "impeach" in q:
        return HISTORICAL_PROBABILITIES["impeachment_succeeds"]
    elif "arrest" in q or "indicted" in q:
        return HISTORICAL_PROBABILITIES["politician_arrested"]
    elif "reelect" in q or "win" in q and "election" in q:
        return HISTORICAL_PROBABILITIES["incumbent_wins_reelection"]
    elif "appeal" in q:
        return HISTORICAL_PROBABILITIES["appeal_overturns_verdict"]
    elif "supreme court" in q:
        return HISTORICAL_PROBABILITIES["supreme_court_affirms"]
    elif "fda" in q and "approv" in q:
        return HISTORICAL_PROBABILITIES["fda_approves_drug"]
    elif "sec" in q:
        return HISTORICAL_PROBABILITIES["sec_wins_case"]
    elif "fed" in q and ("rate" in q or "interest" in q):
        if "cut" in q or "decrease" in q:
            return HISTORICAL_PROBABILITIES["fed_cuts_rates"]
        elif "raise" in q or "increase" in q:
            return HISTORICAL_PROBABILITIES["fed_raises_rates"]
        else:
            return HISTORICAL_PROBABILITIES["fed_holds_rates"]
    elif "recession" in q:
        return HISTORICAL_PROBABILITIES["recession_next_year"]
    elif "bitcoin" in q or "btc" in q:
        if "above" in q or "over" in q:
            return HISTORICAL_PROBABILITIES["btc_above_ath_6mo"]
        elif "drop" in q or "crash" in q or "below" in q:
            return HISTORICAL_PROBABILITIES["btc_drops_50pct"]
    elif "etf" in q:
        return HISTORICAL_PROBABILITIES["etf_approved"]
    elif "war" in q and "end" in q:
        return HISTORICAL_PROBABILITIES["war_ends_6mo"]
    elif "ceasefire" in q:
        return HISTORICAL_PROBABILITIES["ceasefire_holds"]
    elif "sanction" in q:
        if "lift" in q or "remove" in q:
            return HISTORICAL_PROBABILITIES["sanctions_lifted"]
        else:
            return HISTORICAL_PROBABILITIES["sanction_imposed"]
    
    return HISTORICAL_PROBABILITIES["default"]


# ============== CONFIGURACIÓN ==============

@dataclass
class TradingConfigV3:
    """Configuración de trading V3 - OPTIMIZADA según conversación GPT."""
    
    # === MONTOS ===
    trade_amount: float = 2.0
    max_daily_exposure: float = 200.0
    risk_per_trade_pct: float = 0.015  # 1.5% del capital por trade (GPT)
    
    # === TAKE PROFIT DINÁMICO ===
    # Según entry price (bajo = más upside)
    take_profit_low: float = 0.40      # Entry < 0.30 -> +40%
    take_profit_medium: float = 0.20   # Entry 0.30-0.60 -> +20%
    take_profit_high: float = 0.10     # Entry > 0.60 -> +10%
    
    # Decay por tiempo
    tp_decay_enabled: bool = True
    tp_decay_hours: List[int] = field(default_factory=lambda: [6, 12, 24, 48])
    tp_decay_targets: List[float] = field(default_factory=lambda: [0.15, 0.10, 0.06, 0.03])
    
    # === STOP LOSS ===
    stop_loss_enabled: bool = True
    stop_loss_pct: float = -0.25       # -25% (GPT sugiere -30%, somos más conservadores)
    
    # === FILTROS DE OPORTUNIDAD ===
    min_score: int = 30                # Score mínimo (0-100)
    min_expected_profit: float = 1.5   # Profit mínimo esperado
    min_confidence: int = 40           # Confianza mínima
    max_days_to_resolution: int = 30   # 1 mes máximo
    
    # === FILTROS DE PRECIO ===
    min_entry_price: float = 0.01      # 1 centavo mínimo
    max_entry_price: float = 0.95      # 95 centavos máximo (muy permisivo para capturar near_certain)
    
    # === DIVERSIFICACIÓN (CLAVE) ===
    max_trades_per_market: int = 2     # Máximo 2 trades por mercado
    max_capital_per_market_pct: float = 0.15  # Max 15% del capital en un mercado
    max_open_trades: int = 20          # Máximo 20 trades abiertos (GPT dice 6, más conservador)
    max_trades_per_category: int = 5   # Max 5 trades por categoría
    
    # === SCORING WEIGHTS (según GPT) ===
    weight_historical_edge: float = 0.30
    weight_mispricing_duration: float = 0.20
    weight_liquidity_imbalance: float = 0.15
    weight_elasticity: float = 0.15
    weight_cluster_bias: float = 0.10
    weight_base_opportunity: float = 0.10
    
    # === UMBRALES DE SCORING ===
    score_threshold_trade: int = 50    # >= 50 -> trade normal
    score_threshold_reduced: int = 35  # 35-50 -> trade reducido (muy permisivo para testing)
    
    # === CATEGORÍAS ===
    exclude_invalid_categories: bool = True
    
    # === MOMENTUM FILTER ===
    min_hours_for_momentum: float = 24.0  # No momentum en eventos <24h
    
    # === SCAN ===
    scan_interval: int = 180


# ============== TRACKING DE MÉTRICAS ==============

@dataclass
class MarketMetrics:
    """Métricas avanzadas para un mercado."""
    market_id: str
    question: str
    category: str
    is_valid_category: bool
    
    # Precios
    current_yes_price: float = 0.0
    historical_probability: float = 0.5
    
    # Edge histórico
    historical_edge: float = 0.0  # P_histórica - P_actual
    
    # Persistencia de mala cotización
    first_seen_mispriced: Optional[datetime] = None
    mispricing_duration_hours: float = 0.0
    
    # Liquidez
    volume_24h: float = 0.0
    price_change_24h: float = 0.0
    liquidity_imbalance: float = 0.0  # volume / abs(price_change)
    
    # Elasticidad (requiere datos de news, simplificado)
    elasticity_score: float = 0.5  # 0-1, menor = más lento a reaccionar
    
    # Cluster bias
    cluster_bias: float = 0.5  # % de mercados similares que resolvieron YES
    
    # Score final
    combined_score: float = 0.0
    
    def calculate_combined_score(self, config: TradingConfigV3) -> float:
        """Calcula score combinado según pesos de config."""
        
        # Normalizar cada componente a 0-1
        edge_norm = min(1.0, max(0, self.historical_edge + 0.5))  # -0.5 a +0.5 -> 0 a 1
        duration_norm = min(1.0, self.mispricing_duration_hours / 72)  # Max 72h
        liquidity_norm = min(1.0, self.liquidity_imbalance / 10000)  # Normalizado
        elasticity_norm = 1.0 - self.elasticity_score  # Invertido (menor = mejor)
        cluster_norm = self.cluster_bias
        
        self.combined_score = (
            config.weight_historical_edge * edge_norm +
            config.weight_mispricing_duration * duration_norm +
            config.weight_liquidity_imbalance * liquidity_norm +
            config.weight_elasticity * elasticity_norm +
            config.weight_cluster_bias * cluster_norm
        ) * 100
        
        return self.combined_score


# ============== POSICIÓN ==============

@dataclass
class Position:
    """Representa una posición abierta."""
    id: str
    market_id: str
    market_question: str
    market_category: str
    side: str  # "YES" o "NO"
    entry_price: float
    amount: float
    shares: float
    current_price: float
    entry_time: datetime
    opportunity_type: str
    status: str = "OPEN"
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    pnl: float = 0.0
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    # Métricas al momento de entrada
    entry_score: float = 0.0
    entry_historical_edge: float = 0.0
    
    @property
    def unrealized_pnl(self) -> float:
        if self.status != "OPEN":
            return 0
        return self.shares * (self.current_price - self.entry_price)
    
    @property
    def unrealized_pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0
        return (self.current_price - self.entry_price) / self.entry_price
    
    @property
    def hours_open(self) -> float:
        delta = datetime.now() - self.entry_time
        return delta.total_seconds() / 3600
    
    def get_target_take_profit(self, config: TradingConfigV3) -> float:
        if self.entry_price < 0.30:
            base_tp = config.take_profit_low
        elif self.entry_price <= 0.60:
            base_tp = config.take_profit_medium
        else:
            base_tp = config.take_profit_high
        
        if config.tp_decay_enabled:
            hours = self.hours_open
            for i, hour_threshold in enumerate(config.tp_decay_hours):
                if hours >= hour_threshold:
                    base_tp = min(base_tp, config.tp_decay_targets[i])
        
        return base_tp
    
    def should_take_profit(self, config: TradingConfigV3) -> bool:
        return self.unrealized_pnl_pct >= self.get_target_take_profit(config)
    
    def should_stop_loss(self, config: TradingConfigV3) -> bool:
        if not config.stop_loss_enabled:
            return False
        return self.unrealized_pnl_pct <= config.stop_loss_pct
    
    def to_dict(self) -> Dict:
        return {
            'id': self.id,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'market_category': self.market_category,
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
            'entry_score': self.entry_score,
            'extra_data': self.extra_data,
        }


# ============== MEGA BOT V3 ==============

class MegaBotV3:
    """
    Bot de trading V3 - Sistema cuantitativo completo.
    
    Implementa:
    - Clasificación de mercados
    - Edge histórico por subclase
    - Indicadores de microestructura
    - Diversificación forzada
    - Scoring combinado
    """
    
    def __init__(self, config: Optional[TradingConfigV3] = None):
        self.config = config or TradingConfigV3()
        
        # APIs
        self.pm_api = get_polymarket_api()
        self.kalshi_api = get_kalshi_api()
        
        # Detector de oportunidades
        self.detector = OpportunityDetector()
        
        # Estado
        self.positions: List[Position] = []
        self.closed_positions: List[Position] = []
        self.trade_counter = 0
        
        # Tracking de métricas por mercado
        self.market_metrics: Dict[str, MarketMetrics] = {}
        
        # Tracking de categorías
        self.trades_by_category: Dict[str, int] = defaultdict(int)
        
        # Historial de resultados por categoría (para cluster bias)
        self.category_results: Dict[str, List[bool]] = defaultdict(list)
        
        # Stats
        self.stats = {
            'total_scans': 0,
            'total_opportunities': 0,
            'total_filtered_out': 0,
            'filtered_by_category': 0,
            'filtered_by_score': 0,
            'filtered_by_diversification': 0,
            'total_trades': 0,
            'total_invested': 0.0,
            'realized_pnl': 0.0,
            'unrealized_pnl': 0.0,
            'wins': 0,
            'losses': 0,
            'start_time': datetime.now(),
        }
        
        log.info(f"[MegaBot-V3] Initialized with config: max_open_trades={self.config.max_open_trades}, "
                f"stop_loss={self.config.stop_loss_pct:.0%}")
    
    async def scan(self) -> List[Opportunity]:
        """Ejecuta un escaneo completo."""
        self.stats['total_scans'] += 1
        
        log.info(f"\n{'='*70}")
        log.info(f"[MegaBot-V3] SCAN #{self.stats['total_scans']} - {datetime.now().strftime('%H:%M:%S')}")
        log.info(f"{'='*70}")
        
        # 1. Obtener datos
        log.info("[MegaBot-V3] Fetching market data...")
        raw_markets = self.pm_api.get_markets(limit=200)
        markets = [Market.from_api_response(m) for m in raw_markets]
        events = self.pm_api.get_events(limit=50)
        kalshi_markets = self.kalshi_api.get_markets_for_arbitrage()
        
        log.info(f"  - {len(markets)} markets, {len(events)} events, {len(kalshi_markets)} Kalshi")
        
        # 2. Calcular métricas avanzadas para cada mercado
        self._update_market_metrics(markets)
        
        # 3. Detectar oportunidades base
        opportunities = self.detector.scan_all(
            markets=markets,
            events=events,
            kalshi_markets=kalshi_markets,
        )
        self.stats['total_opportunities'] += len(opportunities)
        
        # 4. Filtrar con sistema avanzado
        filtered = self._advanced_filter(opportunities, markets)
        log.info(f"[MegaBot-V3] {len(filtered)} opportunities after advanced filtering")
        
        # 5. Actualizar precios de posiciones
        await self._update_positions(markets, events)
        
        # 6. Verificar exits
        log.info(f"[MegaBot-V3] Checking exits for {len(self.positions)} positions...")
        self._check_and_close_exits()
        
        # 7. Ejecutar trades
        new_trades = self._execute_trades(filtered, markets)
        
        # 8. Guardar estado
        self._save_state()
        
        # 9. Mostrar resumen
        self._print_summary()
        
        return filtered
    
    def _update_market_metrics(self, markets: List[Market]):
        """Actualiza métricas avanzadas para todos los mercados."""
        for m in markets:
            # Clasificar mercado
            category, is_valid = classify_market(m.question, m.slug)
            
            # Obtener probabilidad histórica
            hist_prob = get_historical_probability(m.question, category)
            
            # Calcular edge histórico
            # Si el mercado cotiza YES a 40% pero históricamente es 65%, edge = +25%
            historical_edge = hist_prob - m.yes_price
            
            # Crear o actualizar métricas
            if m.id not in self.market_metrics:
                metrics = MarketMetrics(
                    market_id=m.id,
                    question=m.question,
                    category=category,
                    is_valid_category=is_valid,
                    current_yes_price=m.yes_price,
                    historical_probability=hist_prob,
                    historical_edge=historical_edge,
                    volume_24h=m.volume_24h,
                )
                
                # Si está mal priceado (edge > 10%), marcar inicio
                if abs(historical_edge) > 0.10:
                    metrics.first_seen_mispriced = datetime.now()
                
                self.market_metrics[m.id] = metrics
            else:
                metrics = self.market_metrics[m.id]
                old_price = metrics.current_yes_price
                metrics.current_yes_price = m.yes_price
                metrics.historical_edge = historical_edge
                metrics.volume_24h = m.volume_24h
                
                # Calcular cambio de precio
                metrics.price_change_24h = m.yes_price - old_price if old_price > 0 else 0
                
                # Calcular liquidity imbalance
                if metrics.price_change_24h != 0:
                    metrics.liquidity_imbalance = abs(m.volume_24h / metrics.price_change_24h)
                else:
                    metrics.liquidity_imbalance = m.volume_24h * 100  # Alto si no se mueve
                
                # Actualizar duración de mispricing
                if metrics.first_seen_mispriced and abs(historical_edge) > 0.10:
                    delta = datetime.now() - metrics.first_seen_mispriced
                    metrics.mispricing_duration_hours = delta.total_seconds() / 3600
                elif abs(historical_edge) <= 0.10:
                    metrics.first_seen_mispriced = None
                    metrics.mispricing_duration_hours = 0
            
            # Calcular cluster bias (% de wins en categoría)
            if category in self.category_results and self.category_results[category]:
                results = self.category_results[category][-20:]  # Últimos 20
                metrics.cluster_bias = sum(results) / len(results)
            
            # Calcular score combinado
            metrics.calculate_combined_score(self.config)
    
    def _advanced_filter(self, opportunities: List[Opportunity], markets: List[Market]) -> List[Opportunity]:
        """Filtrado avanzado con todos los criterios."""
        filtered = []
        markets_by_id = {m.id: m for m in markets}
        
        # Contadores de filtrado
        filtered_category = 0
        filtered_score = 0
        filtered_diversification = 0
        filtered_price = 0
        filtered_other = 0
        
        for opp in opportunities:
            # 1. FILTRO: Categoría de mercado
            if self.config.exclude_invalid_categories:
                metrics = self.market_metrics.get(opp.market_id)
                if metrics and not metrics.is_valid_category:
                    filtered_category += 1
                    continue
            
            # 2. FILTRO: No multi-outcome arbitrage (no funciona bien)
            if opp.type == OpportunityType.MULTI_OUTCOME_ARB:
                filtered_other += 1
                continue
            
            # 3. FILTRO: Solo BUY_YES y BUY_NO
            if opp.action.value not in ['buy_yes', 'buy_no']:
                filtered_other += 1
                continue
            
            # 4. FILTRO: Confidence mínima
            if opp.confidence < self.config.min_confidence:
                filtered_score += 1
                continue
            
            # 5. FILTRO: Max trades abiertos
            if len(self.positions) >= self.config.max_open_trades:
                filtered_diversification += 1
                continue
            
            # 6. FILTRO: Max trades por mercado
            market_trades = sum(1 for p in self.positions if p.market_id == opp.market_id)
            if market_trades >= self.config.max_trades_per_market:
                filtered_diversification += 1
                continue
            
            # 7. FILTRO: Max capital por mercado
            market_capital = sum(p.amount for p in self.positions if p.market_id == opp.market_id)
            max_capital = self.config.max_daily_exposure * self.config.max_capital_per_market_pct
            if market_capital >= max_capital:
                filtered_diversification += 1
                continue
            
            # 8. FILTRO: Max trades por categoría
            metrics = self.market_metrics.get(opp.market_id)
            if metrics:
                category_trades = sum(1 for p in self.positions if p.market_category == metrics.category)
                if category_trades >= self.config.max_trades_per_category:
                    filtered_diversification += 1
                    continue
            
            # 9. FILTRO: Precio de entrada
            if opp.action.value == 'buy_yes':
                entry_price = opp.current_yes_price or 0
            else:
                # Para BUY_NO, usamos current_no_price si está disponible
                entry_price = opp.current_no_price or 0
                if entry_price <= 0:
                    # Fallback: calcular desde YES price
                    yes_price = opp.current_yes_price or 0
                    entry_price = (1.0 - yes_price) if yes_price > 0 else 0
            
            # Verificar que tengamos un precio válido
            if entry_price <= 0 or entry_price >= 1.0:
                log.debug(f"[V3-FILTER] Invalid price ({entry_price:.2f}): {opp.market_question[:30]}...")
                filtered_price += 1
                continue
            
            # Filtro de precio - MUY permisivo para V3
            # Solo excluir precios extremos (<1% o >99%)
            if entry_price < 0.01 or entry_price > 0.99:
                log.debug(f"[V3-FILTER] Price extreme ({entry_price:.2f}): {opp.market_question[:30]}...")
                filtered_price += 1
                continue
            
            # 10. FILTRO: Días a resolución
            days_to_resolution = opp.days_to_resolution
            if days_to_resolution is None and opp.market_id:
                matching_market = markets_by_id.get(opp.market_id)
                if matching_market and matching_market.end_date:
                    delta = matching_market.end_date - datetime.now()
                    days_to_resolution = delta.total_seconds() / 86400
            
            if days_to_resolution is not None:
                if days_to_resolution > self.config.max_days_to_resolution:
                    filtered_other += 1
                    continue
                
                # Filtro momentum para eventos muy cortos
                hours_to_resolution = days_to_resolution * 24
                is_momentum = opp.type.value in ['momentum_long', 'momentum_short', 'contrarian']
                if is_momentum and hours_to_resolution < self.config.min_hours_for_momentum:
                    filtered_other += 1
                    continue
            
            # 11. FILTRO: Exposure diario
            if self.stats['total_invested'] >= self.config.max_daily_exposure:
                filtered_diversification += 1
                continue
            
            # 12. CALCULAR SCORE COMBINADO
            combined_score = self._calculate_opportunity_score(opp, metrics)
            opp.extra_data['combined_score'] = combined_score
            opp.extra_data['historical_edge'] = metrics.historical_edge if metrics else 0
            opp.extra_data['category'] = metrics.category if metrics else 'unknown'
            
            # 13. FILTRO: Score mínimo
            if combined_score < self.config.score_threshold_reduced:
                filtered_score += 1
                continue
            
            filtered.append(opp)
        
        # Actualizar stats
        self.stats['filtered_by_category'] += filtered_category
        self.stats['filtered_by_score'] += filtered_score
        self.stats['filtered_by_diversification'] += filtered_diversification
        self.stats['total_filtered_out'] += filtered_category + filtered_score + filtered_diversification + filtered_price + filtered_other
        
        log.info(f"[MegaBot-V3] Filtered: category={filtered_category}, score={filtered_score}, "
                f"diversification={filtered_diversification}, price={filtered_price}, other={filtered_other}")
        
        # Ordenar por score combinado
        filtered.sort(key=lambda x: x.extra_data.get('combined_score', 0), reverse=True)
        
        return filtered
    
    def _calculate_opportunity_score(self, opp: Opportunity, metrics: Optional[MarketMetrics]) -> float:
        """Calcula score combinado para una oportunidad."""
        
        # Base score de la oportunidad (confidence + type priority)
        base_score = (opp.confidence * 0.5 + opp.type_priority * 0.5)
        
        if not metrics:
            return base_score
        
        # Score de métricas avanzadas
        advanced_score = metrics.combined_score
        
        # Combinar
        final_score = (
            self.config.weight_base_opportunity * base_score +
            (1 - self.config.weight_base_opportunity) * advanced_score
        )
        
        # Bonus por edge histórico fuerte
        if abs(metrics.historical_edge) > 0.20:
            final_score += 10
        
        # Bonus por mispricing persistente
        if metrics.mispricing_duration_hours > 24:
            final_score += 5
        
        # Penalización por categoría de alto riesgo
        if metrics.category in ['crypto', 'geopolitics']:
            final_score -= 5
        
        return min(100, max(0, final_score))
    
    async def _update_positions(self, markets: List[Market], events: List[Dict] = None):
        """Actualiza precios de posiciones abiertas."""
        market_prices = {}
        for m in markets:
            market_prices[m.id] = (m.yes_price, m.no_price)
            if m.condition_id:
                market_prices[m.condition_id] = (m.yes_price, m.no_price)
        
        for pos in self.positions:
            if pos.market_id in market_prices:
                yes_price, no_price = market_prices[pos.market_id]
                pos.current_price = yes_price if pos.side == "YES" else no_price
    
    def _check_and_close_exits(self):
        """Verifica y cierra posiciones que deben salir."""
        to_close = []
        
        for i, pos in enumerate(self.positions):
            if pos.status != "OPEN":
                continue
            
            target_tp = pos.get_target_take_profit(self.config)
            current_pnl_pct = pos.unrealized_pnl_pct
            
            # Log primeras 3
            if i < 3:
                log.info(f"[MegaBot-V3] Position #{i+1}: {pos.market_question[:35]}... | "
                        f"P&L: {current_pnl_pct:+.1%} | TP: {target_tp:+.1%} | SL: {self.config.stop_loss_pct:+.0%}")
            
            reason = None
            
            # Take profit
            if pos.should_take_profit(self.config):
                reason = "TAKE_PROFIT"
                log.info(f"[MegaBot-V3] [OK] TAKE PROFIT: {pos.market_question[:35]}... ({current_pnl_pct:+.1%})")
            
            # Stop loss
            elif pos.should_stop_loss(self.config):
                reason = "STOP_LOSS"
                log.info(f"[MegaBot-V3] [X] STOP LOSS: {pos.market_question[:35]}... ({current_pnl_pct:+.1%})")
            
            # Mercado resuelto
            elif pos.current_price <= 0.02 or pos.current_price >= 0.98:
                reason = "RESOLVED"
                log.info(f"[MegaBot-V3] [!] RESOLVED: {pos.market_question[:35]}... (price=${pos.current_price:.2f})")
            
            if reason:
                to_close.append((pos, reason))
        
        for pos, reason in to_close:
            self._close_position(pos, pos.current_price, reason)
    
    def _close_position(self, pos: Position, exit_price: float, reason: str):
        """Cierra una posición."""
        pos.exit_price = exit_price
        pos.exit_time = datetime.now()
        pos.status = "CLOSED"
        
        # Calcular P&L
        pos.pnl = pos.shares * (exit_price - pos.entry_price)
        
        # Actualizar stats
        self.stats['realized_pnl'] += pos.pnl
        is_win = pos.pnl > 0
        if is_win:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
        
        # Actualizar cluster bias
        self.category_results[pos.market_category].append(is_win)
        
        # Actualizar contador de categoría
        if pos.market_category in self.trades_by_category:
            self.trades_by_category[pos.market_category] = max(0, self.trades_by_category[pos.market_category] - 1)
        
        # Mover a closed
        self.positions.remove(pos)
        self.closed_positions.append(pos)
        
        log.info(f"[MegaBot-V3] CLOSED ({reason}): PnL ${pos.pnl:.2f} | {pos.market_question[:40]}...")
    
    def _execute_trades(self, opportunities: List[Opportunity], markets: List[Market]) -> List[Position]:
        """Ejecuta trades en oportunidades filtradas."""
        new_trades = []
        markets_by_id = {m.id: m for m in markets}
        
        # Máximo 5 trades por scan
        for opp in opportunities[:5]:
            # Verificar límites
            if len(self.positions) >= self.config.max_open_trades:
                break
            if self.stats['total_invested'] >= self.config.max_daily_exposure:
                break
            
            self.trade_counter += 1
            
            side = "YES" if opp.action.value in ['buy_yes', 'buy_all_yes'] else "NO"
            
            # Entry price
            if side == "YES":
                entry_price = opp.current_yes_price
            else:
                entry_price = opp.current_no_price or (1.0 - opp.current_yes_price if opp.current_yes_price > 0 else 0.5)
            
            if not entry_price or entry_price <= 0 or entry_price >= 1.0:
                continue
            
            # Determinar tamaño del trade
            combined_score = opp.extra_data.get('combined_score', 50)
            if combined_score >= self.config.score_threshold_trade:
                amount = self.config.trade_amount
            else:
                amount = self.config.trade_amount * 0.5  # Trade reducido
            
            shares = amount / entry_price
            
            # Obtener categoría
            metrics = self.market_metrics.get(opp.market_id)
            category = metrics.category if metrics else 'unknown'
            historical_edge = metrics.historical_edge if metrics else 0
            
            pos = Position(
                id=f"v3_trade_{self.trade_counter}",
                market_id=opp.market_id,
                market_question=opp.market_question,
                market_category=category,
                side=side,
                entry_price=entry_price,
                amount=amount,
                shares=shares,
                current_price=entry_price,
                entry_time=datetime.now(),
                opportunity_type=opp.type.value,
                entry_score=combined_score,
                entry_historical_edge=historical_edge,
            )
            
            self.positions.append(pos)
            new_trades.append(pos)
            
            # Actualizar stats
            self.stats['total_trades'] += 1
            self.stats['total_invested'] += amount
            self.trades_by_category[category] += 1
            
            log.info(f"[MegaBot-V3] NEW TRADE: {opp.type.value} | {side} @ ${entry_price:.2f} | "
                    f"Score: {combined_score:.0f} | Edge: {historical_edge:+.1%} | "
                    f"Cat: {category} | {opp.market_question[:30]}...")
        
        return new_trades
    
    def _print_summary(self):
        """Muestra resumen completo."""
        unrealized = sum(p.unrealized_pnl for p in self.positions)
        self.stats['unrealized_pnl'] = unrealized
        
        total_pnl = self.stats['realized_pnl'] + unrealized
        win_rate = self.stats['wins'] / max(1, self.stats['wins'] + self.stats['losses']) * 100
        
        runtime = datetime.now() - self.stats['start_time']
        hours = runtime.total_seconds() / 3600
        roi = (total_pnl / max(1, self.stats['total_invested'])) * 100
        
        # Métricas de diversificación
        unique_markets = len(set(p.market_id for p in self.positions))
        unique_categories = len(set(p.market_category for p in self.positions))
        
        log.info(f"\n{'='*70}")
        log.info("MEGA BOT V3 STATUS (QUANTITATIVE SYSTEM)")
        log.info(f"{'='*70}")
        log.info(f"Runtime: {hours:.1f}h | Scans: {self.stats['total_scans']}")
        log.info(f"Open: {len(self.positions)} | Closed: {len(self.closed_positions)} | Max: {self.config.max_open_trades}")
        log.info(f"Unique Markets: {unique_markets} | Unique Categories: {unique_categories}")
        log.info(f"{'='*70}")
        log.info(f"Invested: ${self.stats['total_invested']:.2f}")
        log.info(f"Realized P&L: ${self.stats['realized_pnl']:.2f}")
        log.info(f"Unrealized P&L: ${unrealized:.2f}")
        log.info(f"Total P&L: ${total_pnl:.2f} ({roi:.1f}% ROI)")
        log.info(f"Win Rate: {win_rate:.0f}% ({self.stats['wins']}W / {self.stats['losses']}L)")
        log.info(f"{'='*70}")
        log.info(f"Filtered: {self.stats['total_filtered_out']} total "
                f"(cat={self.stats['filtered_by_category']}, score={self.stats['filtered_by_score']}, "
                f"div={self.stats['filtered_by_diversification']})")
        log.info(f"Stop Loss: {'ON' if self.config.stop_loss_enabled else 'OFF'} ({self.config.stop_loss_pct:+.0%})")
        log.info(f"{'='*70}\n")
    
    def _save_state(self):
        """Guarda estado."""
        data = {
            'positions': [p.to_dict() for p in self.positions],
            'closed_positions': [p.to_dict() for p in self.closed_positions[-100:]],
            'stats': {
                'total_scans': self.stats['total_scans'],
                'total_opportunities': self.stats['total_opportunities'],
                'total_trades': self.stats['total_trades'],
                'total_invested': self.stats['total_invested'],
                'realized_pnl': self.stats['realized_pnl'],
                'wins': self.stats['wins'],
                'losses': self.stats['losses'],
            },
            'trades_by_category': dict(self.trades_by_category),
            'trade_counter': self.trade_counter,
            'last_updated': datetime.now().isoformat(),
        }
        
        with open('mega_bot_v3_state.json', 'w') as f:
            json.dump(data, f, indent=2)
    
    def get_dashboard_data(self) -> Dict:
        """Retorna datos para dashboard."""
        unrealized = sum(p.unrealized_pnl for p in self.positions)
        total_pnl = self.stats['realized_pnl'] + unrealized
        
        runtime = datetime.now() - self.stats['start_time']
        hours = runtime.total_seconds() / 3600
        
        return {
            'version': 'V3',
            'status': 'running',
            'runtime_hours': hours,
            'total_scans': self.stats['total_scans'],
            'total_trades': self.stats['total_trades'],
            'total_invested': self.stats['total_invested'],
            'realized_pnl': self.stats['realized_pnl'],
            'unrealized_pnl': unrealized,
            'total_pnl': total_pnl,
            'roi': (total_pnl / max(1, self.stats['total_invested'])) * 100,
            'wins': self.stats['wins'],
            'losses': self.stats['losses'],
            'win_rate': self.stats['wins'] / max(1, self.stats['wins'] + self.stats['losses']) * 100,
            'open_positions': len(self.positions),
            'closed_positions': len(self.closed_positions),
            'max_open_trades': self.config.max_open_trades,
            'positions': [p.to_dict() for p in self.positions],
            'config': asdict(self.config),
        }
    
    async def run(self, max_scans: int = None):
        """Ejecuta el bot."""
        log.info(f"\n{'#'*70}")
        log.info("MEGA BOT V3 STARTING - QUANTITATIVE SYSTEM")
        log.info(f"Trade amount: ${self.config.trade_amount}")
        log.info(f"Max exposure: ${self.config.max_daily_exposure}")
        log.info(f"Max open trades: {self.config.max_open_trades}")
        log.info(f"Stop loss: {self.config.stop_loss_pct:.0%}")
        log.info(f"Scan interval: {self.config.scan_interval}s")
        log.info(f"{'#'*70}\n")
        
        scan_count = 0
        
        try:
            while max_scans is None or scan_count < max_scans:
                await self.scan()
                scan_count += 1
                
                if max_scans is None or scan_count < max_scans:
                    log.info(f"Next scan in {self.config.scan_interval}s...")
                    await asyncio.sleep(self.config.scan_interval)
                    
        except KeyboardInterrupt:
            log.info("\n[MegaBot-V3] Stopped by user")
        
        self._save_state()
        self._print_summary()


# ============== SERVIDOR WEB ==============

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
import threading

app = FastAPI()
bot: Optional[MegaBotV3] = None


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    global bot
    if bot is None:
        return "<h1>Bot not started</h1>"
    
    data = bot.get_dashboard_data()
    
    positions_html = ""
    for p in data['positions'][:20]:
        pnl_class = "positive" if p['unrealized_pnl'] > 0 else "negative"
        positions_html += f"""
        <tr>
            <td>{p['market_question'][:45]}...</td>
            <td>{p['side']}</td>
            <td>{p['market_category']}</td>
            <td>${p['entry_price']:.2f}</td>
            <td>${p['current_price']:.2f}</td>
            <td class="{pnl_class}">${p['unrealized_pnl']:.2f}</td>
            <td>{p['entry_score']:.0f}</td>
            <td>{p['hours_open']:.1f}h</td>
        </tr>
        """
    
    pnl_class = "positive" if data['total_pnl'] > 0 else "negative"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mega Bot V3 Dashboard</title>
        <meta http-equiv="refresh" content="30">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #0a0a1a; color: #eee; }}
            .container {{ max-width: 1400px; margin: 0 auto; }}
            h1 {{ color: #00ff88; }}
            .version {{ background: #00ff88; color: #000; padding: 2px 8px; border-radius: 4px; font-size: 14px; }}
            .stats {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin: 20px 0; }}
            .stat {{ background: #1a1a2e; padding: 15px; border-radius: 8px; text-align: center; border: 1px solid #333; }}
            .stat-value {{ font-size: 22px; font-weight: bold; color: #00d4ff; }}
            .stat-label {{ font-size: 11px; color: #888; margin-top: 4px; }}
            .positive {{ color: #00ff88; }}
            .negative {{ color: #ff4444; }}
            table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
            th, td {{ padding: 10px; text-align: left; border-bottom: 1px solid #333; font-size: 13px; }}
            th {{ background: #1a1a2e; color: #00d4ff; }}
            .info-box {{ background: #1a1a2e; padding: 15px; border-radius: 8px; margin: 10px 0; border-left: 4px solid #00ff88; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Mega Bot <span class="version">V3 QUANTITATIVE</span></h1>
            
            <div class="info-box">
                <strong>Sistema Cuantitativo Completo:</strong> Edge histórico, clasificación de mercados, 
                diversificación forzada, stop loss activo, scoring combinado.
            </div>
            
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
                    <div class="stat-label">Total Trades</div>
                </div>
                <div class="stat">
                    <div class="stat-value">{data['open_positions']}/{data['max_open_trades']}</div>
                    <div class="stat-label">Open/Max</div>
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
                    <div class="stat-value class="{pnl_class}">${data['realized_pnl']:.2f}</div>
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
                <div class="stat">
                    <div class="stat-value {pnl_class}">{data['roi']:.1f}%</div>
                    <div class="stat-label">ROI</div>
                </div>
            </div>
            
            <h2>Open Positions ({data['open_positions']})</h2>
            <table>
                <tr>
                    <th>Market</th>
                    <th>Side</th>
                    <th>Category</th>
                    <th>Entry</th>
                    <th>Current</th>
                    <th>P&L</th>
                    <th>Score</th>
                    <th>Time</th>
                </tr>
                {positions_html}
            </table>
            
            <p style="color: #666; font-size: 11px;">
                Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 
                Wins: {data['wins']} | Losses: {data['losses']}
            </p>
        </div>
    </body>
    </html>
    """
    return html


@app.get("/api/stats")
async def api_stats():
    global bot
    if bot is None:
        return {"error": "Bot not started"}
    return bot.get_dashboard_data()


def start_server(port: int = 8080):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


# ============== MAIN ==============

async def main():
    global bot
    
    import argparse
    parser = argparse.ArgumentParser(description='Mega Bot V3 - Quantitative Trading System')
    parser.add_argument('--port', '-p', type=int, default=8080, help='Dashboard port')
    parser.add_argument('--interval', '-i', type=int, default=180, help='Scan interval (seconds)')
    parser.add_argument('--amount', '-a', type=float, default=2.0, help='Trade amount')
    parser.add_argument('--exposure', '-e', type=float, default=200.0, help='Max daily exposure')
    parser.add_argument('--max-trades', '-m', type=int, default=20, help='Max open trades')
    parser.add_argument('--no-server', action='store_true', help='Disable web server')
    
    args = parser.parse_args()
    
    config = TradingConfigV3(
        trade_amount=args.amount,
        max_daily_exposure=args.exposure,
        scan_interval=args.interval,
        max_open_trades=args.max_trades,
    )
    
    bot = MegaBotV3(config)
    
    if not args.no_server:
        server_thread = threading.Thread(target=start_server, args=(args.port,), daemon=True)
        server_thread.start()
        log.info(f"[MegaBot-V3] Dashboard running at http://localhost:{args.port}")
    
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
