import requests
import json
from datetime import datetime, timedelta
import time
import difflib
import re
from sentiment_analyzer import SentimentAnalyzer

# Instanciar el analizador de sentimiento
sentiment_analyzer = SentimentAnalyzer()

# Cache global para mercados de Kalshi para evitar hits excesivos a la API
KALSHI_CACHE = {
    'timestamp': 0,
    'markets': []
}

def normalize_text(text):
    """
    Normaliza el texto para comparaciones: minúsculas, elimina puntuación
    y palabras de relleno comunes.
    """
    if not text:
        return ""
    # A minúsculas
    text = text.lower()
    # Eliminar puntuación
    text = re.sub(r'[^\w\s]', '', text)
    # Palabras de relleno (stop words) para mayor 'creatividad'
    stop_words = {'will', 'there', 'be', 'the', 'a', 'an', 'at', 'in', 'on', 'of', 'for', 'by', 'is', 'are', 'was', 'were', 'to', 'that', 'with', 'from', 'this', 'it'}
    words = text.split()
    filtered_words = [w for w in words if w not in stop_words]
    return " ".join(filtered_words)

def extract_keywords(text):
    """
    Extrae palabras clave significativas (entidades, nombres propios, términos técnicos).
    """
    if not text:
        return set()
    text = text.lower()
    # Palabras clave de alto valor
    high_value_entities = ['fed', 'interest', 'rate', 'shutdown', 'government', 'iran', 'israel', 'russia', 'ukraine', 'china', 'trump', 'biden', 'harris', 'musk', 'twitter', 'x', 'apple', 'google', 'fed', 'fomc', 'debt', 'ceiling']
    
    words = set(re.findall(r'\b\w{3,}\b', text)) # Palabras de al menos 3 letras
    
    # Filtrar palabras comunes pero mantener las de alto valor
    stop_words = {'will', 'there', 'the', 'and', 'with', 'before', 'after', 'during', 'should', 'could', 'would'}
    keywords = {w for w in words if w not in stop_words}
    
    return keywords

def fetch_kalshi_markets():
    """
    Obtiene la lista de mercados abiertos de Kalshi.
    """
    global KALSHI_CACHE
    now = time.time()
    
    # Cache por 10 minutos
    if now - KALSHI_CACHE['timestamp'] < 600 and KALSHI_CACHE['markets']:
        return KALSHI_CACHE['markets']
    
    url = "https://api.elections.kalshi.com/trade-api/v2/markets"
    params = {
        "status": "open",
        "limit": 1000
    }
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            KALSHI_CACHE['markets'] = data.get('markets', [])
            KALSHI_CACHE['timestamp'] = now
            return KALSHI_CACHE['markets']
    except Exception as e:
        print(f"Error fetching Kalshi markets: {e}")
    
    return []

def fetch_kalshi_price(poly_market):
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

    if highest_score > 0.4:
        yes_price = best_match.get('yes_ask')
        if yes_price is not None:
            return yes_price / 100.0, best_match.get('title')

    # 2. Fallback por Categoría (Politics / Economics)
    if poly_category in ['politics', 'economy', 'business'] or any(kw in poly_norm for kw in ['fed', 'interest', 'shutdown']):
        category_matches = []
        for market in markets:
            k_subtitle = market.get('subtitle', '').lower()
            k_category = market.get('category', '').lower()
            k_title = market.get('title', '').lower()
            
            # Kalshi usa a veces el subtitle para la categoría real
            match_cat = poly_category in k_subtitle or poly_category in k_category or poly_category in k_title
            
            # O si contiene keywords técnicas críticas
            if not match_cat:
                if 'fed' in poly_norm and ('fed' in k_title or 'reserve' in k_title):
                    match_cat = True
                if 'shutdown' in poly_norm and 'shutdown' in k_title:
                    match_cat = True

            if match_cat:
                k_keywords = extract_keywords(k_title)
                common_count = len(poly_keywords.intersection(k_keywords))
                if common_count >= 1: # Al menos una entidad compartida
                    category_matches.append((common_count, market))
        
        if category_matches:
            category_matches.sort(key=lambda x: x[0], reverse=True)
            best_cat_match = category_matches[0][1]
            yes_price = best_cat_match.get('yes_ask')
            if yes_price is not None:
                return yes_price / 100.0, best_cat_match.get('title')

    return None, None

