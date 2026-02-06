"""
OpportunityRanker - Prioriza y ordena oportunidades por expected value.
"""

from typing import List, Dict, Any, Optional
from collections import defaultdict

from models.opportunity import Opportunity, OpportunityType, TYPE_PRIORITY
from logger import log


class OpportunityRanker:
    """
    Ordena y filtra oportunidades de mejor a peor.
    
    Criterios:
    1. Filtrar por umbrales mínimos (confianza, profit)
    2. Resolver conflictos (mismo mercado, acciones opuestas)
    3. Calcular score compuesto
    4. Ordenar por score
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.min_confidence = self.config.get('min_confidence', 55)
        self.min_profit = self.config.get('min_profit', 2.0)
        self.min_ev = self.config.get('min_ev', -20)  # EV mínimo
    
    def rank(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """
        Ordena oportunidades de mejor a peor.
        
        Args:
            opportunities: Lista de oportunidades sin ordenar
        
        Returns:
            Lista ordenada de mejor a peor
        """
        if not opportunities:
            return []
        
        # 1. Filtrar por umbrales
        valid = self._filter_by_thresholds(opportunities)
        log.debug(f"[Ranker] After threshold filter: {len(valid)}/{len(opportunities)}")
        
        # 2. Resolver conflictos
        resolved = self._resolve_conflicts(valid)
        log.debug(f"[Ranker] After conflict resolution: {len(resolved)}/{len(valid)}")
        
        # 3. Calcular scores
        for opp in resolved:
            opp.calculate_rank_score()
        
        # 4. Ordenar por score (mayor primero)
        resolved.sort(key=lambda x: x.rank_score, reverse=True)
        
        return resolved
    
    def _filter_by_thresholds(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """Filtra oportunidades que no cumplen umbrales mínimos."""
        valid = []
        
        for opp in opportunities:
            # Arbitraje siempre pasa (ganancia garantizada)
            if opp.is_arbitrage:
                valid.append(opp)
                continue
            
            # Verificar umbrales
            if opp.confidence < self.min_confidence:
                continue
            
            if opp.expected_profit < self.min_profit:
                continue
            
            if opp.expected_value < self.min_ev:
                continue
            
            valid.append(opp)
        
        return valid
    
    def _resolve_conflicts(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """
        Resuelve conflictos entre oportunidades.
        
        Conflictos:
        - Mismo mercado con acciones opuestas (BUY_YES vs BUY_NO)
        """
        # Agrupar por mercado
        by_market: Dict[str, List[Opportunity]] = defaultdict(list)
        
        for opp in opportunities:
            market_key = opp.market_id or opp.market_question[:50]
            by_market[market_key].append(opp)
        
        resolved = []
        
        for market_key, market_opps in by_market.items():
            if len(market_opps) == 1:
                resolved.append(market_opps[0])
                continue
            
            # Múltiples señales para el mismo mercado
            yes_signals = [
                o for o in market_opps 
                if 'YES' in o.action.value.upper()
            ]
            no_signals = [
                o for o in market_opps 
                if 'NO' in o.action.value.upper()
            ]
            
            if yes_signals and no_signals:
                # Conflicto: elegir el de mayor confianza
                all_signals = yes_signals + no_signals
                best = max(all_signals, key=lambda x: x.confidence)
                
                log.debug(
                    f"[Ranker] Conflict resolved for {market_key[:30]}...: "
                    f"chose {best.action.value} (conf={best.confidence}%)"
                )
                resolved.append(best)
            else:
                # Sin conflicto: combinar señales (boost de confianza)
                best = max(market_opps, key=lambda x: x.expected_value)
                
                # Boost por múltiples señales coincidentes
                signal_count = len(market_opps)
                if signal_count > 1:
                    boost = min(15, (signal_count - 1) * 5)
                    best.confidence = min(95, best.confidence + boost)
                    log.debug(
                        f"[Ranker] Combined {signal_count} signals for {market_key[:30]}..., "
                        f"confidence boosted by {boost}%"
                    )
                
                resolved.append(best)
        
        return resolved
    
    def get_top_n(self, opportunities: List[Opportunity], n: int = 10) -> List[Opportunity]:
        """Retorna las top N oportunidades."""
        ranked = self.rank(opportunities)
        return ranked[:n]
    
    def get_by_type(
        self, 
        opportunities: List[Opportunity], 
        opp_type: OpportunityType
    ) -> List[Opportunity]:
        """Retorna oportunidades de un tipo específico."""
        return [o for o in opportunities if o.type == opp_type]
    
    def get_arbitrage_only(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """Retorna solo oportunidades de arbitraje."""
        return [o for o in opportunities if o.is_arbitrage]
    
    def get_high_confidence(
        self, 
        opportunities: List[Opportunity], 
        min_conf: int = 80
    ) -> List[Opportunity]:
        """Retorna oportunidades de alta confianza."""
        ranked = self.rank(opportunities)
        return [o for o in ranked if o.confidence >= min_conf]
    
    def summarize(self, opportunities: List[Opportunity]) -> Dict[str, Any]:
        """Genera un resumen de las oportunidades."""
        if not opportunities:
            return {
                'total': 0,
                'by_type': {},
                'avg_confidence': 0,
                'avg_profit': 0,
                'arbitrage_count': 0,
            }
        
        by_type = defaultdict(int)
        for opp in opportunities:
            by_type[opp.type.value] += 1
        
        return {
            'total': len(opportunities),
            'by_type': dict(by_type),
            'avg_confidence': sum(o.confidence for o in opportunities) / len(opportunities),
            'avg_profit': sum(o.expected_profit for o in opportunities) / len(opportunities),
            'avg_ev': sum(o.expected_value for o in opportunities) / len(opportunities),
            'arbitrage_count': sum(1 for o in opportunities if o.is_arbitrage),
            'top_opportunity': opportunities[0].to_dict() if opportunities else None,
        }


class OpportunityAggregator:
    """
    Agrega oportunidades de múltiples detectores.
    
    Funciones:
    - Deduplica (mismo mercado de diferentes detectores)
    - Combina scores cuando hay múltiples señales
    - Elimina contradicciones
    """
    
    def __init__(self, ranker: Optional[OpportunityRanker] = None):
        self.ranker = ranker or OpportunityRanker()
    
    def aggregate(
        self, 
        detector_results: Dict[str, List[Opportunity]]
    ) -> List[Opportunity]:
        """
        Agrega resultados de múltiples detectores.
        
        Args:
            detector_results: Dict de detector_name -> lista de oportunidades
        
        Returns:
            Lista unificada y rankeada de oportunidades
        """
        all_opportunities = []
        
        for detector_name, opportunities in detector_results.items():
            for opp in opportunities:
                opp.detector_name = detector_name
                all_opportunities.append(opp)
        
        log.info(
            f"[Aggregator] Aggregating {len(all_opportunities)} opportunities "
            f"from {len(detector_results)} detectors"
        )
        
        # Usar el ranker para deduplicar, resolver conflictos y ordenar
        ranked = self.ranker.rank(all_opportunities)
        
        log.info(f"[Aggregator] After aggregation: {len(ranked)} opportunities")
        
        return ranked
    
    def merge_duplicate_signals(
        self, 
        opportunities: List[Opportunity]
    ) -> List[Opportunity]:
        """
        Merge señales duplicadas para el mismo mercado.
        
        Si múltiples detectores encuentran la misma oportunidad,
        combinamos sus confianzas.
        """
        # Agrupar por (market_id, action)
        by_key: Dict[tuple, List[Opportunity]] = defaultdict(list)
        
        for opp in opportunities:
            key = (opp.market_id or opp.market_question[:50], opp.action)
            by_key[key].append(opp)
        
        merged = []
        
        for key, opps in by_key.items():
            if len(opps) == 1:
                merged.append(opps[0])
                continue
            
            # Combinar: tomar el mejor y boost confidence
            best = max(opps, key=lambda x: x.expected_value)
            
            # Confidence boost: más detectores = más confiable
            detectors = set(o.detector_name for o in opps)
            if len(detectors) > 1:
                boost = min(20, len(detectors) * 7)
                best.confidence = min(98, best.confidence + boost)
                best.extra_data['confirmed_by'] = list(detectors)
                
                log.debug(
                    f"[Aggregator] Merged {len(opps)} signals for {key[0][:30]}... "
                    f"(detectors: {detectors}), confidence +{boost}%"
                )
            
            merged.append(best)
        
        return merged
