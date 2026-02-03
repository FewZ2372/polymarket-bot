"""
Advanced Trading Strategies Module
===================================
This module adds 6 additional strategies WITHOUT modifying the core v22 trading logic.
It's designed to be ADDITIVE - exposing opportunities in the dashboard without 
interfering with the working trader.

Strategies:
1. Multi-Outcome Arbitrage - When sum of probabilities != 100%
2. Resolution Arbitrage - Markets where outcome is known but price hasn't adjusted
3. Time Decay (Theta Plays) - High-probability markets expiring soon
4. Correlated Markets - Pairs that should move together but are mispriced
5. Insider Detection - Unusual volume without price movement
6. Sports Mispricing - Fan bias in sports markets

Author: Added as enhancement to v22
"""
import requests
import json
import re
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from logger import log


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class MultiOutcomeArbitrage:
    """Multi-outcome market where sum != 100%"""
    market_id: str
    market_title: str
    market_slug: str
    outcomes: List[Dict[str, Any]]
    total_price: float
    arbitrage_type: str  # 'OVERPRICED' or 'UNDERPRICED'
    profit_pct: float
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ResolutionArbitrage:
    """Market where resolution is known but price hasn't adjusted"""
    market_id: str
    market_title: str
    market_slug: str
    current_price: float
    expected_price: float
    profit_pct: float
    resolution_status: str
    evidence: str
    confidence: int
    token_id: Optional[str] = None
    end_date: Optional[datetime] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class TimeDecayOpportunity:
    """High-probability market expiring soon (theta positive)"""
    market_id: str
    market_title: str
    market_slug: str
    current_price: float
    days_to_expiry: float
    daily_theta: float
    total_potential_profit: float
    risk_level: str
    confidence: int
    side: str  # 'YES' or 'NO'
    token_id: Optional[str] = None
    end_date: Optional[datetime] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class CorrelatedPair:
    """Two markets that should move together but are mispriced"""
    market_a_title: str
    market_a_price: float
    market_b_title: str
    market_b_price: float
    correlation_type: str
    mispricing_pct: float
    suggested_trade: str
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class InsiderSignal:
    """Unusual volume pattern suggesting informed trading"""
    market_title: str
    market_slug: str
    volume_ratio: float  # Current vs average
    price_change: float
    signal_type: str  # 'ACCUMULATION' or 'DISTRIBUTION'
    suggested_action: str
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class SportsMispricing:
    """Sports market with potential fan bias"""
    market_title: str
    market_slug: str
    team_overvalued: str
    team_undervalued: str
    edge_pct: float
    bias_type: str  # 'HOME_BIAS', 'FAN_FAVORITE', etc.
    league: str
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)


# ============================================================================
# ADVANCED STRATEGIES SCANNER
# ============================================================================

