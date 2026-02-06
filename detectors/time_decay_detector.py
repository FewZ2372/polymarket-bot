"""
TimeDecayDetector - Detecta oportunidades de time decay (theta).

Tipos:
1. Deadline Approaching: Eventos con deadline cercano que no han ocurrido
2. Improbable Expiring: Eventos muy improbables que expiran pronto
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import re

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('time_decay')
class TimeDecayDetector(BaseDetector):
    """
    Detecta oportunidades de time decay.
    
    El time decay (theta) es el decaimiento del valor de una opción
    a medida que se acerca la fecha de expiración sin que el evento ocurra.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.max_days_deadline = self.config.get('max_days_deadline', 14)  # días
        self.min_daily_theta = self.config.get('min_daily_theta', 0.015)  # 1.5%/día
        self.max_yes_price_improbable = self.config.get('max_yes_price_improbable', 0.15)  # 15%
        
        # Palabras clave para detectar eventos "deadline"
        self.deadline_keywords = [
            'before', 'by', 'prior to', 'until', 'antes de',
            'by end of', 'by the end', 'within'
        ]
        
        # Eventos claramente improbables
        self.improbable_patterns = [
            r'alien', r'ufo', r'asteroid.*hit', r'apocalypse',
            r'world.*end', r'zombie', r'vampire', r'unicorn',
            r'moon.*explode', r'sun.*explode', r'teleport',
            r'time.*travel', r'immortal'
        ]
    
    def detect(
        self, 
        markets: List[Market], 
        **kwargs
    ) -> List[Opportunity]:
        """
        Detecta oportunidades de time decay.
        """
        opportunities = []
        
        # 1. Deadline approaching
        deadline_opps = self._detect_deadline_approaching(markets)
        opportunities.extend(deadline_opps)
        
        # 2. Improbable events expiring
        improbable_opps = self._detect_improbable_expiring(markets)
        opportunities.extend(improbable_opps)
        
        return opportunities
    
    # ==================== DEADLINE APPROACHING ====================
    
    def _detect_deadline_approaching(self, markets: List[Market]) -> List[Opportunity]:
        """
        Detecta mercados tipo "X before Y" donde Y está cerca.
        
        Lógica:
        - Si el evento no ha ocurrido y quedan pocos días, NO es más probable
        - Theta = YES_price / days_left
        - Si theta > min_daily_theta, es buena oportunidad
        """
        opportunities = []
        
        for market in markets:
            # Verificar que tenga fecha de resolución
            days_left = market.days_to_resolution
            if days_left is None or days_left <= 0 or days_left > self.max_days_deadline:
                continue
            
            # Verificar que sea un mercado tipo "deadline"
            question_lower = market.question.lower()
            is_deadline_market = any(
                kw in question_lower for kw in self.deadline_keywords
            )
            
            if not is_deadline_market:
                continue
            
            yes_price = market.yes_price
            
            # Si YES es muy alto (>70%), el evento probablemente ocurrirá
            if yes_price > 0.70:
                continue
            
            # Si YES es muy bajo (<5%), ya no hay mucho para ganar
            if yes_price < 0.05:
                continue
            
            # Calcular theta diario
            daily_theta = yes_price / max(days_left, 0.5)
            
            if daily_theta < self.min_daily_theta:
                continue
            
            # Calcular confianza basada en días restantes
            # Menos días = más confianza de que NO ganará
            if days_left <= 2:
                confidence = 85
            elif days_left <= 5:
                confidence = 75
            elif days_left <= 10:
                confidence = 65
            else:
                confidence = 55
            
            opp = Opportunity(
                type=OpportunityType.TIME_DECAY,
                action=Action.BUY_NO,
                expected_profit=yes_price * 100,  # Max profit = YES price
                confidence=confidence,
                market_id=market.id,
                market_question=market.question,
                market_slug=market.slug,
                current_yes_price=yes_price,
                current_no_price=market.no_price,
                days_to_resolution=days_left,
                extra_data={
                    'days_left': days_left,
                    'daily_theta': daily_theta,
                    'theta_annualized': daily_theta * 365 * 100,  # %/año
                    'strategy': f'Buy NO, expect YES to decay {daily_theta:.1%}/day',
                    'max_profit_if_no': yes_price * 100,
                }
            )
            
            self.log_opportunity(opp)
            opportunities.append(opp)
        
        return opportunities
    
    # ==================== IMPROBABLE EVENTS ====================
    
    def _detect_improbable_expiring(self, markets: List[Market]) -> List[Opportunity]:
        """
        Detecta eventos muy improbables que expiran pronto.
        
        Ejemplo:
            "Alien contact before March" - YES @ 4%
            Es casi seguro que NO ganará, pero hay que esperar a expiración.
        """
        opportunities = []
        
        for market in markets:
            days_left = market.days_to_resolution
            if days_left is None or days_left <= 0 or days_left > 30:
                continue
            
            yes_price = market.yes_price
            
            # Solo mercados con YES bajo (evento improbable)
            if yes_price > self.max_yes_price_improbable:
                continue
            
            # Verificar si es claramente improbable
            question_lower = market.question.lower()
            is_improbable = any(
                re.search(pattern, question_lower) 
                for pattern in self.improbable_patterns
            )
            
            # También considerar precios muy bajos como señal de improbabilidad
            if yes_price < 0.05:
                is_improbable = True
            
            if not is_improbable:
                continue
            
            # Calcular retorno diario
            daily_return = yes_price / max(days_left, 0.5)
            annualized_return = daily_return * 365
            
            # Confianza alta para eventos claramente imposibles
            if any(re.search(p, question_lower) for p in self.improbable_patterns[:5]):
                confidence = 95
            elif yes_price < 0.03:
                confidence = 90
            else:
                confidence = 80
            
            opp = Opportunity(
                type=OpportunityType.IMPROBABLE_EXPIRING,
                action=Action.BUY_NO,
                expected_profit=yes_price * 100,
                confidence=confidence,
                market_id=market.id,
                market_question=market.question,
                market_slug=market.slug,
                current_yes_price=yes_price,
                current_no_price=market.no_price,
                days_to_resolution=days_left,
                extra_data={
                    'days_left': days_left,
                    'daily_return': daily_return,
                    'annualized_return': annualized_return,
                    'is_clearly_improbable': any(
                        re.search(p, question_lower) 
                        for p in self.improbable_patterns
                    ),
                    'strategy': f'Buy NO @ {market.no_price:.1%}, wait for expiration'
                }
            )
            
            self.log_opportunity(opp)
            opportunities.append(opp)
        
        return opportunities


# ==================== UTILIDADES ====================

def calculate_theta(yes_price: float, days_to_expiration: float) -> float:
    """
    Calcula el theta (decay por día).
    
    Theta = precio_actual / días_restantes
    """
    if days_to_expiration <= 0:
        return 0
    return yes_price / days_to_expiration


def estimate_decay_value(
    current_price: float, 
    days_left: float,
    target_days: float = 0
) -> float:
    """
    Estima el valor del YES después de X días sin que ocurra el evento.
    
    Asume decaimiento lineal simple.
    """
    if days_left <= 0:
        return 0
    
    days_elapsed = days_left - target_days
    decay_rate = current_price / days_left
    
    return max(0, current_price - (decay_rate * days_elapsed))


def is_deadline_market(question: str) -> bool:
    """Verifica si un mercado es tipo 'deadline'."""
    deadline_keywords = ['before', 'by', 'prior to', 'until', 'by end of']
    question_lower = question.lower()
    return any(kw in question_lower for kw in deadline_keywords)
