"""Test de las APIs de Polymarket y Kalshi."""

import sys
sys.path.insert(0, '.')

from api.polymarket_api import PolymarketAPI
from api.kalshi_api import KalshiAPI

def test_polymarket():
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
        title = e.get('title', 'N/A')
        print(f"  Title: {title[:60] if title else 'N/A'}...")
        markets_in_event = e.get('markets', [])
        print(f"  Markets in event: {len(markets_in_event)}")
        
        # Mostrar suma de YES prices para detectar arbitraje
        if markets_in_event:
            total_yes = 0
            for em in markets_in_event[:5]:
                try:
                    prices = em.get('outcomePrices', '[]')
                    if isinstance(prices, str):
                        import json
                        prices = json.loads(prices)
                    if prices:
                        total_yes += float(prices[0])
                except:
                    pass
            print(f"  Sum of first 5 YES prices: {total_yes:.2%}")
    
    return markets

def test_kalshi():
    print("\n" + "="*60)
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
    
    # Test search
    print("\n--- Testing search for 'fed' ---")
    fed_markets = api.search_markets("fed", limit=5)
    print(f"Found {len(fed_markets)} markets matching 'fed'")
    
    for m in fed_markets[:3]:
        title = m.get('title', 'N/A')
        yes_ask = m.get('yes_ask', 50)
        print(f"  - {title[:50]}... YES={yes_ask}c")
    
    return markets

def compare_markets(pm_markets, k_markets):
    """Busca mercados similares entre PM y Kalshi."""
    print("\n" + "="*60)
    print("CROSS-PLATFORM COMPARISON")
    print("="*60)
    
    pm_api = PolymarketAPI()
    k_api = KalshiAPI()
    
    # Buscar "fed" en ambos
    print("\n--- Searching 'fed' in both platforms ---")
    
    pm_fed = pm_api.search_markets("fed", limit=5)
    k_fed = k_api.search_markets("fed", limit=5)
    
    print(f"\nPolymarket 'fed' markets ({len(pm_fed)}):")
    for m in pm_fed[:3]:
        parsed = pm_api.parse_market(m)
        print(f"  - {parsed.question[:45]}... YES={parsed.yes_price:.1%}")
    
    print(f"\nKalshi 'fed' markets ({len(k_fed)}):")
    for m in k_fed[:3]:
        parsed = k_api.parse_market(m)
        print(f"  - {parsed.title[:45]}... YES={parsed.yes_price:.1%}")

if __name__ == "__main__":
    pm_markets = test_polymarket()
    k_markets = test_kalshi()
    compare_markets(pm_markets, k_markets)
    
    print("\n" + "="*60)
    print("ALL API TESTS COMPLETE")
    print("="*60)
