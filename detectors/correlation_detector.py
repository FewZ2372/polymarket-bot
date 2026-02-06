"""
CorrelationDetector - Detecta divergencias entre mercados correlacionados.

Mercados que deberían moverse juntos pero divergen representan oportunidades.
"""

from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
import re

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


# Pares de mercados que deberían estar correlacionados
CORRELATION_PAIRS = [
    # (pattern1, pattern2, expected_correlation, description)
    (r'trump.*win', r'republican.*win', 0.95, 'Trump winning implies Republican winning'),
    (r'bitcoin.*100k', r'crypto.*bull', 0.80, 'BTC 100K implies crypto bull market'),
    (r'fed.*raise.*rate', r'inflation.*high', 0.70, 'Fed raising rates often due to inflation'),
    (r'shutdown', r'government.*fund', 0.85, 'Shutdown relates to government funding'),
    (r'harris.*win', r'democrat.*win', 0.95, 'Harris winning implies Democrat winning'),
    (r'recession', r'stock.*crash', 0.75, 'Recession often causes stock crashes'),
]


@register_detector('correlation')
class CorrelationDetector(BaseDetector):
    """Detecta divergencias entre mercados correlacionados."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.min_divergence = self.config.get('min_divergence', 0.10)  # 10%
        self.correlation_pairs = CORRELATION_PAIRS
    
    def detect(
        self, 
        markets: List[Market],
        **kwargs
    ) -> List[Opportunity]:
        """Detecta divergencias entre mercados correlacionados."""
        opportunities = []
        
        # Crear índice de mercados por keywords
        market_index = self._create_market_index(markets)
        
        # Buscar pares correlacionados con divergencia
        for pattern1, pattern2, expected_corr, description in self.correlation_pairs:
            divergence_opps = self._find_divergence(
                pattern1, pattern2, expected_corr, description, market_index
            )
            opportunities.extend(divergence_opps)
        
        return opportunities
    
    def _create_market_index(self, markets: List[Market]) -> Dict[str, List[Market]]:
        """Crea un índice de mercados por keywords."""
        index: Dict[str, List[Market]] = {}
        
        for market in markets:
            question_lower = market.question.lower()
            words = re.findall(r'\b\w{3,}\b', question_lower)
            
            for word in words:
                if word not in index:
                    index[word] = []
                index[word].append(market)
        
        return index
    
    def _find_divergence(
        self,
        pattern1: str,
        pattern2: str,
        expected_corr: float,
        description: str,
        market_index: Dict[str, List[Market]]
    ) -> List[Opportunity]:
        """
        Busca mercados que coincidan con los patrones y verifica divergencia.
        """
        opportunities = []
        
        # Encontrar mercados que coincidan con cada patrón
        markets1 = self._find_matching_markets(pattern1, market_index)
        markets2 = self._find_matching_markets(pattern2, market_index)
        
        if not markets1 or not markets2:
            return []
        
        # Comparar cada par
        for m1 in markets1:
            for m2 in markets2:
                if m1.id == m2.id:
                    continue
                
                # Calcular divergencia
                # Si están correlacionados positivamente, deberían tener precios similares
                # ajustados por la correlación
                expected_price2 = m1.yes_price * expected_corr + (1 - expected_corr) * 0.5
                actual_divergence = abs(m2.yes_price - expected_price2)
                
                if actual_divergence < self.min_divergence:
                    continue
                
                # El mercado más barato debería subir
                if m2.yes_price < expected_price2:
                    # m2 está barato, comprar YES en m2
                    opp = Opportunity(
                        type=OpportunityType.CORRELATION_DIVERGENCE,
                        action=Action.BUY_YES,
                        expected_profit=actual_divergence * 50,  # Esperamos convergencia parcial
                        confidence=65,
                        market_id=m2.id,
                        market_question=m2.question,
                        market_slug=m2.slug,
                        current_yes_price=m2.yes_price,
                        extra_data={
                            'correlated_market': m1.question[:50],
                            'correlated_price': m1.yes_price,
                            'expected_correlation': expected_corr,
                            'expected_price': expected_price2,
                            'divergence': actual_divergence,
                            'correlation_description': description,
                            'strategy': f'Buy YES, expect convergence with correlated market'
                        }
                    )
                else:
                    # m2 está caro, comprar NO en m2
                    opp = Opportunity(
                        type=OpportunityType.CORRELATION_DIVERGENCE,
                        action=Action.BUY_NO,
                        expected_profit=actual_divergence * 50,
                        confidence=65,
                        market_id=m2.id,
                        market_question=m2.question,
                        market_slug=m2.slug,
                        current_yes_price=m2.yes_price,
                        extra_data={
                            'correlated_market': m1.question[:50],
                            'correlated_price': m1.yes_price,
                            'expected_correlation': expected_corr,
                            'expected_price': expected_price2,
                            'divergence': actual_divergence,
                            'correlation_description': description,
                            'strategy': f'Buy NO, expect convergence with correlated market'
                        }
                    )
                
                self.log_opportunity(opp)
                opportunities.append(opp)
        
        return opportunities
    
    def _find_matching_markets(
        self, 
        pattern: str, 
        market_index: Dict[str, List[Market]]
    ) -> List[Market]:
        """Encuentra mercados que coincidan con un patrón regex."""
        matching = []
        seen_ids = set()
        
        # Obtener keywords del patrón
        pattern_words = re.findall(r'\w+', pattern.lower())
        
        for word in pattern_words:
            if word in market_index:
                for market in market_index[word]:
                    if market.id in seen_ids:
                        continue
                    
                    # Verificar que el patrón completo coincida
                    if re.search(pattern, market.question.lower()):
                        matching.append(market)
                        seen_ids.add(market.id)
        
        return matching


def find_correlated_pairs(markets: List[Market]) -> List[Tuple[Market, Market, float]]:
    """
    Encuentra pares de mercados potencialmente correlacionados.
    
    Útil para descubrir nuevas correlaciones.
    """
    pairs = []
    
    for i, m1 in enumerate(markets):
        for m2 in markets[i+1:]:
            # Calcular similitud de keywords
            words1 = set(re.findall(r'\b\w{4,}\b', m1.question.lower()))
            words2 = set(re.findall(r'\b\w{4,}\b', m2.question.lower()))
            
            if not words1 or not words2:
                continue
            
            common = words1.intersection(words2)
            similarity = len(common) / min(len(words1), len(words2))
            
            if similarity > 0.3:  # Al menos 30% de keywords en común
                pairs.append((m1, m2, similarity))
    
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs[:20]  # Top 20 pares
