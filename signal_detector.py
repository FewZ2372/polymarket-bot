"""
Signal Detector - Detects insider activity and market inefficiencies.

1. Insider Detection: Unusual volume without price movement = accumulation/distribution
2. Sports Mispricing: Fan bias creates predictable mispricings in sports markets
"""
import requests
import json
import re
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

from logger import log


@dataclass
class InsiderSignal:
    """Signal indicating potential insider activity."""
    market_id: str
    market_title: str
    market_slug: str
    signal_type: str  # 'ACCUMULATION', 'DISTRIBUTION', 'UNUSUAL_VOLUME'
    current_price: float
    price_change_24h: float  # How much price moved
    volume_24h: float
    avg_volume: float  # Historical average (estimated)
    volume_spike_ratio: float  # Current / Average
    interpretation: str  # What this might mean
    confidence: int  # 0-100
    suggested_action: str  # 'BUY', 'SELL', 'WATCH'
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:60],
            'slug': self.market_slug,
            'signal': self.signal_type,
            'price': self.current_price,
            'price_change': round(self.price_change_24h * 100, 2),
            'volume_24h': round(self.volume_24h, 0),
            'volume_spike': round(self.volume_spike_ratio, 1),
            'interpretation': self.interpretation,
            'action': self.suggested_action,
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class SportsMispricing:
    """A sports market with detected fan bias."""
    market_id: str
    market_title: str
    market_slug: str
    sport: str  # 'NBA', 'NFL', 'MLB', 'NHL', 'Soccer'
    team_favored: str  # The team that's overvalued
    team_undervalued: str  # The team that's undervalued
    favored_price: float
    fair_value_estimate: float  # Our estimate of true probability
    edge_pct: float  # Potential profit from betting against bias
    bias_type: str  # 'BIG_MARKET', 'RECENT_FORM', 'STAR_PLAYER', 'HOME_BIAS'
    confidence: int
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market': self.market_title[:60],
            'slug': self.market_slug,
            'sport': self.sport,
            'overvalued': self.team_favored,
            'undervalued': self.team_undervalued,
            'current_price': self.favored_price,
            'fair_value': self.fair_value_estimate,
            'edge': round(self.edge_pct, 2),
            'bias_type': self.bias_type,
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat(),
        }


