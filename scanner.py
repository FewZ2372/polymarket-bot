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


def calculate_movement_potential(market: Dict, days_to_resolution: float, yes_price: float) -> float:
    """
    Calcula el potencial de movimiento de precio.
    
    Factores que aumentan movimiento:
    1. Cercanía a resolución (más cerca = movimientos más bruscos)
    2. Precio cerca de 50% (más incertidumbre = más potencial)
    3. Volatilidad reciente alta (mercado activo)
    4. Tipo de mercado (deportes mueven rápido, política lento)
    
    Returns: 0-100 score de potencial de movimiento
    """
    score = 0
    
    # 1. Cercanía a resolución (0-35 puntos)
    # Más cerca = mejor para swing trading
    if days_to_resolution <= 1:
        score += 35  # Hoy/mañana - movimiento inminente
    elif days_to_resolution <= 3:
        score += 30  # Esta semana
    elif days_to_resolution <= 7:
        score += 25
    elif days_to_resolution <= 14:
        score += 20
    elif days_to_resolution <= 30:
        score += 15
    elif days_to_resolution <= 60:
        score += 5
    # > 60 días = 0 puntos (muy lejos)
    
    # 2. Precio cerca de 50% = más potencial de swing (0-30 puntos)
    # Precios extremos (5% o 95%) tienen poco upside
    distance_from_50 = abs(yes_price - 0.5)
    if distance_from_50 <= 0.15:  # 35%-65%
        score += 30  # Máxima incertidumbre
    elif distance_from_50 <= 0.25:  # 25%-75%
        score += 20
    elif distance_from_50 <= 0.35:  # 15%-85%
        score += 10
    # > 85% o < 15% = poco potencial de movimiento significativo
    
    # 3. Volatilidad reciente (0-20 puntos)
    # Si ya se está moviendo, puede seguir moviéndose
    price_change_24h = abs(float(market.get('oneDayPriceChange', 0) or 0))
    if price_change_24h > 0.10:
        score += 20  # >10% en 24h = muy activo
    elif price_change_24h > 0.05:
        score += 15
    elif price_change_24h > 0.02:
        score += 10
    elif price_change_24h > 0.01:
        score += 5
    
    # 4. Tipo de mercado (0-15 puntos)
    question = market.get('question', '').lower()
    category = market.get('category', '').lower()
    
    # Deportes = resolución rápida y clara
    if any(sport in question or sport in category for sport in ['nba', 'nfl', 'nhl', 'mlb', 'soccer', 'football', 'basketball']):
        score += 15
    # Crypto = volátil
    elif any(crypto in question for crypto in ['bitcoin', 'btc', 'ethereum', 'eth', 'crypto']):
        score += 12
    # Eventos con fecha fija
    elif any(event in question for event in ['earnings', 'report', 'announce', 'release']):
        score += 10
    # Política = movimientos en eventos específicos
    elif any(pol in question for pol in ['trump', 'biden', 'election', 'vote']):
        score += 8
    
    return min(100, score)


