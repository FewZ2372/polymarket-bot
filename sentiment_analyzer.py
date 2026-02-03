"""
Sentiment analyzer for market-related news.
Uses Google News RSS to gauge market sentiment.
"""
import requests
import json
import re
import datetime
from typing import Dict, List, Any

from logger import log


class SentimentAnalyzer:
    """
    Analyzes sentiment from news sources to inform trading decisions.
    """
    
    BULLISH_INDICATORS = [
        'likely', 'confirmed', 'expected', 'imminent', 'will', 'plans', 
        'escalates', 'rising', 'high chance', 'sources say yes', 'improving',
        'approved', 'passes', 'wins', 'succeeds', 'agrees', 'deal'
    ]
    
    BEARISH_INDICATORS = [
        'unlikely', 'denied', 'canceled', 'rejected', 'falling', 'low chance', 
        'not happening', 'avoided', 'declines', 'delayed', 'no sign',
        'fails', 'loses', 'blocked', 'vetoed', 'opposed'
    ]
    
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = 300  # 5 minutes

    def fetch_recent_data(self, keyword: str) -> List[str]:
        """
        Obtiene noticias o posteos recientes relacionados con la keyword.
        Usa Google News RSS como fuente pública.
        """
        encoded_keyword = requests.utils.quote(keyword)
        url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=en-US&gl=US&ceid=US:en"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                # Extraer títulos usando regex
                items = re.findall(r'<title>(.*?)</title>', response.text)
                return items[1:] if len(items) > 1 else []
        except Exception as e:
            log.debug(f"Error fetching news for '{keyword}': {e}")
        return []

    def classify_sentiment(self, text_list: List[str], keyword: str) -> str:
        """
        Clasifica el sentimiento basado en keywords de probabilidad/confirmación.
        """
        if not text_list:
            return "NEUTRAL"

        score = 0
        combined_text = " ".join(text_list).lower()

        for word in self.BULLISH_INDICATORS:
            score += combined_text.count(word)
        for word in self.BEARISH_INDICATORS:
            score -= combined_text.count(word)

        if score > 2:
            return "BULLISH"
        elif score < -2:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def calculate_buzz(self, text_list: List[str]) -> int:
        """
        Calcula el Buzz Score (0-100) basado en volumen de menciones.
        """
        count = len(text_list)
        # Normalización: 20 menciones = 100 buzz
        return min(count * 5, 100)

    def analyze(self, keyword: str) -> Dict[str, Any]:
        """
        Función principal para ser llamada por el scanner.
        Incluye caché para evitar hits excesivos a Google News.
        """
        # Check cache
        cache_key = keyword.lower()
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            # Use total_seconds() to get the correct time difference
            age = (datetime.datetime.now() - datetime.datetime.fromisoformat(cached['timestamp'])).total_seconds()
            if age < self._cache_ttl:
                return cached
        
        data = self.fetch_recent_data(keyword)
        sentiment = self.classify_sentiment(data, keyword)
        buzz = self.calculate_buzz(data)
        
        # Inefficiency weight
        inefficiency_modifier = 0
        if sentiment == "BULLISH":
            inefficiency_modifier = (buzz / 10)
        elif sentiment == "BEARISH":
            inefficiency_modifier = -(buzz / 10)

        result = {
            "keyword": keyword,
            "sentiment": sentiment,
            "buzz_score": buzz,
            "mentions_found": len(data),
            "inefficiency_modifier": round(inefficiency_modifier, 2),
            "timestamp": datetime.datetime.now().isoformat()
        }
        
        # Update cache
        self._cache[cache_key] = result
        
        return result


if __name__ == "__main__":
    import sys
    analyzer = SentimentAnalyzer()
    test_keyword = sys.argv[1] if len(sys.argv) > 1 else "US Shutdown"
    print(f"--- Analyzing Sentiment for: {test_keyword} ---")
    result = analyzer.analyze(test_keyword)
    print(json.dumps(result, indent=4))
