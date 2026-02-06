"""
Live Scanner - Escanea oportunidades en tiempo real usando APIs reales.

Este es el script principal para ejecutar el bot con datos reales.
"""

import sys
import time
from datetime import datetime
from typing import List, Dict, Any

# Asegurar que podemos importar módulos locales
sys.path.insert(0, '.')

from api.polymarket_api import PolymarketAPI, get_polymarket_api
from api.kalshi_api import KalshiAPI, get_kalshi_api
from models.market import Market
from opportunity_detector import OpportunityDetector
from logger import log


class LiveScanner:
    """
    Escáner en vivo que detecta oportunidades usando APIs reales.
    """
    
    def __init__(self):
        # APIs
        self.pm_api = get_polymarket_api()
        self.kalshi_api = get_kalshi_api()
        
        # Detector
        self.detector = OpportunityDetector()
        
        # Stats
        self.total_scans = 0
        self.total_opportunities = 0
        self.arbitrage_found = 0
    
    def fetch_polymarket_data(self, limit: int = 200) -> tuple:
        """
        Obtiene datos de Polymarket.
        
        Returns:
            (markets, events)
        """
        # Obtener mercados
        raw_markets = self.pm_api.get_markets(limit=limit)
        markets = [Market.from_api_response(m) for m in raw_markets]
        
        # Obtener eventos (para arbitraje multi-outcome)
        events = self.pm_api.get_events(limit=50)
        
        return markets, events
    
    def fetch_kalshi_data(self) -> List[Dict]:
        """Obtiene mercados de Kalshi formateados para arbitraje."""
        return self.kalshi_api.get_markets_for_arbitrage()
    
    def scan(self) -> List[Dict]:
        """
        Ejecuta un escaneo completo.
        
        Returns:
            Lista de oportunidades encontradas
        """
        self.total_scans += 1
        log.info(f"\n{'='*60}")
        log.info(f"LIVE SCAN #{self.total_scans} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"{'='*60}")
        
        # 1. Obtener datos
        log.info("\n[1/3] Fetching Polymarket data...")
        markets, events = self.fetch_polymarket_data(limit=200)
        log.info(f"  - Markets: {len(markets)}")
        log.info(f"  - Events: {len(events)}")
        
        log.info("\n[2/3] Fetching Kalshi data...")
        kalshi_markets = self.fetch_kalshi_data()
        log.info(f"  - Kalshi markets: {len(kalshi_markets)}")
        
        # 2. Detectar oportunidades
        log.info("\n[3/3] Detecting opportunities...")
        opportunities = self.detector.scan_all(
            markets=markets,
            events=events,
            kalshi_markets=kalshi_markets,
        )
        
        self.total_opportunities += len(opportunities)
        
        # Contar arbitraje
        arb_count = sum(1 for o in opportunities if o.is_arbitrage)
        self.arbitrage_found += arb_count
        
        # 3. Mostrar resultados
        self._print_results(opportunities)
        
        return [o.to_dict() for o in opportunities]
    
    def _print_results(self, opportunities):
        """Muestra resultados del escaneo."""
        log.info(f"\n{'='*60}")
        log.info(f"SCAN RESULTS - Found {len(opportunities)} opportunities")
        log.info(f"{'='*60}")
        
        if not opportunities:
            log.info("No opportunities found in this scan.")
            return
        
        # Agrupar por tipo
        by_type = {}
        for opp in opportunities:
            t = opp.type.value
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(opp)
        
        log.info("\nBy type:")
        for t, opps in sorted(by_type.items(), key=lambda x: -len(x[1])):
            log.info(f"  - {t}: {len(opps)}")
        
        # Top 10 oportunidades
        log.info("\nTop 10 Opportunities:")
        log.info("-" * 80)
        
        for i, opp in enumerate(opportunities[:10], 1):
            arb_marker = "[ARB]" if opp.is_arbitrage else "     "
            log.info(
                f"{i:2}. {arb_marker} {opp.type.value:25} | "
                f"Profit: {opp.expected_profit:5.1f}% | "
                f"Conf: {opp.confidence:3}% | "
                f"EV: {opp.expected_value:6.1f}"
            )
            log.info(f"    Market: {opp.market_question[:65]}...")
        
        # Resumen de arbitraje
        arb_opps = [o for o in opportunities if o.is_arbitrage]
        if arb_opps:
            log.info(f"\n*** ARBITRAGE OPPORTUNITIES: {len(arb_opps)} ***")
            for opp in arb_opps:
                log.info(f"  - {opp.type.value}: {opp.expected_profit:.1f}% profit")
                log.info(f"    {opp.market_question[:70]}...")
    
    def run_continuous(self, interval_seconds: int = 180, max_scans: int = None):
        """
        Ejecuta escaneos continuos.
        
        Args:
            interval_seconds: Segundos entre escaneos
            max_scans: Número máximo de escaneos (None = infinito)
        """
        log.info(f"\n{'#'*60}")
        log.info(f"STARTING LIVE SCANNER")
        log.info(f"Interval: {interval_seconds}s | Max scans: {max_scans or 'unlimited'}")
        log.info(f"{'#'*60}\n")
        
        scan_count = 0
        
        try:
            while max_scans is None or scan_count < max_scans:
                self.scan()
                scan_count += 1
                
                if max_scans is None or scan_count < max_scans:
                    log.info(f"\nNext scan in {interval_seconds} seconds...")
                    time.sleep(interval_seconds)
                    
        except KeyboardInterrupt:
            log.info("\n\nScanner stopped by user.")
        
        # Estadísticas finales
        log.info(f"\n{'='*60}")
        log.info("FINAL STATISTICS")
        log.info(f"{'='*60}")
        log.info(f"Total scans: {self.total_scans}")
        log.info(f"Total opportunities found: {self.total_opportunities}")
        log.info(f"Arbitrage opportunities: {self.arbitrage_found}")


def quick_scan():
    """Ejecuta un escaneo rápido."""
    scanner = LiveScanner()
    return scanner.scan()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Live Polymarket Scanner')
    parser.add_argument('--continuous', '-c', action='store_true',
                       help='Run continuous scanning')
    parser.add_argument('--interval', '-i', type=int, default=180,
                       help='Seconds between scans (default: 180)')
    parser.add_argument('--max-scans', '-m', type=int, default=None,
                       help='Maximum number of scans')
    
    args = parser.parse_args()
    
    scanner = LiveScanner()
    
    if args.continuous:
        scanner.run_continuous(
            interval_seconds=args.interval,
            max_scans=args.max_scans
        )
    else:
        scanner.scan()
