"""
Opportunity model - Representa una oportunidad de trading detectada.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
from datetime import datetime


class OpportunityType(Enum):
    """Tipos de oportunidades detectables."""
    
    # Arbitraje (Prioridad 1 - Ganancia garantizada)
    MULTI_OUTCOME_ARB = "multi_outcome_arb"          # Suma outcomes != 100%
    CROSS_PLATFORM_ARB = "cross_platform_arb"        # PM vs Kalshi spread
    YES_NO_MISMATCH = "yes_no_mismatch"              # YES + NO != 100%
    
    # Time Decay (Prioridad 2)
    TIME_DECAY = "time_decay"                        # Deadline approaching
    IMPROBABLE_EXPIRING = "improbable_expiring"      # Evento improbable expirando
    
    # Resolution Predictable (Prioridad 3)
    ALREADY_RESOLVED = "already_resolved"            # Evento ya ocurrió
    NEAR_CERTAIN = "near_certain"                    # Outcome casi seguro
    
    # Whale / Smart Money (Prioridad 4)
    WHALE_ACTIVITY = "whale_activity"                # Whales comprando
    ABNORMAL_VOLUME = "abnormal_volume"              # Volumen anómalo sin noticias
    
    # Momentum (Prioridad 5)
    MOMENTUM_SHORT = "momentum_short"                # Momentum 1h
    MOMENTUM_LONG = "momentum_long"                  # Momentum 24h
    CONTRARIAN = "contrarian"                        # Mean reversion
    
    # Mispricing (Prioridad 6)
    NEW_MARKET_MISPRICING = "new_market_mispricing"  # Mercado nuevo
    LOW_LIQUIDITY_MISPRICING = "low_liquidity_mispricing"  # Baja liquidez
    
    # News / Event-driven (Prioridad 7)
    NEWS_LAG = "news_lag"                            # Noticia no reflejada
    PRE_EVENT = "pre_event"                          # Antes de evento conocido
    
    # Correlation (Prioridad 8)
    CORRELATION_DIVERGENCE = "correlation_divergence"  # Mercados correlacionados divergen


class Action(Enum):
    """Acciones posibles a tomar."""
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"
    BUY_BOTH = "buy_both"          # Para arbitraje YES/NO
    BUY_ALL_YES = "buy_all_yes"    # Para multi-outcome
    BUY_ALL_NO = "buy_all_no"      # Para multi-outcome
    SELL_YES = "sell_yes"
    SELL_NO = "sell_no"
    HOLD = "hold"                   # Esperar


# Prioridad base por tipo (mayor = mejor)
TYPE_PRIORITY: Dict[OpportunityType, int] = {
    # Arbitraje = máxima prioridad (ganancia garantizada)
    OpportunityType.MULTI_OUTCOME_ARB: 100,
    OpportunityType.YES_NO_MISMATCH: 98,
    OpportunityType.CROSS_PLATFORM_ARB: 95,
    
    # Resolution predecible
    OpportunityType.ALREADY_RESOLVED: 90,
    OpportunityType.NEAR_CERTAIN: 85,
    
    # Time decay
    OpportunityType.TIME_DECAY: 80,
    OpportunityType.IMPROBABLE_EXPIRING: 75,
    
    # Whale/Smart money
    OpportunityType.WHALE_ACTIVITY: 70,
    OpportunityType.ABNORMAL_VOLUME: 65,
    
    # Momentum
    OpportunityType.MOMENTUM_LONG: 60,
    OpportunityType.MOMENTUM_SHORT: 55,
    OpportunityType.CONTRARIAN: 50,
    
    # Mispricing
    OpportunityType.NEW_MARKET_MISPRICING: 45,
    OpportunityType.LOW_LIQUIDITY_MISPRICING: 40,
    
    # News/Event
    OpportunityType.NEWS_LAG: 35,
    OpportunityType.PRE_EVENT: 30,
    
    # Correlation
    OpportunityType.CORRELATION_DIVERGENCE: 25,
}


@dataclass
class Opportunity:
    """Representa una oportunidad de trading detectada."""
    
    # Identificación
    type: OpportunityType
    action: Action
    detector_name: str = ""
    
    # Métricas principales
    expected_profit: float = 0.0    # Porcentaje esperado de profit
    confidence: int = 50            # 0-100, confianza en la oportunidad
    
    # Información del mercado
    market_id: str = ""
    market_question: str = ""
    market_slug: str = ""
    current_yes_price: float = 0.0
    current_no_price: float = 0.0
    
    # Para multi-market opportunities (arbitraje multi-outcome)
    markets: List[Dict[str, Any]] = field(default_factory=list)
    
    # Timing
    detected_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    days_to_resolution: Optional[float] = None
    
    # Metadata adicional
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    # Scoring (calculado)
    rank_score: float = 0.0
    
    @property
    def type_priority(self) -> int:
        """Prioridad basada en el tipo de oportunidad."""
        return TYPE_PRIORITY.get(self.type, 0)
    
    @property
    def expected_value(self) -> float:
        """
        Calcula el Expected Value.
        EV = P(win) * profit - P(loss) * loss
        
        Asumiendo pérdida máxima = 100% del monto apostado
        """
        prob_win = self.confidence / 100
        prob_loss = 1 - prob_win
        
        # Profit si ganamos, asumimos pérdida total si perdemos
        ev = (prob_win * self.expected_profit) - (prob_loss * 100)
        return ev
    
    @property
    def risk_reward_ratio(self) -> float:
        """
        Ratio de riesgo/recompensa.
        Cuanto mayor, mejor (más reward por unidad de riesgo).
        """
        if self.confidence == 100:
            return float('inf')
        
        risk = 100 - self.confidence  # Probabilidad de perder
        reward = self.expected_profit
        
        if risk == 0:
            return float('inf')
        
        return reward / risk
    
    @property
    def is_arbitrage(self) -> bool:
        """¿Es una oportunidad de arbitraje (ganancia garantizada)?"""
        return self.type in [
            OpportunityType.MULTI_OUTCOME_ARB,
            OpportunityType.CROSS_PLATFORM_ARB,
            OpportunityType.YES_NO_MISMATCH,
        ]
    
    @property
    def kelly_fraction(self) -> float:
        """
        Calcula la fracción de Kelly óptima para esta oportunidad.
        f* = (bp - q) / b
        donde:
        - b = odds (expected_profit / 100)
        - p = probabilidad de ganar (confidence / 100)
        - q = probabilidad de perder (1 - p)
        """
        if self.expected_profit <= 0:
            return 0.0
        
        b = self.expected_profit / 100  # Convertir % a decimal
        p = self.confidence / 100
        q = 1 - p
        
        kelly = (b * p - q) / b
        
        # Limitar entre 0 y 0.25 (nunca apostar más del 25%)
        return max(0, min(0.25, kelly))
    
    def calculate_rank_score(self) -> float:
        """
        Calcula el score compuesto para ranking.
        
        Score = (type_priority * 0.3) + (EV_norm * 0.4) + (confidence_norm * 0.3)
        """
        # Normalizar prioridad de tipo (0-1)
        type_norm = self.type_priority / 100
        
        # Normalizar EV (-100 a +100 -> 0 a 1)
        ev = self.expected_value
        ev_norm = (ev + 100) / 200
        ev_norm = max(0, min(1, ev_norm))
        
        # Normalizar confianza (0-1)
        conf_norm = self.confidence / 100
        
        # Calcular score compuesto
        score = (
            type_norm * 0.30 +
            ev_norm * 0.40 +
            conf_norm * 0.30
        )
        
        # Bonus para arbitraje (siempre priorizar)
        if self.is_arbitrage:
            score += 0.2
        
        self.rank_score = min(1.0, score) * 100
        return self.rank_score
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario."""
        return {
            'type': self.type.value,
            'action': self.action.value,
            'expected_profit': self.expected_profit,
            'confidence': self.confidence,
            'expected_value': self.expected_value,
            'rank_score': self.rank_score,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'current_yes_price': self.current_yes_price,
            'detected_at': self.detected_at.isoformat(),
            'detector_name': self.detector_name,
            'extra_data': self.extra_data,
        }
    
    def __repr__(self) -> str:
        return (
            f"Opportunity({self.type.value}, {self.action.value}, "
            f"profit={self.expected_profit:.1f}%, conf={self.confidence}%, "
            f"EV={self.expected_value:.1f})"
        )
    
    def __str__(self) -> str:
        return (
            f"[{self.type.value.upper()}] {self.market_question[:50]}... | "
            f"Action: {self.action.value} | "
            f"Profit: {self.expected_profit:.1f}% | "
            f"Conf: {self.confidence}%"
        )
