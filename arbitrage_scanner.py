"""
Multi-Outcome Arbitrage Scanner - Finds guaranteed profit opportunities.

When a market has multiple outcomes, the prices should sum to ~100%.
If they sum to >100%, you can SHORT all outcomes and profit the difference.
If they sum to <100%, you can LONG all outcomes and profit the difference.

This is RISK-FREE arbitrage when executed correctly.

Also includes:
- Resolution Arbitrage: Markets where outcome is known but price hasn't adjusted
- Time Decay: Markets expiring soon with high certainty
- Correlated Markets: Pairs that should move together but are mispriced
"""
import requests
import json
import re
from typing import Dict, List, Any, Optional, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from logger import log


@dataclass
class ArbitrageOpportunity:
    """A detected multi-outcome arbitrage opportunity."""
    market_id: str
    market_title: str
    market_slug: str
    outcomes: List[Dict[str, Any]]  # [{name, price, token_id}, ...]
    total_price: float  # Sum of all outcome prices
    arbitrage_type: str  # 'OVERPRICED' (>100%) or 'UNDERPRICED' (<100%)
    profit_pct: float  # Expected profit percentage
    required_capital: float  # Capital needed to execute
    expected_profit: float  # Dollar profit
    confidence: int  # 0-100
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:60],
            'slug': self.market_slug,
            'outcomes_count': len(self.outcomes),
            'total_price': round(self.total_price, 4),
            'type': self.arbitrage_type,
            'profit_pct': round(self.profit_pct, 2),
            'required_capital': round(self.required_capital, 2),
            'expected_profit': round(self.expected_profit, 2),
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat(),
            'outcomes': [
                {'name': o['name'][:30], 'price': o['price']} 
                for o in self.outcomes
            ],
        }


@dataclass 
class ResolutionArbitrage:
    """A market where resolution is known but price hasn't adjusted."""
    market_id: str
    market_title: str
    market_slug: str
    current_price: float
    expected_price: float  # What it should be (0 or 1)
    profit_pct: float
    resolution_status: str  # 'RESOLVED', 'NEAR_CERTAIN', 'HIGHLY_LIKELY'
    evidence: str  # Why we think it's resolved
    confidence: int
    token_id: Optional[str] = None
    end_date: Optional[datetime] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:60],
            'slug': self.market_slug,
            'current_price': self.current_price,
            'expected_price': self.expected_price,
            'profit_pct': round(self.profit_pct, 2),
            'status': self.resolution_status,
            'evidence': self.evidence[:100],
            'confidence': self.confidence,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class TimeDecayOpportunity:
    """A market expiring soon with high probability - theta positive."""
    market_id: str
    market_title: str
    market_slug: str
    current_price: float
    days_to_expiry: float
    daily_theta: float  # Expected daily return if price converges to 1
    total_potential_profit: float
    risk_level: str  # 'LOW', 'MEDIUM', 'HIGH'
    confidence: int
    token_id: Optional[str] = None
    end_date: Optional[datetime] = None
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:60],
            'slug': self.market_slug,
            'current_price': self.current_price,
            'days_to_expiry': round(self.days_to_expiry, 1),
            'daily_theta': round(self.daily_theta, 2),
            'total_profit': round(self.total_potential_profit, 2),
            'risk': self.risk_level,
            'confidence': self.confidence,
            'end_date': self.end_date.isoformat() if self.end_date else None,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class CorrelatedPair:
    """Two markets that should move together but are mispriced."""
    market_a_id: str
    market_a_title: str
    market_a_price: float
    market_b_id: str
    market_b_title: str
    market_b_price: float
    correlation_type: str  # 'SAME_EVENT', 'CONDITIONAL', 'INVERSE'
    expected_relationship: str  # Description of how they should relate
    mispricing_pct: float  # How much they're mispriced
    suggested_trade: str  # 'LONG_A_SHORT_B', 'LONG_B_SHORT_A', etc.
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_a': self.market_a_title[:40],
            'price_a': self.market_a_price,
            'market_b': self.market_b_title[:40],
            'price_b': self.market_b_price,
            'correlation': self.correlation_type,
            'relationship': self.expected_relationship[:60],
            'mispricing_pct': round(self.mispricing_pct, 2),
            'trade': self.suggested_trade,
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat(),
        }


