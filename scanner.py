"""
Market scanner for Polymarket and Kalshi arbitrage detection.
Refactored with proper logging and structure.
"""
import requests
import json
import time
import difflib
import re
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Set

from logger import log
from config import config


# Cache global para mercados de Kalshi
class KalshiCache:
    def __init__(self, ttl_seconds: int = 600):
        self.timestamp: float = 0
        self.markets: List[Dict] = []
        self.ttl = ttl_seconds
    
    def is_valid(self) -> bool:
        return (time.time() - self.timestamp) < self.ttl and bool(self.markets)
    
    def update(self, markets: List[Dict]):
        self.markets = markets
        self.timestamp = time.time()
    
    def get(self) -> List[Dict]:
        return self.markets if self.is_valid() else []


_kalshi_cache = KalshiCache()


def normalize_text(text: str) -> str:
    """
    Normaliza el texto para comparaciones: minúsculas, elimina puntuación
    y palabras de relleno comunes.
    """
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    stop_words = {
        'will', 'there', 'be', 'the', 'a', 'an', 'at', 'in', 'on', 
        'of', 'for', 'by', 'is', 'are', 'was', 'were', 'to', 'that', 
        'with', 'from', 'this', 'it'
    }
    words = text.split()
    filtered_words = [w for w in words if w not in stop_words]
    return " ".join(filtered_words)


def extract_keywords(text: str) -> Set[str]:
    """
    Extrae palabras clave significativas (entidades, nombres propios, términos técnicos).
    """
    if not text:
        return set()
    
    text = text.lower()
    high_value_entities = {
        'fed', 'interest', 'rate', 'shutdown', 'government', 
        'iran', 'israel', 'russia', 'ukraine', 'china', 
        'trump', 'biden', 'harris', 'musk', 'twitter', 'x', 
        'apple', 'google', 'fomc', 'debt', 'ceiling'
    }
    
    words = set(re.findall(r'\b\w{3,}\b', text))
    stop_words = {
        'will', 'there', 'the', 'and', 'with', 'before', 
        'after', 'during', 'should', 'could', 'would'
    }
    keywords = {w for w in words if w not in stop_words}
    
    return keywords


