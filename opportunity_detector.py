"""
OpportunityDetector - Coordina todos los detectores de oportunidades.

Este es el punto de entrada principal para detectar oportunidades.
Ejecuta todos los detectores, agrega resultados y los prioriza.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from collections import defaultdict

from models.opportunity import Opportunity
from models.market import Market
from detectors import (
    ArbitrageDetector,
    TimeDecayDetector,
    ResolutionDetector,
    WhaleDetector,
    MomentumDetector,
    MispricingDetector,
    CorrelationDetector,
)
from ranker import OpportunityRanker, OpportunityAggregator
from logger import log


class OpportunityDetector:
    """
    Coordina la detección de todas las oportunidades.
    
    Pipeline:
    1. Ejecutar cada detector en paralelo
    2. Agregar y deduplicar resultados
    3. Rankear por expected value
    4. Filtrar por umbrales
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        
        # Inicializar detectores
        self.detectors = {
            'arbitrage': ArbitrageDetector(self.config.get('arbitrage', {})),
            'time_decay': TimeDecayDetector(self.config.get('time_decay', {})),
            'resolution': ResolutionDetector(self.config.get('resolution', {})),
            'whale': WhaleDetector(self.config.get('whale', {})),
            'momentum': MomentumDetector(self.config.get('momentum', {})),
            'mispricing': MispricingDetector(self.config.get('mispricing', {})),
            'correlation': CorrelationDetector(self.config.get('correlation', {})),
        }
        
        # Ranker y agregador
        self.ranker = OpportunityRanker(self.config.get('ranker', {}))
        self.aggregator = OpportunityAggregator(self.ranker)
        
        # Estadísticas
        self.stats = {
            'total_scans': 0,
            'total_opportunities': 0,
            'by_type': defaultdict(int),
            'last_scan': None,
        }
    
    def scan_all(
        self,
        markets: List[Market],
        events: Optional[List[Dict]] = None,
        kalshi_markets: Optional[List[Dict]] = None,
        whale_transactions: Optional[List[Dict]] = None,
        volume_history: Optional[Dict[str, float]] = None,
        news_data: Optional[List[Dict]] = None,
        similar_markets: Optional[Dict[str, List[Market]]] = None,
    ) -> List[Opportunity]:
        """
        Ejecuta todos los detectores y retorna oportunidades ordenadas.
        
        Args:
            markets: Lista de mercados de Polymarket
            events: Eventos con múltiples outcomes (para arbitraje)
            kalshi_markets: Mercados de Kalshi (para cross-platform arb)
            whale_transactions: Transacciones de whales
            volume_history: Historial de volumen por mercado
            news_data: Feed de noticias
            similar_markets: Mercados agrupados por categoría
        
        Returns:
            Lista de oportunidades ordenadas por prioridad
        """
        self.stats['total_scans'] += 1
        self.stats['last_scan'] = datetime.now()
        
        log.info(f"[OpportunityDetector] Scanning {len(markets)} markets...")
        
        # Ejecutar cada detector
        detector_results: Dict[str, List[Opportunity]] = {}
        
        # 1. Arbitraje (máxima prioridad)
        detector_results['arbitrage'] = self.detectors['arbitrage'].scan(
            markets,
            events=events,
            kalshi_markets=kalshi_markets
        )
        
        # 2. Time Decay
        detector_results['time_decay'] = self.detectors['time_decay'].scan(markets)
        
        # 3. Resolution
        detector_results['resolution'] = self.detectors['resolution'].scan(
            markets,
            news_data=news_data
        )
        
        # 4. Whale
        detector_results['whale'] = self.detectors['whale'].scan(
            markets,
            whale_transactions=whale_transactions,
            volume_history=volume_history,
            news_data=news_data
        )
        
        # 5. Momentum
        detector_results['momentum'] = self.detectors['momentum'].scan(
            markets,
            news_data=news_data
        )
        
        # 6. Mispricing
        detector_results['mispricing'] = self.detectors['mispricing'].scan(
            markets,
            similar_markets=similar_markets
        )
        
        # 7. Correlation
        detector_results['correlation'] = self.detectors['correlation'].scan(markets)
        
        # Agregar y rankear
        all_opportunities = self.aggregator.aggregate(detector_results)
        
        # Actualizar estadísticas
        self.stats['total_opportunities'] += len(all_opportunities)
        for opp in all_opportunities:
            self.stats['by_type'][opp.type.value] += 1
        
        # Log resumen
        log.info(f"[OpportunityDetector] Found {len(all_opportunities)} opportunities:")
        for name, opps in detector_results.items():
            if opps:
                log.info(f"  - {name}: {len(opps)}")
        
        return all_opportunities
    
    def get_arbitrage_only(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """Retorna solo oportunidades de arbitraje."""
        return self.ranker.get_arbitrage_only(opportunities)
    
    def get_high_confidence(
        self, 
        opportunities: List[Opportunity],
        min_conf: int = 80
    ) -> List[Opportunity]:
        """Retorna solo oportunidades de alta confianza."""
        return self.ranker.get_high_confidence(opportunities, min_conf)
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas agregadas."""
        detector_stats = {
            name: detector.get_stats() 
            for name, detector in self.detectors.items()
        }
        
        return {
            **self.stats,
            'by_type': dict(self.stats['by_type']),
            'detectors': detector_stats,
        }
    
    def summarize(self, opportunities: List[Opportunity]) -> Dict[str, Any]:
        """Genera un resumen de las oportunidades."""
        return self.ranker.summarize(opportunities)


# ============ FUNCIONES DE CONVENIENCIA ============

def quick_scan(markets: List[Market]) -> List[Opportunity]:
    """
    Escaneo rápido con configuración por defecto.
    """
    detector = OpportunityDetector()
    return detector.scan_all(markets)


def scan_for_arbitrage(
    markets: List[Market],
    events: Optional[List[Dict]] = None,
    kalshi_markets: Optional[List[Dict]] = None
) -> List[Opportunity]:
    """
    Escaneo enfocado solo en arbitraje.
    """
    detector = ArbitrageDetector()
    return detector.scan(
        markets,
        events=events,
        kalshi_markets=kalshi_markets
    )


if __name__ == "__main__":
    # Test básico
    from tests.mock_data import generate_test_dataset
    
    print("="*60)
    print("OPPORTUNITY DETECTOR TEST")
    print("="*60)
    
    # Generar datos de prueba
    dataset = generate_test_dataset(size=100)
    
    # Convertir a Market objects
    markets = []
    for m in dataset['markets']:
        if isinstance(m, Market):
            markets.append(m)
        else:
            markets.append(Market.from_api_response(m) if isinstance(m, dict) else m)
    
    # Crear detector y escanear
    detector = OpportunityDetector()
    opportunities = detector.scan_all(
        markets=markets,
        events=dataset.get('events', []),
    )
    
    print(f"\nTotal opportunities found: {len(opportunities)}")
    print("\nTop 10 opportunities:")
    for i, opp in enumerate(opportunities[:10], 1):
        print(f"{i}. {opp}")
    
    print("\nSummary:")
    summary = detector.summarize(opportunities)
    print(f"  Total: {summary['total']}")
    print(f"  By type: {summary['by_type']}")
    print(f"  Avg confidence: {summary['avg_confidence']:.1f}%")
    print(f"  Avg profit: {summary['avg_profit']:.1f}%")
    print(f"  Arbitrage count: {summary['arbitrage_count']}")