class SignalDetector:
    """
    Detects trading signals from market behavior patterns.
    """
    
    GAMMA_API = "https://gamma-api.polymarket.com"
    
    # Insider Detection Thresholds
    VOLUME_SPIKE_THRESHOLD = 3.0  # 3x normal volume = suspicious
    LOW_PRICE_CHANGE_THRESHOLD = 0.02  # <2% price change with high volume = accumulation
    HIGH_VOLUME_MINIMUM = 50000  # $50k minimum to consider
    
    # Sports Bias Thresholds
    BIG_MARKET_BIAS = 0.05  # Big market teams overvalued by ~5%
    HOME_BIAS = 0.03  # Home teams overvalued by ~3%
    STAR_PLAYER_BIAS = 0.04  # Teams with stars overvalued by ~4%
    
    # Big market / popular teams (typically overvalued)
    BIG_MARKET_TEAMS = {
        # NBA
        'lakers', 'knicks', 'warriors', 'celtics', 'bulls', 'nets', 'heat',
        # NFL
        'cowboys', 'patriots', 'packers', '49ers', 'chiefs', 'raiders', 'steelers',
        # MLB
        'yankees', 'dodgers', 'red sox', 'cubs', 'mets',
        # Soccer
        'manchester united', 'real madrid', 'barcelona', 'liverpool', 'chelsea',
        # NHL
        'maple leafs', 'canadiens', 'rangers', 'blackhawks', 'bruins',
    }
    
    # Star players that create bias
    STAR_PLAYERS = {
        'lebron', 'curry', 'durant', 'giannis', 'jokic', 'luka', 'tatum',
        'mahomes', 'allen', 'burrow', 'kelce',
        'ohtani', 'judge', 'trout',
        'mcdavid', 'crosby', 'ovechkin',
        'messi', 'ronaldo', 'mbappe', 'haaland',
    }
    
    def __init__(self):
        self._insider_signals: List[InsiderSignal] = []
        self._sports_mispricings: List[SportsMispricing] = []
        self._volume_history: Dict[str, List[float]] = defaultdict(list)
        self._markets_cache: List[Dict] = []
    
    def _fetch_markets(self, limit: int = 200) -> List[Dict]:
        """Fetch active markets."""
        if self._markets_cache:
            return self._markets_cache
        
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
                        market['no_price'] = float(prices[1]) if len(prices) > 1 else 0
                    except:
                        market['yes_price'] = 0
                        market['no_price'] = 0
                    
                    market['volume_24h'] = float(market.get('volume24hr', 0) or 0)
                    market['price_change'] = float(market.get('priceChange24h', 0) or 0)
                
                self._markets_cache = markets
                return markets
        
        except Exception as e:
            log.error(f"Error fetching markets: {e}")
        
        return []
    
    def detect_insider_activity(self, markets: List[Dict] = None) -> List[InsiderSignal]:
        """
        Detect unusual volume patterns that may indicate insider trading.
        
        Key patterns:
        1. ACCUMULATION: High volume + flat/slight increase in price
           - Insiders buying without moving price much
        2. DISTRIBUTION: High volume + flat/slight decrease in price
           - Insiders selling without crashing price
        3. UNUSUAL_VOLUME: Massive volume spike regardless of price
           - Something is happening, watch closely
        """
        if markets is None:
            markets = self._fetch_markets()
        
        signals = []
        
        for market in markets:
            volume_24h = market.get('volume_24h', 0)
            price_change = abs(market.get('price_change', 0))
            yes_price = market.get('yes_price', 0)
            question = market.get('question', '')
            category = market.get('category', '').lower()
            
            # Skip low volume markets
            if volume_24h < self.HIGH_VOLUME_MINIMUM:
                continue
            
            # Skip sports (handled separately)
            if category == 'sports' or self._is_sports_market(question):
                continue
            
            # Track volume history for this market
            market_id = market.get('conditionId', market.get('slug', ''))
            self._volume_history[market_id].append(volume_24h)
            
            # Keep only last 10 observations
            if len(self._volume_history[market_id]) > 10:
                self._volume_history[market_id] = self._volume_history[market_id][-10:]
            
            # Calculate average volume (estimate if not enough history)
            history = self._volume_history[market_id]
            if len(history) >= 3:
                # Use history excluding current observation
                past_volumes = history[:-1]
                avg_volume = sum(past_volumes) / len(past_volumes) if past_volumes else volume_24h / 3
            elif len(history) == 2:
                # Only one prior observation
                avg_volume = history[0] if history[0] > 0 else volume_24h / 3
            else:
                # Estimate average as 1/3 of current (assuming spike)
                avg_volume = volume_24h / 3
            
            # Ensure avg_volume is never zero
            avg_volume = max(avg_volume, 1.0)
            
            # Calculate volume spike ratio
            volume_spike = volume_24h / max(avg_volume, 1)
            
            # Detect patterns
            signal_type = None
            interpretation = None
            suggested_action = 'WATCH'
            confidence = 50
            
            # Pattern 1: ACCUMULATION
            # High volume + low price change + price slightly up or flat
            if volume_spike >= self.VOLUME_SPIKE_THRESHOLD and price_change < self.LOW_PRICE_CHANGE_THRESHOLD:
                if market.get('price_change', 0) >= 0:
                    signal_type = 'ACCUMULATION'
                    interpretation = f"Volume {volume_spike:.1f}x normal but price only moved {price_change*100:.1f}%. Someone may be quietly buying."
                    suggested_action = 'BUY'
                    confidence = 60 + min(20, int(volume_spike * 5))
                else:
                    signal_type = 'DISTRIBUTION'
                    interpretation = f"Volume {volume_spike:.1f}x normal but price only moved {price_change*100:.1f}%. Someone may be quietly selling."
                    suggested_action = 'SELL'
                    confidence = 60 + min(20, int(volume_spike * 5))
            
            # Pattern 2: UNUSUAL_VOLUME
            # Extreme volume spike regardless of price
            elif volume_spike >= self.VOLUME_SPIKE_THRESHOLD * 2:
                signal_type = 'UNUSUAL_VOLUME'
                interpretation = f"Volume {volume_spike:.1f}x normal. Major activity detected - could be news or insider knowledge."
                suggested_action = 'WATCH'
                confidence = 55
            
            if signal_type:
                sig = InsiderSignal(
                    market_id=market_id,
                    market_title=question,
                    market_slug=market.get('slug', ''),
                    signal_type=signal_type,
                    current_price=yes_price,
                    price_change_24h=market.get('price_change', 0),
                    volume_24h=volume_24h,
                    avg_volume=avg_volume,
                    volume_spike_ratio=volume_spike,
                    interpretation=interpretation,
                    confidence=confidence,
                    suggested_action=suggested_action,
                )
                signals.append(sig)
                log.info(f"[INSIDER] {signal_type}: {question[:40]} | Vol: {volume_spike:.1f}x | dP: {price_change*100:.1f}%")
        
        # Sort by confidence
        signals.sort(key=lambda x: x.confidence * x.volume_spike_ratio, reverse=True)
        self._insider_signals = signals
        
        return signals
    
    def _is_sports_market(self, question: str) -> bool:
        """Check if a market is sports-related."""
        question_lower = question.lower()
        sports_indicators = [
            ' vs ', ' vs.', 'game', 'match', 'win ', 'beat', 'score',
            'nba', 'nfl', 'mlb', 'nhl', 'premier league', 'champions league',
            'super bowl', 'world series', 'stanley cup', 'playoffs',
            'points', 'touchdown', 'goal', 'home run',
        ]
        return any(ind in question_lower for ind in sports_indicators)
    
    def _extract_teams(self, question: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract team names from a sports market question."""
        question_lower = question.lower()
        
        # Common pattern: "Team A vs Team B" or "Team A vs. Team B"
        vs_match = re.search(r'([a-z\s]+)\s+vs\.?\s+([a-z\s]+)', question_lower)
        if vs_match:
            team_a = vs_match.group(1).strip()
            team_b = vs_match.group(2).strip()
            # Clean up common suffixes
            for suffix in ['to win', 'win', '?', 'will', 'the']:
                team_a = team_a.replace(suffix, '').strip()
                team_b = team_b.replace(suffix, '').strip()
            return team_a, team_b
        
        return None, None
    
    def _estimate_fair_value(self, team: str, opponent: str, current_price: float, 
                            question: str) -> Tuple[float, str, float]:
        """
        Estimate fair value for a team based on detected biases.
        Returns (fair_value, bias_type, edge).
        """
        team_lower = team.lower()
        opponent_lower = opponent.lower()
        question_lower = question.lower()
        
        total_bias = 0
        bias_types = []
        
        # Check for big market bias
        is_big_market = any(bmt in team_lower for bmt in self.BIG_MARKET_TEAMS)
        opponent_is_big_market = any(bmt in opponent_lower for bmt in self.BIG_MARKET_TEAMS)
        
        if is_big_market and not opponent_is_big_market:
            total_bias += self.BIG_MARKET_BIAS
            bias_types.append('BIG_MARKET')
        
        # Check for star player bias (if star name appears and is on this team)
        has_star_on_team = self._team_has_star(team_lower, question_lower)
        if has_star_on_team:
            total_bias += self.STAR_PLAYER_BIAS
            bias_types.append('STAR_PLAYER')
        
        # Home bias (if "home" or "@" or location indicators)
        # This is harder to detect without more context
        
        # Calculate fair value
        if total_bias > 0:
            fair_value = current_price - total_bias
            fair_value = max(0.05, min(0.95, fair_value))  # Bound between 5% and 95%
            edge = (current_price - fair_value) * 100
            bias_type = '+'.join(bias_types) if bias_types else 'UNKNOWN'
            return fair_value, bias_type, edge
        
        return current_price, 'NONE', 0
    
    def _team_has_star(self, team: str, question: str) -> bool:
        """Check if the team has a star player mentioned."""
        # Map stars to teams (simplified)
        star_team_map = {
            'lebron': 'lakers',
            'curry': 'warriors',
            'durant': 'suns',
            'giannis': 'bucks',
            'jokic': 'nuggets',
            'luka': 'mavericks',
            'mahomes': 'chiefs',
        }
        
        for star, star_team in star_team_map.items():
            if star in question.lower() and star_team in team.lower():
                return True
        return False
    
    def detect_sports_mispricing(self, markets: List[Dict] = None) -> List[SportsMispricing]:
        """
        Detect mispriced sports markets due to fan bias.
        
        Common biases:
        1. BIG_MARKET: Lakers, Cowboys, Yankees always overvalued
        2. STAR_PLAYER: Teams with famous players get more bets
        3. RECENT_FORM: Hot/cold streaks overweighted
        4. HOME_BIAS: Home teams slightly overvalued
        """
        if markets is None:
            markets = self._fetch_markets()
        
        mispricings = []
        
        for market in markets:
            question = market.get('question', '')
            category = market.get('category', '').lower()
            yes_price = market.get('yes_price', 0)
            
            # Only process sports markets
            if category != 'sports' and not self._is_sports_market(question):
                continue
            
            # Skip if price is too extreme
            if yes_price < 0.10 or yes_price > 0.90:
                continue
            
            # Extract teams
            team_a, team_b = self._extract_teams(question)
            if not team_a or not team_b:
                continue
            
            # Determine sport
            sport = 'OTHER'
            q_lower = question.lower()
            if 'nba' in q_lower or any(t in q_lower for t in ['lakers', 'celtics', 'warriors', 'nets', 'knicks']):
                sport = 'NBA'
            elif 'nfl' in q_lower or any(t in q_lower for t in ['cowboys', 'patriots', 'chiefs', '49ers']):
                sport = 'NFL'
            elif 'mlb' in q_lower or any(t in q_lower for t in ['yankees', 'dodgers', 'red sox']):
                sport = 'MLB'
            elif 'nhl' in q_lower or any(t in q_lower for t in ['maple leafs', 'bruins', 'rangers']):
                sport = 'NHL'
            elif 'premier' in q_lower or 'champions' in q_lower or 'soccer' in q_lower:
                sport = 'Soccer'
            
            # Estimate fair values for both teams
            fair_a, bias_a, edge_a = self._estimate_fair_value(team_a, team_b, yes_price, question)
            
            # For team B (assuming NO side), price is 1 - yes_price
            no_price = 1 - yes_price
            fair_b, bias_b, edge_b = self._estimate_fair_value(team_b, team_a, no_price, question)
            
            # Determine which team is overvalued
            if edge_a > self.BIG_MARKET_BIAS * 100 * 0.5:  # Significant edge
                mispricing = SportsMispricing(
                    market_id=market.get('conditionId', ''),
                    market_title=question,
                    market_slug=market.get('slug', ''),
                    sport=sport,
                    team_favored=team_a,
                    team_undervalued=team_b,
                    favored_price=yes_price,
                    fair_value_estimate=fair_a,
                    edge_pct=edge_a,
                    bias_type=bias_a,
                    confidence=55 + min(25, int(edge_a * 3)),
                )
                mispricings.append(mispricing)
                log.info(f"[SPORTS] {sport}: {team_a} overvalued by {edge_a:.1f}% ({bias_a}) vs {team_b}")
            
            elif edge_b > self.BIG_MARKET_BIAS * 100 * 0.5:
                mispricing = SportsMispricing(
                    market_id=market.get('conditionId', ''),
                    market_title=question,
                    market_slug=market.get('slug', ''),
                    sport=sport,
                    team_favored=team_b,
                    team_undervalued=team_a,
                    favored_price=no_price,
                    fair_value_estimate=fair_b,
                    edge_pct=edge_b,
                    bias_type=bias_b,
                    confidence=55 + min(25, int(edge_b * 3)),
                )
                mispricings.append(mispricing)
                log.info(f"[SPORTS] {sport}: {team_b} overvalued by {edge_b:.1f}% ({bias_b}) vs {team_a}")
        
        # Sort by edge
        mispricings.sort(key=lambda x: x.edge_pct * x.confidence / 100, reverse=True)
        self._sports_mispricings = mispricings
        
        return mispricings
    
    def scan_all(self) -> Dict[str, Any]:
        """Run all signal detection scans."""
        # Clear cache
        self._markets_cache = []
        
        # Fetch fresh markets
        markets = self._fetch_markets()
        
        # Run scans
        insider_signals = self.detect_insider_activity(markets)
        sports_mispricings = self.detect_sports_mispricing(markets)
        
        return {
            'insider_signals': len(insider_signals),
            'sports_mispricings': len(sports_mispricings),
            'total_signals': len(insider_signals) + len(sports_mispricings),
            'markets_analyzed': len(markets),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get signal detector statistics."""
        insider_buy = len([s for s in self._insider_signals if s.suggested_action == 'BUY'])
        insider_sell = len([s for s in self._insider_signals if s.suggested_action == 'SELL'])
        
        sports_by_type = defaultdict(int)
        for m in self._sports_mispricings:
            sports_by_type[m.sport] += 1
        
        return {
            'insider_signals': len(self._insider_signals),
            'insider_buy_signals': insider_buy,
            'insider_sell_signals': insider_sell,
            'sports_mispricings': len(self._sports_mispricings),
            'sports_by_league': dict(sports_by_type),
            'avg_insider_confidence': (
                sum(s.confidence for s in self._insider_signals) / len(self._insider_signals)
                if self._insider_signals else 0
            ),
            'best_sports_edge': max((m.edge_pct for m in self._sports_mispricings), default=0),
        }
    
    def get_actionable_signals(self, min_confidence: int = 60) -> List[Dict]:
        """Get signals that are actionable (high enough confidence)."""
        actionable = []
        
        for sig in self._insider_signals:
            if sig.confidence >= min_confidence and sig.suggested_action != 'WATCH':
                actionable.append({
                    'type': 'INSIDER',
                    'action': sig.suggested_action,
                    'confidence': sig.confidence,
                    'data': sig.to_dict(),
                })
        
        for mp in self._sports_mispricings:
            if mp.confidence >= min_confidence:
                actionable.append({
                    'type': 'SPORTS',
                    'action': 'BET_UNDERDOG',  # Bet against the overvalued team
                    'confidence': mp.confidence,
                    'data': mp.to_dict(),
                })
        
        actionable.sort(key=lambda x: x['confidence'], reverse=True)
        return actionable


# Global instance
signal_detector = SignalDetector()


if __name__ == "__main__":
    print("Scanning for signals...")
    summary = signal_detector.scan_all()
    print(f"\nSummary: {summary}")
    
    print("\n=== Insider Signals ===")
    for sig in signal_detector._insider_signals[:5]:
        print(f"  {sig.signal_type}: {sig.market_title[:40]}")
        print(f"    Volume: {sig.volume_spike_ratio:.1f}x | Action: {sig.suggested_action}")
    
    print("\n=== Sports Mispricings ===")
    for mp in signal_detector._sports_mispricings[:5]:
        print(f"  {mp.sport}: {mp.team_favored} overvalued by {mp.edge_pct:.1f}%")
        print(f"    Bias: {mp.bias_type} | Bet on: {mp.team_undervalued}")
