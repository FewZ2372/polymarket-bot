"""
Multi-platform market scanner.
Fetches and normalizes data from multiple prediction markets.
"""
import requests
import re
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from abc import ABC, abstractmethod

from logger import log


@dataclass
class NormalizedMarket:
    """Normalized market data across platforms."""
    platform: str
    title: str
    slug: str
    yes_price: float
    no_price: float
    volume_24h: float
    category: str
    url: str
    raw_data: Optional[Dict] = field(default=None)


class PlatformScanner(ABC):
    """Base class for platform scanners."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        pass
    
    @abstractmethod
    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        pass


class PolymarketScanner(PlatformScanner):
    """Scanner for Polymarket."""
    
    name = "Polymarket"
    BASE_URL = "https://gamma-api.polymarket.com/markets"
    
    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        params = {
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
            "limit": limit
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=15)
            response.raise_for_status()
            markets = response.json()
            
            normalized = []
            for m in markets:
                prices = m.get('outcomePrices', '[]')
                try:
                    import json
                    prices = json.loads(prices)
                    yes_price = float(prices[0]) if prices else 0
                    no_price = float(prices[1]) if len(prices) > 1 else 1 - yes_price
                except:
                    yes_price, no_price = 0.5, 0.5
                
                normalized.append(NormalizedMarket(
                    platform="Polymarket",
                    title=m.get('question', ''),
                    slug=m.get('slug', ''),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_24h=float(m.get('volume24hr', 0)),
                    category=m.get('category', 'Other'),
                    url=f"https://polymarket.com/event/{m.get('slug', '')}",
                    raw_data=m
                ))
            
            log.info(f"Fetched {len(normalized)} markets from Polymarket")
            return normalized
            
        except Exception as e:
            log.error(f"Error fetching Polymarket: {e}")
            return []


class KalshiScanner(PlatformScanner):
    """Scanner for Kalshi."""
    
    name = "Kalshi"
    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
    
    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        params = {
            "status": "open",
            "limit": min(limit, 200)
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=15)
            if response.status_code != 200:
                return []
            
            data = response.json()
            markets = data.get('markets', [])
            
            normalized = []
            for m in markets:
                yes_price = m.get('yes_ask', 50) / 100 if m.get('yes_ask') else 0.5
                no_price = 1 - yes_price
                
                normalized.append(NormalizedMarket(
                    platform="Kalshi",
                    title=m.get('title', ''),
                    slug=m.get('ticker', ''),
                    yes_price=yes_price,
                    no_price=no_price,
                    volume_24h=float(m.get('volume_24h', 0)),
                    category=m.get('category', 'Other'),
                    url=f"https://kalshi.com/markets/{m.get('ticker', '')}",
                    raw_data=m
                ))
            
            log.info(f"Fetched {len(normalized)} markets from Kalshi")
            return normalized[:limit]
            
        except Exception as e:
            log.error(f"Error fetching Kalshi: {e}")
            return []


class MetaculusScanner(PlatformScanner):
    """Scanner for Metaculus (forecasting platform)."""
    
    name = "Metaculus"
    BASE_URL = "https://www.metaculus.com/api2/questions/"
    
    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        params = {
            "status": "open",
            "type": "forecast",
            "order_by": "-activity",
            "limit": limit
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=15)
            if response.status_code != 200:
                return []
            
            data = response.json()
            questions = data.get('results', [])
            
            normalized = []
            for q in questions:
                # Metaculus uses probability forecasts
                prediction = q.get('community_prediction', {})
                if isinstance(prediction, dict):
                    yes_price = prediction.get('full', {}).get('q2', 0.5)
                else:
                    yes_price = 0.5
                
                normalized.append(NormalizedMarket(
                    platform="Metaculus",
                    title=q.get('title', ''),
                    slug=str(q.get('id', '')),
                    yes_price=yes_price,
                    no_price=1 - yes_price,
                    volume_24h=q.get('number_of_predictions', 0) * 10,  # Approximate
                    category=q.get('group', {}).get('name', 'Other') if q.get('group') else 'Other',
                    url=f"https://www.metaculus.com/questions/{q.get('id')}/",
                    raw_data=q
                ))
            
            log.info(f"Fetched {len(normalized)} markets from Metaculus")
            return normalized
            
        except Exception as e:
            log.error(f"Error fetching Metaculus: {e}")
            return []


class ManifoldScanner(PlatformScanner):
    """Scanner for Manifold Markets."""
    
    name = "Manifold"
    BASE_URL = "https://api.manifold.markets/v0/markets"
    
    def fetch_markets(self, limit: int = 50) -> List[NormalizedMarket]:
        params = {
            "limit": limit,
            "sort": "liquidity"
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=15)
            if response.status_code != 200:
                return []
            
            markets = response.json()
            
            normalized = []
            for m in markets:
                if m.get('outcomeType') != 'BINARY':
                    continue
                    
                yes_price = m.get('probability', 0.5)
                
                normalized.append(NormalizedMarket(
                    platform="Manifold",
                    title=m.get('question', ''),
                    slug=m.get('slug', ''),
                    yes_price=yes_price,
                    no_price=1 - yes_price,
                    volume_24h=m.get('volume24Hours', 0),
                    category=m.get('groupSlugs', ['Other'])[0] if m.get('groupSlugs') else 'Other',
                    url=m.get('url', ''),
                    raw_data=m
                ))
            
            log.info(f"Fetched {len(normalized)} markets from Manifold")
            return normalized
            
        except Exception as e:
            log.error(f"Error fetching Manifold: {e}")
            return []


class MultiPlatformScanner:
    """Aggregates data from multiple prediction markets."""
    
    def __init__(self):
        self.scanners = [
            PolymarketScanner(),
            KalshiScanner(),
            MetaculusScanner(),
            ManifoldScanner(),
        ]
        self._cache: Dict[str, List[NormalizedMarket]] = {}
        self._cache_time: float = 0
        self._cache_ttl = 120  # 2 minutes
    
    def fetch_all_markets(self, limit_per_platform: int = 30) -> Dict[str, List[NormalizedMarket]]:
        """Fetch markets from all platforms."""
        now = time.time()
        
        if now - self._cache_time < self._cache_ttl and self._cache:
            return self._cache
        
        results = {}
        for scanner in self.scanners:
            try:
                markets = scanner.fetch_markets(limit=limit_per_platform)
                results[scanner.name] = markets
            except Exception as e:
                log.error(f"Error in {scanner.name}: {e}")
                results[scanner.name] = []
        
        self._cache = results
        self._cache_time = now
        return results
    
    def find_arbitrage_opportunities(self, min_spread: float = 0.03, polymarket_only: bool = True) -> List[Dict[str, Any]]:
        """
        Find arbitrage opportunities across platforms.
        Returns markets where the same question has different prices.
        
        Args:
            min_spread: Minimum price spread to consider (default 3%)
            polymarket_only: If True, only return opportunities where we BUY on Polymarket
                            (since that's the only platform we can actually trade on)
        """
        all_markets = self.fetch_all_markets()
        opportunities = []
        
        # Create a normalized index of all markets
        market_index: Dict[str, List[NormalizedMarket]] = {}
        
        for platform, markets in all_markets.items():
            for market in markets:
                # Normalize title for matching
                key = self._normalize_title(market.title)
                if key not in market_index:
                    market_index[key] = []
                market_index[key].append(market)
        
        # Find markets that appear on multiple platforms
        for key, markets in market_index.items():
            if len(markets) < 2:
                continue
            
            # Compare prices across platforms
            markets.sort(key=lambda m: m.yes_price)
            lowest = markets[0]
            highest = markets[-1]
            
            spread = highest.yes_price - lowest.yes_price
            
            if spread >= min_spread:
                opp = {
                    'title': lowest.title,
                    'spread': spread,
                    'spread_pct': spread * 100,
                    'buy_on': lowest.platform,
                    'buy_price': lowest.yes_price,
                    'buy_url': lowest.url,
                    'buy_slug': lowest.slug,
                    'sell_on': highest.platform,
                    'sell_price': highest.yes_price,
                    'sell_url': highest.url,
                    'markets': markets,
                    'actionable': lowest.platform == "Polymarket",  # Can we actually trade this?
                }
                
                # If polymarket_only, skip opportunities where we can't buy on Polymarket
                if polymarket_only and lowest.platform != "Polymarket":
                    log.debug(f"Skipping arb: buy on {lowest.platform}, not Polymarket | {lowest.title[:40]}")
                    continue
                
                opportunities.append(opp)
        
        # Sort by spread
        opportunities.sort(key=lambda x: x['spread'], reverse=True)
        
        actionable = len([o for o in opportunities if o.get('actionable', False)])
        log.info(f"Found {len(opportunities)} cross-platform arbitrage opportunities ({actionable} actionable on Polymarket)")
        return opportunities
    
    def get_polymarket_arbitrage_trades(self, min_spread: float = 0.05, min_confidence: int = 70) -> List[Dict[str, Any]]:
        """
        Get arbitrage opportunities that we can actually trade on Polymarket.
        
        Returns opportunities formatted for the trading system.
        Only returns trades where:
        1. Polymarket has the LOWEST price (we buy cheap)
        2. Another platform has a SIGNIFICANTLY higher price (validates our thesis)
        3. Spread is significant enough to profit after fees
        4. BOTH prices are in a "reasonable" range (not betting on impossible events)
        """
        opportunities = self.find_arbitrage_opportunities(min_spread=min_spread, polymarket_only=True)
        
        tradeable = []
        for opp in opportunities:
            if not opp.get('actionable'):
                continue
            
            pm_price = opp['buy_price']
            other_price = opp['sell_price']
            spread_pct = opp['spread_pct']
            
            # FILTER 1: Both prices must be in reasonable range
            # If PM is 1c and Kalshi is 5c, that's not arbitrage - both think it won't happen
            # Real arbitrage: PM at 40c, Kalshi at 55c (meaningful disagreement)
            if pm_price < 0.10:
                # Low price on PM - only valid if other platform thinks it's likely (>30%)
                if other_price < 0.30:
                    log.debug(f"Skipping arb: both prices too low | PM: {pm_price:.2%}, Other: {other_price:.2%}")
                    continue
            
            if pm_price > 0.90:
                # High price on PM - skip, not much upside
                log.debug(f"Skipping arb: PM price too high {pm_price:.2%}")
                continue
            
            # FILTER 2: The spread must represent genuine disagreement
            # At least one platform should think it's >25% likely
            if pm_price < 0.15 and other_price < 0.25:
                log.debug(f"Skipping arb: no platform thinks it's likely | {opp['title'][:30]}")
                continue
            
            # FILTER 3: Calculate confidence based on price levels AND spread
            # Higher confidence if both platforms have meaningful prices
            base_confidence = 60
            if pm_price >= 0.20 and other_price >= 0.35:
                base_confidence = 75  # Both platforms think it's possible
            elif pm_price >= 0.10 and other_price >= 0.25:
                base_confidence = 70
            
            confidence = min(95, base_confidence + int(spread_pct * 3))
            
            if confidence < min_confidence:
                continue
            
            # Format for trading system
            trade = {
                'question': opp['title'],
                'slug': opp['buy_slug'],
                'strategy': 'CROSS_PLATFORM_ARB',
                'yes': pm_price,
                'no': 1 - pm_price,
                'score': confidence,
                'spread': opp['spread'],
                'spread_pct': spread_pct,
                'suggested_side': 'YES',
                'buy_platform': opp['buy_on'],
                'reference_platform': opp['sell_on'],
                'reference_price': other_price,
                'expected_profit_pct': spread_pct,
                'reason': f"Cross-platform arb: PM {pm_price:.0%} vs {opp['sell_on']} {other_price:.0%} ({spread_pct:.0f}% edge)",
            }
            tradeable.append(trade)
        
        if tradeable:
            log.info(f"[CROSS-PLATFORM ARB] {len(tradeable)} valid opportunities (filtered for quality)")
            for t in tradeable[:3]:
                log.info(f"  {t['question'][:40]} | PM {t['yes']:.0%} vs {t['reference_platform']} {t['reference_price']:.0%}")
        
        return tradeable
    
    def _normalize_title(self, title: str) -> str:
        """Normalize title for matching across platforms."""
        title = title.lower()
        title = re.sub(r'[^\w\s]', '', title)
        # Remove common words
        stop_words = {'will', 'the', 'a', 'an', 'be', 'to', 'in', 'on', 'by', 'for', 'at', 'of'}
        words = [w for w in title.split() if w not in stop_words]
        return ' '.join(sorted(words))
    
    def get_platform_summary(self) -> Dict[str, Any]:
        """Get summary statistics for all platforms."""
        all_markets = self.fetch_all_markets()
        
        summary = {}
        for platform, markets in all_markets.items():
            if not markets:
                summary[platform] = {'status': 'error', 'count': 0}
                continue
            
            total_volume = sum(m.volume_24h for m in markets)
            avg_price = sum(m.yes_price for m in markets) / len(markets)
            
            summary[platform] = {
                'status': 'ok',
                'count': len(markets),
                'total_volume_24h': total_volume,
                'avg_yes_price': avg_price,
            }
        
        return summary


# Global instance
multi_scanner = MultiPlatformScanner()


if __name__ == "__main__":
    scanner = MultiPlatformScanner()
    
    print("Fetching markets from all platforms...")
    all_markets = scanner.fetch_all_markets()
    
    for platform, markets in all_markets.items():
        print(f"\n{platform}: {len(markets)} markets")
        for m in markets[:3]:
            print(f"  - {m.title[:50]}... @ {m.yes_price:.2f}")
    
    print("\n\nSearching for arbitrage opportunities...")
    opportunities = scanner.find_arbitrage_opportunities()
    
    for opp in opportunities[:5]:
        print(f"\n[{opp['spread_pct']:.1f}% spread] {opp['title'][:50]}")
        print(f"  Buy on {opp['buy_on']} @ {opp['buy_price']:.2f}")
        print(f"  Sell on {opp['sell_on']} @ {opp['sell_price']:.2f}")
