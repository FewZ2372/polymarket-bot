"""
Strategy Performance Learner
============================
Tracks strategy performance and automatically adjusts weights/parameters.
This creates a feedback loop that improves the bot over time.
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field, asdict
from collections import defaultdict

from logger import log

DATA_FILE = "strategy_performance.json"


@dataclass
class StrategyStats:
    """Statistics for a single strategy."""
    name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_profit_per_win: float = 0.0
    avg_loss_per_loss: float = 0.0
    current_weight: float = 1.0
    last_updated: str = ""
    
    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return (self.wins / self.total_trades) * 100
    
    @property
    def expected_value(self) -> float:
        """Expected value per trade (simplified Kelly-like metric)."""
        if self.total_trades < 5:
            return 0.0  # Not enough data
        wr = self.win_rate / 100
        avg_win = self.avg_profit_per_win if self.avg_profit_per_win > 0 else 0.5
        avg_loss = abs(self.avg_loss_per_loss) if self.avg_loss_per_loss < 0 else 0.5
        if avg_loss == 0:
            return avg_win * wr
        return (wr * avg_win) - ((1 - wr) * avg_loss)


class StrategyLearner:
    """
    Learns from trade outcomes and adjusts strategy weights.
    
    Features:
    - Tracks win rate per strategy
    - Adjusts confidence thresholds based on performance
    - Recommends which strategies to use/avoid
    - Provides optimal parameters based on historical data
    """
    
    # Minimum trades before adjusting weights
    MIN_TRADES_FOR_LEARNING = 10
    
    # Base weights for strategies (before learning)
    DEFAULT_WEIGHTS = {
        'V22_CORE': 1.0,          # Original v22 logic
        'RESOLUTION_ARB': 0.8,
        'TIME_DECAY': 0.6,
        'MULTI_OUTCOME': 0.5,
        'CORRELATED': 0.4,
        'INSIDER': 0.5,
        'SPORTS': 0.3,
    }
    
    # Price ranges that work best (learned from v22 success)
    OPTIMAL_PRICE_RANGES = {
        'low': (0.01, 0.15),      # Best win rate historically
        'medium': (0.15, 0.50),   # Moderate win rate
        'high': (0.50, 0.99),     # Lower win rate
    }
    
    def __init__(self):
        self.strategies: Dict[str, StrategyStats] = {}
        self.trade_history: List[Dict] = []
        self.price_performance: Dict[str, Dict] = defaultdict(lambda: {'trades': 0, 'wins': 0})
        self._load()
    
    def _load(self):
        """Load performance data from disk."""
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
                
                for name, stats_dict in data.get('strategies', {}).items():
                    self.strategies[name] = StrategyStats(
                        name=name,
                        total_trades=stats_dict.get('total_trades', 0),
                        wins=stats_dict.get('wins', 0),
                        losses=stats_dict.get('losses', 0),
                        total_pnl=stats_dict.get('total_pnl', 0.0),
                        avg_profit_per_win=stats_dict.get('avg_profit_per_win', 0.0),
                        avg_loss_per_loss=stats_dict.get('avg_loss_per_loss', 0.0),
                        current_weight=stats_dict.get('current_weight', 1.0),
                        last_updated=stats_dict.get('last_updated', ''),
                    )
                
                self.trade_history = data.get('trade_history', [])[-1000:]  # Keep last 1000
                self.price_performance = defaultdict(
                    lambda: {'trades': 0, 'wins': 0},
                    data.get('price_performance', {})
                )
                
            except Exception as e:
                log.warning(f"[LEARNER] Could not load data: {e}")
    
    def _save(self):
        """Save performance data to disk."""
        try:
            data = {
                'strategies': {
                    name: asdict(stats) for name, stats in self.strategies.items()
                },
                'trade_history': self.trade_history[-1000:],
                'price_performance': dict(self.price_performance),
                'last_saved': datetime.now().isoformat(),
            }
            
            with open(DATA_FILE, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"[LEARNER] Could not save data: {e}")
    
    def record_trade(self, strategy: str, entry_price: float, side: str, 
                     market_title: str, amount: float):
        """Record a new trade entry."""
        trade = {
            'id': f"{strategy}_{datetime.now().timestamp()}",
            'strategy': strategy,
            'entry_price': entry_price,
            'side': side,
            'market': market_title[:50],
            'amount': amount,
            'entry_time': datetime.now().isoformat(),
            'status': 'OPEN',
            'pnl': 0.0,
        }
        
        self.trade_history.append(trade)
        
        # Initialize strategy stats if needed
        if strategy not in self.strategies:
            self.strategies[strategy] = StrategyStats(
                name=strategy,
                current_weight=self.DEFAULT_WEIGHTS.get(strategy, 0.5)
            )
        
        self._save()
        return trade['id']
    
    def record_outcome(self, trade_id: str = None, strategy: str = None, 
                       pnl: float = 0.0, is_win: bool = None):
        """Record trade outcome and update strategy stats."""
        
        # Find trade in history
        trade = None
        if trade_id:
            for t in reversed(self.trade_history):
                if t.get('id') == trade_id:
                    trade = t
                    break
        
        if not strategy and trade:
            strategy = trade.get('strategy', 'UNKNOWN')
        
        if not strategy:
            return
        
        # Initialize if needed
        if strategy not in self.strategies:
            self.strategies[strategy] = StrategyStats(
                name=strategy,
                current_weight=self.DEFAULT_WEIGHTS.get(strategy, 0.5)
            )
        
        stats = self.strategies[strategy]
        stats.total_trades += 1
        
        if is_win is None:
            is_win = pnl > 0
        
        if is_win:
            stats.wins += 1
            if pnl > 0:
                # Update average profit
                total_profit = stats.avg_profit_per_win * (stats.wins - 1) + pnl
                stats.avg_profit_per_win = total_profit / stats.wins
        else:
            stats.losses += 1
            if pnl < 0:
                # Update average loss
                total_loss = stats.avg_loss_per_loss * (stats.losses - 1) + pnl
                stats.avg_loss_per_loss = total_loss / stats.losses if stats.losses > 0 else 0
        
        stats.total_pnl += pnl
        stats.last_updated = datetime.now().isoformat()
        
        # Update price performance
        if trade:
            entry_price = trade.get('entry_price', 0)
            price_range = self._get_price_range(entry_price)
            self.price_performance[price_range]['trades'] += 1
            if is_win:
                self.price_performance[price_range]['wins'] += 1
        
        # Recalculate weight based on performance
        self._update_weight(strategy)
        
        self._save()
    
    def _get_price_range(self, price: float) -> str:
        """Categorize price into range."""
        if price < 0.15:
            return 'low'
        elif price < 0.50:
            return 'medium'
        else:
            return 'high'
    
    def _update_weight(self, strategy: str):
        """Update strategy weight based on performance."""
        stats = self.strategies.get(strategy)
        if not stats or stats.total_trades < self.MIN_TRADES_FOR_LEARNING:
            return
        
        # Calculate performance score
        base_weight = self.DEFAULT_WEIGHTS.get(strategy, 0.5)
        
        # Win rate factor (target: 70%+)
        wr_factor = stats.win_rate / 70.0  # 1.0 at 70%, >1.0 if better
        
        # Expected value factor
        ev = stats.expected_value
        ev_factor = 1.0 + (ev * 2)  # Boost for positive EV
        
        # Calculate new weight (capped 0.1 to 2.0)
        new_weight = base_weight * wr_factor * ev_factor
        new_weight = max(0.1, min(2.0, new_weight))
        
        # Smooth adjustment (don't change too fast)
        stats.current_weight = 0.7 * stats.current_weight + 0.3 * new_weight
    
    def get_strategy_weight(self, strategy: str) -> float:
        """Get current weight for a strategy."""
        if strategy in self.strategies:
            return self.strategies[strategy].current_weight
        return self.DEFAULT_WEIGHTS.get(strategy, 0.5)
    
    def should_trade(self, strategy: str, entry_price: float, 
                     base_confidence: int) -> tuple[bool, int, str]:
        """
        Determine if a trade should be taken based on learned performance.
        
        Returns:
            (should_trade, adjusted_confidence, reason)
        """
        weight = self.get_strategy_weight(strategy)
        price_range = self._get_price_range(entry_price)
        
        # Get price range performance
        price_stats = self.price_performance.get(price_range, {'trades': 0, 'wins': 0})
        price_wr = (price_stats['wins'] / price_stats['trades'] * 100) if price_stats['trades'] > 5 else 50
        
        # Adjust confidence based on:
        # 1. Strategy weight
        # 2. Price range historical performance
        adjusted_confidence = base_confidence
        
        # Weight adjustment
        adjusted_confidence = int(adjusted_confidence * weight)
        
        # Price range adjustment
        if price_stats['trades'] >= 10:
            if price_wr >= 80:
                adjusted_confidence += 10  # Boost for proven price range
            elif price_wr < 40:
                adjusted_confidence -= 15  # Penalty for bad price range
        
        # Determine if should trade
        reasons = []
        
        # Check strategy performance
        stats = self.strategies.get(strategy)
        if stats and stats.total_trades >= 10:
            if stats.win_rate < 40:
                reasons.append(f"Strategy WR too low ({stats.win_rate:.0f}%)")
                return False, adjusted_confidence, "; ".join(reasons)
        
        # Check price range
        if price_stats['trades'] >= 20 and price_wr < 30:
            reasons.append(f"Price range {price_range} has {price_wr:.0f}% WR")
            return False, adjusted_confidence, "; ".join(reasons)
        
        # Apply strict filter for high prices (learned from v22)
        if entry_price > 0.50 and not self._is_proven_high_price_strategy(strategy):
            reasons.append(f"High price ({entry_price:.0%}) not proven for {strategy}")
            return False, adjusted_confidence, "; ".join(reasons)
        
        return True, adjusted_confidence, "OK"
    
    def _is_proven_high_price_strategy(self, strategy: str) -> bool:
        """Check if strategy has proven to work at high prices."""
        stats = self.strategies.get(strategy)
        if not stats or stats.total_trades < 20:
            return False
        
        # Need 60%+ win rate with at least 20 trades
        return stats.win_rate >= 60
    
    def get_optimal_min_confidence(self, strategy: str) -> int:
        """Get optimal minimum confidence for a strategy based on historical data."""
        stats = self.strategies.get(strategy)
        if not stats or stats.total_trades < 10:
            return 85  # Default to v22 level
        
        # If win rate is good, can lower threshold slightly
        if stats.win_rate >= 80:
            return 75
        elif stats.win_rate >= 70:
            return 80
        else:
            return 90  # Raise threshold for underperforming strategies
    
    def get_optimal_max_price(self, strategy: str) -> float:
        """Get optimal maximum entry price for a strategy."""
        # Start conservative (like v22)
        max_price = 0.15
        
        # Check if strategy has proven to work at higher prices
        if self._is_proven_high_price_strategy(strategy):
            max_price = 0.30
        
        # Check price range performance
        for range_name, (low, high) in self.OPTIMAL_PRICE_RANGES.items():
            stats = self.price_performance.get(range_name, {})
            if stats.get('trades', 0) >= 20:
                wr = stats['wins'] / stats['trades'] * 100
                if wr >= 70:
                    max_price = max(max_price, high)
        
        return max_price
    
    def get_recommendations(self) -> Dict[str, Any]:
        """Get current recommendations based on learned performance."""
        recommendations = {
            'enabled_strategies': [],
            'disabled_strategies': [],
            'optimal_params': {},
            'price_insights': {},
        }
        
        # Strategy recommendations
        for name, stats in self.strategies.items():
            if stats.total_trades >= self.MIN_TRADES_FOR_LEARNING:
                if stats.win_rate >= 50 and stats.expected_value > 0:
                    recommendations['enabled_strategies'].append({
                        'name': name,
                        'weight': round(stats.current_weight, 2),
                        'win_rate': round(stats.win_rate, 1),
                        'ev': round(stats.expected_value, 3),
                    })
                else:
                    recommendations['disabled_strategies'].append({
                        'name': name,
                        'reason': f"WR={stats.win_rate:.0f}%, EV={stats.expected_value:.3f}",
                    })
        
        # Optimal parameters
        for strategy in self.DEFAULT_WEIGHTS.keys():
            recommendations['optimal_params'][strategy] = {
                'min_confidence': self.get_optimal_min_confidence(strategy),
                'max_price': self.get_optimal_max_price(strategy),
                'weight': self.get_strategy_weight(strategy),
            }
        
        # Price insights
        for range_name, stats in self.price_performance.items():
            if stats['trades'] > 0:
                wr = stats['wins'] / stats['trades'] * 100
                recommendations['price_insights'][range_name] = {
                    'trades': stats['trades'],
                    'win_rate': round(wr, 1),
                    'recommendation': 'USE' if wr >= 60 else 'AVOID' if wr < 40 else 'CAUTION',
                }
        
        return recommendations
    
    def get_stats_summary(self) -> str:
        """Get human-readable stats summary."""
        lines = [
            "=" * 60,
            "STRATEGY LEARNER - PERFORMANCE SUMMARY",
            "=" * 60,
        ]
        
        if not self.strategies:
            lines.append("No data yet. Start trading to collect performance metrics.")
            return "\n".join(lines)
        
        lines.append(f"\n{'Strategy':<20} {'Trades':>8} {'WR':>8} {'PnL':>10} {'Weight':>8}")
        lines.append("-" * 60)
        
        for name, stats in sorted(self.strategies.items(), key=lambda x: x[1].win_rate, reverse=True):
            lines.append(
                f"{name:<20} {stats.total_trades:>8} {stats.win_rate:>7.1f}% "
                f"${stats.total_pnl:>9.2f} {stats.current_weight:>7.2f}x"
            )
        
        # Price performance
        lines.append("\n" + "-" * 60)
        lines.append("PRICE RANGE PERFORMANCE:")
        for range_name, stats in self.price_performance.items():
            if stats['trades'] > 0:
                wr = stats['wins'] / stats['trades'] * 100
                lines.append(f"  {range_name.upper():<10} {stats['trades']:>5} trades, {wr:>5.1f}% win rate")
        
        return "\n".join(lines)
    
    def reset(self):
        """Reset all learned data."""
        self.strategies = {}
        self.trade_history = []
        self.price_performance = defaultdict(lambda: {'trades': 0, 'wins': 0})
        self._save()
        log.info("[LEARNER] All data reset")


# Global instance
strategy_learner = StrategyLearner()


if __name__ == "__main__":
    # Test
    learner = StrategyLearner()
    print(learner.get_stats_summary())
    print("\nRecommendations:")
    for k, v in learner.get_recommendations().items():
        print(f"  {k}: {v}")
