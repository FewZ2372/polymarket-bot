"""
Backtester - Test trading strategies against historical data.
Allows validation of strategy before live trading.
"""
import json
import random
import requests
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
import statistics

from logger import log


@dataclass
class BacktestTrade:
    """A trade in the backtest."""
    timestamp: datetime
    market: str
    market_slug: str
    side: str  # BUY/SELL
    outcome: str  # YES/NO
    entry_price: float
    exit_price: float
    amount: float
    shares: float
    pnl: float
    pnl_pct: float
    hold_time_hours: float
    win: bool


@dataclass
class BacktestResult:
    """Results of a backtest run."""
    start_date: datetime
    end_date: datetime
    initial_balance: float
    final_balance: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_pnl_pct: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_hold_time_hours: float
    best_trade: float
    worst_trade: float
    trades: List[BacktestTrade] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'initial_balance': self.initial_balance,
            'final_balance': self.final_balance,
            'total_trades': self.total_trades,
            'winning_trades': self.winning_trades,
            'losing_trades': self.losing_trades,
            'win_rate': round(self.win_rate, 2),
            'total_pnl': round(self.total_pnl, 2),
            'total_pnl_pct': round(self.total_pnl_pct, 2),
            'max_drawdown': round(self.max_drawdown, 2),
            'max_drawdown_pct': round(self.max_drawdown_pct, 2),
            'sharpe_ratio': round(self.sharpe_ratio, 3),
            'profit_factor': round(self.profit_factor, 2),
            'avg_win': round(self.avg_win, 2),
            'avg_loss': round(self.avg_loss, 2),
            'avg_hold_time_hours': round(self.avg_hold_time_hours, 1),
            'best_trade': round(self.best_trade, 2),
            'worst_trade': round(self.worst_trade, 2),
        }
    
    def print_report(self):
        """Print formatted backtest report."""
        print(f"\n{'='*70}")
        print(f" BACKTEST REPORT")
        print(f" Period: {self.start_date.strftime('%Y-%m-%d')} to {self.end_date.strftime('%Y-%m-%d')}")
        print(f"{'='*70}\n")
        
        print(f"Initial Balance:    ${self.initial_balance:,.2f}")
        print(f"Final Balance:      ${self.final_balance:,.2f}")
        print(f"Total P&L:          ${self.total_pnl:+,.2f} ({self.total_pnl_pct:+.1f}%)")
        print(f"\n{'─'*40}")
        print(f"Total Trades:       {self.total_trades}")
        print(f"Winning Trades:     {self.winning_trades} ({self.win_rate:.1f}%)")
        print(f"Losing Trades:      {self.losing_trades}")
        print(f"\n{'─'*40}")
        print(f"Max Drawdown:       ${self.max_drawdown:,.2f} ({self.max_drawdown_pct:.1f}%)")
        print(f"Sharpe Ratio:       {self.sharpe_ratio:.3f}")
        print(f"Profit Factor:      {self.profit_factor:.2f}")
        print(f"\n{'─'*40}")
        print(f"Avg Win:            ${self.avg_win:+,.2f}")
        print(f"Avg Loss:           ${self.avg_loss:,.2f}")
        print(f"Best Trade:         ${self.best_trade:+,.2f}")
        print(f"Worst Trade:        ${self.worst_trade:,.2f}")
        print(f"Avg Hold Time:      {self.avg_hold_time_hours:.1f} hours")
        print(f"\n{'='*70}\n")


