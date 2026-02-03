"""
Simulation Tracker - Tracks virtual trades and calculates hypothetical P&L.
Persists data to JSON file for analysis.
VERSION: v22 (production working version)
"""
import json
import os
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, field, asdict
from pathlib import Path

from logger import log


@dataclass
class SimulatedTrade:
    """Represents a simulated trade."""
    id: str
    timestamp: str
    market: str
    market_slug: str
    side: str  # BUY or SELL
    outcome: str  # YES or NO
    entry_price: float
    amount_usd: float
    shares: float  # amount / price
    score: int
    spread: float
    sentiment: Optional[str] = None
    
    # These get filled in when we check the outcome
    current_price: Optional[float] = None
    exit_price: Optional[float] = None
    resolved: bool = False
    resolution: Optional[str] = None  # YES, NO, or None if not resolved
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "OPEN"  # OPEN, WIN, LOSS, PENDING
    
    def calculate_pnl(self, current_price: float) -> float:
        """Calculate unrealized P&L based on current price."""
        self.current_price = current_price
        # If we bought YES, we profit if price goes up
        if self.side == "BUY" and self.outcome == "YES":
            self.pnl_usd = (current_price - self.entry_price) * self.shares
        elif self.side == "BUY" and self.outcome == "NO":
            self.pnl_usd = (current_price - self.entry_price) * self.shares
        
        self.pnl_pct = (self.pnl_usd / self.amount_usd) * 100 if self.amount_usd > 0 else 0
        return self.pnl_usd
    
    def resolve(self, winning_outcome: str):
        """Resolve the trade when market settles."""
        self.resolved = True
        self.resolution = winning_outcome
        
        if self.outcome == winning_outcome:
            # We win - we get $1 per share
            self.exit_price = 1.0
            self.pnl_usd = (1.0 - self.entry_price) * self.shares
            self.status = "WIN"
        else:
            # We lose - shares worth $0
            self.exit_price = 0.0
            self.pnl_usd = -self.amount_usd
            self.status = "LOSS"
        
        self.pnl_pct = (self.pnl_usd / self.amount_usd) * 100 if self.amount_usd > 0 else 0


