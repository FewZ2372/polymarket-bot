"""
Kalshi API Client - Conecta con la API pública de Kalshi.

La API de lectura es pública y no requiere autenticación.
Endpoints:
- /markets - Lista de mercados
- /events - Eventos
"""

import requests
from typing import List, Dict, Any, Optional
from datetime import datetime
from dataclasses import dataclass

from logger import log


# Base URL
KALSHI_API_URL = "https://api.elections.kalshi.com/trade-api/v2"


@dataclass
class KalshiMarket:
    """Representa un mercado de Kalshi."""
    ticker: str
    title: str
    subtitle: str
    category: str
    yes_price: float  # 0-1 (normalizado de centavos)
    no_price: float
    yes_ask: int  # Precio en centavos
    yes_bid: int
    no_ask: int
    no_bid: int
    volume: int
    volume_24h: int
    open_interest: int
    status: str
    close_time: Optional[datetime]
    raw_data: Dict[str, Any]


class KalshiAPI:
    """
    Cliente para la API de Kalshi.
    
    Uso:
        api = KalshiAPI()
        markets = api.get_markets(limit=100)
    """
    
    def __init__(self, timeout: int = 15):
        self.base_url = KALSHI_API_URL
        self.timeout = timeout
        self.session = requests.Session()
        
        # Headers
        self.session.headers.update({
            'Accept': 'application/json',
        })
        
        # Cache
        self._markets_cache: List[Dict] = []
        self._markets_cache_time: Optional[datetime] = None
        self._cache_ttl = 300  # 5 minutos (Kalshi rate limits son más estrictos)
    
    def get_markets(
        self,
        limit: int = 200,
        status: str = "open",
        use_cache: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Obtiene lista de mercados de Kalshi.
        
        Args:
            limit: Número máximo de mercados
            status: Estado del mercado (open, closed, settled)
            use_cache: Usar cache si está disponible
        
        Returns:
            Lista de mercados raw de la API
        """
        # Verificar cache
        if use_cache and self._is_cache_valid():
            log.debug(f"[KalshiAPI] Using cached markets ({len(self._markets_cache)})")
            return self._markets_cache[:limit]
        
        params = {
            "limit": min(limit, 1000),
            "status": status,
        }
        
        try:
            response = self.session.get(
                f"{self.base_url}/markets",
                params=params,
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            markets = data.get('markets', [])
            
            # Actualizar cache
            self._markets_cache = markets
            self._markets_cache_time = datetime.now()
            
            log.info(f"[KalshiAPI] Fetched {len(markets)} markets")
            return markets
            
        except requests.RequestException as e:
            log.error(f"[KalshiAPI] Error fetching markets: {e}")
            return self._markets_cache if self._markets_cache else []
    
    def get_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Obtiene eventos de Kalshi.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/events",
                params={
                    "limit": limit,
                    "status": "open",
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            events = data.get('events', [])
            log.info(f"[KalshiAPI] Fetched {len(events)} events")
            return events
            
        except requests.RequestException as e:
            log.error(f"[KalshiAPI] Error fetching events: {e}")
            return []
    
    def get_market_by_ticker(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Obtiene un mercado específico por ticker."""
        try:
            response = self.session.get(
                f"{self.base_url}/markets/{ticker}",
                timeout=self.timeout
            )
            response.raise_for_status()
            
            data = response.json()
            return data.get('market')
            
        except requests.RequestException as e:
            log.error(f"[KalshiAPI] Error fetching market {ticker}: {e}")
            return None
    
    def search_markets(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Busca mercados por texto."""
        markets = self.get_markets(limit=500, use_cache=True)
        
        query_lower = query.lower()
        filtered = []
        
        for m in markets:
            title = m.get('title', '').lower()
            subtitle = m.get('subtitle', '').lower()
            
            if query_lower in title or query_lower in subtitle:
                filtered.append(m)
                
                if len(filtered) >= limit:
                    break
        
        return filtered
    
    def _is_cache_valid(self) -> bool:
        """Verifica si el cache es válido."""
        if not self._markets_cache or not self._markets_cache_time:
            return False
        
        age = (datetime.now() - self._markets_cache_time).total_seconds()
        return age < self._cache_ttl
    
    def parse_market(self, raw: Dict[str, Any]) -> KalshiMarket:
        """Convierte datos raw a KalshiMarket."""
        # Precios en Kalshi vienen en centavos (1-99)
        yes_ask = raw.get('yes_ask', 50)
        yes_bid = raw.get('yes_bid', 50)
        no_ask = raw.get('no_ask', 50)
        no_bid = raw.get('no_bid', 50)
        
        # Convertir a 0-1
        yes_price = yes_ask / 100 if yes_ask else 0.5
        no_price = no_ask / 100 if no_ask else 0.5
        
        # Parse close time
        close_time = None
        if raw.get('close_time'):
            try:
                close_time = datetime.fromisoformat(raw['close_time'].replace('Z', '+00:00'))
                if close_time.tzinfo:
                    close_time = close_time.replace(tzinfo=None)
            except:
                pass
        
        return KalshiMarket(
            ticker=raw.get('ticker', ''),
            title=raw.get('title', ''),
            subtitle=raw.get('subtitle', ''),
            category=raw.get('category', ''),
            yes_price=yes_price,
            no_price=no_price,
            yes_ask=yes_ask,
            yes_bid=yes_bid,
            no_ask=no_ask,
            no_bid=no_bid,
            volume=raw.get('volume', 0),
            volume_24h=raw.get('volume_24h', 0),
            open_interest=raw.get('open_interest', 0),
            status=raw.get('status', 'unknown'),
            close_time=close_time,
            raw_data=raw,
        )
    
    def get_markets_for_arbitrage(self) -> List[Dict[str, Any]]:
        """
        Obtiene mercados formateados para comparación de arbitraje.
        
        Returns:
            Lista de mercados con formato compatible con ArbitrageDetector
        """
        raw_markets = self.get_markets(limit=500)
        
        formatted = []
        for m in raw_markets:
            # Normalizar precio (Kalshi usa centavos)
            yes_ask = m.get('yes_ask', 50)
            yes_price = yes_ask / 100 if yes_ask else 0.5
            
            formatted.append({
                'ticker': m.get('ticker', ''),
                'title': m.get('title', ''),
                'subtitle': m.get('subtitle', ''),
                'yes_price': yes_price,
                'yes_ask': yes_ask,
                'category': m.get('category', ''),
                'status': m.get('status', ''),
            })
        
        return formatted


# Singleton instance
_api_instance: Optional[KalshiAPI] = None


def get_kalshi_api() -> KalshiAPI:
    """Obtiene la instancia singleton de la API."""
    global _api_instance
    if _api_instance is None:
        _api_instance = KalshiAPI()
    return _api_instance


if __name__ == "__main__":
    # Test de la API
    print("="*60)
    print("KALSHI API TEST")
    print("="*60)
    
    api = KalshiAPI()
    
    # Test get_markets
    print("\n--- Testing get_markets ---")
    markets = api.get_markets(limit=10)
    print(f"Fetched {len(markets)} markets")
    
    if markets:
        m = markets[0]
        parsed = api.parse_market(m)
        print(f"\nFirst market:")
        print(f"  Ticker: {parsed.ticker}")
        print(f"  Title: {parsed.title[:60]}...")
        print(f"  YES price: {parsed.yes_price:.2%}")
        print(f"  Volume: {parsed.volume:,}")
    
    # Test search
    print("\n--- Testing search ---")
    fed_markets = api.search_markets("fed", limit=5)
    print(f"Found {len(fed_markets)} markets matching 'fed'")
    
    for m in fed_markets[:3]:
        print(f"  - {m.get('title', 'N/A')[:50]}...")
    
    print("\n" + "="*60)
    print("KALSHI API TEST COMPLETE")
    print("="*60)
