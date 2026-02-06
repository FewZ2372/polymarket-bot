"""
ArbitrageDetector - Detecta oportunidades de arbitraje (ganancia garantizada).

Tipos de arbitraje:
1. Multi-Outcome: Suma de YES prices != 100% en eventos con múltiples outcomes
2. Cross-Platform: Diferencia de precio entre Polymarket y Kalshi
3. YES/NO Mismatch: YES + NO != 100% en un solo mercado
"""

import difflib
import re
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime

from detectors.base_detector import BaseDetector, register_detector
from models.opportunity import Opportunity, OpportunityType, Action
from models.market import Market
from logger import log


@register_detector('arbitrage')
class ArbitrageDetector(BaseDetector):
    """
    Detecta oportunidades de arbitraje.
    
    El arbitraje es la oportunidad de mayor prioridad porque
    representa ganancia matemáticamente garantizada.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        
        # Configuración
        self.min_multi_outcome_profit = self.config.get('min_multi_outcome_profit', 2.0)  # 2%
        self.min_cross_platform_spread = self.config.get('min_cross_platform_spread', 0.03)  # 3%
        self.min_yes_no_mismatch = self.config.get('min_yes_no_mismatch', 0.02)  # 2%
        
        # Cache de mercados Kalshi
        self._kalshi_cache: List[Dict] = []
        self._kalshi_cache_time: Optional[datetime] = None
        self._kalshi_cache_ttl = 600  # 10 minutos
    
    def detect(
        self, 
        markets: List[Market], 
        events: Optional[List[Dict]] = None,
        kalshi_markets: Optional[List[Dict]] = None,
        **kwargs
    ) -> List[Opportunity]:
        """
        Detecta todas las oportunidades de arbitraje.
        
        Args:
            markets: Lista de mercados de Polymarket
            events: Lista de eventos con múltiples outcomes
            kalshi_markets: Lista de mercados de Kalshi (opcional)
        
        Returns:
            Lista de oportunidades de arbitraje
        """
        opportunities = []
        
        # 1. Detectar YES/NO mismatch (más simple, más común)
        yes_no_opps = self._detect_yes_no_mismatch(markets)
        opportunities.extend(yes_no_opps)
        
        # 2. Detectar multi-outcome arbitrage
        if events:
            multi_opps = self._detect_multi_outcome_arbitrage(events)
            opportunities.extend(multi_opps)
        
        # 3. Detectar cross-platform arbitrage
        if kalshi_markets:
            cross_opps = self._detect_cross_platform_arbitrage(markets, kalshi_markets)
            opportunities.extend(cross_opps)
        
        return opportunities
    
    # ==================== YES/NO MISMATCH ====================
    
    def _detect_yes_no_mismatch(self, markets: List[Market]) -> List[Opportunity]:
        """
        Detecta mercados donde YES + NO != 100%.
        
        Ejemplo:
            YES = 52%, NO = 45% → Suma = 97%
            Comprar ambos por $0.97, recibir $1.00 → 3% profit
        """
        opportunities = []
        
        for market in markets:
            total = market.yes_price + market.no_price
            
            # Mismatch: suma < 98% o > 102%
            if total < (1 - self.min_yes_no_mismatch):
                # Suma < 100%: comprar ambos es rentable
                profit = (1 - total) * 100
                
                opp = Opportunity(
                    type=OpportunityType.YES_NO_MISMATCH,
                    action=Action.BUY_BOTH,
                    expected_profit=profit,
                    confidence=99,  # Matemáticamente seguro
                    market_id=market.id,
                    market_question=market.question,
                    market_slug=market.slug,
                    current_yes_price=market.yes_price,
                    current_no_price=market.no_price,
                    extra_data={
                        'yes_price': market.yes_price,
                        'no_price': market.no_price,
                        'sum': total,
                        'strategy': 'Buy YES and NO, guaranteed profit on resolution'
                    }
                )
                
                self.log_opportunity(opp)
                opportunities.append(opp)
            
            elif total > (1 + self.min_yes_no_mismatch):
                # Suma > 100%: esto es raro en Polymarket pero posible con fees
                # En teoría se podría vender ambos, pero no es común
                log.debug(
                    f"[Arbitrage] YES+NO > 100% in {market.question[:40]}... "
                    f"({total:.1%}) - rare, skipping"
                )
        
        return opportunities
    
    # ==================== MULTI-OUTCOME ARBITRAGE ====================
    
    def _detect_multi_outcome_arbitrage(
        self, 
        events: List[Dict]
    ) -> List[Opportunity]:
        """
        Detecta arbitraje en eventos con múltiples outcomes.
        
        Ejemplo:
            Evento: "¿Quién ganará?"
            A = 45%, B = 40%, C = 20%
            Suma = 105%
            
            Comprar NO en todos:
            Cost = (1-0.45) + (1-0.40) + (1-0.20) = 0.55 + 0.60 + 0.80 = 1.95
            Solo uno puede ganar → uno de los NO pierde (-1), dos ganan (+1 cada uno)
            Return = 2.00 (los dos NO que ganan)
            Profit = 2.00 - 1.95 = 0.05 = 5%
            
        En realidad es más simple:
            Si suma de YES > 100%, comprar NO en todos garantiza profit
            Si suma de YES < 100%, comprar YES en todos garantiza profit
        """
        import json
        opportunities = []
        
        for event in events:
            event_markets = event.get('markets', [])
            
            if len(event_markets) < 2:
                continue
            
            # Calcular suma de YES prices
            yes_prices = []
            market_names = []
            
            for m in event_markets:
                # Intentar obtener precio de diferentes formas
                price = 0
                
                # Forma 1: yes_price directo
                if 'yes_price' in m:
                    price = m.get('yes_price', 0)
                    if isinstance(price, str):
                        try:
                            price = float(price)
                        except:
                            price = 0
                
                # Forma 2: outcomePrices (formato Polymarket API)
                elif 'outcomePrices' in m:
                    prices_str = m.get('outcomePrices', '[]')
                    try:
                        prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                        if prices and len(prices) > 0:
                            price = float(prices[0])
                    except:
                        price = 0
                
                # Forma 3: price directo
                elif 'price' in m:
                    price = m.get('price', 0)
                    if isinstance(price, str):
                        try:
                            price = float(price)
                        except:
                            price = 0
                
                # Validar precio (debe estar entre 0 y 1)
                if price > 1:
                    price = price / 100  # Convertir de centavos
                
                # Solo agregar si el precio es válido
                if 0 < price < 1:
                    yes_prices.append(price)
                    market_names.append(m.get('question', m.get('title', 'Unknown'))[:50])
            
            # Necesitamos al menos 2 precios válidos
            if len(yes_prices) < 2:
                continue
            
            total_yes = sum(yes_prices)
            event_title = event.get('title', event.get('event_question', 'Multi-outcome event'))
            
            # Calcular profit real considerando fees (~2% por operación)
            fee_per_trade = 0.02
            num_outcomes = len(yes_prices)
            total_fees = fee_per_trade * num_outcomes * 2  # Compra y venta
            
            # Arbitraje: suma != 100% con margen suficiente para cubrir fees
            min_profit_after_fees = self.min_multi_outcome_profit / 100
            
            if total_yes > 1 + min_profit_after_fees + total_fees:
                # Suma > 100%: comprar NO en todos
                gross_profit = (total_yes - 1) * 100
                net_profit = gross_profit - (total_fees * 100)
                
                if net_profit > self.min_multi_outcome_profit:
                    # Calcular precio promedio NO para referencia
                    avg_no_price = (num_outcomes - total_yes) / num_outcomes if num_outcomes > 0 else 0.5
                    
                    opp = Opportunity(
                        type=OpportunityType.MULTI_OUTCOME_ARB,
                        action=Action.BUY_ALL_NO,
                        expected_profit=net_profit,
                        confidence=95,  # 95 no 99 - hay riesgo de ejecución
                        market_id=event.get('id', event.get('event_id', '')),
                        market_question=f"[MULTI-ARB] {event_title[:60]}",
                        current_yes_price=total_yes / num_outcomes if num_outcomes > 0 else 0.5,  # Precio promedio YES
                        current_no_price=avg_no_price,  # Precio promedio NO
                        markets=[m for m in event_markets],
                        extra_data={
                            'total_yes_sum': total_yes,
                            'outcome_count': num_outcomes,
                            'individual_prices': yes_prices,
                            'market_names': market_names,
                            'gross_profit': gross_profit,
                            'estimated_fees': total_fees * 100,
                            'strategy': f'Buy NO on all {num_outcomes} outcomes (sum={total_yes:.1%})'
                        }
                    )
                    
                    self.log_opportunity(opp)
                    opportunities.append(opp)
            
            elif total_yes < 1 - min_profit_after_fees - total_fees:
                # Suma < 100%: comprar YES en todos
                gross_profit = (1 - total_yes) * 100
                net_profit = gross_profit - (total_fees * 100)
                
                if net_profit > self.min_multi_outcome_profit:
                    # Calcular precio promedio YES para referencia
                    avg_yes_price = total_yes / num_outcomes if num_outcomes > 0 else 0.5
                    avg_no_price = (num_outcomes - total_yes) / num_outcomes if num_outcomes > 0 else 0.5
                    
                    opp = Opportunity(
                        type=OpportunityType.MULTI_OUTCOME_ARB,
                        action=Action.BUY_ALL_YES,
                        expected_profit=net_profit,
                        confidence=95,
                        market_id=event.get('id', event.get('event_id', '')),
                        market_question=f"[MULTI-ARB] {event_title[:60]}",
                        current_yes_price=avg_yes_price,  # Precio promedio YES
                        current_no_price=avg_no_price,  # Precio promedio NO
                        markets=[m for m in event_markets],
                        extra_data={
                            'total_yes_sum': total_yes,
                            'outcome_count': num_outcomes,
                            'individual_prices': yes_prices,
                            'market_names': market_names,
                            'gross_profit': gross_profit,
                            'estimated_fees': total_fees * 100,
                            'strategy': f'Buy YES on all {num_outcomes} outcomes (sum={total_yes:.1%})'
                        }
                    )
                    
                    self.log_opportunity(opp)
                    opportunities.append(opp)
        
        return opportunities
    
    # ==================== CROSS-PLATFORM ARBITRAGE ====================
    
    def _detect_cross_platform_arbitrage(
        self, 
        pm_markets: List[Market],
        kalshi_markets: List[Dict]
    ) -> List[Opportunity]:
        """
        Detecta diferencias de precio entre Polymarket y Kalshi.
        
        Ejemplo:
            PM: "Fed cuts rates" YES = 85%
            Kalshi: Same question YES = 78%
            Spread = 7%
            
            Estrategia: Comprar YES en Kalshi (barato), vender YES en PM (caro)
            O: Comprar YES en Kalshi, comprar NO en PM
        """
        opportunities = []
        
        for pm_market in pm_markets:
            # Buscar mercado equivalente en Kalshi
            kalshi_match = self._find_kalshi_match(pm_market, kalshi_markets)
            
            if not kalshi_match:
                continue
            
            pm_yes = pm_market.yes_price
            k_yes = kalshi_match.get('yes_price', 0)
            
            # Convertir precio de Kalshi si viene en centavos
            if k_yes > 1:
                k_yes = k_yes / 100
            
            spread = abs(pm_yes - k_yes)
            
            if spread < self.min_cross_platform_spread:
                continue
            
            # SOLO operar cuando Polymarket está más barato (solo podemos operar en PM)
            if pm_yes >= k_yes:
                # PM más caro o igual → skip (no podemos operar en Kalshi)
                continue
            
            # Polymarket está más barato → comprar YES en PM
            # La oportunidad es que PM está subvaluado vs Kalshi
            action = Action.BUY_YES  # En PM
            strategy = f"Buy YES on PM @ {pm_yes:.1%} (PM cheaper than Kalshi @ {k_yes:.1%})"
            
            # Profit estimado: diferencia de precio (asumiendo convergencia)
            estimated_profit = (k_yes - pm_yes) * 100  # En porcentaje
            
            opp = Opportunity(
                type=OpportunityType.CROSS_PLATFORM_ARB,
                action=action,
                expected_profit=estimated_profit,
                confidence=85,  # Menor que multi-outcome porque requiere convergencia
                market_id=pm_market.id,
                market_question=pm_market.question,
                market_slug=pm_market.slug,
                current_yes_price=pm_yes,
                extra_data={
                    'pm_yes_price': pm_yes,
                    'kalshi_yes_price': k_yes,
                    'spread': spread,
                    'kalshi_ticker': kalshi_match.get('ticker', ''),
                    'kalshi_title': kalshi_match.get('title', ''),
                    'strategy': strategy,
                    'execution_note': 'PM is cheaper - buy YES on PM only'
                }
            )
            
            self.log_opportunity(opp)
            opportunities.append(opp)
        
        return opportunities
    
    def _find_kalshi_match(
        self, 
        pm_market: Market, 
        kalshi_markets: List[Dict]
    ) -> Optional[Dict]:
        """
        Busca un mercado equivalente en Kalshi usando fuzzy matching.
        """
        pm_question = pm_market.question.lower()
        pm_normalized = self._normalize_text(pm_question)
        pm_keywords = self._extract_keywords(pm_question)
        
        best_match = None
        best_score = 0.0
        
        for k_market in kalshi_markets:
            k_title = k_market.get('title', '').lower()
            k_normalized = self._normalize_text(k_title)
            k_keywords = self._extract_keywords(k_title)
            
            # Similitud de texto
            text_ratio = difflib.SequenceMatcher(
                None, pm_normalized, k_normalized
            ).ratio()
            
            # Coincidencia de keywords
            if pm_keywords and k_keywords:
                common = pm_keywords.intersection(k_keywords)
                keyword_score = len(common) / max(len(pm_keywords), 1)
            else:
                keyword_score = 0
            
            # Score combinado
            combined = (text_ratio * 0.6) + (keyword_score * 0.4)
            
            if combined > best_score:
                best_score = combined
                best_match = k_market
        
        # Umbral mínimo de similitud
        if best_score > 0.5:
            return best_match
        
        return None
    
    def _normalize_text(self, text: str) -> str:
        """Normaliza texto para comparación."""
        if not text:
            return ""
        
        text = text.lower()
        text = re.sub(r'[^\w\s]', '', text)
        
        stop_words = {
            'will', 'there', 'be', 'the', 'a', 'an', 'at', 'in', 'on',
            'of', 'for', 'by', 'is', 'are', 'was', 'were', 'to', 'that'
        }
        
        words = text.split()
        filtered = [w for w in words if w not in stop_words]
        
        return " ".join(filtered)
    
    def _extract_keywords(self, text: str) -> Set[str]:
        """Extrae keywords importantes."""
        if not text:
            return set()
        
        text = text.lower()
        
        # Entidades de alto valor
        high_value = {
            'fed', 'interest', 'rate', 'shutdown', 'government',
            'iran', 'israel', 'russia', 'ukraine', 'china',
            'trump', 'biden', 'harris', 'musk', 'bitcoin', 'btc'
        }
        
        words = set(re.findall(r'\b\w{3,}\b', text))
        stop_words = {'will', 'there', 'the', 'and', 'with', 'before', 'after'}
        
        keywords = {w for w in words if w not in stop_words}
        
        return keywords


# ==================== FUNCIONES DE AYUDA ====================

def calculate_multi_outcome_profit(yes_prices: List[float]) -> Tuple[float, str]:
    """
    Calcula el profit de arbitraje multi-outcome.
    
    Returns:
        (profit_percentage, strategy)
    """
    total = sum(yes_prices)
    
    if total > 1:
        profit = (total - 1) * 100
        strategy = "BUY_ALL_NO"
    elif total < 1:
        profit = (1 - total) * 100
        strategy = "BUY_ALL_YES"
    else:
        profit = 0
        strategy = "NO_ARBITRAGE"
    
    return profit, strategy


def is_arbitrage_opportunity(
    yes_price: float, 
    no_price: float, 
    threshold: float = 0.02
) -> bool:
    """Verifica si hay oportunidad de arbitraje YES/NO."""
    total = yes_price + no_price
    return total < (1 - threshold) or total > (1 + threshold)