class SimulationTracker:
    """
    Tracks all simulated trades and provides analytics.
    """
    
    def __init__(self, data_file: str = "simulation_data.json"):
        self.data_file = Path(data_file)
        self.trades: List[SimulatedTrade] = []
        self.starting_balance: float = 10.0
        self.load()
    
    def load(self):
        """Load simulation data from file."""
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.starting_balance = data.get('starting_balance', 10.0)
                    self.trades = [
                        SimulatedTrade(**t) for t in data.get('trades', [])
                    ]
                log.info(f"Loaded {len(self.trades)} simulated trades from {self.data_file}")
            except Exception as e:
                log.error(f"Error loading simulation data: {e}")
                self.trades = []
    
    def save(self):
        """Save simulation data to file."""
        try:
            data = {
                'starting_balance': self.starting_balance,
                'last_updated': datetime.now().isoformat(),
                'trades': [asdict(t) for t in self.trades]
            }
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
            log.debug(f"Saved simulation data to {self.data_file}")
        except Exception as e:
            log.error(f"Error saving simulation data: {e}")
    
    def record_trade(self, opportunity: Dict[str, Any], side: str = "BUY", outcome: str = "YES") -> Optional[SimulatedTrade]:
        """
        Record a new simulated trade.
        In simulation mode, we track ALL opportunities (no balance limit).
        """
        entry_price = opportunity.get('yes', 0) if outcome == "YES" else opportunity.get('no', 0)
        amount = 5.0  # Increased to $5 per trade (was $2)
        
        # Avoid division by zero and unrealistic prices
        if entry_price <= 0.001:
            log.warning(f"Entry price too low ({entry_price}), skipping simulation")
            return None
        
        shares = amount / entry_price
        
        trade = SimulatedTrade(
            id=f"sim_{int(datetime.now().timestamp())}",
            timestamp=datetime.now().isoformat(),
            market=opportunity.get('question', 'Unknown')[:100],
            market_slug=opportunity.get('slug', ''),
            side=side,
            outcome=outcome,
            entry_price=entry_price,
            amount_usd=amount,
            shares=shares,
            score=opportunity.get('score', 0),
            spread=opportunity.get('spread', 0),
            sentiment=opportunity.get('sentiment'),
        )
        
        self.trades.append(trade)
        self.save()
        
        log.info(f"[SIM] Recorded trade: {side} {outcome} ${amount:.2f} @ {entry_price:.4f} | {trade.market[:40]}")
        
        return trade
    
    def get_available_balance(self) -> float:
        """Calculate available balance (starting - open positions)."""
        open_exposure = sum(t.amount_usd for t in self.trades if t.status == "OPEN")
        return self.starting_balance - open_exposure
    
    def get_total_pnl(self) -> float:
        """Calculate total P&L from all trades."""
        return sum(t.pnl_usd for t in self.trades)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive simulation statistics."""
        if not self.trades:
            return {
                'total_trades': 0,
                'total_invested': 0,
                'total_pnl': 0,
                'pnl_pct': 0,
                'win_rate': 0,
                'open_positions': 0,
            }
        
        closed = [t for t in self.trades if t.status in ["WIN", "LOSS", "EXITED"]]
        open_trades = [t for t in self.trades if t.status == "OPEN"]
        wins = [t for t in closed if t.status == "WIN" or (t.status == "EXITED" and t.pnl_usd > 0)]
        losses = [t for t in closed if t.status == "LOSS" or (t.status == "EXITED" and t.pnl_usd <= 0)]
        
        total_invested = sum(t.amount_usd for t in self.trades)
        total_pnl = self.get_total_pnl()
        realized_pnl = sum(t.pnl_usd for t in closed)
        unrealized_pnl = sum(t.pnl_usd for t in open_trades)
        
        return {
            'total_trades': len(self.trades),
            'open_positions': len(open_trades),
            'closed_trades': len(closed),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': (len(wins) / len(closed) * 100) if closed else 0,
            'total_invested': total_invested,
            'total_pnl': total_pnl,
            'realized_pnl': realized_pnl,
            'unrealized_pnl': unrealized_pnl,
            'pnl_pct': (total_pnl / total_invested * 100) if total_invested > 0 else 0,
            'avg_trade_size': total_invested / len(self.trades) if self.trades else 0,
            'best_trade': max((t.pnl_usd for t in closed), default=0),
            'worst_trade': min((t.pnl_usd for t in closed), default=0),
        }
    
    def get_open_trades(self) -> List[SimulatedTrade]:
        """Get all open (unresolved) trades."""
        return [t for t in self.trades if t.status == "OPEN"]
    
    def get_closed_trades(self) -> List[SimulatedTrade]:
        """Get all closed trades (resolved, exited, or lost)."""
        return [t for t in self.trades if t.status in ["WIN", "LOSS", "EXITED"]]
    
    def print_report(self):
        """Print a detailed simulation report."""
        stats = self.get_stats()
        
        print(f"\n{'='*70}")
        print(f" SIMULATION REPORT")
        print(f" Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")
        
        print(f"Starting Balance:  ${self.starting_balance:.2f}")
        print(f"Total Invested:    ${stats['total_invested']:.2f}")
        print(f"Total P&L:         ${stats['total_pnl']:+.2f} ({stats['pnl_pct']:+.1f}%)")
        print(f"\nTotal Trades:      {stats['total_trades']}")
        print(f"Open Positions:    {stats['open_positions']}")
        print(f"Closed:            {stats['closed_trades']}")
        print(f"Win Rate:          {stats['win_rate']:.1f}%")
        
        if self.trades:
            print(f"\n{'-'*70}")
            print(f"{'STATUS':<8} | {'MARKET':<35} | {'ENTRY':<7} | {'P&L':<10}")
            print(f"{'-'*70}")
            
            for trade in self.trades[-10:]:  # Last 10 trades
                pnl_str = f"${trade.pnl_usd:+.2f}" if trade.pnl_usd != 0 else "pending"
                print(f"{trade.status:<8} | {trade.market[:33]:<35} | ${trade.entry_price:<6.4f} | {pnl_str:<10}")
        
        print(f"\n{'='*70}\n")


# Global instance
simulation_tracker = SimulationTracker()


if __name__ == "__main__":
    # Test the tracker
    tracker = SimulationTracker()
    tracker.print_report()
