"""
ResolutionDetector - Detecta mercados con resolución predecible.

Tipos:
1. Already Resolved: El evento ya ocurrió pero el mercado no se actualizó
2. Near Certain: Outcomes casi seguros (>90% probabilidad real)
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import re

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('resolution')
class ResolutionDetector(BaseDetector):
    """Detecta mercados con resolución predecible."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Eventos "casi seguros" que van a pasar
        self.certain_yes_patterns = [
            (r'super\s*bowl.*202[4-9]', 0.99, "Super Bowl siempre ocurre"),
            (r'nba\s*finals', 0.99, "NBA Finals siempre ocurre"),
            (r'world\s*series', 0.99, "World Series siempre ocurre"),
            (r'sun.*rise', 0.9999, "El sol siempre sale"),
            (r'earth.*rotate', 0.9999, "La tierra siempre rota"),
        ]
        
        # Eventos "casi imposibles"
        self.certain_no_patterns = [
            (r'alien.*contact.*202[4-6]', 0.01, "Contacto alien muy improbable"),
            (r'world.*end', 0.001, "Fin del mundo muy improbable"),
            (r'asteroid.*hit.*earth', 0.01, "Impacto de asteroide muy improbable"),
            (r'human.*mars.*202[4-5]', 0.01, "Humanos en Marte muy pronto"),
        ]
    
    def detect(
        self, 
        markets: List[Market],
        news_data: Optional[List[Dict]] = None,
        **kwargs
    ) -> List[Opportunity]:
        """Detecta oportunidades de resolución predecible."""
        opportunities = []
        
        # 1. Near certain outcomes
        certain_opps = self._detect_near_certain(markets)
        opportunities.extend(certain_opps)
        
        # 2. Already resolved (requiere news data)
        if news_data:
            resolved_opps = self._detect_already_resolved(markets, news_data)
            opportunities.extend(resolved_opps)
        
        return opportunities
    
    def _detect_near_certain(self, markets: List[Market]) -> List[Opportunity]:
        """Detecta mercados con outcomes casi seguros."""
        opportunities = []
        
        for market in markets:
            question_lower = market.question.lower()
            
            # Verificar patrones de YES casi seguro
            for pattern, expected_prob, reason in self.certain_yes_patterns:
                if re.search(pattern, question_lower):
                    if market.yes_price < expected_prob - 0.03:  # 3% margen
                        profit = (expected_prob - market.yes_price) * 100
                        
                        opp = Opportunity(
                            type=OpportunityType.NEAR_CERTAIN,
                            action=Action.BUY_YES,
                            expected_profit=profit,
                            confidence=int(expected_prob * 100),
                            market_id=market.id,
                            market_question=market.question,
                            market_slug=market.slug,
                            current_yes_price=market.yes_price,
                            extra_data={
                                'expected_probability': expected_prob,
                                'reason': reason,
                                'current_price': market.yes_price,
                            }
                        )
                        self.log_opportunity(opp)
                        opportunities.append(opp)
                    break
            
            # Verificar patrones de NO casi seguro
            for pattern, expected_prob, reason in self.certain_no_patterns:
                if re.search(pattern, question_lower):
                    if market.yes_price > expected_prob + 0.03:  # 3% margen
                        profit = (market.yes_price - expected_prob) * 100
                        
                        opp = Opportunity(
                            type=OpportunityType.NEAR_CERTAIN,
                            action=Action.BUY_NO,
                            expected_profit=profit,
                            confidence=int((1 - expected_prob) * 100),
                            market_id=market.id,
                            market_question=market.question,
                            market_slug=market.slug,
                            current_yes_price=market.yes_price,
                            extra_data={
                                'expected_probability': expected_prob,
                                'reason': reason,
                                'current_price': market.yes_price,
                            }
                        )
                        self.log_opportunity(opp)
                        opportunities.append(opp)
                    break
        
        return opportunities
    
    def _detect_already_resolved(
        self, 
        markets: List[Market],
        news_data: List[Dict]
    ) -> List[Opportunity]:
        """
        Detecta mercados cuyo evento ya ocurrió pero el precio no refleja.
        
        Requiere feed de noticias para verificar resultados.
        """
        opportunities = []
        
        for market in markets:
            # Buscar noticias relacionadas
            question_keywords = self._extract_keywords(market.question)
            
            for news in news_data:
                news_text = (news.get('title', '') + ' ' + news.get('description', '')).lower()
                
                # Verificar si la noticia es relevante
                keyword_matches = sum(1 for kw in question_keywords if kw in news_text)
                if keyword_matches < 2:
                    continue
                
                # Verificar si la noticia indica un resultado
                outcome = self._determine_outcome_from_news(market.question, news_text)
                
                if outcome is None:
                    continue
                
                if outcome == 'YES' and market.yes_price < 0.95:
                    profit = (1.0 - market.yes_price) * 100
                    opp = Opportunity(
                        type=OpportunityType.ALREADY_RESOLVED,
                        action=Action.BUY_YES,
                        expected_profit=profit,
                        confidence=92,
                        market_id=market.id,
                        market_question=market.question,
                        current_yes_price=market.yes_price,
                        extra_data={
                            'news_title': news.get('title'),
                            'outcome': outcome,
                        }
                    )
                    self.log_opportunity(opp)
                    opportunities.append(opp)
                    
                elif outcome == 'NO' and market.no_price < 0.95:
                    profit = (1.0 - market.no_price) * 100
                    opp = Opportunity(
                        type=OpportunityType.ALREADY_RESOLVED,
                        action=Action.BUY_NO,
                        expected_profit=profit,
                        confidence=92,
                        market_id=market.id,
                        market_question=market.question,
                        current_yes_price=market.yes_price,
                        extra_data={
                            'news_title': news.get('title'),
                            'outcome': outcome,
                        }
                    )
                    self.log_opportunity(opp)
                    opportunities.append(opp)
        
        return opportunities
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extrae keywords de un texto."""
        text = text.lower()
        words = re.findall(r'\b\w{4,}\b', text)
        stop_words = {'will', 'have', 'been', 'this', 'that', 'with', 'from', 'they'}
        return [w for w in words if w not in stop_words][:5]
    
    def _determine_outcome_from_news(self, question: str, news_text: str) -> Optional[str]:
        """
        Determina el outcome basado en la noticia.
        
        Returns:
            'YES', 'NO', o None si no se puede determinar
        """
        question_lower = question.lower()
        
        # Patrones de confirmación
        confirm_patterns = ['confirmed', 'announced', 'happened', 'won', 'passed', 'approved']
        deny_patterns = ['denied', 'rejected', 'failed', 'lost', 'cancelled', 'not']
        
        has_confirm = any(p in news_text for p in confirm_patterns)
        has_deny = any(p in news_text for p in deny_patterns)
        
        if has_confirm and not has_deny:
            return 'YES'
        elif has_deny and not has_confirm:
            return 'NO'
        
        return None