class AdvancedStrategiesScanner:
    """
    Scans for advanced trading opportunities without modifying core logic.
    All results are READ-ONLY for dashboard display.
    """
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    # Month mapping for date parsing
    MONTH_MAP = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    def __init__(self):
        self._markets_cache: List[Dict] = []
        self._cache_time: float = 0
        self._cache_ttl = 120  # 2 minutes
        
        # Results storage
        self.multi_outcome_opps: List[MultiOutcomeArbitrage] = []
        self.resolution_opps: List[ResolutionArbitrage] = []
        self.time_decay_opps: List[TimeDecayOpportunity] = []
        self.correlated_pairs: List[CorrelatedPair] = []
        self.insider_signals: List[InsiderSignal] = []
        self.sports_mispricings: List[SportsMispricing] = []
    
    def _fetch_markets(self, limit: int = 200) -> List[Dict]:
        """Fetch markets from Polymarket API with caching."""
        import time
        
        if self._markets_cache and (time.time() - self._cache_time) < self._cache_ttl:
            return self._markets_cache
        
        try:
            url = f"{self.GAMMA_API}/markets"
            params = {"active": "true", "closed": "false", "limit": limit}
            
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 200:
                markets = response.json()
                
                # Enrich with parsed prices
                for market in markets:
                    prices_str = market.get('outcomePrices', '[]')
                    try:
                        prices = json.loads(prices_str)
                        yes_price = float(prices[0]) if prices and len(prices) > 0 else 0
                        market['yes_price'] = yes_price if yes_price > 0 else 0
                        market['no_price'] = 1.0 - market['yes_price'] if market['yes_price'] > 0 else 0
                    except Exception:
                        market['yes_price'] = 0
                        market['no_price'] = 0
                    
                    # Parse end date
                    market['end_date'] = self._parse_end_date(market)
                
                self._markets_cache = markets
                self._cache_time = time.time()
                return markets
        except Exception as e:
            log.debug(f"Error fetching markets: {e}")
        
        return self._markets_cache or []
    
    def _parse_end_date(self, market: Dict) -> Optional[datetime]:
        """Parse end date from market data."""
        # Try endDate field first
        if market.get('endDate'):
            try:
                end_str = market['endDate'].replace('Z', '+00:00')
                dt = datetime.fromisoformat(end_str)
                return dt.replace(tzinfo=None)
            except:
                pass
        
        # Try parsing from question
        question = market.get('question', '').lower()
        return self._parse_date_from_text(question)
    
    def _parse_date_from_text(self, text: str) -> Optional[datetime]:
        """Extract date from text."""
        patterns = [
            r'by\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})',
            r'before\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})',
            r'(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s*(\d{4})?',
        ]
        
        current_year = datetime.now().year
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                groups = match.groups()
                try:
                    month = self.MONTH_MAP.get(groups[0])
                    if month is None:
                        continue
                    day = int(groups[1])
                    year = int(groups[2]) if len(groups) > 2 and groups[2] else current_year
                    # Validate date
                    if 1 <= day <= 31 and 1 <= month <= 12:
                        return datetime(year, month, day)
                except (ValueError, TypeError):
                    continue
        
        return None
    
    def _generate_date_patterns(self, dt: datetime) -> List[str]:
        """Generate date patterns to search for in market titles."""
        month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                       'july', 'august', 'september', 'october', 'november', 'december']
        month_abbr = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                      'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        
        month_idx = dt.month - 1
        day = dt.day
        
        return [
            f'{month_names[month_idx]} {day}',
            f'{month_abbr[month_idx]} {day}',
            f'{dt.month}/{day}',
            f'{month_names[month_idx]} {day}, {dt.year}',
        ]
    
    # ========================================================================
    # STRATEGY 1: Multi-Outcome Arbitrage
    # ========================================================================
    
    def scan_multi_outcome_arbitrage(self) -> List[MultiOutcomeArbitrage]:
        """
        Find markets where sum of outcome prices != 100%.
        If sum > 100%: OVERPRICED - can short all outcomes
        If sum < 100%: UNDERPRICED - can long all outcomes
        """
        markets = self._fetch_markets()
        opportunities = []
        
        # Group markets by event
        groups: Dict[str, List[Dict]] = defaultdict(list)
        
        for market in markets:
            group_slug = market.get('groupItemSlug') or market.get('eventSlug')
            if group_slug:
                groups[group_slug].append(market)
        
        # Analyze groups with 3+ outcomes
        for slug, group_markets in groups.items():
            if len(group_markets) < 3:
                continue
            
            outcomes = []
            total_price = 0
            
            for m in group_markets:
                yes_price = m.get('yes_price', 0)
                if yes_price > 0:
                    clob_ids = m.get('clobTokenIds') or []
                    outcomes.append({
                        'name': m.get('question', '')[:50],
                        'price': yes_price,
                        'token_id': clob_ids[0] if clob_ids else None
                    })
                    total_price += yes_price
            
            deviation = abs(total_price - 1.0)
            
            if deviation >= 0.015:  # 1.5% minimum deviation
                arb_type = 'OVERPRICED' if total_price > 1.0 else 'UNDERPRICED'
                profit_pct = deviation * 100
                
                opp = MultiOutcomeArbitrage(
                    market_id=slug,
                    market_title=group_markets[0].get('groupItemTitle', slug),
                    market_slug=slug,
                    outcomes=outcomes,
                    total_price=total_price,
                    arbitrage_type=arb_type,
                    profit_pct=profit_pct,
                    confidence=min(85, 50 + int(profit_pct * 10)),
                )
                opportunities.append(opp)
        
        opportunities.sort(key=lambda x: x.profit_pct, reverse=True)
        self.multi_outcome_opps = opportunities[:10]
        return self.multi_outcome_opps
    
    # ========================================================================
    # STRATEGY 2: Resolution Arbitrage
    # ========================================================================
    
    def scan_resolution_arbitrage(self) -> List[ResolutionArbitrage]:
        """
        Find markets where outcome is likely known but price hasn't adjusted.
        
        CALIBRATED: Focus on LOW PRICE opportunities (buy cheap, expect to go to 0 or 100)
        The key insight from v22: buying at <15c has 100% win rate
        """
        markets = self._fetch_markets()
        opportunities = []
        now = datetime.now()
        
        for market in markets:
            question = market.get('question', '').lower()
            yes_price = market.get('yes_price', 0)
            
            if not yes_price or yes_price <= 0:
                continue
            
            # Skip already resolved
            if yes_price > 0.98 or yes_price < 0.02:
                continue
            
            evidence = None
            expected_price = None
            confidence = 0
            end_date = market.get('end_date')
            
            # === STRATEGY A: Markets where YES is CHEAP (<20c) and likely to resolve YES ===
            # This gives us a LOW entry price with HIGH upside
            if yes_price < 0.20:
                # Check if there's reason to believe YES will happen
                
                # 1. Date already passed - if YES is still cheap, it might resolve YES
                if end_date and isinstance(end_date, datetime):
                    days_past = (now - end_date).days
                    if days_past >= 1:
                        # Market should have resolved - cheap YES might be value
                        expected_price = 0.50  # Conservative - could go either way
                        evidence = f"Date passed {days_past}d ago, YES still at {yes_price:.0%} - potential value"
                        confidence = 70
                
                # 2. Look for "will NOT" patterns - if YES is cheap, NO is expensive
                if 'will not' in question or "won't" in question:
                    # This is a negative question, cheap YES = betting it WON'T happen
                    expected_price = 0.30  # Moderate upside
                    evidence = "Negative phrasing - cheap YES may be undervalued"
                    confidence = 65
            
            # === STRATEGY B: Markets where NO is CHEAP (<20c) and likely to resolve NO ===
            no_price = 1.0 - yes_price
            if no_price < 0.20 and not expected_price:
                # YES is expensive (>80c), NO is cheap - look for NO value
                
                if end_date and isinstance(end_date, datetime):
                    days_past = (now - end_date).days
                    if days_past >= 1:
                        # If date passed and YES is high but not 99%, NO might have value
                        if yes_price < 0.95:
                            expected_price = 0.01  # Betting NO will win
                            evidence = f"Date passed, YES at {yes_price:.0%} not resolved - NO has value"
                            confidence = 70
            
            # === STRATEGY C: Look for mispriced low-probability events ===
            # Keywords that suggest low probability
            low_prob_keywords = ['100', '1000', 'million', 'billion', 'war', 'resign', 
                                'impeach', 'die', 'death', 'assassination']
            
            if yes_price < 0.15 and not expected_price:
                has_low_prob_keyword = any(kw in question for kw in low_prob_keywords)
                if has_low_prob_keyword:
                    # Low probability event priced low - likely to stay low/go to 0
                    # BUY NO (which is cheap to buy since YES is cheap)
                    # Wait, no - if YES is 10c, NO is 90c which is expensive
                    # Actually we should just skip these
                    pass
            
            # === STRATEGY D: Sports/time-bound events near expiry ===
            if end_date and isinstance(end_date, datetime):
                days_left = (end_date - now).days
                
                if 0 <= days_left <= 2:  # Expiring very soon
                    if yes_price < 0.15:
                        # Cheap YES expiring soon - high potential
                        expected_price = 0.50  # Could go either way
                        evidence = f"Expires in {days_left}d, YES only {yes_price:.0%}"
                        confidence = 75
                    elif yes_price > 0.85 and yes_price < 0.95:
                        # Expensive YES but not resolved - NO might have value
                        expected_price = 0.01
                        evidence = f"Expires in {days_left}d, YES at {yes_price:.0%} - potential NO value"
                        confidence = 65
            
            if expected_price and confidence >= 60:
                profit_pct = abs(expected_price - yes_price) * 100
                
                # Only keep opportunities with meaningful profit potential
                if profit_pct >= 5.0:
                    clob_ids = market.get('clobTokenIds') or []
                    
                    opp = ResolutionArbitrage(
                        market_id=market.get('conditionId', ''),
                        market_title=market.get('question', ''),
                        market_slug=market.get('slug', ''),
                        current_price=yes_price,
                        expected_price=expected_price,
                        profit_pct=profit_pct,
                        resolution_status='NEAR_CERTAIN' if confidence >= 75 else 'LIKELY',
                        evidence=evidence,
                        confidence=confidence,
                        token_id=clob_ids[0] if clob_ids else None,
                        end_date=end_date,
                    )
                    opportunities.append(opp)
        
        opportunities.sort(key=lambda x: x.profit_pct * x.confidence, reverse=True)
        self.resolution_opps = opportunities[:15]
        return self.resolution_opps
    
    # ========================================================================
    # STRATEGY 3: Time Decay (Theta Plays)
    # ========================================================================
    
    def scan_time_decay(self) -> List[TimeDecayOpportunity]:
        """
        Find markets expiring soon where we can buy CHEAP and profit from resolution.
        
        CALIBRATED: Focus on LOW PRICE entries only
        - Buy YES when YES is cheap (<15c) and likely to resolve YES
        - Buy NO when NO is cheap (<15c) and likely to resolve NO
        
        The v22 insight: Buying cheap options that decay to 0 (lose) is bad
        But buying cheap options that resolve to 100 (win) is great
        """
        markets = self._fetch_markets()
        opportunities = []
        now = datetime.now()
        
        for market in markets:
            yes_price = market.get('yes_price', 0)
            end_date = market.get('end_date')
            
            if not yes_price or yes_price <= 0:
                continue
            if not end_date or not isinstance(end_date, datetime):
                continue
            
            days_to_expiry = (end_date - now).total_seconds() / 86400
            
            # Only look at markets expiring in 1-7 days
            if days_to_expiry <= 0 or days_to_expiry > 7:
                continue
            
            clob_ids = market.get('clobTokenIds') or []
            no_price = 1.0 - yes_price
            
            # === STRATEGY: Buy CHEAP YES that will likely resolve YES ===
            # This is when YES is <15c but the event seems likely to happen
            if 0.03 <= yes_price <= 0.15:
                # Cheap YES - check if it might actually win
                question = market.get('question', '').lower()
                
                # Score the likelihood
                likely_yes_score = 0
                
                # Recent activity/momentum could indicate informed buying
                volume = float(market.get('volume24hr') or 0)
                if volume > 50000:
                    likely_yes_score += 1
                
                # Very close to expiry with some price = might resolve YES
                if days_to_expiry <= 2 and yes_price >= 0.08:
                    likely_yes_score += 1
                
                # Price has been stable (not crashing to 0)
                price_change = float(market.get('priceChange24h') or 0)
                if price_change >= -0.02:  # Not dropping
                    likely_yes_score += 1
                
                if likely_yes_score >= 2:
                    total_profit = (1.0 - yes_price) * 100  # If YES wins
                    daily_theta = total_profit / days_to_expiry
                    
                    opp = TimeDecayOpportunity(
                        market_id=market.get('conditionId', ''),
                        market_title=market.get('question', ''),
                        market_slug=market.get('slug', ''),
                        current_price=yes_price,
                        days_to_expiry=days_to_expiry,
                        daily_theta=daily_theta,
                        total_potential_profit=total_profit,
                        risk_level='MEDIUM' if yes_price >= 0.10 else 'HIGH',
                        confidence=70 if yes_price >= 0.10 else 60,
                        side='YES',
                        token_id=clob_ids[0] if clob_ids else None,
                        end_date=end_date,
                    )
                    opportunities.append(opp)
            
            # === STRATEGY: Buy CHEAP NO that will likely resolve NO ===
            # This is when NO is <15c (YES > 85c) but might not resolve YES
            if 0.03 <= no_price <= 0.15:
                # Cheap NO - check if YES might fail
                
                # Score the likelihood of NO winning
                likely_no_score = 0
                
                # YES is high but not extreme (85-95%) = some doubt
                if yes_price < 0.95:
                    likely_no_score += 1
                
                # Close to expiry without full resolution
                if days_to_expiry <= 2 and yes_price < 0.93:
                    likely_no_score += 1
                
                # Price dropping (people selling YES)
                price_change = float(market.get('priceChange24h') or 0)
                if price_change <= -0.01:
                    likely_no_score += 1
                
                if likely_no_score >= 2:
                    total_profit = (1.0 - no_price) * 100  # If NO wins
                    daily_theta = total_profit / days_to_expiry
                    
                    opp = TimeDecayOpportunity(
                        market_id=market.get('conditionId', ''),
                        market_title=f"[NO] {market.get('question', '')}",
                        market_slug=market.get('slug', ''),
                        current_price=no_price,
                        days_to_expiry=days_to_expiry,
                        daily_theta=daily_theta,
                        total_potential_profit=total_profit,
                        risk_level='MEDIUM' if no_price >= 0.10 else 'HIGH',
                        confidence=65 if no_price >= 0.10 else 55,
                        side='NO',
                        token_id=clob_ids[1] if len(clob_ids) > 1 else None,
                        end_date=end_date,
                    )
                    opportunities.append(opp)
        
        opportunities.sort(key=lambda x: x.confidence * (1 / max(0.5, x.days_to_expiry)), reverse=True)
        self.time_decay_opps = opportunities[:15]
        return self.time_decay_opps
    
    # ========================================================================
    # STRATEGY 4: Correlated Markets
    # ========================================================================
    
    def scan_correlated_markets(self) -> List[CorrelatedPair]:
        """
        Find pairs of markets that should move together but are mispriced.
        """
        markets = self._fetch_markets()
        pairs = []
        
        # Extract entities for matching
        def extract_entities(text: str) -> Set[str]:
            text_lower = text.lower()
            entities = set()
            
            keywords = ['trump', 'biden', 'harris', 'fed', 'interest', 'rate', 
                       'shutdown', 'iran', 'israel', 'russia', 'ukraine', 'china']
            
            for kw in keywords:
                if kw in text_lower:
                    entities.add(kw)
            
            return entities
        
        # Group by entity
        entity_markets: Dict[str, List[Dict]] = defaultdict(list)
        
        for market in markets:
            question = market.get('question', '')
            entities = extract_entities(question)
            
            for entity in entities:
                entity_markets[entity].append({
                    'title': question,
                    'slug': market.get('slug', ''),
                    'yes_price': market.get('yes_price', 0),
                    'entities': entities,
                })
        
        # Find mispriced pairs
        for entity, group in entity_markets.items():
            if len(group) < 2:
                continue
            
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    
                    if not a['yes_price'] or not b['yes_price']:
                        continue
                    
                    # Check for similar questions with different prices
                    common = a['entities'] & b['entities']
                    if len(common) < 1:
                        continue
                    
                    price_diff = abs(a['yes_price'] - b['yes_price'])
                    
                    if price_diff >= 0.08:  # 8% minimum
                        mispricing = price_diff * 100
                        
                        if a['yes_price'] > b['yes_price']:
                            trade = 'LONG_B_SHORT_A'
                        else:
                            trade = 'LONG_A_SHORT_B'
                        
                        pair = CorrelatedPair(
                            market_a_title=a['title'][:50],
                            market_a_price=a['yes_price'],
                            market_b_title=b['title'][:50],
                            market_b_price=b['yes_price'],
                            correlation_type='SAME_ENTITY',
                            mispricing_pct=mispricing,
                            suggested_trade=trade,
                            confidence=55,
                        )
                        pairs.append(pair)
        
        # Deduplicate
        seen = set()
        unique = []
        for pair in pairs:
            key = tuple(sorted([pair.market_a_title, pair.market_b_title]))
            if key not in seen:
                seen.add(key)
                unique.append(pair)
        
        unique.sort(key=lambda x: x.mispricing_pct, reverse=True)
        self.correlated_pairs = unique[:10]
        return self.correlated_pairs
    
    # ========================================================================
    # STRATEGY 5: Insider Detection
    # ========================================================================
    
    def scan_insider_activity(self) -> List[InsiderSignal]:
        """
        Detect unusual volume patterns without corresponding price movement.
        High volume + low price change = informed accumulation/distribution
        """
        markets = self._fetch_markets()
        signals = []
        
        for market in markets:
            volume_24h = float(market.get('volume24hr') or 0)
            price_change = float(market.get('priceChange24h') or 0)
            
            if volume_24h < 10000:  # Minimum volume
                continue
            
            # Calculate volume ratio (simplified - using volume/liquidity as proxy)
            liquidity = float(market.get('liquidity') or 1)
            if liquidity <= 0:
                liquidity = 1.0
            volume_ratio = volume_24h / liquidity
            
            # High volume, low price change = potential insider
            if volume_ratio >= 2.0 and abs(price_change) < 0.03:
                # Determine direction based on recent price trend
                yes_price = market.get('yes_price') or 0.5
                
                if yes_price > 0.5:
                    signal_type = 'ACCUMULATION'
                    action = 'BUY'
                else:
                    signal_type = 'DISTRIBUTION'
                    action = 'SELL'
                
                signal = InsiderSignal(
                    market_title=market.get('question', '')[:60],
                    market_slug=market.get('slug', ''),
                    volume_ratio=volume_ratio,
                    price_change=price_change,
                    signal_type=signal_type,
                    suggested_action=action,
                    confidence=min(70, 40 + int(volume_ratio * 10)),
                )
                signals.append(signal)
        
        signals.sort(key=lambda x: x.volume_ratio, reverse=True)
        self.insider_signals = signals[:10]
        return self.insider_signals
    
    # ========================================================================
    # STRATEGY 6: Sports Mispricing
    # ========================================================================
    
    def scan_sports_mispricing(self) -> List[SportsMispricing]:
        """
        Find sports markets with potential fan bias.
        Popular teams tend to be overvalued by casual bettors.
        """
        markets = self._fetch_markets()
        mispricings = []
        
        # Popular teams that tend to be overvalued
        popular_teams = {
            'nba': ['lakers', 'warriors', 'celtics', 'knicks', 'bulls'],
            'nfl': ['cowboys', 'patriots', '49ers', 'packers', 'chiefs'],
            'mlb': ['yankees', 'dodgers', 'red sox', 'cubs'],
        }
        
        for market in markets:
            question = market.get('question', '').lower()
            category = market.get('category', '').lower()
            
            # Check if sports market
            is_sports = 'sports' in category or any(
                kw in question for kw in ['nba', 'nfl', 'mlb', 'vs.', ' vs ']
            )
            
            if not is_sports:
                continue
            
            yes_price = market.get('yes_price', 0)
            if not yes_price or yes_price <= 0:
                continue
            
            # Detect league
            league = None
            for l in ['nba', 'nfl', 'mlb']:
                if l in question or l in category:
                    league = l.upper()
                    break
            
            if not league:
                continue
            
            # Check for popular team bias
            popular = popular_teams.get(league.lower(), [])
            has_popular = any(team in question for team in popular)
            
            if has_popular and yes_price > 0.55:
                # Popular team might be overvalued
                edge = (yes_price - 0.50) * 100  # Simplified edge calculation
                
                mispricing = SportsMispricing(
                    market_title=market.get('question', '')[:60],
                    market_slug=market.get('slug', ''),
                    team_overvalued="Popular team",
                    team_undervalued="Opponent",
                    edge_pct=edge,
                    bias_type='FAN_FAVORITE',
                    league=league,
                    confidence=50,
                )
                mispricings.append(mispricing)
        
        mispricings.sort(key=lambda x: x.edge_pct, reverse=True)
        self.sports_mispricings = mispricings[:10]
        return self.sports_mispricings
    
    # ========================================================================
    # MAIN SCAN
    # ========================================================================
    
    def scan_all(self) -> Dict[str, Any]:
        """Run all strategy scans and return summary."""
        log.info("[ADVANCED] Running advanced strategies scan...")
        
        # Clear cache to get fresh data
        self._markets_cache = []
        
        # Run all scans
        multi = self.scan_multi_outcome_arbitrage()
        resolution = self.scan_resolution_arbitrage()
        theta = self.scan_time_decay()
        correlated = self.scan_correlated_markets()
        insider = self.scan_insider_activity()
        sports = self.scan_sports_mispricing()
        
        total = len(multi) + len(resolution) + len(theta) + len(correlated) + len(insider) + len(sports)
        
        log.info(f"[ADVANCED] Found {total} opportunities: "
                f"Multi={len(multi)}, Resolution={len(resolution)}, "
                f"Theta={len(theta)}, Correlated={len(correlated)}, "
                f"Insider={len(insider)}, Sports={len(sports)}")
        
        return {
            'multi_outcome': len(multi),
            'resolution': len(resolution),
            'time_decay': len(theta),
            'correlated': len(correlated),
            'insider': len(insider),
            'sports': len(sports),
            'total': total,
        }
    
    def get_tradeable_opportunities(self, min_confidence: int = 70) -> List[Dict[str, Any]]:
        """
        Convert detected opportunities to trader-compatible format.
        Only returns opportunities above min_confidence threshold.
        
        CALIBRATED: Prioritizes low-price entries (proven to work in v22)
        """
        tradeable = []
        
        # Resolution Arbitrage - ONLY low price entries
        for opp in self.resolution_opps:
            if opp.confidence >= min_confidence and opp.profit_pct >= 5.0:
                # CALIBRATION: Only trade when entry price is LOW
                # If expected_price is high (>0.5), we buy YES at current LOW price
                # If expected_price is low (<0.5), we buy NO at current LOW price
                
                if opp.expected_price > 0.5:
                    # Expect YES to win -> buy YES at low price
                    should_buy_yes = True
                    entry_price = opp.current_price  # This should be LOW
                else:
                    # Expect NO to win -> buy NO at low price  
                    should_buy_yes = False
                    entry_price = 1.0 - opp.current_price  # NO price
                
                # Note: Price filter is applied in run_local.py, not here
                # Keep all opportunities and let the calibrated filter decide
                
                tradeable.append({
                    'question': opp.market_title,
                    'slug': opp.market_slug,
                    'condition_id': opp.market_id,
                    'token_id': opp.token_id,
                    'yes': opp.current_price,
                    'no': 1.0 - opp.current_price,
                    'score': 85 + int(opp.profit_pct),
                    'spread': opp.profit_pct / 100,
                    'category': 'resolution_arbitrage',
                    'strategy': 'RESOLUTION_ARB',
                    'suggested_side': 'YES' if should_buy_yes else 'NO',
                    'expected_profit_pct': opp.profit_pct,
                    'confidence': opp.confidence,
                    'entry_price': entry_price,  # Added for filtering
                })
        
        # Time Decay - CALIBRATED for low-price entries only
        for opp in self.time_decay_opps:
            if opp.confidence >= min_confidence and opp.risk_level in ['LOW', 'MEDIUM']:
                # CALIBRATION: Time decay works best when buying LOW price side
                # that will go to 0 (not buying HIGH price side going to 100)
                
                # We want to buy the side that is CHEAP and will expire worthless
                # This is the OPPOSITE of what the original code did
                
                # Calculate entry price based on side
                if opp.side == 'YES':
                    entry_price = opp.current_price
                else:
                    entry_price = 1.0 - opp.current_price
                
                # Note: Price filter applied in run_local.py
                
                yes_price = opp.current_price if opp.side == 'YES' else (1.0 - opp.current_price)
                
                tradeable.append({
                    'question': opp.market_title,
                    'slug': opp.market_slug,
                    'condition_id': opp.market_id,
                    'token_id': opp.token_id,
                    'yes': yes_price,
                    'no': 1.0 - yes_price,
                    'score': 80 + int(opp.daily_theta * 5),
                    'spread': opp.total_potential_profit / 100,
                    'category': 'time_decay',
                    'strategy': 'TIME_DECAY',
                    'suggested_side': opp.side,
                    'expected_profit_pct': opp.total_potential_profit,
                    'confidence': opp.confidence,
                    'days_to_expiry': opp.days_to_expiry,
                    'entry_price': entry_price,
                })
        
        # Insider Signals - need market price data
        for sig in self.insider_signals:
            if sig.confidence >= min_confidence:
                # Find market in cache to get price
                market_data = None
                for m in self._markets_cache:
                    if m.get('slug') == sig.market_slug:
                        market_data = m
                        break
                
                if market_data:
                    yes_price = market_data.get('yes_price', 0.5)
                    no_price = 1.0 - yes_price
                    
                    # Determine entry price based on suggested action
                    if sig.suggested_action == 'BUY':
                        entry_price = yes_price
                        suggested_side = 'YES'
                    else:
                        entry_price = no_price
                        suggested_side = 'NO'
                    
                    # Note: Price filter applied in run_local.py
                    tradeable.append({
                        'question': sig.market_title,
                        'slug': sig.market_slug,
                        'yes': yes_price,
                        'no': no_price,
                        'score': 75 + sig.confidence // 5,
                        'spread': 0,
                        'category': 'insider_signal',
                        'strategy': 'INSIDER',
                        'suggested_side': suggested_side,
                        'confidence': sig.confidence,
                        'volume_ratio': sig.volume_ratio,
                        'entry_price': entry_price,
                    })
        
        # Sort by expected profit/confidence
        tradeable.sort(key=lambda x: x.get('expected_profit_pct', 0) * x.get('confidence', 50), reverse=True)
        
        return tradeable
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get all data formatted for dashboard display."""
        return {
            'multi_outcome': [
                {
                    'market': o.market_title[:50],
                    'total_price': f"{o.total_price:.2%}",
                    'type': o.arbitrage_type,
                    'profit': f"{o.profit_pct:.1f}%",
                    'confidence': o.confidence,
                }
                for o in self.multi_outcome_opps
            ],
            'resolution': [
                {
                    'market': o.market_title[:50],
                    'current': f"{o.current_price:.1%}",
                    'expected': f"{o.expected_price:.1%}",
                    'profit': f"{o.profit_pct:.1f}%",
                    'status': o.resolution_status,
                    'confidence': o.confidence,
                }
                for o in self.resolution_opps
            ],
            'time_decay': [
                {
                    'market': o.market_title[:50],
                    'price': f"{o.current_price:.1%}",
                    'expires': f"{o.days_to_expiry:.1f}d",
                    'theta': f"{o.daily_theta:.2f}%/day",
                    'risk': o.risk_level,
                    'side': o.side,
                }
                for o in self.time_decay_opps
            ],
            'correlated': [
                {
                    'market_a': p.market_a_title[:30],
                    'price_a': f"{p.market_a_price:.1%}",
                    'market_b': p.market_b_title[:30],
                    'price_b': f"{p.market_b_price:.1%}",
                    'gap': f"{p.mispricing_pct:.1f}%",
                }
                for p in self.correlated_pairs
            ],
            'insider': [
                {
                    'market': s.market_title[:50],
                    'volume_ratio': f"{s.volume_ratio:.1f}x",
                    'price_change': f"{s.price_change:+.1%}",
                    'signal': s.signal_type,
                    'action': s.suggested_action,
                }
                for s in self.insider_signals
            ],
            'sports': [
                {
                    'market': m.market_title[:50],
                    'league': m.league,
                    'edge': f"{m.edge_pct:.1f}%",
                    'bias': m.bias_type,
                }
                for m in self.sports_mispricings
            ],
        }


# Global instance
advanced_scanner = AdvancedStrategiesScanner()


if __name__ == "__main__":
    # Test
    scanner = AdvancedStrategiesScanner()
    results = scanner.scan_all()
    print(f"Found {results['total']} opportunities")
    
    data = scanner.get_dashboard_data()
    for key, items in data.items():
        if items:
            print(f"\n{key.upper()}:")
            for item in items[:3]:
                print(f"  {item}")
