"""
WhaleDetector - Detecta actividad de smart money/whales.

Tipos:
1. Whale Activity: Grandes transacciones de wallets conocidas
2. Abnormal Volume: Volumen anormalmente alto sin noticias
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from collections import defaultdict

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('whale')
class WhaleDetector(BaseDetector):
    """Detecta actividad de whales/smart money."""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.min_whale_amount = self.config.get('min_whale_amount', 5000)  # $5K
        self.min_volume_ratio = self.config.get('min_volume_ratio', 5.0)  # 5x normal
        self.whale_consensus_threshold = self.config.get('whale_consensus_threshold', 0.7)  # 70%
    
    def detect(
        self, 
        markets: List[Market],
        whale_transactions: Optional[List[Dict]] = None,
        volume_history: Optional[Dict[str, float]] = None,
        news_data: Optional[List[Dict]] = None,
        **kwargs
    ) -> List[Opportunity]:
        """Detecta oportunidades basadas en whale activity."""
        opportunities = []
        
        # 1. Whale activity
        if whale_transactions:
            whale_opps = self._detect_whale_activity(markets, whale_transactions)
            opportunities.extend(whale_opps)
        
        # 2. Abnormal volume
        if volume_history:
            volume_opps = self._detect_abnormal_volume(markets, volume_history, news_data)
            opportunities.extend(volume_opps)
        
        return opportunities
    
    def _detect_whale_activity(
        self, 
        markets: List[Market],
        transactions: List[Dict]
    ) -> List[Opportunity]:
        """
        Detecta cuando whales están comprando/vendiendo.
        
        Si múltiples whales compran en la misma dirección = señal fuerte.
        """
        opportunities = []
        
        # Agrupar transacciones por mercado
        txs_by_market: Dict[str, List[Dict]] = defaultdict(list)
        for tx in transactions:
            market_id = tx.get('market_id', '')
            if tx.get('amount_usd', 0) >= self.min_whale_amount:
                txs_by_market[market_id].append(tx)
        
        for market in markets:
            market_txs = txs_by_market.get(market.id, [])
            
            if not market_txs:
                continue
            
            # Calcular dirección del consenso
            yes_volume = sum(tx['amount_usd'] for tx in market_txs if tx.get('side', '').upper() == 'YES')
            no_volume = sum(tx['amount_usd'] for tx in market_txs if tx.get('side', '').upper() == 'NO')
            total_volume = yes_volume + no_volume
            
            if total_volume < self.min_whale_amount * 2:
                continue
            
            yes_ratio = yes_volume / total_volume if total_volume > 0 else 0.5
            
            # Verificar consenso
            if yes_ratio >= self.whale_consensus_threshold:
                # Consenso en YES
                opp = Opportunity(
                    type=OpportunityType.WHALE_ACTIVITY,
                    action=Action.BUY_YES,
                    expected_profit=15.0,  # Estimado basado en seguir a whales
                    confidence=int(yes_ratio * 90),
                    market_id=market.id,
                    market_question=market.question,
                    market_slug=market.slug,
                    current_yes_price=market.yes_price,
                    extra_data={
                        'whale_count': len(market_txs),
                        'total_volume': total_volume,
                        'yes_volume': yes_volume,
                        'no_volume': no_volume,
                        'consensus_ratio': yes_ratio,
                    }
                )
                self.log_opportunity(opp)
                opportunities.append(opp)
                
            elif yes_ratio <= (1 - self.whale_consensus_threshold):
                # Consenso en NO
                opp = Opportunity(
                    type=OpportunityType.WHALE_ACTIVITY,
                    action=Action.BUY_NO,
                    expected_profit=15.0,
                    confidence=int((1 - yes_ratio) * 90),
                    market_id=market.id,
                    market_question=market.question,
                    market_slug=market.slug,
                    current_yes_price=market.yes_price,
                    extra_data={
                        'whale_count': len(market_txs),
                        'total_volume': total_volume,
                        'yes_volume': yes_volume,
                        'no_volume': no_volume,
                        'consensus_ratio': 1 - yes_ratio,
                    }
                )
                self.log_opportunity(opp)
                opportunities.append(opp)
        
        return opportunities
    
    def _detect_abnormal_volume(
        self, 
        markets: List[Market],
        volume_history: Dict[str, float],
        news_data: Optional[List[Dict]] = None
    ) -> List[Opportunity]:
        """
        Detecta volumen anormalmente alto sin noticias (posible insider).
        """
        opportunities = []
        
        for market in markets:
            avg_volume = volume_history.get(market.id, market.volume_24h / 24)  # Promedio horario
            current_volume = market.volume_24h / 24  # Aproximación volumen reciente
            
            if avg_volume <= 0:
                continue
            
            volume_ratio = current_volume / avg_volume
            
            if volume_ratio < self.min_volume_ratio:
                continue
            
            # Verificar que no hay noticias que expliquen el volumen
            has_news = False
            if news_data:
                market_keywords = market.question.lower().split()[:3]
                for news in news_data:
                    news_text = (news.get('title', '') + news.get('description', '')).lower()
                    if any(kw in news_text for kw in market_keywords):
                        has_news = True
                        break
            
            if has_news:
                continue  # Volumen explicado por noticias
            
            # Determinar dirección por cambio de precio
            if market.price_change_1h > 0.03:
                action = Action.BUY_YES
                direction_confidence = min(80, 60 + abs(market.price_change_1h) * 200)
            elif market.price_change_1h < -0.03:
                action = Action.BUY_NO
                direction_confidence = min(80, 60 + abs(market.price_change_1h) * 200)
            else:
                continue  # Sin dirección clara
            
            opp = Opportunity(
                type=OpportunityType.ABNORMAL_VOLUME,
                action=action,
                expected_profit=10.0,
                confidence=int(direction_confidence),
                market_id=market.id,
                market_question=market.question,
                market_slug=market.slug,
                current_yes_price=market.yes_price,
                extra_data={
                    'volume_ratio': volume_ratio,
                    'avg_volume': avg_volume,
                    'current_volume': current_volume,
                    'price_change_1h': market.price_change_1h,
                }
            )
            self.log_opportunity(opp)
            opportunities.append(opp)
        
        return opportunities