def fetch_kalshi_markets() -> List[Dict]:
    """
    Obtiene la lista de mercados abiertos de Kalshi.
    """
    if _kalshi_cache.is_valid():
        return _kalshi_cache.get()
    
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    params = {
        "status": "open",
        "limit": 1000
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            markets = data.get('markets', [])
            _kalshi_cache.update(markets)
            log.debug(f"Fetched {len(markets)} Kalshi markets")
            return markets
    except Exception as e:
        log.warning(f"Error fetching Kalshi markets: {e}")
    
    return []


def fetch_kalshi_price(poly_market: Dict) -> Tuple[Optional[float], Optional[str]]:
    """
    Busca un mercado equivalente en Kalshi y devuelve el precio de 'Yes'.
    Implementa Keyword Normalization y fallback por Categoría.
    """
    markets = fetch_kalshi_markets()
    if not markets:
        return None, None

    poly_question = poly_market.get('question', '')
    poly_category = poly_market.get('category', '').lower()
    
    poly_norm = normalize_text(poly_question)
    poly_keywords = extract_keywords(poly_question)
    
    best_match = None
    highest_score = 0.0

    # 1. Intento de Matching Inteligente por Palabras Clave y Similitud
    for market in markets:
        k_title = market.get('title', '')
        k_norm = normalize_text(k_title)
        k_keywords = extract_keywords(k_title)
        
        # Similitud de texto normalizado
        text_ratio = difflib.SequenceMatcher(None, poly_norm, k_norm).ratio()
        
        # Coincidencia de palabras clave
        keyword_score = 0
        if poly_keywords and k_keywords:
            common_keywords = poly_keywords.intersection(k_keywords)
            keyword_score = len(common_keywords) / max(len(poly_keywords), 1)
            
        # Score combinado (60% similitud de texto, 40% keywords)
        combined_score = (text_ratio * 0.6) + (keyword_score * 0.4)
        
        if combined_score > highest_score:
            highest_score = combined_score
            best_match = market

    if highest_score > 0.4 and best_match:
        yes_price = best_match.get('yes_ask')
        if yes_price is not None:
            return yes_price / 100.0, best_match.get('title')

    # 2. Fallback por Categoría (Politics / Economics)
    if poly_category in ['politics', 'economy', 'business'] or \
       any(kw in poly_norm for kw in ['fed', 'interest', 'shutdown']):
        category_matches = []
        for market in markets:
            k_subtitle = market.get('subtitle', '').lower()
            k_category = market.get('category', '').lower()
            k_title = market.get('title', '').lower()
            
            match_cat = poly_category in k_subtitle or \
                       poly_category in k_category or \
                       poly_category in k_title
            
            if not match_cat:
                if 'fed' in poly_norm and ('fed' in k_title or 'reserve' in k_title):
                    match_cat = True
                if 'shutdown' in poly_norm and 'shutdown' in k_title:
                    match_cat = True

            if match_cat:
                k_keywords = extract_keywords(k_title)
                common_count = len(poly_keywords.intersection(k_keywords))
                if common_count >= 1:
                    category_matches.append((common_count, market))
        
        if category_matches:
            category_matches.sort(key=lambda x: x[0], reverse=True)
            best_cat_match = category_matches[0][1]
            yes_price = best_cat_match.get('yes_ask')
            if yes_price is not None:
                return yes_price / 100.0, best_cat_match.get('title')

    return None, None


def calculate_inefficiency_score(
    market: Dict, 
    spread_pct: float = 0,
    sentiment_data: Optional[Dict] = None
) -> int:
    """
    Calcula el Inefficiency Score (0-100).
    
    Lógica:
    1. Volumen alto (normalizado).
    2. Baja volatilidad reciente (< 2% en 2h).
    3. Categorías técnicas (Política, Justicia, Ciencia).
    4. SPREAD KALSHI: Si hay spread > 5% entre plataformas, el score es 100.
    """
    # Condición de oro: Ineficiencia cross-platform
    if spread_pct > 0.05:
        return 100

    score = 0
    
    # 1. Factor Volumen (0-40 puntos)
    vol24h = float(market.get('volume24hr', 0))
    if vol24h > 1_000_000:
        score += 40
    elif vol24h > 100_000:
        score += 20
    elif vol24h > 10_000:
        score += 10

    # 2. Factor Volatilidad Reciente (0-40 puntos)
    price_change = abs(float(market.get('priceChange24h', 0)))
    if price_change < 0.02:
        score += 40
    elif price_change < 0.05:
        score += 20

    # 3. Factor Categoría (0-20 puntos)
    category = market.get('category', '').lower()
    technical_categories = [
        'politics', 'crypto', 'science', 'law', 
        'justice', 'business', 'economy', 'world'
    ]
    noise_categories = ['sports', 'entertainment', 'pop culture']
    
    question = market.get('question', '').lower()
    
    is_technical = any(tech in category for tech in technical_categories) or \
                   any(tech in question for tech in ['fed', 'interest rate', 'shutdown', 'government', 'acquired'])
    
    is_noise = any(noise in category for noise in noise_categories) or \
               any(noise in question for noise in ['nfl', 'nba', 'spread', 'seahawks', 'score'])

    if is_technical:
        score += 20
        # Integración de sentimiento si está disponible
        if sentiment_data:
            sentiment_bonus = abs(sentiment_data.get('inefficiency_modifier', 0))
            score += int(sentiment_bonus)
    
    if is_noise:
        score -= 20 
    
    return max(0, min(100, score))


def get_top_markets(limit: int = 15, sentiment_analyzer=None) -> List[Dict[str, Any]]:
    """
    Obtiene los top mercados basados en el Inefficiency Score.
    Retorna una lista de diccionarios con la información del mercado.
    """
    url = "https://gamma-api.polymarket.com/markets"
    params = {
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
        "limit": 50 
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        markets = response.json()
        
        analyzed_markets = []
        fetch_kalshi_markets()  # Pre-cache
        
        for market in markets:
            # Parse prices
            prices_str = market.get('outcomePrices', '[]')
            try:
                prices = json.loads(prices_str)
                yes_price = float(prices[0]) if len(prices) > 0 else 0
                no_price = float(prices[1]) if len(prices) > 1 else 0
            except:
                yes_price = 0
                no_price = 0
            
            # Get Kalshi comparison
            k_yes, k_title = fetch_kalshi_price(market)
            spread = abs(yes_price - k_yes) if k_yes and yes_price > 0 else 0
            
            # Get sentiment if analyzer provided
            sentiment_data = None
            if sentiment_analyzer:
                try:
                    question = market.get('question', '')
                    keywords = list(extract_keywords(question))
                    if keywords:
                        tech_keywords = [
                            kw for kw in keywords 
                            if kw in ['fed', 'interest', 'shutdown', 'government', 
                                     'iran', 'israel', 'russia', 'ukraine', 'china']
                        ]
                        target_kw = tech_keywords[0] if tech_keywords else keywords[0]
                        sentiment_data = sentiment_analyzer.analyze(target_kw)
                except Exception as e:
                    log.debug(f"Sentiment analysis failed: {e}")
            
            # Calculate score
            score = calculate_inefficiency_score(market, spread_pct=spread, sentiment_data=sentiment_data)
            
            analyzed_markets.append({
                'question': market.get('question', 'N/A'),
                'slug': market.get('slug', ''),
                'condition_id': market.get('conditionId'),
                'token_id': market.get('clobTokenIds', [None])[0] if market.get('clobTokenIds') else None,
                'vol24h': float(market.get('volume24hr', 0)),
                'yes': yes_price,
                'no': no_price,
                'score': score,
                'category': market.get('category', 'Other'),
                'k_yes': k_yes,
                'k_title': k_title,
                'spread': spread,
                'sentiment': sentiment_data.get('sentiment') if sentiment_data else None,
                'buzz_score': sentiment_data.get('buzz_score') if sentiment_data else None,
            })

        analyzed_markets.sort(key=lambda x: x['score'], reverse=True)
        return analyzed_markets[:limit]
            
    except Exception as e:
        log.error(f"Error fetching top markets: {e}")
        return []


def print_market_report(markets: List[Dict[str, Any]]):
    """Print a formatted market report to the console."""
    print(f"\n{'='*90}")
    print(f" POLYMARKET SCANNER REPORT")
    print(f" Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*90}\n")
    
    print(f"{'SCORE':<7} | {'MARKET':<45} | {'POLY':<6} | {'KALSHI':<6} | {'SPREAD':<6}")
    print("-" * 90)
    
    for m in markets[:15]: 
        k_val = f"{m['k_yes']:.2f}" if m['k_yes'] else "N/A"
        s_val = f"{m['spread']*100:.1f}%" if m['k_yes'] else "N/A"
        
        print(f"{m['score']:<7} | {m['question'][:43]:<45} | {m['yes']:<6.2f} | {k_val:<6} | {s_val:<6}")

    print(f"\n{'='*90}")
    print(" Score 100 = Spread > 5%. Otherwise: High Volume + Low Volatility + Technical Category")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    from sentiment_analyzer import SentimentAnalyzer
    analyzer = SentimentAnalyzer()
    markets = get_top_markets(limit=50, sentiment_analyzer=analyzer)
    print_market_report(markets)