class Backtester:
    """
    Backtests trading strategies using historical market data.
    """
    
    POLYMARKET_API = "https://gamma-api.polymarket.com"
    
    def __init__(self, initial_balance: float = 100.0):
        self.initial_balance = initial_balance
        self._market_history_cache: Dict[str, List[Dict]] = {}
    
    def fetch_market_history(self, slug: str, days: int = 30) -> List[Dict]:
        """Fetch historical price data for a market."""
        if slug in self._market_history_cache:
            return self._market_history_cache[slug]
        
        try:
            # Get market info first
            url = f"{self.POLYMARKET_API}/markets?slug={slug}"
            response = requests.get(url, timeout=10)
            
            if response.status_code != 200:
                return []
            
            markets = response.json()
            if not markets:
                return []
            
            market = markets[0]
            condition_id = market.get('conditionId', '')
            
            if not condition_id:
                return []
            
            # Get price history
            # Note: This endpoint may not be publicly available
            # Using a simplified approach with current data
            history_url = f"{self.POLYMARKET_API}/prices?conditionId={condition_id}"
            
            # For now, generate synthetic history from current price
            current_price = 0.5
            prices_str = market.get('outcomePrices', '[]')
            try:
                prices = json.loads(prices_str)
                current_price = float(prices[0]) if prices else 0.5
            except (json.JSONDecodeError, ValueError, IndexError):
                pass
            
            # Generate synthetic price history (for demo purposes)
            # In production, you'd use real historical data
            history = []
            now = datetime.now()
            
            for i in range(days * 24):  # Hourly data
                timestamp = now - timedelta(hours=i)
                # Add some noise to simulate price movement
                noise = random.uniform(-0.05, 0.05)
                divisor = max(1, days * 24)  # Prevent division by zero
                price = max(0.01, min(0.99, current_price + noise * (i / divisor)))
                
                history.append({
                    'timestamp': timestamp.isoformat(),
                    'price': price,
                    'volume': random.uniform(1000, 10000),
                })
            
            history.reverse()  # Oldest first
            self._market_history_cache[slug] = history
            return history
            
        except Exception as e:
            log.debug(f"Error fetching history for {slug}: {e}")
            return []
    
    def backtest_from_simulation(self, simulation_file: str = "simulation_data.json") -> Optional[BacktestResult]:
        """
        Run backtest using existing simulation data.
        This validates actual trades made by the bot.
        """
        try:
            with open(simulation_file, 'r') as f:
                data = json.load(f)
        except FileNotFoundError:
            log.error(f"Simulation file not found: {simulation_file}")
            return None
        except Exception as e:
            log.error(f"Error loading simulation data: {e}")
            return None
        
        trades_data = data.get('trades', [])
        if not trades_data:
            log.warning("No trades in simulation data")
            return None
        
        # Convert to BacktestTrade objects
        trades = []
        for t in trades_data:
            try:
                entry_time = datetime.fromisoformat(t.get('timestamp', ''))
                
                # Calculate hold time (if resolved)
                hold_hours = 0
                if t.get('resolved'):
                    # Estimate hold time from status
                    hold_hours = 24  # Default assumption
                
                entry_price = t.get('entry_price', 0)
                exit_price = t.get('exit_price', entry_price)
                pnl = t.get('pnl_usd', 0)
                amount = t.get('amount_usd', 0)
                
                trades.append(BacktestTrade(
                    timestamp=entry_time,
                    market=t.get('market', ''),
                    market_slug=t.get('market_slug', ''),
                    side=t.get('side', 'BUY'),
                    outcome=t.get('outcome', 'YES'),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    amount=amount,
                    shares=t.get('shares', 0),
                    pnl=pnl,
                    pnl_pct=t.get('pnl_pct', 0),
                    hold_time_hours=hold_hours,
                    win=pnl > 0,
                ))
            except Exception as e:
                log.debug(f"Error parsing trade: {e}")
                continue
        
        return self._calculate_results(trades)
    
    def backtest_strategy(
        self,
        markets: List[str],
        days: int = 30,
        min_score: int = 80,
        trade_amount: float = 2.0,
    ) -> BacktestResult:
        """
        Backtest a strategy on historical data.
        
        Args:
            markets: List of market slugs to test
            days: Number of days to backtest
            min_score: Minimum score to enter a trade
            trade_amount: Amount per trade
        """
        trades = []
        
        for slug in markets:
            history = self.fetch_market_history(slug, days)
            if len(history) < 48:  # Need at least 2 days of data
                continue
            
            # Simple strategy: buy when price drops 5%, sell when up 10% or down 15%
            in_position = False
            entry_price = 0
            entry_time = None
            
            for i, candle in enumerate(history[:-1]):
                price = candle['price']
                timestamp = datetime.fromisoformat(candle['timestamp'])
                
                if not in_position:
                    # Look for entry: price dropped from recent high
                    if i > 24:
                        recent_high = max(h['price'] for h in history[i-24:i])
                        if price < recent_high * 0.95:  # 5% drop
                            in_position = True
                            entry_price = price
                            entry_time = timestamp
                
                else:
                    # Look for exit (protect against division by zero)
                    if entry_price <= 0:
                        in_position = False
                        continue
                    change_pct = (price - entry_price) / entry_price
                    
                    if change_pct >= 0.10:  # Take profit at +10%
                        hold_hours = (timestamp - entry_time).total_seconds() / 3600
                        pnl = (price - entry_price) * (trade_amount / entry_price)
                        
                        trades.append(BacktestTrade(
                            timestamp=entry_time,
                            market=slug,
                            market_slug=slug,
                            side='BUY',
                            outcome='YES',
                            entry_price=entry_price,
                            exit_price=price,
                            amount=trade_amount,
                            shares=trade_amount / entry_price,
                            pnl=pnl,
                            pnl_pct=change_pct * 100,
                            hold_time_hours=hold_hours,
                            win=True,
                        ))
                        in_position = False
                    
                    elif change_pct <= -0.15:  # Stop loss at -15%
                        hold_hours = (timestamp - entry_time).total_seconds() / 3600
                        pnl = (price - entry_price) * (trade_amount / entry_price)
                        
                        trades.append(BacktestTrade(
                            timestamp=entry_time,
                            market=slug,
                            market_slug=slug,
                            side='BUY',
                            outcome='YES',
                            entry_price=entry_price,
                            exit_price=price,
                            amount=trade_amount,
                            shares=trade_amount / entry_price,
                            pnl=pnl,
                            pnl_pct=change_pct * 100,
                            hold_time_hours=hold_hours,
                            win=False,
                        ))
                        in_position = False
        
        return self._calculate_results(trades)
    
    def _calculate_results(self, trades: List[BacktestTrade]) -> BacktestResult:
        """Calculate backtest metrics from trades."""
        if not trades:
            return BacktestResult(
                start_date=datetime.now(),
                end_date=datetime.now(),
                initial_balance=self.initial_balance,
                final_balance=self.initial_balance,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0,
                total_pnl=0,
                total_pnl_pct=0,
                max_drawdown=0,
                max_drawdown_pct=0,
                sharpe_ratio=0,
                profit_factor=0,
                avg_win=0,
                avg_loss=0,
                avg_hold_time_hours=0,
                best_trade=0,
                worst_trade=0,
                trades=[],
            )
        
        # Sort by timestamp
        trades.sort(key=lambda t: t.timestamp)
        
        # Basic metrics
        total_pnl = sum(t.pnl for t in trades)
        winning = [t for t in trades if t.win]
        losing = [t for t in trades if not t.win]
        
        win_rate = len(winning) / len(trades) * 100 if trades else 0
        
        # Calculate drawdown
        balance = self.initial_balance
        peak = balance
        max_drawdown = 0
        
        returns = []
        for trade in trades:
            balance += trade.pnl
            returns.append(trade.pnl / self.initial_balance)
            
            if balance > peak:
                peak = balance
            
            drawdown = peak - balance
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # Sharpe ratio (simplified - assumes daily returns)
        sharpe = 0
        if returns and len(returns) > 1:
            avg_return = statistics.mean(returns)
            try:
                std_return = statistics.stdev(returns)
                if std_return > 0:
                    sharpe = (avg_return / std_return) * (252 ** 0.5)
            except statistics.StatisticsError:
                pass  # Not enough data points
        
        # Profit factor
        gross_profit = sum(t.pnl for t in winning) if winning else 0
        gross_loss = abs(sum(t.pnl for t in losing)) if losing else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit
        
        # Averages
        avg_win = statistics.mean([t.pnl for t in winning]) if winning else 0
        avg_loss = statistics.mean([t.pnl for t in losing]) if losing else 0
        avg_hold = statistics.mean([t.hold_time_hours for t in trades]) if trades else 0
        
        return BacktestResult(
            start_date=trades[0].timestamp,
            end_date=trades[-1].timestamp,
            initial_balance=self.initial_balance,
            final_balance=self.initial_balance + total_pnl,
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_pnl_pct=(total_pnl / self.initial_balance) * 100,
            max_drawdown=max_drawdown,
            max_drawdown_pct=(max_drawdown / self.initial_balance) * 100,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
            avg_hold_time_hours=avg_hold,
            best_trade=max(t.pnl for t in trades) if trades else 0,
            worst_trade=min(t.pnl for t in trades) if trades else 0,
            trades=trades,
        )
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary from simulation data."""
        result = self.backtest_from_simulation()
        
        if not result:
            return {'error': 'No simulation data available'}
        
        return result.to_dict()


# Global instance
backtester = Backtester()
