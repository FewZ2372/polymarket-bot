import requests
import json
import re
import datetime
from typing import Dict, List, Any

class SentimentAnalyzer:
    def __init__(self):
        # In a real scenario, we might use dedicated news APIs. 
        # For this implementation, we'll use a public search/RSS endpoint approach or simulate the scrape.
        # Since I have access to tools, I will design this to be usable by the bot.
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def fetch_recent_data(self, keyword: str) -> List[str]:
        """
        Obtiene noticias o posteos recientes relacionados con la keyword.
        Usa Google News RSS como una fuente pública y estable de 'noticias rápidas'.
        """
        encoded_keyword = requests.utils.quote(keyword)
        url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=en-US&gl=US&ceid=US:en"
        
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                # Extraer títulos simples usando regex para evitar dependencias pesadas de XML/BS4
                items = re.findall(r'<title>(.*?)</title>', response.text)
                # El primer título suele ser el del feed, lo saltamos
                return items[1:] if len(items) > 1 else []
        except Exception as e:
            print(f"Error fetching data: {e}")
        return []

    def classify_sentiment(self, text_list: List[str], keyword: str) -> str:
        """
        Clasifica el sentimiento basado en keywords de probabilidad/confirmación.
        BULLISH: Sí va a pasar / Probabilidad alta.
        BEARISH: No va a pasar / Negación.
        """
        if not text_list:
            return "NEUTRAL"

        bullish_indicators = [
            'likely', 'confirmed', 'expected', 'imminent', 'will', 'plans', 
            'escalates', 'rising', 'high chance', 'sources say yes', 'improving'
        ]
        bearish_indicators = [
            'unlikely', 'denied', 'canceled', 'rejected', 'falling', 'low chance', 
            'not happening', 'avoided', 'declines', 'delayed', 'no sign'
        ]

        score = 0
        combined_text = " ".join(text_list).lower()

        for word in bullish_indicators:
            score += combined_text.count(word)
        for word in bearish_indicators:
            score -= combined_text.count(word)

        if score > 2:
            return "BULLISH"
        elif score < -2:
            return "BEARISH"
        else:
            return "NEUTRAL"

    def calculate_buzz(self, text_list: List[str]) -> int:
        """
        Calcula el Buzz Score (0-100) basado en volumen.
        """
        count = len(text_list)
        # Normalización simple: 20 menciones recientes = 100 buzz
        score = min(count * 5, 100)
        return score

    def analyze(self, keyword: str) -> Dict[str, Any]:
        """
        Función principal para ser llamada por el scanner.
        """
        data = self.fetch_recent_data(keyword)
        sentiment = self.classify_sentiment(data, keyword)
        buzz = self.calculate_buzz(data)
        
        # Inefficiency weight: Bullish + High Buzz increases likelihood of 'Yes' outcome
        # Bearish + High Buzz increases likelihood of 'No' outcome
        inefficiency_modifier = 0
        if sentiment == "BULLISH":
            inefficiency_modifier = (buzz / 10)
        elif sentiment == "BEARISH":
            inefficiency_modifier = -(buzz / 10)

        return {
            "keyword": keyword,
            "sentiment": sentiment,
            "buzz_score": buzz,
            "mentions_found": len(data),
            "inefficiency_modifier": round(inefficiency_modifier, 2),
            "timestamp": datetime.datetime.now().isoformat()
        }

if __name__ == "__main__":
    import sys
    analyzer = SentimentAnalyzer()
    test_keyword = sys.argv[1] if len(sys.argv) > 1 else "US Shutdown"
    print(f"--- Analyzing Sentiment for: {test_keyword} ---")
    result = analyzer.analyze(test_keyword)
    print(json.dumps(result, indent=4))