def calculate_inefficiency_score(market, spread_pct=0):
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
    if price_change < 0.02: # Menos del 2%
        score += 40
    elif price_change < 0.05: # Menos del 5%
        score += 20

    # 3. Factor Categoría (0-20 puntos)
    category = market.get('category', '').lower()
    technical_categories = ['politics', 'crypto', 'science', 'law', 'justice', 'business', 'economy', 'world']
    noise_categories = ['sports', 'entertainment', 'pop culture']
    
    question = market.get('question', '').lower()
    
    is_technical = any(tech in category for tech in technical_categories) or \
                   any(tech in question for tech in ['fed', 'interest rate', 'shutdown', 'government', 'acquired'])
    
    is_noise = any(noise in category for noise in noise_categories) or \
               any(noise in question for noise in ['nfl', 'nba', 'spread', 'seahawks', 'score'])

    if is_technical:
        score += 20
        # Integración de sentimiento para mercados técnicos
        try:
            # Usamos la primera keyword significativa del mercado para el análisis
            keywords = list(extract_keywords(question))
            if keywords:
                # Priorizar keywords técnicas si existen
                tech_keywords = [kw for kw in keywords if kw in ['fed', 'interest', 'shutdown', 'government', 'iran', 'israel', 'russia', 'ukraine', 'china']]
                target_kw = tech_keywords[0] if tech_keywords else keywords[0]
                
                sentiment_data = sentiment_analyzer.analyze(target_kw)
                # Sumar el modificador de sentimiento al score (ej: +10 si es muy bullish/popular)
                sentiment_bonus = abs(sentiment_data.get('inefficiency_modifier', 0))
                score += sentiment_bonus
        except Exception as e:
            # No bloqueamos el scanner si falla el sentimiento
            pass
    
    if is_noise:
        score -= 20 
    
    return max(0, min(100, score))

def get_top_markets(limit=15):
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
        response = requests.get(url, params=params)
        response.raise_for_status()
        markets = response.json()
        
        analyzed_markets = []
        fetch_kalshi_markets()
        
        for market in markets:
            prices_str = market.get('outcomePrices', '[]')
            try:
                prices = json.loads(prices_str)
                yes_price = float(prices[0]) if len(prices) > 0 else 0
                no_price = float(prices[1]) if len(prices) > 1 else 0
            except:
                yes_price = 0
                no_price = 0
            
            k_yes, k_title = fetch_kalshi_price(market)
            spread = 0
            if k_yes and yes_price > 0:
                spread = abs(yes_price - k_yes)
            
            score = calculate_inefficiency_score(market, spread_pct=spread)
            
            analyzed_markets.append({
                'question': market.get('question', 'N/A'),
                'vol24h': float(market.get('volume24hr', 0)),
                'yes': yes_price,
                'no': no_price,
                'score': score,
                'category': market.get('category', 'Other'),
                'k_yes': k_yes,
                'spread': spread
            })

        analyzed_markets.sort(key=lambda x: x['score'], reverse=True)
        return analyzed_markets[:limit]
            
    except Exception as e:
        print(f"Error fetching top markets: {e}")
        return []

def fetch_and_analyze_markets():
    analyzed_markets = get_top_markets(limit=50)
    
    if not analyzed_markets:
        return
        
    print(f"\n{'='*85}")
    print(f" REPORT: TOP OPPORTUNITIES (Polymarket vs Kalshi Analysis)")
    print(f" Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*85}\n")
    
    print(f"{'SCORE':<7} | {'MARKET':<40} | {'POLY':<6} | {'KALSHI':<6} | {'SPREAD':<6}")
    print("-" * 85)
    
    for m in analyzed_markets[:15]: 
        k_val = f"{m['k_yes']:.2f}" if m['k_yes'] else "N/A"
        s_val = f"{m['spread']*100:.1f}%" if m['k_yes'] else "N/A"
        
        print(f"{m['score']:<7} | {m['question'][:38]:<40} | {m['yes']:<6.2f} | {k_val:<6} | {s_val:<6}")

    print(f"\n{'='*85}")
    print(" Logic: Score 100 if Spread > 5%. High Volume + Low Volatility otherwise.")
    print(f"{'='*85}\n")

if __name__ == "__main__":
    fetch_and_analyze_markets()
