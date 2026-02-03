"""
Risk Manager - Advanced position sizing, filters, and drawdown protection.
"""
import json
from typing import Dict, Any, Optional, Tuple, List
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path

from logger import log
from config import config


@dataclass
class DailyPnL:
    """Track daily P&L for drawdown protection."""
    date: str
    starting_balance: float
    ending_balance: float
    pnl: float
    pnl_pct: float
    trades_count: int
    wins: int
    losses: int


@dataclass
class RiskState:
    """Current risk management state."""
    is_trading_allowed: bool = True
    pause_until: Optional[str] = None
    pause_reason: Optional[str] = None
    current_drawdown_pct: float = 0.0
    peak_balance: float = 0.0  # Start at 0, will be set by first update_balance call
    daily_pnl_history: List[Dict] = field(default_factory=list)
    

class RiskManager:
    """
    Manages risk through:
    1. Market quality filters
    2. Kelly Criterion position sizing
    3. Drawdown protection
    """
    
    # === MARKET QUALITY FILTERS (HIGH FREQUENCY) ===
    # VERY LOW thresholds - we want MAXIMUM VOLUME
    # Protection: small bets + diversification + short resolution
    MIN_LIQUIDITY = 0  # No minimum - we trade tiny amounts
    MIN_VOLUME_24H = 0  # No minimum
    MAX_SPREAD_PCT = 0.50  # 50% spread OK for small bets
    MAX_DAYS_TO_RESOLUTION = 30  # 1 month max
    MIN_PRICE = 0.02  # Don't buy below 2 cents
    MAX_PRICE = 0.50  # 50 cents max
    
    # === DRAWDOWN LIMITS ===
    DAILY_LOSS_LIMIT = -0.15  # -15% daily loss → pause 24h
    WEEKLY_LOSS_LIMIT = -0.25  # -25% weekly loss → pause 1 week
    MAX_DRAWDOWN = -0.35  # -35% from peak → pause indefinitely
    
    # === KELLY CRITERION PARAMETERS (HIGH FREQUENCY) ===
    KELLY_FRACTION = 0.15  # Use 15% of Kelly (more conservative for high freq)
    MIN_BET_SIZE = 0.50  # Minimum $0.50 (smaller trades)
    MAX_BET_SIZE = 3.0  # Maximum $3 per trade (was $10)
    MAX_PORTFOLIO_PCT = 0.05  # Max 5% of portfolio in one trade (was 10%)
    
    def __init__(self, state_file: str = "risk_state.json"):
        self.state_file = Path(state_file)
        self.state = RiskState()
        self._load_state()
    
    def _load_state(self):
        """Load risk state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    data = json.load(f)
                    self.state = RiskState(
                        is_trading_allowed=data.get('is_trading_allowed', True),
                        pause_until=data.get('pause_until'),
                        pause_reason=data.get('pause_reason'),
                        current_drawdown_pct=data.get('current_drawdown_pct', 0),
                        peak_balance=data.get('peak_balance', 10.0),
                        daily_pnl_history=data.get('daily_pnl_history', []),
                    )
            except Exception as e:
                log.error(f"Error loading risk state: {e}")
    
    def _save_state(self):
        """Save risk state to file."""
        try:
            data = {
                'is_trading_allowed': self.state.is_trading_allowed,
                'pause_until': self.state.pause_until,
                'pause_reason': self.state.pause_reason,
                'current_drawdown_pct': self.state.current_drawdown_pct,
                'peak_balance': self.state.peak_balance,
                'daily_pnl_history': self.state.daily_pnl_history,
                'last_updated': datetime.now().isoformat(),
            }
            with open(self.state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"Error saving risk state: {e}")
    
    # === MARKET QUALITY FILTERS ===
    
    def filter_market(self, market: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Check if a market passes quality filters.
        Returns (passed, reason).
        """
        # Check liquidity
        liquidity = market.get('liquidity', 0) or market.get('vol24h', 0)
        if liquidity < self.MIN_LIQUIDITY:
            return False, f"Low liquidity: ${liquidity:,.0f} < ${self.MIN_LIQUIDITY:,.0f}"
        
        # Check 24h volume
        volume = market.get('vol24h', 0)
        if volume < self.MIN_VOLUME_24H:
            return False, f"Low volume: ${volume:,.0f} < ${self.MIN_VOLUME_24H:,.0f}"
        
        # Check price bounds
        yes_price = market.get('yes', 0)
        if yes_price < self.MIN_PRICE:
            return False, f"Price too low: {yes_price:.2f} < {self.MIN_PRICE}"
        if yes_price > self.MAX_PRICE:
            return False, f"Price too high: {yes_price:.2f} > {self.MAX_PRICE}"
        
        # Check spread (if available)
        spread = market.get('spread', 0)
        if spread > self.MAX_SPREAD_PCT:
            return False, f"Spread too wide: {spread*100:.1f}% > {self.MAX_SPREAD_PCT*100:.0f}%"
        
        # Check for pump detection (sudden volume spike)
        # This would need historical data, skip for now
        
        return True, "Passed all filters"
    
    def get_filtered_markets(self, markets: List[Dict]) -> List[Dict]:
        """Filter markets and return only quality ones."""
        filtered = []
        for market in markets:
            passed, reason = self.filter_market(market)
            if passed:
                filtered.append(market)
            else:
                log.debug(f"Filtered out: {market.get('question', '')[:40]} - {reason}")
        
        log.info(f"Market filter: {len(filtered)}/{len(markets)} passed quality checks")
        return filtered
    
    # === KELLY CRITERION POSITION SIZING ===
    
    def calculate_kelly_size(
        self, 
        win_probability: float,
        win_payout: float,
        loss_payout: float,
        portfolio_balance: float
    ) -> float:
        """
        Calculate optimal position size using Kelly Criterion.
        
        Kelly formula: f* = (p * b - q) / b
        Where:
          p = probability of winning
          q = probability of losing (1 - p)
          b = ratio of win to loss (win_payout / loss_payout)
        
        We use fractional Kelly (25%) for safety.
        """
        if win_probability <= 0 or win_probability >= 1:
            return self.MIN_BET_SIZE
        
        p = win_probability
        q = 1 - p
        b = abs(win_payout / loss_payout) if loss_payout != 0 else 1
        
        # Kelly formula
        kelly_fraction = (p * b - q) / b if b > 0 else 0
        
        # If Kelly is negative or zero, this is a bad bet
        # Return MIN_BET_SIZE as floor (caller decides if to skip)
        if kelly_fraction <= 0:
            return self.MIN_BET_SIZE  # Return minimum, let caller decide to skip
        
        # Apply fractional Kelly for safety
        adjusted_kelly = kelly_fraction * self.KELLY_FRACTION
        
        # Calculate dollar amount
        kelly_dollars = portfolio_balance * adjusted_kelly
        
        # Apply constraints
        max_by_portfolio = portfolio_balance * self.MAX_PORTFOLIO_PCT
        
        final_size = min(
            kelly_dollars,
            self.MAX_BET_SIZE,
            max_by_portfolio
        )
        final_size = max(final_size, self.MIN_BET_SIZE)
        
        return round(final_size, 2)
    
    def calculate_position_size(
        self,
        market: Dict[str, Any],
        portfolio_balance: float
    ) -> Tuple[float, Dict[str, Any]]:
        """
        Calculate position size for a market opportunity.
        Returns (size, metadata).
        """
        score = market.get('score', 50)
        yes_price = market.get('yes', 0.5)
        spread = market.get('spread', 0)
        
        # Estimate win probability from score and other signals
        # Higher score = higher confidence
        base_win_prob = score / 100
        
        # Adjust for spread (arbitrage signal)
        if spread > 0.03:
            base_win_prob = min(0.9, base_win_prob + spread)
        
        # Adjust for price (extreme prices are riskier)
        if yes_price < 0.1 or yes_price > 0.9:
            base_win_prob *= 0.8  # Reduce confidence for extreme prices
        
        # Calculate payouts
        # If we buy YES at price P, we win (1-P) and lose P
        entry_price = yes_price
        win_payout = 1 - entry_price  # What we gain if YES wins
        loss_payout = entry_price  # What we lose if NO wins
        
        # Calculate Kelly size
        kelly_size = self.calculate_kelly_size(
            win_probability=base_win_prob,
            win_payout=win_payout,
            loss_payout=loss_payout,
            portfolio_balance=portfolio_balance
        )
        
        metadata = {
            'estimated_win_prob': base_win_prob,
            'kelly_raw': (base_win_prob * win_payout - (1-base_win_prob) * loss_payout) / win_payout if win_payout > 0 else 0,
            'kelly_adjusted': kelly_size / portfolio_balance if portfolio_balance > 0 else 0,
            'win_payout': win_payout,
            'loss_payout': loss_payout,
        }
        
        return kelly_size, metadata
    
    # === DRAWDOWN PROTECTION ===
    
    def update_balance(self, current_balance: float):
        """Update balance tracking for drawdown calculation."""
        # First time initialization: set peak to current balance
        if self.state.peak_balance <= 0:
            log.info(f"[RISK] Initializing peak balance to {current_balance:.2f}")
            self.state.peak_balance = current_balance
            self.state.current_drawdown_pct = 0.0
            self._save_state()
            return
        
        # Sanity check: if peak is way higher than current (>2x), reset it
        # This handles cases where peak was set incorrectly (e.g., from starting_balance)
        if self.state.peak_balance > current_balance * 2 and current_balance > 0:
            log.info(f"[RISK] Resetting peak balance from {self.state.peak_balance:.2f} to {current_balance:.2f}")
            self.state.peak_balance = current_balance
            self.state.current_drawdown_pct = 0.0
            self.state.is_trading_allowed = True
            self.state.pause_reason = None
            self._save_state()
            return
        
        # Update peak balance (high water mark)
        if current_balance > self.state.peak_balance:
            self.state.peak_balance = current_balance
        
        # Calculate current drawdown from peak
        self.state.current_drawdown_pct = (
            current_balance - self.state.peak_balance
        ) / self.state.peak_balance
        
        self._save_state()
    
    def record_daily_pnl(self, pnl: float, pnl_pct: float, trades: int, wins: int, losses: int):
        """Record daily P&L for drawdown tracking."""
        today = date.today().isoformat()
        
        # Check if we already have today's record
        existing = next(
            (d for d in self.state.daily_pnl_history if d.get('date') == today),
            None
        )
        
        if existing:
            existing['pnl'] = pnl
            existing['pnl_pct'] = pnl_pct
            existing['trades_count'] = trades
            existing['wins'] = wins
            existing['losses'] = losses
        else:
            self.state.daily_pnl_history.append({
                'date': today,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'trades_count': trades,
                'wins': wins,
                'losses': losses,
            })
        
        # Keep only last 30 days
        self.state.daily_pnl_history = self.state.daily_pnl_history[-30:]
        self._save_state()
    
    def check_drawdown_limits(self) -> Tuple[bool, Optional[str]]:
        """
        Check if we've hit drawdown limits.
        Returns (can_trade, pause_reason).
        """
        # Check if currently paused
        if self.state.pause_until:
            pause_date = datetime.fromisoformat(self.state.pause_until)
            if datetime.now() < pause_date:
                return False, self.state.pause_reason
            else:
                # Pause expired, reset
                self.state.is_trading_allowed = True
                self.state.pause_until = None
                self.state.pause_reason = None
                self._save_state()
        
        # Check max drawdown from peak
        if self.state.current_drawdown_pct <= self.MAX_DRAWDOWN:
            self._pause_trading(
                days=None,  # Indefinite
                reason=f"Max drawdown reached: {self.state.current_drawdown_pct*100:.1f}%"
            )
            return False, self.state.pause_reason
        
        # Check daily loss
        today = date.today().isoformat()
        today_record = next(
            (d for d in self.state.daily_pnl_history if d.get('date') == today),
            None
        )
        
        if today_record and today_record.get('pnl_pct', 0) <= self.DAILY_LOSS_LIMIT:
            self._pause_trading(
                days=1,
                reason=f"Daily loss limit: {today_record['pnl_pct']*100:.1f}%"
            )
            return False, self.state.pause_reason
        
        # Check weekly loss
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        weekly_records = [
            d for d in self.state.daily_pnl_history 
            if d.get('date', '') >= week_ago
        ]
        weekly_pnl_pct = sum(d.get('pnl_pct', 0) for d in weekly_records)
        
        if weekly_pnl_pct <= self.WEEKLY_LOSS_LIMIT:
            self._pause_trading(
                days=7,
                reason=f"Weekly loss limit: {weekly_pnl_pct*100:.1f}%"
            )
            return False, self.state.pause_reason
        
        return True, None
    
    def _pause_trading(self, days: Optional[int], reason: str):
        """Pause trading for specified days."""
        self.state.is_trading_allowed = False
        self.state.pause_reason = reason
        
        if days:
            self.state.pause_until = (
                datetime.now() + timedelta(days=days)
            ).isoformat()
        else:
            self.state.pause_until = None  # Indefinite
        
        log.warning(f"[RISK] Trading paused: {reason}")
        self._save_state()
    
    def can_trade(self) -> Tuple[bool, Optional[str]]:
        """Check if trading is allowed."""
        return self.check_drawdown_limits()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get risk management statistics."""
        return {
            'is_trading_allowed': self.state.is_trading_allowed,
            'pause_until': self.state.pause_until,
            'pause_reason': self.state.pause_reason,
            'current_drawdown_pct': round(self.state.current_drawdown_pct * 100, 2),
            'peak_balance': self.state.peak_balance,
            'daily_loss_limit': self.DAILY_LOSS_LIMIT * 100,
            'weekly_loss_limit': self.WEEKLY_LOSS_LIMIT * 100,
            'max_drawdown': self.MAX_DRAWDOWN * 100,
            'recent_pnl': self.state.daily_pnl_history[-7:] if self.state.daily_pnl_history else [],
        }


# Global instance
risk_manager = RiskManager()