class ArbitrageScanner:
    """
    Scans for multi-outcome, resolution, time decay, and correlated market opportunities.
    """
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    CLOB_API = "https://clob.polymarket.com"
    
    # Thresholds
    MIN_OVERPRICED_PCT = 1.5  # Minimum 1.5% overpriced to be worth it
    MIN_UNDERPRICED_PCT = 1.5  # Minimum 1.5% underpriced
    MIN_LIQUIDITY = 10000  # $10k minimum liquidity
    MIN_RESOLUTION_PROFIT = 3.0  # 3% minimum for resolution arb
    MIN_TIME_DECAY_PROFIT = 2.0  # 2% minimum for time decay
    MAX_DAYS_FOR_TIME_DECAY = 14  # Only consider markets expiring within 14 days
    MIN_CORRELATION_MISPRICING = 5.0  # 5% mispricing for correlated pairs
    
    # Date patterns for parsing market titles
    DATE_PATTERNS = [
        r'by\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2}),?\s*(\d{4})?',
        r'before\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})',
        r'on\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})',
        r'(\d{1,2})/(\d{1,2})/(\d{4})',
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{1,2})',
    ]
    
    MONTH_MAP = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
        'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    
    def __init__(self):
        self._opportunities: List[ArbitrageOpportunity] = []
        self._resolution_opps: List[ResolutionArbitrage] = []
        self._time_decay_opps: List[TimeDecayOpportunity] = []
        self._correlated_pairs: List[CorrelatedPair] = []
        self._market_cache: Dict[str, Dict] = {}
        self._all_markets_cache: List[Dict] = []
        self._last_scan = 0
    
    def _safe_get_token_id(self, market: Dict, index: int = 0) -> Optional[str]:
        """Safely get token ID from market, handling missing/empty data."""
        clob_ids = market.get('clobTokenIds')
        if clob_ids and isinstance(clob_ids, list) and len(clob_ids) > index:
            return clob_ids[index]
        return None
    
    def _generate_date_patterns(self, dt: datetime) -> List[str]:
        """Generate date patterns to search for in market titles."""
        month_names = ['january', 'february', 'march', 'april', 'may', 'june',
                       'july', 'august', 'september', 'october', 'november', 'december']
        month_abbr = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                      'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
        
        month_idx = dt.month - 1
        day = dt.day
        
        patterns = [
            f'{month_names[month_idx]} {day}',           # january 31
            f'{month_abbr[month_idx]} {day}',            # jan 31
            f'{dt.month}/{day}',                         # 1/31
            f'{dt.month:02d}/{day:02d}',                 # 01/31
            f'{month_names[month_idx]} {day}, {dt.year}', # january 31, 2026
            f'{month_abbr[month_idx]} {day}, {dt.year}', # jan 31, 2026
        ]
        
        return patterns
    
    def fetch_multi_outcome_markets(self) -> List[Dict]:
        """
        Fetch markets that have multiple outcomes (not just YES/NO).
        These are the ones where arbitrage is possible.
        """
        markets = []
        
        # Clear cache to avoid stale data
        self._market_cache = {}
        
        try:
            # Fetch active markets
            url = f"{self.GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false", 
                "limit": 100,
            }
            
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                return markets
            
            all_markets = response.json()
            
            # Filter for multi-outcome markets
            # These typically have 'groupItemTitle' or multiple tokens
            for market in all_markets:
                # Check if it's part of a group (multi-outcome)
                group_slug = market.get('groupItemSlug') or market.get('eventSlug')
                
                if group_slug:
                    # This market is part of a group
                    if group_slug not in self._market_cache:
                        self._market_cache[group_slug] = {
                            'title': market.get('groupItemTitle', market.get('question', '')),
                            'slug': group_slug,
                            'outcomes': [],
                            'liquidity': 0,
                        }
                    
                    # Parse outcome price
                    prices_str = market.get('outcomePrices', '[]')
                    try:
                        prices = json.loads(prices_str)
                        yes_price = float(prices[0]) if len(prices) > 0 else 0
                    except:
                        yes_price = 0
                    
                    # Add this outcome to the group
                    clob_ids = market.get('clobTokenIds') or []
                    self._market_cache[group_slug]['outcomes'].append({
                        'name': market.get('question', 'Unknown'),
                        'price': yes_price,
                        'token_id': clob_ids[0] if clob_ids else None,
                        'condition_id': market.get('conditionId'),
                        'volume': float(market.get('volume24hr', 0) or 0),
                    })
                    
                    self._market_cache[group_slug]['liquidity'] += float(market.get('liquidity', 0) or 0)
            
            # Filter groups with 3+ outcomes
            for slug, group in self._market_cache.items():
                if len(group['outcomes']) >= 3:
                    markets.append(group)
            
            log.debug(f"Found {len(markets)} multi-outcome markets")
            
        except Exception as e:
            log.error(f"Error fetching multi-outcome markets: {e}")
        
        return markets
    
    def _parse_date_from_title(self, title: str) -> Optional[datetime]:
        """Extract date from market title."""
        title_lower = title.lower()
        current_year = datetime.now().year
        
        for pattern in self.DATE_PATTERNS:
            match = re.search(pattern, title_lower)
            if match:
                groups = match.groups()
                try:
                    if len(groups) >= 2:
                        # Pattern with month name
                        if groups[0] in self.MONTH_MAP:
                            month = self.MONTH_MAP[groups[0]]
                            day = int(groups[1])
                            year = int(groups[2]) if len(groups) > 2 and groups[2] else current_year
                            return datetime(year, month, day)
                        # Pattern MM/DD/YYYY
                        elif groups[0].isdigit():
                            month = int(groups[0])
                            day = int(groups[1])
                            year = int(groups[2]) if len(groups) > 2 else current_year
                            return datetime(year, month, day)
                except (ValueError, TypeError):
                    continue
        
        return None
    
    def _fetch_all_markets(self, limit: int = 200) -> List[Dict]:
        """Fetch all active markets for analysis."""
        if self._all_markets_cache:
            return self._all_markets_cache
        
        try:
            url = f"{self.GAMMA_API}/markets"
            params = {
                "active": "true",
                "closed": "false",
                "limit": limit,
            }
            
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 200:
                markets = response.json()
                
                # Enrich with parsed data
                for market in markets:
                    # Parse prices
                    prices_str = market.get('outcomePrices', '[]')
                    try:
                        prices = json.loads(prices_str)
                        market['yes_price'] = float(prices[0]) if len(prices) > 0 else 0
                        # IMPORTANT: NO price is always 1 - YES for binary markets
                        # Don't use prices[1] as it's wrong for multi-outcome markets
                        market['no_price'] = 1.0 - market['yes_price']
                    except:
                        market['yes_price'] = 0
                        market['no_price'] = 1.0
                    
                    # Parse end date from title
                    market['parsed_end_date'] = self._parse_date_from_title(market.get('question', ''))
                    
                    # Also check endDate field
                    if market.get('endDate'):
                        try:
                            # Parse ISO format and convert to naive datetime (no timezone)
                            end_dt_str = market['endDate'].replace('Z', '+00:00')
                            end_dt = datetime.fromisoformat(end_dt_str)
                            # Convert to naive datetime for comparison with datetime.now()
                            if end_dt.tzinfo is not None:
                                end_dt = end_dt.replace(tzinfo=None)
                            market['end_date'] = end_dt
                        except Exception:
                            market['end_date'] = market['parsed_end_date']
                    else:
                        market['end_date'] = market['parsed_end_date']
                
                self._all_markets_cache = markets
                return markets
        
        except Exception as e:
            log.error(f"Error fetching all markets: {e}")
        
        return []
    
    def analyze_multi_outcome_market(self, market: Dict) -> Optional[ArbitrageOpportunity]:
        """
        Analyze a multi-outcome market for arbitrage opportunity.
        
        If sum of prices > 1: Market is OVERPRICED
          - Short all outcomes (sell YES on all)
          - You pay $X, you're guaranteed to receive $1 back
          - Profit = Sum - 1
        
        If sum of prices < 1: Market is UNDERPRICED  
          - Long all outcomes (buy YES on all)
          - You pay $X, you're guaranteed to receive $1 back
          - Profit = 1 - Sum
        """
        outcomes = market.get('outcomes', [])
        
        if len(outcomes) < 3:
            return None
        
        # Calculate total price
        total_price = sum(o.get('price', 0) for o in outcomes)
        
        # Skip if prices are close to 100% (no opportunity)
        deviation = abs(total_price - 1.0)
        if deviation < self.MIN_OVERPRICED_PCT / 100:
            return None
        
        # Check liquidity
        liquidity = market.get('liquidity', 0)
        if liquidity < self.MIN_LIQUIDITY:
            return None
        
        # Determine arbitrage type
        if total_price > 1.0:
            arb_type = 'OVERPRICED'
            profit_pct = (total_price - 1.0) * 100
            # To short all outcomes, we need to buy NO on each
            # Cost = sum of NO prices = sum of (1 - YES_price) = n - sum(YES)
            # But simpler: we sell YES on each, receive total_price, pay back $1
            required_capital = total_price * 100  # For $100 position
            expected_profit = (total_price - 1.0) * 100
        else:
            arb_type = 'UNDERPRICED'
            profit_pct = (1.0 - total_price) * 100
            required_capital = total_price * 100
            expected_profit = (1.0 - total_price) * 100
        
        # Calculate confidence based on liquidity and number of outcomes
        confidence = min(90, 50 + int(liquidity / 10000) * 5)
        if len(outcomes) <= 5:
            confidence += 10  # Easier to execute with fewer outcomes
        
        return ArbitrageOpportunity(
            market_id=market.get('slug', ''),
            market_title=market.get('title', 'Unknown'),
            market_slug=market.get('slug', ''),
            outcomes=outcomes,
            total_price=total_price,
            arbitrage_type=arb_type,
            profit_pct=profit_pct,
            required_capital=required_capital,
            expected_profit=expected_profit,
            confidence=confidence,
        )
    
    def scan_for_multi_outcome_arbitrage(self) -> List[ArbitrageOpportunity]:
        """
        Scan all multi-outcome markets for arbitrage opportunities.
        """
        opportunities = []
        
        markets = self.fetch_multi_outcome_markets()
        
        for market in markets:
            opp = self.analyze_multi_outcome_market(market)
            if opp:
                opportunities.append(opp)
                log.info(f"[ARBITRAGE] {opp.arbitrage_type}: {opp.market_title[:40]} | "
                        f"Total: {opp.total_price:.2%} | Profit: {opp.profit_pct:.2f}%")
        
        # Sort by profit percentage
        opportunities.sort(key=lambda x: x.profit_pct, reverse=True)
        
        self._opportunities = opportunities
        return opportunities
    
    def check_resolution_arbitrage(self, markets: List[Dict] = None) -> List[ResolutionArbitrage]:
        """
        Check for markets where resolution is known/near-certain but price hasn't adjusted.
        
        Looks for:
        1. Markets with end dates in the past
        2. Markets where news clearly indicates outcome
        3. Markets with extreme prices (>90% or <10%) that should be 99%/1%
        4. Markets with very recent events (today/yesterday)
        """
        if markets is None:
            markets = self._fetch_all_markets()
        
        resolution_opps = []
        now = datetime.now()
        
        for market in markets:
            question = market.get('question', '').lower()
            yes_price = market.get('yes', 0) or market.get('yes_price', 0)
            
            # Skip if no valid price
            if not yes_price or yes_price <= 0:
                continue
            
            # Skip extreme prices (already priced correctly)
            if yes_price > 0.97 or yes_price < 0.03:
                continue
            
            evidence = None
            expected_price = None
            confidence = 0
            end_date = market.get('end_date') or market.get('parsed_end_date')
            
            # 1. Check for markets with dates in the past
            if end_date and isinstance(end_date, datetime):
                days_past = (now - end_date).days
                
                if days_past >= 0:  # Date has passed
                    if yes_price > 0.80:
                        expected_price = 0.99
                        evidence = f"Market date passed {days_past} days ago, was at {yes_price:.0%} - should resolve YES"
                        confidence = 80 + min(10, days_past * 2)
                    elif yes_price < 0.20:
                        expected_price = 0.01
                        evidence = f"Market date passed {days_past} days ago, was at {yes_price:.0%} - should resolve NO"
                        confidence = 80 + min(10, days_past * 2)
            
            # 2. Check for specific date patterns in question (dynamic today/yesterday)
            today_patterns = self._generate_date_patterns(now)
            yesterday_patterns = self._generate_date_patterns(now - timedelta(days=1))
            
            for pattern in today_patterns:
                if pattern in question:
                    if yes_price > 0.75:
                        expected_price = 0.98
                        evidence = "Market expires TODAY - high probability should be near-certain"
                        confidence = 78
                    elif yes_price < 0.25:
                        expected_price = 0.02
                        evidence = "Market expires TODAY - low probability should be near-zero"
                        confidence = 78
                    break
            
            for pattern in yesterday_patterns:
                if pattern in question and not expected_price:
                    if yes_price > 0.70:
                        expected_price = 0.99
                        evidence = "Market expired YESTERDAY - should have resolved by now"
                        confidence = 85
                    elif yes_price < 0.30:
                        expected_price = 0.01
                        evidence = "Market expired YESTERDAY - should have resolved by now"
                        confidence = 85
                    break
            
            # 3. Check for high-probability markets that are lagging
            # If market is at 90-95%, and has high volume/score, should be 97%+
            if not expected_price and yes_price > 0.88 and yes_price < 0.96:
                score = market.get('score', 0)
                volume = market.get('vol24h', 0) or market.get('volume24hr', 0) or 0
                
                if score >= 85 or volume > 100000:
                    expected_price = 0.97
                    evidence = f"High confidence market (score:{score}, vol:${volume/1000:.0f}k) trading below fair value"
                    confidence = 65
            
            # 4. Check for low-probability markets that are too high
            if not expected_price and yes_price > 0.05 and yes_price < 0.12:
                score = market.get('score', 0)
                if score >= 80:
                    expected_price = 0.03
                    evidence = "Low probability event priced too high"
                    confidence = 60
            
            # Create opportunity if found
            if expected_price and confidence >= 60:
                profit_pct = abs(expected_price - yes_price) * 100
                
                if profit_pct >= self.MIN_RESOLUTION_PROFIT:
                    opp = ResolutionArbitrage(
                        market_id=market.get('condition_id', ''),
                        market_title=market.get('question', ''),
                        market_slug=market.get('slug', ''),
                        current_price=yes_price,
                        expected_price=expected_price,
                        profit_pct=profit_pct,
                        resolution_status='RESOLVED' if confidence >= 85 else 'NEAR_CERTAIN' if confidence >= 75 else 'HIGHLY_LIKELY',
                        evidence=evidence,
                        confidence=confidence,
                        token_id=self._safe_get_token_id(market, 0),
                        end_date=end_date if isinstance(end_date, datetime) else None,
                    )
                    resolution_opps.append(opp)
                    log.info(f"[RESOLUTION ARB] {opp.market_title[:40]} | "
                            f"Current: {yes_price:.2%} -> Expected: {expected_price:.2%} | "
                            f"Profit: {profit_pct:.1f}%")
        
        # Sort by profit potential
        resolution_opps.sort(key=lambda x: x.profit_pct * x.confidence, reverse=True)
        self._resolution_opps = resolution_opps
        return resolution_opps
    
    def scan_time_decay_opportunities(self, markets: List[Dict] = None) -> List[TimeDecayOpportunity]:
        """
        Find markets expiring soon where holding high-probability positions yields daily returns.
        
        Time decay (theta) = (expected_price - current_price) / days_to_expiry
        
        Example:
        - Market at 92% expiring in 5 days
        - Expected resolution: YES (100%)
        - Daily theta = (1.00 - 0.92) / 5 = 1.6% per day
        """
        if markets is None:
            markets = self._fetch_all_markets()
        
        time_decay_opps = []
        now = datetime.now()
        
        for market in markets:
            yes_price = market.get('yes', 0) or market.get('yes_price', 0)
            end_date = market.get('end_date') or market.get('parsed_end_date')
            
            # Skip if no valid data
            if not yes_price or yes_price <= 0:
                continue
            if not end_date or not isinstance(end_date, datetime):
                continue
            
            # Calculate days to expiry
            days_to_expiry = (end_date - now).total_seconds() / 86400
            
            # Only consider markets expiring within our window
            if days_to_expiry <= 0 or days_to_expiry > self.MAX_DAYS_FOR_TIME_DECAY:
                continue
            
            # Determine if this is a good theta play
            # For YES side (buying high probability)
            if yes_price >= 0.80 and yes_price <= 0.96:
                expected_price = 1.0
                total_profit = (expected_price - yes_price) * 100
                daily_theta = total_profit / days_to_expiry
                
                # Determine risk level based on price and time
                if yes_price >= 0.90 and days_to_expiry <= 3:
                    risk_level = 'LOW'
                    confidence = 80
                elif yes_price >= 0.85 and days_to_expiry <= 7:
                    risk_level = 'MEDIUM'
                    confidence = 70
                else:
                    risk_level = 'HIGH'
                    confidence = 60
                
                if daily_theta >= 0.5:  # At least 0.5% daily return
                    opp = TimeDecayOpportunity(
                        market_id=market.get('condition_id', ''),
                        market_title=market.get('question', ''),
                        market_slug=market.get('slug', ''),
                        current_price=yes_price,
                        days_to_expiry=days_to_expiry,
                        daily_theta=daily_theta,
                        total_potential_profit=total_profit,
                        risk_level=risk_level,
                        confidence=confidence,
                        token_id=self._safe_get_token_id(market, 0),
                        end_date=end_date,
                    )
                    time_decay_opps.append(opp)
            
            # For NO side (buying low probability expecting it to go to 0)
            elif yes_price >= 0.04 and yes_price <= 0.20:
                # Buying NO means we profit if YES goes to 0
                no_price = 1.0 - yes_price
                expected_no_price = 1.0
                total_profit = (expected_no_price - no_price) * 100
                daily_theta = total_profit / days_to_expiry
                
                if yes_price <= 0.10 and days_to_expiry <= 3:
                    risk_level = 'LOW'
                    confidence = 75
                elif yes_price <= 0.15 and days_to_expiry <= 7:
                    risk_level = 'MEDIUM'
                    confidence = 65
                else:
                    risk_level = 'HIGH'
                    confidence = 55
                
                if daily_theta >= 0.5:
                    opp = TimeDecayOpportunity(
                        market_id=market.get('condition_id', ''),
                        market_title=f"[NO] {market.get('question', '')}",
                        market_slug=market.get('slug', ''),
                        current_price=no_price,
                        days_to_expiry=days_to_expiry,
                        daily_theta=daily_theta,
                        total_potential_profit=total_profit,
                        risk_level=risk_level,
                        confidence=confidence,
                        token_id=self._safe_get_token_id(market, 1),
                        end_date=end_date,
                    )
                    time_decay_opps.append(opp)
        
        # Sort by daily theta (best daily return first)
        time_decay_opps.sort(key=lambda x: x.daily_theta * x.confidence / 100, reverse=True)
        self._time_decay_opps = time_decay_opps
        
        if time_decay_opps:
            log.info(f"[TIME DECAY] Found {len(time_decay_opps)} theta opportunities")
            for opp in time_decay_opps[:3]:
                log.info(f"  {opp.market_title[:35]} | Theta={opp.daily_theta:.2f}%/day | "
                        f"Expires in {opp.days_to_expiry:.1f}d | Risk: {opp.risk_level}")
        
        return time_decay_opps
    
    def scan_correlated_markets(self, markets: List[Dict] = None) -> List[CorrelatedPair]:
        """
        Find pairs of markets that should move together but are mispriced.
        
        Types of correlation:
        1. SAME_EVENT: Same outcome, different phrasing
        2. CONDITIONAL: If A happens, B is more likely (A should be <= B)
        3. INVERSE: If A happens, B is less likely
        """
        if markets is None:
            markets = self._fetch_all_markets()
        
        correlated_pairs = []
        
        # Extract keywords and entities for matching
        def extract_entities(text: str) -> Set[str]:
            text_lower = text.lower()
            entities = set()
            
            # Political figures
            politicians = ['trump', 'biden', 'harris', 'desantis', 'newsom', 'pence', 'haley']
            for p in politicians:
                if p in text_lower:
                    entities.add(p)
            
            # Economic terms
            economic = ['fed', 'interest rate', 'inflation', 'recession', 'gdp', 'unemployment']
            for e in economic:
                if e in text_lower:
                    entities.add(e.replace(' ', '_'))
            
            # Events
            events = ['election', 'shutdown', 'impeach', 'resign', 'indictment', 'conviction']
            for ev in events:
                if ev in text_lower:
                    entities.add(ev)
            
            return entities
        
        # Group markets by entities
        entity_markets: Dict[str, List[Dict]] = defaultdict(list)
        
        for market in markets:
            question = market.get('question', '')
            entities = extract_entities(question)
            
            for entity in entities:
                entity_markets[entity].append({
                    'id': market.get('condition_id', ''),
                    'title': question,
                    'slug': market.get('slug', ''),
                    'yes_price': market.get('yes', 0) or market.get('yes_price', 0),
                    'entities': entities,
                })
        
        # Find potential pairs within each entity group
        for entity, group in entity_markets.items():
            if len(group) < 2:
                continue
            
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    market_a = group[i]
                    market_b = group[j]
                    
                    # Skip if same market or no valid prices
                    if market_a['id'] == market_b['id']:
                        continue
                    if not market_a['yes_price'] or not market_b['yes_price']:
                        continue
                    
                    # Calculate entity overlap
                    common_entities = market_a['entities'] & market_b['entities']
                    if len(common_entities) < 1:
                        continue
                    
                    title_a = market_a['title'].lower()
                    title_b = market_b['title'].lower()
                    price_a = market_a['yes_price']
                    price_b = market_b['yes_price']
                    
                    correlation_type = None
                    expected_relationship = None
                    mispricing = 0
                    suggested_trade = None
                    confidence = 50
                    
                    # Check for CONDITIONAL relationships
                    # "X wins nomination" vs "X wins election"
                    # Nomination should be >= Election (can't win election without nomination)
                    if 'nomination' in title_a and 'election' in title_b and entity in common_entities:
                        if 'win' in title_a and 'win' in title_b:
                            # P(win election) should be <= P(win nomination)
                            if price_b > price_a + 0.05:  # Election > Nomination by 5%+
                                correlation_type = 'CONDITIONAL'
                                expected_relationship = f"P({entity} wins election) should be <= P({entity} wins nomination)"
                                mispricing = (price_b - price_a) * 100
                                suggested_trade = 'LONG_A_SHORT_B'  # Buy nomination, sell election
                                confidence = 70
                    
                    elif 'nomination' in title_b and 'election' in title_a and entity in common_entities:
                        if 'win' in title_a and 'win' in title_b:
                            if price_a > price_b + 0.05:
                                correlation_type = 'CONDITIONAL'
                                expected_relationship = f"P({entity} wins election) should be <= P({entity} wins nomination)"
                                mispricing = (price_a - price_b) * 100
                                suggested_trade = 'LONG_B_SHORT_A'
                                confidence = 70
                    
                    # Check for SAME_EVENT (similar questions, big price difference)
                    # High word overlap + different prices = potential mispricing
                    words_a = set(title_a.split())
                    words_b = set(title_b.split())
                    word_overlap = len(words_a & words_b) / max(len(words_a | words_b), 1)
                    
                    if word_overlap > 0.6 and abs(price_a - price_b) > 0.08:
                        if not correlation_type:  # Don't override conditional
                            correlation_type = 'SAME_EVENT'
                            expected_relationship = "Similar markets should have similar prices"
                            mispricing = abs(price_a - price_b) * 100
                            if price_a > price_b:
                                suggested_trade = 'LONG_B_SHORT_A'
                            else:
                                suggested_trade = 'LONG_A_SHORT_B'
                            confidence = 55
                    
                    # Create pair if mispricing is significant
                    if correlation_type and mispricing >= self.MIN_CORRELATION_MISPRICING:
                        pair = CorrelatedPair(
                            market_a_id=market_a['id'],
                            market_a_title=market_a['title'],
                            market_a_price=price_a,
                            market_b_id=market_b['id'],
                            market_b_title=market_b['title'],
                            market_b_price=price_b,
                            correlation_type=correlation_type,
                            expected_relationship=expected_relationship,
                            mispricing_pct=mispricing,
                            suggested_trade=suggested_trade,
                            confidence=confidence,
                        )
                        correlated_pairs.append(pair)
        
        # Remove duplicates and sort
        seen = set()
        unique_pairs = []
        for pair in correlated_pairs:
            key = tuple(sorted([pair.market_a_id, pair.market_b_id]))
            if key not in seen:
                seen.add(key)
                unique_pairs.append(pair)
        
        unique_pairs.sort(key=lambda x: x.mispricing_pct * x.confidence / 100, reverse=True)
        self._correlated_pairs = unique_pairs
        
        if unique_pairs:
            log.info(f"[CORRELATION] Found {len(unique_pairs)} mispriced pairs")
            for pair in unique_pairs[:3]:
                log.info(f"  {pair.market_a_title[:25]} vs {pair.market_b_title[:25]} | "
                        f"Mispricing: {pair.mispricing_pct:.1f}% | {pair.correlation_type}")
        
        return unique_pairs
    
    def get_best_opportunities(self, limit: int = 10) -> List[Dict]:
        """Get the best current arbitrage opportunities across all types."""
        all_opps = []
        
        # Add multi-outcome opportunities
        for opp in self._opportunities:
            all_opps.append({
                'type': 'MULTI_OUTCOME',
                'profit_pct': opp.profit_pct,
                'confidence': opp.confidence,
                'data': opp.to_dict(),
            })
        
        # Add resolution opportunities
        for opp in self._resolution_opps:
            all_opps.append({
                'type': 'RESOLUTION',
                'profit_pct': opp.profit_pct,
                'confidence': opp.confidence,
                'data': opp.to_dict(),
            })
        
        # Add time decay opportunities
        for opp in self._time_decay_opps:
            all_opps.append({
                'type': 'TIME_DECAY',
                'profit_pct': opp.total_potential_profit,
                'daily_return': opp.daily_theta,
                'confidence': opp.confidence,
                'data': opp.to_dict(),
            })
        
        # Add correlated pair opportunities
        for opp in self._correlated_pairs:
            all_opps.append({
                'type': 'CORRELATION',
                'profit_pct': opp.mispricing_pct,
                'confidence': opp.confidence,
                'data': opp.to_dict(),
            })
        
        # Sort by expected value (profit * confidence)
        all_opps.sort(key=lambda x: x['profit_pct'] * x['confidence'], reverse=True)
        
        return all_opps[:limit]
    
    def scan_all(self) -> Dict[str, Any]:
        """Run all scans and return summary."""
        # Clear cache to get fresh data
        self._all_markets_cache = []
        
        # Fetch fresh markets
        markets = self._fetch_all_markets()
        
        # Run all scans
        multi_opps = self.scan_for_multi_outcome_arbitrage()
        resolution_opps = self.check_resolution_arbitrage(markets)
        time_decay_opps = self.scan_time_decay_opportunities(markets)
        correlated_pairs = self.scan_correlated_markets(markets)
        
        return {
            'multi_outcome': len(multi_opps),
            'resolution': len(resolution_opps),
            'time_decay': len(time_decay_opps),
            'correlated': len(correlated_pairs),
            'total': len(multi_opps) + len(resolution_opps) + len(time_decay_opps) + len(correlated_pairs),
            'best_opportunities': self.get_best_opportunities(5),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get arbitrage scanner statistics."""
        return {
            'multi_outcome_opportunities': len(self._opportunities),
            'resolution_opportunities': len(self._resolution_opps),
            'time_decay_opportunities': len(self._time_decay_opps),
            'correlated_pairs': len(self._correlated_pairs),
            'markets_analyzed': len(self._all_markets_cache),
            'best_multi_outcome_profit': max((o.profit_pct for o in self._opportunities), default=0),
            'best_resolution_profit': max((o.profit_pct for o in self._resolution_opps), default=0),
            'best_daily_theta': max((o.daily_theta for o in self._time_decay_opps), default=0),
            'best_correlation_mispricing': max((o.mispricing_pct for o in self._correlated_pairs), default=0),
            'total_opportunities': (len(self._opportunities) + len(self._resolution_opps) + 
                                   len(self._time_decay_opps) + len(self._correlated_pairs)),
        }


# Global instance
arbitrage_scanner = ArbitrageScanner()


if __name__ == "__main__":
    # Test the scanner
    print("Scanning for multi-outcome arbitrage...")
    opps = arbitrage_scanner.scan_for_multi_outcome_arbitrage()
    
    print(f"\nFound {len(opps)} opportunities:\n")
    for opp in opps[:5]:
        print(f"  {opp.market_title[:50]}")
        print(f"    Type: {opp.arbitrage_type}")
        print(f"    Total Price: {opp.total_price:.2%}")
        print(f"    Profit: {opp.profit_pct:.2f}%")
        print(f"    Outcomes: {len(opp.outcomes)}")
        print()
