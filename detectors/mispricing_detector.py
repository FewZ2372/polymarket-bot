"""
MispricingDetector - Detecta mercados mal priceados.

Tipos:
1. New Market Mispricing: Mercados recién creados con precios no estabilizados
2. Low Liquidity Mispricing: Mercados con poca liquidez y precios ineficientes
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('mispricing')
class MispricingDetector(BaseDetector):
    """Detecta mercados mal priceados."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.new_market_hours = self.config.get('new_market_hours', 24)  # <24h = nuevo
        self.low_liquidity_threshold = self.config.get('low_liquidity_threshold', 10000)  # <$10K
        self.min_mispricing = self.config.get('min_mispricing', 0.10)  # 10%
    
    def detect(
        self, 
        markets: List[Market],
        similar_markets: Optional[Dict[str, List[Market]]] = None,
        **kwargs
    ) -> List[Opportunity]:
        """Detecta oportunidades de mispricing."""
        opportunities = []
        
        for market in markets:
            # 1. New market mispricing
            new_opp = self._detect_new_market_mispricing(market)
            if new_opp:
                opportunities.append(new_opp)
            
            # 2. Low liquidity mispricing
            if similar_markets:
                low_liq_opp = self._detect_low_liquidity_mispricing(
                    market, 
                    similar_markets.get(market.category.value, [])
                )
                if low_liq_opp:
                    opportunities.append(low_liq_opp)
        
        return opportunities
    
    def _detect_new_market_mispricing(self, market: Market) -> Optional[Opportunity]:
        """
        Detecta mispricing en mercados nuevos.
        
        Mercados recién creados suelen tener precios menos eficientes
        porque no han sido arbitraged.
        """
        if not market.created_at:
            return None
        
        hours_since_creation = (datetime.now() - market.created_at).total_seconds() / 3600
        
        # Solo mercados de menos de 24h
        if hours_since_creation > self.new_market_hours:
            return None
        
        # Mercados con alto volumen ya están bien priceados
        if market.volume_24h > 50000:
            return None
        
        # Analizar si el precio tiene sentido
        fair_value = self._estimate_fair_value(market)
        if fair_value is None:
            return None
        
        mispricing = fair_value - market.yes_price
        
        if abs(mispricing) < self.min_mispricing:
            return None
        
        if mispricing > 0:
            # Fair value > current = comprar YES
            action = Action.BUY_YES
        else:
            # Fair value < current = comprar NO
            action = Action.BUY_NO
            mispricing = -mispricing
        
        opp = Opportunity(
            type=OpportunityType.NEW_MARKET_MISPRICING,
            action=action,
            expected_profit=mispricing * 100,
            confidence=60,  # Menor confianza porque es estimación
            market_id=market.id,
            market_question=market.question,
            market_slug=market.slug,
            current_yes_price=market.yes_price,
            extra_data={
                'hours_since_creation': hours_since_creation,
                'fair_value_estimate': fair_value,
                'mispricing': mispricing,
                'volume_24h': market.volume_24h,
                'strategy': 'New market arbitrage, price not yet efficient'
            }
        )
        
        self.log_opportunity(opp)
        return opp
    
    def _detect_low_liquidity_mispricing(
        self, 
        market: Market,
        similar_markets: List[Market]
    ) -> Optional[Opportunity]:
        """
        Detecta mispricing en mercados de baja liquidez comparando con similares.
        """
        # Solo mercados de baja liquidez
        if market.volume_24h > self.low_liquidity_threshold:
            return None
        
        # Pero con algo de actividad (no muertos)
        if market.volume_24h < 100:
            return None
        
        if not similar_markets:
            return None
        
        # Calcular precio promedio de mercados similares
        similar_prices = [m.yes_price for m in similar_markets if m.id != market.id]
        
        if len(similar_prices) < 2:
            return None
        
        avg_price = sum(similar_prices) / len(similar_prices)
        mispricing = avg_price - market.yes_price
        
        if abs(mispricing) < self.min_mispricing:
            return None
        
        if mispricing > 0:
            action = Action.BUY_YES
        else:
            action = Action.BUY_NO
            mispricing = -mispricing
        
        opp = Opportunity(
            type=OpportunityType.LOW_LIQUIDITY_MISPRICING,
            action=action,
            expected_profit=mispricing * 100,
            confidence=55,  # Menor confianza por baja liquidez
            market_id=market.id,
            market_question=market.question,
            market_slug=market.slug,
            current_yes_price=market.yes_price,
            extra_data={
                'similar_market_count': len(similar_prices),
                'avg_similar_price': avg_price,
                'mispricing': mispricing,
                'volume_24h': market.volume_24h,
                'warning': 'Low liquidity - may be hard to exit'
            }
        )
        
        self.log_opportunity(opp)
        return opp
    
    def _estimate_fair_value(self, market: Market) -> Optional[float]:
        """
        Estima el fair value de un mercado.
        
        Método simple basado en:
        - Categoría (política tiende a 50%, deportes más extremo)
        - Keywords (nombres conocidos tienen más certeza)
        """
        question_lower = market.question.lower()
        
        # Patrones con probabilidades conocidas
        patterns = [
            (r'super\s*bowl', 0.95, 'certain'),
            (r'fed.*rate.*cut', 0.65, 'likely'),
            (r'fed.*rate.*hike', 0.30, 'unlikely'),
            (r'shutdown', 0.25, 'unlikely'),
            (r'alien', 0.02, 'very_unlikely'),
        ]
        
        import re
        for pattern, prob, _ in patterns:
            if re.search(pattern, question_lower):
                return prob
        
        # Default: asumir que mercados nuevos tienden a 50%
        return 0.5
