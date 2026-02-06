"""
MomentumDetector - Detecta oportunidades basadas en momentum de precio.

Tipos:
1. Momentum Short: Momentum fuerte en 1h
2. Momentum Long: Momentum sostenido en 24h
3. Contrarian: Mean reversion después de caída irracional
"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('momentum')
class MomentumDetector(BaseDetector):
    """Detecta oportunidades de momentum."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.min_momentum_1h = self.config.get('min_momentum_1h', 0.05)  # 5%
        self.min_momentum_24h = self.config.get('min_momentum_24h', 0.15)  # 15%
        self.contrarian_threshold = self.config.get('contrarian_threshold', -0.10)  # -10%
    
    def detect(
        self, 
        markets: List[Market],
        news_data: Optional[List[Dict]] = None,
        **kwargs
    ) -> List[Opportunity]:
        """Detecta oportunidades de momentum."""
        opportunities = []
        
        for market in markets:
            # 1. Momentum corto plazo (1h)
            short_opp = self._detect_short_momentum(market)
            if short_opp:
                opportunities.append(short_opp)
            
            # 2. Momentum largo plazo (24h)
            long_opp = self._detect_long_momentum(market)
            if long_opp:
                opportunities.append(long_opp)
            
            # 3. Contrarian (mean reversion)
            contrarian_opp = self._detect_contrarian(market, news_data)
            if contrarian_opp:
                opportunities.append(contrarian_opp)
        
        return opportunities
    
    def _detect_short_momentum(self, market: Market) -> Optional[Opportunity]:
        """Detecta momentum de corto plazo (1h)."""
        change_1h = market.price_change_1h
        
        if abs(change_1h) < self.min_momentum_1h:
            return None
        
        # Dirección del momentum
        if change_1h > 0:
            action = Action.BUY_YES
            direction = "UP"
        else:
            action = Action.BUY_NO
            direction = "DOWN"
        
        # Confianza basada en magnitud
        confidence = min(75, 55 + abs(change_1h) * 200)
        
        # Profit esperado: ~50% del momentum (esperamos que continúe parcialmente)
        expected_profit = abs(change_1h) * 50
        
        opp = Opportunity(
            type=OpportunityType.MOMENTUM_SHORT,
            action=action,
            expected_profit=expected_profit,
            confidence=int(confidence),
            market_id=market.id,
            market_question=market.question,
            market_slug=market.slug,
            current_yes_price=market.yes_price,
            extra_data={
                'change_1h': change_1h,
                'direction': direction,
                'strategy': f'Follow {direction} momentum, expect continuation'
            }
        )
        
        self.log_opportunity(opp)
        return opp
    
    def _detect_long_momentum(self, market: Market) -> Optional[Opportunity]:
        """Detecta momentum de largo plazo (24h)."""
        change_24h = market.price_change_24h
        
        if abs(change_24h) < self.min_momentum_24h:
            return None
        
        # Verificar que el momentum es consistente (1h y 24h misma dirección)
        if market.price_change_1h * change_24h < 0:
            return None  # Direcciones opuestas = momentum perdiendo fuerza
        
        if change_24h > 0:
            action = Action.BUY_YES
            direction = "UP"
        else:
            action = Action.BUY_NO
            direction = "DOWN"
        
        # Momentum largo plazo = más confiable
        confidence = min(80, 60 + abs(change_24h) * 100)
        expected_profit = abs(change_24h) * 30  # Más conservador para largo plazo
        
        opp = Opportunity(
            type=OpportunityType.MOMENTUM_LONG,
            action=action,
            expected_profit=expected_profit,
            confidence=int(confidence),
            market_id=market.id,
            market_question=market.question,
            market_slug=market.slug,
            current_yes_price=market.yes_price,
            extra_data={
                'change_24h': change_24h,
                'change_1h': market.price_change_1h,
                'direction': direction,
                'momentum_consistency': 'aligned' if market.price_change_1h * change_24h > 0 else 'diverging'
            }
        )
        
        self.log_opportunity(opp)
        return opp
    
    def _detect_contrarian(
        self, 
        market: Market,
        news_data: Optional[List[Dict]] = None
    ) -> Optional[Opportunity]:
        """
        Detecta oportunidades contrarian (comprar el dip).
        
        Solo cuando la caída parece irracional (sin noticias que la justifiquen).
        """
        change_1h = market.price_change_1h
        
        # Solo buscar caídas fuertes
        if change_1h > self.contrarian_threshold:
            return None
        
        # Verificar si hay noticias que justifiquen la caída
        has_bad_news = False
        if news_data:
            market_keywords = market.question.lower().split()[:3]
            for news in news_data:
                news_text = (news.get('title', '') + news.get('description', '')).lower()
                if any(kw in news_text for kw in market_keywords):
                    # Verificar si es noticia negativa
                    negative_words = ['fall', 'drop', 'crash', 'fail', 'reject', 'deny', 'bad']
                    if any(neg in news_text for neg in negative_words):
                        has_bad_news = True
                        break
        
        if has_bad_news:
            return None  # Caída justificada, no es contrarian play
        
        # Caída sin noticias = posible pánico irracional
        expected_rebound = abs(change_1h) * 0.5  # Esperamos 50% de rebote
        confidence = min(70, 50 + abs(change_1h) * 150)
        
        opp = Opportunity(
            type=OpportunityType.CONTRARIAN,
            action=Action.BUY_YES,  # Comprar el dip
            expected_profit=expected_rebound * 100,
            confidence=int(confidence),
            market_id=market.id,
            market_question=market.question,
            market_slug=market.slug,
            current_yes_price=market.yes_price,
            extra_data={
                'drop_1h': change_1h,
                'expected_rebound': expected_rebound,
                'has_news': False,
                'strategy': 'Buy the dip, expect mean reversion'
            }
        )
        
        self.log_opportunity(opp)
        return opp