def get_top_markets(
    limit: int = 75, 
    sentiment_analyzer=None,
    max_days_to_resolution: int = 30,  # NEW: Filter by resolution date
    include_all_timeframes: bool = False,  # NEW: Override filter for analysis
) -> List[Dict[str, Any]]:
    """
    Obtiene los top mercados priorizando:
    1. Mercados de CORTO PLAZO (< 30 días por defecto)
    2. Alto potencial de movimiento
    3. Volumen suficiente para liquidez
    
    Cambios vs versión anterior:
    - Descarga más mercados (500 vs 200)
    - FILTRA por fecha de resolución primero
    - Ordena por potencial de movimiento, no solo volumen
    """
    url = "https://gamma-api.polymarket.com/markets"
    
    # Fetch more markets to have better selection after filtering
    params = {
        "active": "true",
        "closed": "false",
        "limit": 500  # Increased from 200
    }
    
    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        markets = response.json()
        
        log.info(f"Fetched {len(markets)} raw markets from Polymarket")
        
        analyzed_markets = []
        fetch_kalshi_markets()  # Pre-cache
        
        now = datetime.now()
        short_term_count = 0
        filtered_count = 0
        
        for market in markets:
            # Parse end date FIRST - this is the key filter
            end_date = None
            days_to_resolution = 999
            
            if market.get('endDate'):
                try:
                    end_date_str = market['endDate'].replace('Z', '+00:00')
                    end_date = datetime.fromisoformat(end_date_str)
                    if end_date.tzinfo is not None:
                        end_date = end_date.replace(tzinfo=None)
                    days_to_resolution = (end_date - now).total_seconds() / 86400
                except Exception:
                    pass
            
            # FILTER: Skip markets that resolve too far in the future
            if not include_all_timeframes:
                if days_to_resolution > max_days_to_resolution:
                    filtered_count += 1
                    continue
                if days_to_resolution < 0:  # Already resolved
                    filtered_count += 1
                    continue
            
            short_term_count += 1
            
            # Parse prices
            prices_str = market.get('outcomePrices', '[]')
            try:
                prices = json.loads(prices_str)
                yes_price = float(prices[0]) if len(prices) > 0 else 0
                no_price = float(prices[1]) if len(prices) > 1 else 0
            except:
                yes_price = 0
                no_price = 0
            
            # Skip invalid prices
            if yes_price <= 0 or yes_price >= 1:
                continue
            
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
            
            # Calculate scores
            inefficiency_score = calculate_inefficiency_score(market, spread_pct=spread, sentiment_data=sentiment_data)
            movement_potential = calculate_movement_potential(market, days_to_resolution, yes_price)
            
            # Combined score: 50% inefficiency + 50% movement potential
            # This balances "good opportunity" with "will actually move"
            combined_score = int(inefficiency_score * 0.5 + movement_potential * 0.5)
            
            # Bonus for very short-term markets (< 7 days)
            if days_to_resolution <= 7:
                combined_score = min(100, combined_score + 10)
            
            # Get token_id safely
            clob_ids = market.get('clobTokenIds') or []
            token_id = clob_ids[0] if clob_ids else None
            
            # Volume for liquidity check
            volume = float(market.get('volume24hr', 0) or market.get('volume', 0) or 0)
            
            analyzed_markets.append({
                'question': market.get('question', 'N/A'),
                'slug': market.get('slug', ''),
                'condition_id': market.get('conditionId'),
                'token_id': token_id,
                'vol24h': volume,
                'yes': yes_price,
                'no': no_price,
                'score': combined_score,  # NEW: Combined score
                'inefficiency_score': inefficiency_score,
                'movement_potential': movement_potential,
                'days_to_resolution': days_to_resolution,
                'category': market.get('category', 'Other'),
                'k_yes': k_yes,
                'k_title': k_title,
                'spread': spread,
                'sentiment': sentiment_data.get('sentiment') if sentiment_data else None,
                'buzz_score': sentiment_data.get('buzz_score') if sentiment_data else None,
                'end_date': end_date,
                'oneHourPriceChange': float(market.get('oneHourPriceChange', 0) or 0),
                'oneDayPriceChange': float(market.get('oneDayPriceChange', 0) or 0),
            })

        # Sort by combined score (which includes movement potential)
        analyzed_markets.sort(key=lambda x: x['score'], reverse=True)
        
        log.info(f"After filtering: {len(analyzed_markets)} markets (filtered {filtered_count} long-term)")
        log.info(f"Short-term markets (<{max_days_to_resolution}d): {short_term_count}")
        
        return analyzed_markets[:limit]
            
    except Exception as e:
        log.error(f"Error fetching top markets: {e}")
        return []


def print_market_report(markets: List[Dict[str, Any]]):
    """Print a formatted market report to the console."""
    print(f"\n{'='*100}")
    print(f" POLYMARKET SCANNER REPORT - Short Term Focus")
    print(f" Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*100}\n")
    
    print(f"{'SCORE':<6} | {'DAYS':<5} | {'MARKET':<40} | {'PRICE':<6} | {'MOVE%':<6} | {'VOL':<10}")
    print("-" * 100)
    
    for m in markets[:20]: 
        days = m.get('days_to_resolution', 999)
        days_str = f"{days:.0f}d" if days < 999 else "N/A"
        movement = m.get('movement_potential', 0)
        vol = m.get('vol24h', 0)
        vol_str = f"${vol/1000:.0f}k" if vol >= 1000 else f"${vol:.0f}"
        
        print(f"{m['score']:<6} | {days_str:<5} | {m['question'][:38]:<40} | {m['yes']:<5.0%} | {movement:<5} | {vol_str:<10}")

    print(f"\n{'='*100}")
    print(" SCORE = 50% Inefficiency + 50% Movement Potential + Bonus for <7 days")
    print(" Movement factors: Days to resolution, Price near 50%, Volatility, Market type")
    print(f"{'='*100}\n")


if __name__ == "__main__":
    from sentiment_analyzer import SentimentAnalyzer
    analyzer = SentimentAnalyzer()
    markets = get_top_markets(limit=50, sentiment_analyzer=analyzer)
    print_market_report(markets)
