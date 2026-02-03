"""
Events Tracker - Monitors upcoming events and triggers alerts.
"""
import requests
import re
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from logger import log


class EventType(Enum):
    POLITICAL = "political"
    ECONOMIC = "economic"
    EARNINGS = "earnings"
    SPORTS = "sports"
    CRYPTO = "crypto"
    LEGAL = "legal"
    SCIENCE = "science"
    OTHER = "other"


@dataclass
class UpcomingEvent:
    """An upcoming event that may affect markets."""
    title: str
    event_type: EventType
    date: datetime
    importance: int  # 1-10
    related_keywords: List[str]
    source: str
    description: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'title': self.title,
            'type': self.event_type.value,
            'date': self.date.isoformat(),
            'importance': self.importance,
            'keywords': self.related_keywords,
            'source': self.source,
            'description': self.description,
        }


@dataclass
class EventAlert:
    """An alert triggered by an event."""
    event: UpcomingEvent
    related_markets: List[Dict[str, Any]]
    alert_type: str  # 'IMMINENT', 'TODAY', 'UPCOMING'
    timestamp: datetime = field(default_factory=datetime.now)


class EventsTracker:
    """
    Tracks upcoming events that may affect prediction markets.
    """
    
    # Keywords to identify event types
    EVENT_KEYWORDS = {
        EventType.POLITICAL: [
            'election', 'vote', 'congress', 'senate', 'house', 'president',
            'governor', 'supreme court', 'legislation', 'bill', 'shutdown',
            'impeach', 'nomination', 'confirm', 'cabinet', 'trump', 'biden'
        ],
        EventType.ECONOMIC: [
            'fed', 'interest rate', 'fomc', 'inflation', 'gdp', 'jobs report',
            'unemployment', 'cpi', 'ppi', 'treasury', 'debt ceiling', 'tariff'
        ],
        EventType.EARNINGS: [
            'earnings', 'quarterly report', 'revenue', 'guidance', 'ipo',
            'acquisition', 'merger', 'stock split'
        ],
        EventType.CRYPTO: [
            'bitcoin', 'ethereum', 'crypto', 'btc', 'eth', 'sec', 'etf',
            'halving', 'defi', 'nft'
        ],
        EventType.LEGAL: [
            'lawsuit', 'trial', 'verdict', 'settlement', 'indictment',
            'hearing', 'testimony', 'ruling', 'appeal'
        ],
        EventType.SCIENCE: [
            'nasa', 'spacex', 'launch', 'fda', 'approval', 'clinical trial',
            'vaccine', 'ai', 'breakthrough'
        ],
        EventType.SPORTS: [
            'super bowl', 'world series', 'nba finals', 'stanley cup',
            'world cup', 'olympics', 'championship'
        ],
    }
    
    # Known important recurring events
    RECURRING_EVENTS = [
        {
            'title': 'FOMC Interest Rate Decision',
            'type': EventType.ECONOMIC,
            'importance': 10,
            'keywords': ['fed', 'interest rate', 'fomc', 'powell'],
        },
        {
            'title': 'Monthly Jobs Report',
            'type': EventType.ECONOMIC,
            'importance': 8,
            'keywords': ['jobs', 'unemployment', 'labor', 'employment'],
        },
        {
            'title': 'CPI Inflation Data',
            'type': EventType.ECONOMIC,
            'importance': 9,
            'keywords': ['cpi', 'inflation', 'prices'],
        },
    ]
    
    def __init__(self):
        self._events: List[UpcomingEvent] = []
        self._alerts: List[EventAlert] = []
        self._last_news_fetch = 0
    
    def fetch_events_from_news(self) -> List[UpcomingEvent]:
        """
        Fetch upcoming events by analyzing news headlines.
        """
        events = []
        
        # Fetch from Google News RSS
        keywords = ['federal reserve', 'election 2026', 'congress vote', 'economic data']
        
        for keyword in keywords:
            try:
                encoded = requests.utils.quote(keyword)
                url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
                
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    continue
                
                # Extract titles
                titles = re.findall(r'<title>(.*?)</title>', response.text)
                
                for title in titles[1:6]:  # Skip feed title, get first 5
                    event = self._parse_event_from_headline(title)
                    if event:
                        events.append(event)
                        
            except Exception as e:
                log.debug(f"Error fetching events for '{keyword}': {e}")
        
        # Add known upcoming events
        events.extend(self._get_known_upcoming_events())
        
        self._events = events
        log.info(f"Tracked {len(events)} upcoming events")
        return events
    
    def _parse_event_from_headline(self, headline: str) -> Optional[UpcomingEvent]:
        """Parse a news headline to extract event information."""
        headline_lower = headline.lower()
        
        # Determine event type
        event_type = EventType.OTHER
        importance = 5
        keywords = []
        
        for etype, kwords in self.EVENT_KEYWORDS.items():
            for kw in kwords:
                if kw in headline_lower:
                    event_type = etype
                    keywords.append(kw)
                    if etype in [EventType.POLITICAL, EventType.ECONOMIC]:
                        importance = 7
        
        if not keywords:
            return None
        
        # Check for time indicators
        date = datetime.now()
        if 'today' in headline_lower:
            importance += 2
        elif 'tomorrow' in headline_lower:
            date = datetime.now() + timedelta(days=1)
            importance += 1
        elif 'this week' in headline_lower:
            date = datetime.now() + timedelta(days=3)
        
        return UpcomingEvent(
            title=headline,
            event_type=event_type,
            date=date,
            importance=min(10, importance),
            related_keywords=keywords,
            source='Google News',
        )
    
    def _get_known_upcoming_events(self) -> List[UpcomingEvent]:
        """Get known upcoming events (FOMC, jobs report, etc.)."""
        events = []
        
        # This would ideally fetch from an economic calendar API
        # For now, we use static data
        
        return events
    
    def find_related_markets(self, event: UpcomingEvent, markets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Find markets related to an event."""
        related = []
        
        for market in markets:
            question = market.get('question', '').lower()
            category = market.get('category', '').lower()
            
            # Check if market matches event keywords
            matches = sum(1 for kw in event.related_keywords if kw in question)
            
            if matches > 0:
                market['event_relevance'] = matches
                related.append(market)
        
        # Sort by relevance
        related.sort(key=lambda m: m.get('event_relevance', 0), reverse=True)
        return related[:5]
    
    def check_for_alerts(self, markets: List[Dict[str, Any]]) -> List[EventAlert]:
        """Check for events that should trigger alerts."""
        alerts = []
        now = datetime.now()
        
        for event in self._events:
            # Skip low importance events
            if event.importance < 6:
                continue
            
            # Determine alert type based on time
            time_to_event = event.date - now
            
            # Skip past events (negative time difference)
            if time_to_event < timedelta(0):
                continue
            
            alert_type = None
            if time_to_event < timedelta(hours=1):
                alert_type = 'IMMINENT'
            elif time_to_event < timedelta(hours=24):
                alert_type = 'TODAY'
            elif time_to_event < timedelta(days=3):
                alert_type = 'UPCOMING'
            
            if alert_type:
                related = self.find_related_markets(event, markets)
                if related:
                    alert = EventAlert(
                        event=event,
                        related_markets=related,
                        alert_type=alert_type,
                    )
                    alerts.append(alert)
                    log.info(f"[EVENT] {alert_type}: {event.title[:50]} | {len(related)} related markets")
        
        self._alerts = alerts
        return alerts
    
    def get_events_for_market(self, market: Dict[str, Any]) -> List[UpcomingEvent]:
        """Get events that may affect a specific market."""
        question = market.get('question', '').lower()
        related = []
        
        for event in self._events:
            if any(kw in question for kw in event.related_keywords):
                related.append(event)
        
        return sorted(related, key=lambda e: e.importance, reverse=True)
    
    def get_high_impact_events(self, min_importance: int = 7) -> List[UpcomingEvent]:
        """Get high impact upcoming events."""
        return [e for e in self._events if e.importance >= min_importance]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get event tracking statistics."""
        now = datetime.now()
        # Only count future events within 24h (exclude past events)
        upcoming_24h = [
            e for e in self._events 
            if timedelta(0) <= (e.date - now) < timedelta(hours=24)
        ]
        
        return {
            'total_events_tracked': len(self._events),
            'events_next_24h': len(upcoming_24h),
            'active_alerts': len(self._alerts),
            'high_impact_events': len([e for e in self._events if e.importance >= 8]),
            'event_types': {
                etype.value: len([e for e in self._events if e.event_type == etype])
                for etype in EventType
            },
        }


# Global instance
events_tracker = EventsTracker()
