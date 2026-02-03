"""
Social Sentiment Analyzer - Analyzes social media for market sentiment.
Uses public data sources since API access may be limited.
"""
import requests
import re
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from logger import log


@dataclass
class SocialMention:
    """A social media mention related to a market."""
    source: str  # 'twitter', 'reddit', 'news'
    text: str
    sentiment: str  # 'BULLISH', 'BEARISH', 'NEUTRAL'
    engagement: int  # likes, upvotes, etc.
    url: str
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SocialSignal:
    """Aggregated social sentiment signal."""
    keyword: str
    total_mentions: int
    bullish_pct: float
    bearish_pct: float
    neutral_pct: float
    buzz_score: int  # 0-100
    sentiment_score: float  # -1 to 1
    trending: bool
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'keyword': self.keyword,
            'mentions': self.total_mentions,
            'bullish_pct': self.bullish_pct,
            'bearish_pct': self.bearish_pct,
            'buzz_score': self.buzz_score,
            'sentiment_score': self.sentiment_score,
            'trending': self.trending,
            'timestamp': self.timestamp.isoformat(),
        }


class SocialSentimentAnalyzer:
    """
    Analyzes social media sentiment for prediction market topics.
    """
    
    # Sentiment keywords
    BULLISH_WORDS = [
        'bullish', 'moon', 'pump', 'buy', 'long', 'undervalued', 'opportunity',
        'confirmed', 'likely', 'yes', 'will happen', 'imminent', 'certain',
        'guaranteed', 'obvious', 'easy money', 'no brainer', 'slam dunk'
    ]
    
    BEARISH_WORDS = [
        'bearish', 'dump', 'sell', 'short', 'overvalued', 'scam', 'no chance',
        'unlikely', 'no', 'wont happen', 'impossible', 'doubt', 'skeptical',
        'fake', 'manipulation', 'trap', 'avoid'
    ]
    
    def __init__(self):
        self._cache: Dict[str, SocialSignal] = {}
        self._cache_ttl = 300  # 5 minutes
        self._mentions: List[SocialMention] = []
    
    def analyze_topic(self, keyword: str) -> SocialSignal:
        """Analyze social sentiment for a topic/keyword."""
        # Check cache
        if keyword in self._cache:
            cached = self._cache[keyword]
            # Use total_seconds() to get the correct time difference
            if (datetime.now() - cached.timestamp).total_seconds() < self._cache_ttl:
                return cached
        
        mentions = []
        
        # Fetch from multiple sources
        mentions.extend(self._fetch_google_news(keyword))
        mentions.extend(self._fetch_reddit_public(keyword))
        
        # Analyze sentiment
        signal = self._compute_signal(keyword, mentions)
        
        self._cache[keyword] = signal
        return signal
    
    def _fetch_google_news(self, keyword: str) -> List[SocialMention]:
        """Fetch mentions from Google News RSS."""
        mentions = []
        
        try:
            encoded = requests.utils.quote(keyword)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                return mentions
            
            titles = re.findall(r'<title>(.*?)</title>', response.text)
            links = re.findall(r'<link>(.*?)</link>', response.text)
            
            for i, title in enumerate(titles[1:11]):  # Skip feed title, get 10
                sentiment = self._classify_text_sentiment(title)
                mentions.append(SocialMention(
                    source='news',
                    text=title,
                    sentiment=sentiment,
                    engagement=10,  # News articles get base engagement
                    url=links[i+1] if i+1 < len(links) else '',
                ))
                
        except Exception as e:
            log.debug(f"Error fetching news for '{keyword}': {e}")
        
        return mentions
    
    def _fetch_reddit_public(self, keyword: str) -> List[SocialMention]:
        """Fetch mentions from Reddit's public JSON API."""
        mentions = []
        
        try:
            # Reddit's public search API
            encoded = requests.utils.quote(keyword)
            url = f"https://www.reddit.com/search.json?q={encoded}&sort=hot&limit=10"
            
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; MarketBot/1.0)'}
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code != 200:
                return mentions
            
            data = response.json()
            posts = data.get('data', {}).get('children', [])
            
            for post in posts:
                post_data = post.get('data', {})
                title = post_data.get('title', '')
                selftext = post_data.get('selftext', '')
                
                combined_text = f"{title} {selftext}"
                sentiment = self._classify_text_sentiment(combined_text)
                
                mentions.append(SocialMention(
                    source='reddit',
                    text=title,
                    sentiment=sentiment,
                    engagement=post_data.get('score', 0) + post_data.get('num_comments', 0),
                    url=f"https://reddit.com{post_data.get('permalink', '')}",
                ))
                
        except Exception as e:
            log.debug(f"Error fetching Reddit for '{keyword}': {e}")
        
        return mentions
    
    def _classify_text_sentiment(self, text: str) -> str:
        """Classify text sentiment using keyword matching."""
        text_lower = text.lower()
        
        bullish_count = sum(1 for word in self.BULLISH_WORDS if word in text_lower)
        bearish_count = sum(1 for word in self.BEARISH_WORDS if word in text_lower)
        
        if bullish_count > bearish_count + 1:
            return 'BULLISH'
        elif bearish_count > bullish_count + 1:
            return 'BEARISH'
        else:
            return 'NEUTRAL'
    
    def _compute_signal(self, keyword: str, mentions: List[SocialMention]) -> SocialSignal:
        """Compute aggregated signal from mentions."""
        if not mentions:
            return SocialSignal(
                keyword=keyword,
                total_mentions=0,
                bullish_pct=0,
                bearish_pct=0,
                neutral_pct=100,
                buzz_score=0,
                sentiment_score=0,
                trending=False,
            )
        
        bullish = [m for m in mentions if m.sentiment == 'BULLISH']
        bearish = [m for m in mentions if m.sentiment == 'BEARISH']
        neutral = [m for m in mentions if m.sentiment == 'NEUTRAL']
        
        total = len(mentions)
        bullish_pct = len(bullish) / total * 100
        bearish_pct = len(bearish) / total * 100
        neutral_pct = len(neutral) / total * 100
        
        # Calculate sentiment score (-1 to 1)
        # Weight by engagement
        total_engagement = sum(m.engagement for m in mentions) or 1
        weighted_sentiment = 0
        for m in mentions:
            weight = m.engagement / total_engagement
            if m.sentiment == 'BULLISH':
                weighted_sentiment += weight
            elif m.sentiment == 'BEARISH':
                weighted_sentiment -= weight
        
        # Calculate buzz score (0-100)
        # Based on mention count and engagement
        buzz_score = min(100, (total * 5) + (total_engagement / 10))
        
        # Determine if trending (high buzz in short time)
        trending = buzz_score > 50 and total >= 5
        
        return SocialSignal(
            keyword=keyword,
            total_mentions=total,
            bullish_pct=bullish_pct,
            bearish_pct=bearish_pct,
            neutral_pct=neutral_pct,
            buzz_score=int(buzz_score),
            sentiment_score=round(weighted_sentiment, 2),
            trending=trending,
        )
    
    def analyze_market(self, market: Dict[str, Any]) -> SocialSignal:
        """Analyze social sentiment for a specific market."""
        question = market.get('question', '')
        
        # Extract key terms from question
        keywords = self._extract_keywords(question)
        
        if not keywords:
            return self.analyze_topic(question[:30])
        
        # Analyze the most relevant keyword
        return self.analyze_topic(keywords[0])
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords from text."""
        # Remove common words
        stop_words = {
            'will', 'the', 'a', 'an', 'be', 'to', 'in', 'on', 'by', 'for',
            'at', 'of', 'this', 'that', 'is', 'are', 'was', 'were', 'has',
            'have', 'had', 'do', 'does', 'did', 'before', 'after', 'during'
        }
        
        # Extract words
        words = re.findall(r'\b[A-Za-z]{3,}\b', text)
        keywords = [w.lower() for w in words if w.lower() not in stop_words]
        
        # Prioritize known important entities
        important = ['trump', 'biden', 'fed', 'bitcoin', 'ethereum', 'tesla', 
                    'shutdown', 'election', 'congress', 'supreme court']
        
        priority_keywords = [w for w in keywords if w in important]
        other_keywords = [w for w in keywords if w not in important]
        
        return priority_keywords + other_keywords[:3]
    
    def get_trending_topics(self, min_buzz: int = 50) -> List[SocialSignal]:
        """Get currently trending topics from cache."""
        trending = [
            signal for signal in self._cache.values()
            if signal.trending or signal.buzz_score >= min_buzz
        ]
        return sorted(trending, key=lambda s: s.buzz_score, reverse=True)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get social sentiment tracking statistics."""
        signals = list(self._cache.values())
        
        if not signals:
            return {
                'topics_analyzed': 0,
                'avg_buzz_score': 0,
                'trending_topics': 0,
                'overall_sentiment': 'NEUTRAL',
            }
        
        avg_buzz = sum(s.buzz_score for s in signals) / len(signals)
        avg_sentiment = sum(s.sentiment_score for s in signals) / len(signals)
        trending = len([s for s in signals if s.trending])
        
        overall = 'NEUTRAL'
        if avg_sentiment > 0.2:
            overall = 'BULLISH'
        elif avg_sentiment < -0.2:
            overall = 'BEARISH'
        
        return {
            'topics_analyzed': len(signals),
            'avg_buzz_score': round(avg_buzz, 1),
            'trending_topics': trending,
            'overall_sentiment': overall,
            'avg_sentiment_score': round(avg_sentiment, 2),
        }


# Global instance
social_analyzer = SocialSentimentAnalyzer()
