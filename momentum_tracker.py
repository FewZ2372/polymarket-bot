"""
Momentum Tracker - Detects rapid price movements and trading signals.
"""
import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import deque

from logger import log


@dataclass
class PricePoint:
    """A single price observation."""
    timestamp: datetime
    price: float
    volume: float = 0


@dataclass
class MomentumSignal:
    """A detected momentum signal."""
    market_id: str
    market_title: str
    signal_type: str  # 'SURGE', 'DUMP', 'BREAKOUT', 'BREAKDOWN'
    price_change: float
    price_change_pct: float
    time_window_minutes: int
    current_price: float
    previous_price: float
    volume_change: float
    strength: float  # 0-100
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'market_id': self.market_id,
            'market_title': self.market_title,
            'signal_type': self.signal_type,
            'price_change': self.price_change,
            'price_change_pct': self.price_change_pct,
            'time_window_minutes': self.time_window_minutes,
            'current_price': self.current_price,
            'previous_price': self.previous_price,
            'volume_change': self.volume_change,
            'strength': self.strength,
            'timestamp': self.timestamp.isoformat(),
        }


class MomentumTracker:
    """
    Tracks price momentum across markets to detect trading opportunities.
    """
    
    # Thresholds for signal detection
    SURGE_THRESHOLD = 0.05  # 5% up move
    DUMP_THRESHOLD = -0.05  # 5% down move
    VOLUME_SPIKE_MULTIPLIER = 2.0  # 2x average volume
    
    def __init__(self, history_minutes: int = 60):
        self.history_minutes = history_minutes
        # market_id -> deque of PricePoints
        self._price_history: Dict[str, deque] = {}
        self._max_history_points = 100
        self._signals: List[MomentumSignal] = []
        self._last_signal_time: Dict[str, datetime] = {}
        self._signal_cooldown = timedelta(minutes=10)
    
    def record_price(self, market_id: str, price: float, volume: float = 0):
        """Record a new price observation."""
        if market_id not in self._price_history:
            self._price_history[market_id] = deque(maxlen=self._max_history_points)
        
        self._price_history[market_id].append(PricePoint(
            timestamp=datetime.now(),
            price=price,
            volume=volume
        ))
    
    def analyze_market(self, market_id: str, market_title: str) -> Optional[MomentumSignal]:
        """
        Analyze a market for momentum signals.
        """
        if market_id not in self._price_history:
            return None
        
        history = self._price_history[market_id]
        if len(history) < 2:
            return None
        
        # Check cooldown
        if market_id in self._last_signal_time:
            if datetime.now() - self._last_signal_time[market_id] < self._signal_cooldown:
                return None
        
        current = history[-1]
        
        # Check different time windows
        for minutes in [5, 15, 30]:
            signal = self._check_momentum(market_id, market_title, minutes)
            if signal and signal.strength >= 50:
                self._signals.append(signal)
                self._last_signal_time[market_id] = datetime.now()
                return signal
        
        return None
    
    def _check_momentum(self, market_id: str, market_title: str, minutes: int) -> Optional[MomentumSignal]:
        """Check for momentum signal in a specific time window."""
        history = self._price_history[market_id]
        cutoff = datetime.now() - timedelta(minutes=minutes)
        
        # Find price at the start of the window
        start_price = None
        start_volume = 0
        for point in history:
            if point.timestamp >= cutoff:
                if start_price is None:
                    start_price = point.price
                    start_volume = point.volume
                break
        
        if start_price is None or start_price == 0:
            return None
        
        current = history[-1]
        price_change = current.price - start_price
        price_change_pct = price_change / start_price
        volume_change = (current.volume - start_volume) / max(start_volume, 1)
        
        # Determine signal type and strength
        signal_type = None
        strength = 0
        
        if price_change_pct >= self.SURGE_THRESHOLD:
            signal_type = 'SURGE'
            strength = min(100, abs(price_change_pct) * 1000)
        elif price_change_pct <= self.DUMP_THRESHOLD:
            signal_type = 'DUMP'
            strength = min(100, abs(price_change_pct) * 1000)
        elif price_change_pct >= 0.03 and current.price > 0.7:
            signal_type = 'BREAKOUT'
            strength = min(100, abs(price_change_pct) * 800)
        elif price_change_pct <= -0.03 and current.price < 0.3:
            signal_type = 'BREAKDOWN'
            strength = min(100, abs(price_change_pct) * 800)
        
        if signal_type is None:
            return None
        
        # Boost strength if volume is spiking
        if volume_change >= self.VOLUME_SPIKE_MULTIPLIER:
            strength = min(100, strength * 1.5)
        
        return MomentumSignal(
            market_id=market_id,
            market_title=market_title,
            signal_type=signal_type,
            price_change=price_change,
            price_change_pct=price_change_pct,
            time_window_minutes=minutes,
            current_price=current.price,
            previous_price=start_price,
            volume_change=volume_change,
            strength=strength,
        )
    
    def get_recent_signals(self, limit: int = 10) -> List[MomentumSignal]:
        """Get most recent momentum signals."""
        return sorted(self._signals, key=lambda s: s.timestamp, reverse=True)[:limit]
    
    def get_strong_signals(self, min_strength: int = 70) -> List[MomentumSignal]:
        """Get strong momentum signals."""
        recent = datetime.now() - timedelta(minutes=30)
        return [
            s for s in self._signals 
            if s.strength >= min_strength and s.timestamp >= recent
        ]
    
    def bulk_analyze(self, markets: List[Dict[str, Any]]) -> List[MomentumSignal]:
        """
        Analyze multiple markets and record their prices.
        Returns any detected signals.
        """
        signals = []
        
        for market in markets:
            market_id = market.get('slug') or market.get('condition_id') or market.get('question', '')[:50]
            price = market.get('yes', 0)
            volume = market.get('vol24h', 0)
            title = market.get('question', 'Unknown')
            
            # Record price
            self.record_price(market_id, price, volume)
            
            # Check for signals
            signal = self.analyze_market(market_id, title)
            if signal:
                signals.append(signal)
                log.info(f"[MOMENTUM] {signal.signal_type}: {title[:40]} | {signal.price_change_pct*100:.1f}% in {signal.time_window_minutes}min")
        
        return signals
    
    def get_stats(self) -> Dict[str, Any]:
        """Get momentum tracking statistics."""
        recent = datetime.now() - timedelta(hours=1)
        recent_signals = [s for s in self._signals if s.timestamp >= recent]
        
        return {
            'markets_tracked': len(self._price_history),
            'total_signals': len(self._signals),
            'signals_last_hour': len(recent_signals),
            'surges': len([s for s in recent_signals if s.signal_type == 'SURGE']),
            'dumps': len([s for s in recent_signals if s.signal_type == 'DUMP']),
            'avg_strength': sum(s.strength for s in recent_signals) / len(recent_signals) if recent_signals else 0,
        }


# Global instance
momentum_tracker = MomentumTracker()
