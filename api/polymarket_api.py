"""
Polymarket API Client - Conecta con la API Gamma de Polymarket.

La API Gamma es pública y no requiere autenticación para lectura.
Endpoints:
- /markets - Lista de mercados
- /events - Eventos con múltiples outcomes
"""

import requests
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass

from logger import log


# Base URLs
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"


@dataclass
class PolymarketMarket:
    """Representa un mercado de Polymarket."""
    id: str
    question: str
    slug: str
    condition_id: str
    yes_price: float
    no_price: float
    volume_24h: float
    volume_total: float
    liquidity: float
    end_date: Optional[datetime]
    category: str
    is_active: bool
    token_ids: List[str]
    raw_data: Dict[str, Any]


class PolymarketAPI:
    """
    Cliente para la API de Polymarket.
    
    Uso:
        api = PolymarketAPI()
        markets = api.get_markets(limit=100)
        events = api.get_events()
    """
    
    def __init__(self, timeout: int = 15):
        self.base_url = GAMMA_API_URL
        self.timeout = timeout
        self.session = requests.Session()
        
        # Cache
        self._markets_cache: List[Dict] = []
        self._markets_cache_time: Optional[datetime] = None
        self._cache_ttl = 60  # 1 minuto
    
    def get_markets(
        self,
        limit: int = 100,
        active_only: bool = True,
        order_by: str = "volume24hr",
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Obtiene lista de mercados.
        
        Args:
            limit: Número máximo de mercados
            active_only: Solo mercados activos
            order_by: Campo para ordenar (volume24hr, liquidity, etc)
            use_cache: Usar cache si está disponible
        
        Returns:
            Lista de mercados raw de la API
        """
        # Verificar cache
        if use_cache and self._is_cache_valid():
            log.debug(f"[PolymarketAPI] Using cached markets ({len(self._markets_cache)})")
            return self._markets_cache[:limit]
        
        params = {
            "limit": min(limit, 500),  # Max 500 por request
            "order": order_by,
            "ascending": "false",
        }
        
        if active_only:
            params["active"] = "true"
            params["closed"] = "false"
        
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            markets = response.json()
            
            # Actualizar cache
            self._markets_cache = markets
            self._markets_cache_time = datetime.now()
            
            log.info(f"[PolymarketAPI] Fetched {len(markets)} markets")
            return markets
            
        except requests.RequestException as e:
            log.error(f"[PolymarketAPI] Error fetching markets: {e}")
            return self._markets_cache if self._markets_cache else []
    
    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Obtiene eventos (que pueden tener múltiples outcomes/mercados).
        
        Útil para detectar arbitraje multi-outcome.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/events",
                params={
                    "limit": limit,
                    "active": "true",
                    "closed": "false",
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            
            events = response.json()
            log.info(f"[PolymarketAPI] Fetched {len(events)} events")
            return events
            
        except requests.RequestException as e:
            log.error(f"[PolymarketAPI] Error fetching events: {e}")
            return []
    
    def get_market_by_id(self, market_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un mercado específico por ID."""
        try:
            response = self.session.get(
                f"{self.base_url}/markets/{market_id}",
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
            
        except requests.RequestException as e:
            log.error(f"[PolymarketAPI] Error fetching market {market_id}: {e}")
            return None
    
    def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Obtiene un mercado por slug."""
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params={"slug": slug},
                timeout=self.timeout
            )
            response.raise_for_status()
            markets = response.json()
            return markets[0] if markets else None
            
        except requests.RequestException as e:
            log.error(f"[PolymarketAPI] Error fetching market by slug {slug}: {e}")
            return None
    
    def get_market_history(
        self, 
        market_id: str,
        interval: str = "1h",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Obtiene historial de precios de un mercado.
        
        Args:
            market_id: ID del mercado
            interval: Intervalo (1m, 5m, 1h, 1d)
            limit: Número de puntos
        """
        try:
            response = self.session.get(
                f"{self.base_url}/markets/{market_id}/prices",
                params={
                    "interval": interval,
                    "limit": limit,
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
            
        except requests.RequestException as e:
            log.debug(f"[PolymarketAPI] Error fetching history for {market_id}: {e}")
            return []
    
    def search_markets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Busca mercados por texto."""
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params={
                    "limit": limit,
                    "active": "true",
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            markets = response.json()
            
            # Filtrar por query
            query_lower = query.lower()
            filtered = [
                m for m in markets
                if query_lower in m.get('question', '').lower()
                or query_lower in m.get('slug', '').lower()
            ]
            
            return filtered[:limit]
            
        except requests.RequestException as e:
            log.error(f"[PolymarketAPI] Error searching markets: {e}")
            return []
    
    def _is_cache_valid(self) -> bool:
        """Verifica si el cache es válido."""
        if not self._markets_cache or not self._markets_cache_time:
            return False
        
        age = (datetime.now() - self._markets_cache_time).total_seconds()
        return age < self._cache_ttl
    
    def parse_market(self, raw: Dict[str, Any]) -> PolymarketMarket:
        """Convierte datos raw a PolymarketMarket."""
        # Parse prices
        prices_str = raw.get('outcomePrices', '[]')
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            yes_price = float(prices[0]) if len(prices) > 0 else 0.0
            no_price = float(prices[1]) if len(prices) > 1 else 0.0
        except:
            yes_price = 0.0
            no_price = 0.0
        
        # Parse end date
        end_date = None
        if raw.get('endDate'):
            try:
                end_str = raw['endDate'].replace('Z', '+00:00')
                end_date = datetime.fromisoformat(end_str)
                if end_date.tzinfo:
                    end_date = end_date.replace(tzinfo=None)
            except:
                pass
        
        return PolymarketMarket(
            id=raw.get('id', ''),
            question=raw.get('question', ''),
            slug=raw.get('slug', ''),
            condition_id=raw.get('conditionId', ''),
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=float(raw.get('volume24hr', 0) or 0),
            volume_total=float(raw.get('volume', 0) or 0),
            liquidity=float(raw.get('liquidity', 0) or 0),
            end_date=end_date,
            category=raw.get('category', 'Other'),
            is_active=raw.get('active', True),
            token_ids=raw.get('clobTokenIds', []),
            raw_data=raw,
        )


# Singleton instance
_api_instance: Optional[PolymarketAPI] = None


def get_polymarket_api() -> PolymarketAPI:
    """Obtiene la instancia singleton de la API."""
    global _api_instance
    if _api_instance is None:
        _api_instance = PolymarketAPI()
    return _api_instance


if __name__ == "__main__":
    # Test de la API
    print("="*60)
    print("POLYMARKET API TEST")
    print("="*60)
    
    api = PolymarketAPI()
    
    # Test get_markets
    print("\n--- Testing get_markets ---")
    markets = api.get_markets(limit=10)
    print(f"Fetched {len(markets)} markets")
    
    if markets:
        m = markets[0]
        parsed = api.parse_market(m)
        print(f"\nFirst market:")
        print(f"  Question: {parsed.question[:60]}...")
        print(f"  YES: {parsed.yes_price:.2%}")
        print(f"  Volume 24h: ${parsed.volume_24h:,.0f}")
    
    # Test get_events
    print("\n--- Testing get_events ---")
    events = api.get_events(limit=5)
    print(f"Fetched {len(events)} events")
    
    if events:
        e = events[0]
        print(f"\nFirst event:")
        print(f"  Title: {e.get('title', 'N/A')[:60]}...")
        print(f"  Markets: {len(e.get('markets', []))}")
    
    print("\n" + "="*60)
    print("POLYMARKET API TEST COMPLETE")
    print("="*60)
