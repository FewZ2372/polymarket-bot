import requests
import feedparser
import urllib.parse

def get_news_by_keyword(keyword):
    """
    Obtiene noticias desde Google News RSS filtradas por una palabra clave.
    """
    encoded_keyword = urllib.parse.quote(keyword)
    url = f"https://news.google.com/rss/search?q={encoded_keyword}&hl=en-US&gl=US&ceid=US:en"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        results = []
        for entry in feed.entries[:10]:  # Tomamos las 10 más recientes
            relevance = calculate_relevance(entry.title)
            results.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.published,
                'relevance_level': relevance
            })
        
        return results
    except Exception as e:
        print(f"Error fetching news: {e}")
        return []

def calculate_relevance(title):
    """
    Lógica simple de relevancia basada en palabras de acción fuertes.
    """
    strong_words = [
        'emergency', 'shutdown', 'crisis', 'urgent', 'breaking', 
        'war', 'attack', 'crash', 'collapse', 'ban', 'sanction', 
        'legal', 'sue', 'lawsuit', 'investigation', 'arrest'
    ]
    
    title_lower = title.lower()
    score = 1  # Nivel base
    
    for word in strong_words:
        if word in title_lower:
            score += 1
            
    return score

if __name__ == "__main__":
    keyword = "shutdown"
    print(f"--- Buscando noticias sobre: {keyword} ---\n")
    news = get_news_by_keyword(keyword)
    
    if not news:
        print("No se encontraron noticias.")
    else:
        for item in news:
            print(f"[{item['relevance_level']}] {item['title']}")
            print(f"URL: {item['link']}")
            print("-" * 20)
