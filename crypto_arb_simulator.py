"""
Crypto Arbitrage Simulator

Simula el funcionamiento del bot de arbitraje de latencia crypto con:
- Posiciones virtuales
- P&L tracking
- Resolución de mercados simulada
- Estadísticas de rendimiento

Usa datos REALES de Binance y Polymarket, pero trades SIMULADOS.
"""

import asyncio
import json
import time
import random
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from logger import log
from crypto_latency_arb import (
    CryptoArbBot, ArbConfig, ArbOpportunity, CryptoMarket,
    BinanceFeed, PolymarketCryptoScanner, LatencyArbDetector,
    CryptoPrice, MarketType
)


# ============================================================================
# SIMULATION CONFIG
# ============================================================================

@dataclass
class SimConfig:
    """Configuration for simulation."""
    # Starting capital
    initial_capital: float = 100.0  # USDC
    
    # Position sizing
    max_position_size: float = 10.0  # Max per trade
    min_position_size: float = 2.0   # Min per trade
    position_size_pct: float = 0.10  # % of capital per trade
    
    # Risk management
    max_open_positions: int = 5
    max_exposure_pct: float = 0.50  # Max 50% of capital at risk
    
    # Edge thresholds
    min_edge_to_trade: float = 0.08  # 8% minimum edge
    min_confidence: float = 0.70     # 70% confidence minimum
    
    # Simulation parameters
    price_impact_pct: float = 0.005  # 0.5% slippage simulation
    
    # Resolution simulation
    # In reality, markets resolve when they reach end_date
    # For simulation, we can simulate faster resolution
    simulate_fast_resolution: bool = True
    resolution_check_interval: int = 30  # seconds
    
    # Demo mode: generates fake opportunities to test the system
    demo_mode: bool = False
    demo_trade_interval: int = 20  # seconds between demo trades
    
    # State persistence
    state_file: str = "crypto_arb_sim_state.json"


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"
    RESOLVED_WIN = "resolved_win"
    RESOLVED_LOSS = "resolved_loss"


@dataclass
class SimulatedPosition:
    """A simulated trading position."""
    id: str
    market_id: str
    market_question: str
    crypto_symbol: str
    threshold_price: float
    market_type: MarketType
    
    # Position details
    side: str  # "YES" or "NO"
    entry_price: float
    size: float  # USDC amount
    shares: float  # Number of shares (size / entry_price)
    
    # Crypto price at entry
    crypto_price_at_entry: float
    
    # Timestamps
    opened_at: datetime
    closed_at: Optional[datetime] = None
    
    # P&L
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    
    # Status
    status: PositionStatus = PositionStatus.OPEN
    resolution_reason: str = ""
    
    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN
    
    @property
    def current_value(self) -> float:
        """Current value if we were to close now."""
        if self.exit_price:
            return self.shares * self.exit_price
        return self.size  # Assume flat if no exit price
    
    def close(self, exit_price: float, reason: str = "manual"):
        """Close the position."""
        self.exit_price = exit_price
        self.closed_at = datetime.now()
        self.pnl = (exit_price - self.entry_price) * self.shares
        self.pnl_pct = (exit_price - self.entry_price) / self.entry_price if self.entry_price > 0 else 0
        
        if self.pnl > 0:
            self.status = PositionStatus.RESOLVED_WIN
        else:
            self.status = PositionStatus.RESOLVED_LOSS
        
        self.resolution_reason = reason
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'crypto_symbol': self.crypto_symbol,
            'threshold_price': self.threshold_price,
            'market_type': self.market_type.value,
            'side': self.side,
            'entry_price': self.entry_price,
            'size': self.size,
            'shares': self.shares,
            'crypto_price_at_entry': self.crypto_price_at_entry,
            'opened_at': self.opened_at.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'exit_price': self.exit_price,
            'pnl': self.pnl,
            'pnl_pct': self.pnl_pct,
            'status': self.status.value,
            'resolution_reason': self.resolution_reason,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SimulatedPosition':
        return cls(
            id=data['id'],
            market_id=data['market_id'],
            market_question=data['market_question'],
            crypto_symbol=data['crypto_symbol'],
            threshold_price=data['threshold_price'],
            market_type=MarketType(data['market_type']),
            side=data['side'],
            entry_price=data['entry_price'],
            size=data['size'],
            shares=data['shares'],
            crypto_price_at_entry=data['crypto_price_at_entry'],
            opened_at=datetime.fromisoformat(data['opened_at']),
            closed_at=datetime.fromisoformat(data['closed_at']) if data.get('closed_at') else None,
            exit_price=data.get('exit_price'),
            pnl=data.get('pnl', 0),
            pnl_pct=data.get('pnl_pct', 0),
            status=PositionStatus(data.get('status', 'open')),
            resolution_reason=data.get('resolution_reason', ''),
        )


# ============================================================================
# PORTFOLIO TRACKER
# ============================================================================

class SimulatedPortfolio:
    """
    Tracks simulated positions and P&L.
    """
    
    def __init__(self, config: SimConfig):
        self.config = config
        self.initial_capital = config.initial_capital
        self.cash = config.initial_capital
        self.positions: Dict[str, SimulatedPosition] = {}
        self.closed_positions: List[SimulatedPosition] = []
        self.trade_count = 0
        self.started_at = datetime.now()
        
        # Stats
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.largest_win = 0.0
        self.largest_loss = 0.0
    
    @property
    def open_positions(self) -> List[SimulatedPosition]:
        return [p for p in self.positions.values() if p.is_open]
    
    @property
    def total_exposure(self) -> float:
        return sum(p.size for p in self.open_positions)
    
    @property
    def available_capital(self) -> float:
        return self.cash
    
    @property
    def total_value(self) -> float:
        return self.cash + self.total_exposure
    
    @property
    def total_return_pct(self) -> float:
        if self.initial_capital == 0:
            return 0
        return (self.total_value - self.initial_capital) / self.initial_capital * 100
    
    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        if total == 0:
            return 0
        return self.wins / total * 100
    
    def can_open_position(self, size: float) -> tuple[bool, str]:
        """Check if we can open a new position."""
        if len(self.open_positions) >= self.config.max_open_positions:
            return False, f"Max positions reached ({self.config.max_open_positions})"
        
        if self.cash < size:
            return False, f"Insufficient capital (need ${size:.2f}, have ${self.cash:.2f})"
        
        exposure_after = (self.total_exposure + size) / self.initial_capital
        if exposure_after > self.config.max_exposure_pct:
            return False, f"Would exceed max exposure ({exposure_after:.1%} > {self.config.max_exposure_pct:.1%})"
        
        return True, "OK"
    
    def calculate_position_size(self, opportunity: ArbOpportunity) -> float:
        """Calculate optimal position size for an opportunity."""
        # Base size: % of available capital
        base_size = self.available_capital * self.config.position_size_pct
        
        # Adjust based on confidence
        confidence_multiplier = opportunity.confidence
        adjusted_size = base_size * (0.5 + confidence_multiplier * 0.5)
        
        # Adjust based on edge (higher edge = bigger position)
        edge_multiplier = min(2.0, 1 + opportunity.edge)
        adjusted_size *= edge_multiplier
        
        # Apply limits
        size = max(self.config.min_position_size, adjusted_size)
        size = min(self.config.max_position_size, size)
        size = min(self.available_capital, size)
        
        return round(size, 2)
    
    def open_position(self, opportunity: ArbOpportunity) -> Optional[SimulatedPosition]:
        """Open a new simulated position."""
        size = self.calculate_position_size(opportunity)
        
        can_open, reason = self.can_open_position(size)
        if not can_open:
            log.warning(f"[Portfolio] Cannot open position: {reason}")
            return None
        
        # Determine entry price with slippage
        if "YES" in opportunity.action:
            side = "YES"
            base_price = opportunity.market.yes_price
        else:
            side = "NO"
            base_price = opportunity.market.no_price
        
        # REALISTIC LIMITS: Don't trade at extreme prices
        if base_price < 0.05:  # Below 5 cents - too risky/unrealistic
            log.debug(f"[Portfolio] Skipping: price too low ({base_price:.4f})")
            return None
        if base_price > 0.95:  # Above 95 cents - not enough upside
            log.debug(f"[Portfolio] Skipping: price too high ({base_price:.4f})")
            return None
        
        # Add slippage (we pay slightly more)
        entry_price = base_price * (1 + self.config.price_impact_pct)
        entry_price = max(0.05, min(0.95, entry_price))  # Keep between 5-95 cents
        
        # Calculate shares with realistic limits
        shares = size / entry_price
        max_shares = 100  # Realistic limit per position
        if shares > max_shares:
            shares = max_shares
            size = shares * entry_price  # Adjust size to match
        
        # Create position
        position = SimulatedPosition(
            id=f"SIM-{self.trade_count + 1:04d}",
            market_id=opportunity.market.market_id,
            market_question=opportunity.market.question,
            crypto_symbol=opportunity.market.crypto_symbol,
            threshold_price=opportunity.market.threshold_price,
            market_type=opportunity.market.market_type,
            side=side,
            entry_price=entry_price,
            size=size,
            shares=shares,
            crypto_price_at_entry=opportunity.crypto_price.price,
            opened_at=datetime.now(),
        )
        
        # Update portfolio
        self.cash -= size
        self.positions[position.id] = position
        self.trade_count += 1
        
        return position
    
    def close_position(self, position_id: str, exit_price: float, reason: str = "manual"):
        """Close a position."""
        if position_id not in self.positions:
            return
        
        position = self.positions[position_id]
        position.close(exit_price, reason)
        
        # Update stats
        self.total_pnl += position.pnl
        if position.pnl > 0:
            self.wins += 1
            self.largest_win = max(self.largest_win, position.pnl)
        else:
            self.losses += 1
            self.largest_loss = min(self.largest_loss, position.pnl)
        
        # Return capital + PnL
        self.cash += position.size + position.pnl
        
        # Move to closed
        self.closed_positions.append(position)
        del self.positions[position_id]
    
    def check_resolutions(self, current_prices: Dict[str, CryptoPrice], markets: Dict[str, CryptoMarket]):
        """
        Check if any positions should be resolved based on current crypto prices.
        
        Resolution logic:
        - If crypto price clearly supports our position, resolve as WIN
        - If crypto price clearly contradicts our position, resolve as LOSS
        - Positions must be at least 30 seconds old to resolve
        """
        for pos_id, position in list(self.positions.items()):
            # Don't resolve positions that are too new
            age_seconds = (datetime.now() - position.opened_at).total_seconds()
            if age_seconds < 30:  # Minimum 30 seconds before resolution
                continue
            
            # Get current crypto price
            symbol = f"{position.crypto_symbol}USDT"
            current_crypto = current_prices.get(symbol)
            
            if not current_crypto or not current_crypto.is_fresh:
                continue
            
            # Determine if position should resolve
            should_resolve = False
            win = False
            
            crypto_price = current_crypto.price
            threshold = position.threshold_price
            buffer = threshold * 0.02  # 2% buffer for more stability
            
            if position.market_type == MarketType.ABOVE:
                if position.side == "YES":
                    # We bet YES on "above threshold"
                    if crypto_price > threshold + buffer:
                        should_resolve = True
                        win = True  # Price is above, YES wins
                    elif crypto_price < threshold - buffer:
                        should_resolve = True
                        win = False  # Price is below, YES loses
                else:  # NO
                    # We bet NO on "above threshold"
                    if crypto_price < threshold - buffer:
                        should_resolve = True
                        win = True  # Price is below, NO wins
                    elif crypto_price > threshold + buffer:
                        should_resolve = True
                        win = False  # Price is above, NO loses
                        
            elif position.market_type == MarketType.BELOW:
                if position.side == "YES":
                    # We bet YES on "below threshold"
                    if crypto_price < threshold - buffer:
                        should_resolve = True
                        win = True
                    elif crypto_price > threshold + buffer:
                        should_resolve = True
                        win = False
                else:  # NO
                    if crypto_price > threshold + buffer:
                        should_resolve = True
                        win = True
                    elif crypto_price < threshold - buffer:
                        should_resolve = True
                        win = False
            
            if should_resolve:
                # Determine exit price - MORE REALISTIC
                # In real markets, resolution isn't always at extremes
                if win:
                    # Winning trade: exit between 80-95 cents (realistic profit)
                    exit_price = 0.80 + random.uniform(0, 0.15)
                else:
                    # Losing trade: exit between 5-20 cents (realistic loss)
                    exit_price = 0.05 + random.uniform(0, 0.15)
                
                reason = f"Resolved: {position.crypto_symbol} @ ${crypto_price:,.2f} vs threshold ${threshold:,.0f}"
                self.close_position(pos_id, exit_price, reason)
                
                result_str = "[WIN]" if win else "[LOSS]"
                log.info(f"""
{result_str} POSITION RESOLVED
   ID: {position.id}
   Market: {position.market_question[:50]}...
   Side: {position.side} @ {position.entry_price:.4f}
   Exit: {exit_price:.4f}
   P&L: ${position.pnl:+.2f} ({position.pnl_pct:+.1%})
   Reason: {reason}
""")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get portfolio statistics."""
        runtime = (datetime.now() - self.started_at).total_seconds()
        
        return {
            'runtime_seconds': runtime,
            'runtime_formatted': str(timedelta(seconds=int(runtime))),
            'initial_capital': self.initial_capital,
            'current_cash': self.cash,
            'total_exposure': self.total_exposure,
            'total_value': self.total_value,
            'total_return_pct': self.total_return_pct,
            'total_pnl': self.total_pnl,
            'trade_count': self.trade_count,
            'open_positions': len(self.open_positions),
            'closed_positions': len(self.closed_positions),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': self.win_rate,
            'largest_win': self.largest_win,
            'largest_loss': self.largest_loss,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize portfolio state."""
        return {
            'config': {
                'initial_capital': self.config.initial_capital,
                'max_position_size': self.config.max_position_size,
                'min_position_size': self.config.min_position_size,
            },
            'cash': self.cash,
            'trade_count': self.trade_count,
            'started_at': self.started_at.isoformat(),
            'total_pnl': self.total_pnl,
            'wins': self.wins,
            'losses': self.losses,
            'largest_win': self.largest_win,
            'largest_loss': self.largest_loss,
            'positions': {k: v.to_dict() for k, v in self.positions.items()},
            'closed_positions': [p.to_dict() for p in self.closed_positions[-50:]],  # Keep last 50
        }
    
    def save_state(self, filepath: str):
        """Save portfolio state to file."""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load_state(cls, filepath: str, config: SimConfig) -> 'SimulatedPortfolio':
        """Load portfolio state from file."""
        portfolio = cls(config)
        
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            portfolio.cash = data.get('cash', config.initial_capital)
            portfolio.trade_count = data.get('trade_count', 0)
            portfolio.total_pnl = data.get('total_pnl', 0)
            portfolio.wins = data.get('wins', 0)
            portfolio.losses = data.get('losses', 0)
            portfolio.largest_win = data.get('largest_win', 0)
            portfolio.largest_loss = data.get('largest_loss', 0)
            
            if data.get('started_at'):
                portfolio.started_at = datetime.fromisoformat(data['started_at'])
            
            for pos_data in data.get('positions', {}).values():
                pos = SimulatedPosition.from_dict(pos_data)
                portfolio.positions[pos.id] = pos
            
            for pos_data in data.get('closed_positions', []):
                pos = SimulatedPosition.from_dict(pos_data)
                portfolio.closed_positions.append(pos)
            
            log.info(f"[Portfolio] Loaded state: ${portfolio.cash:.2f} cash, {len(portfolio.positions)} open positions")
            
        except FileNotFoundError:
            log.info("[Portfolio] No saved state found, starting fresh")
        except Exception as e:
            log.error(f"[Portfolio] Error loading state: {e}")
        
        return portfolio


# ============================================================================
# SIMULATION BOT
# ============================================================================

class CryptoArbSimulator:
    """
    Simulation wrapper for the crypto arbitrage bot.
    
    Uses real market data but simulates trades and tracks performance.
    """
    
    def __init__(self, sim_config: SimConfig = None, arb_config: ArbConfig = None):
        self.sim_config = sim_config or SimConfig()
        self.arb_config = arb_config or ArbConfig(
            min_edge=self.sim_config.min_edge_to_trade,
            auto_trade=False,  # We handle trading in simulation
            dry_run=True,
        )
        
        # Components
        self.binance_feed = BinanceFeed(symbols=self.arb_config.symbols)
        self.poly_scanner = PolymarketCryptoScanner()
        self.arb_detector = LatencyArbDetector(self.arb_config)
        
        # Portfolio
        state_path = Path(self.sim_config.state_file)
        if state_path.exists():
            self.portfolio = SimulatedPortfolio.load_state(str(state_path), self.sim_config)
        else:
            self.portfolio = SimulatedPortfolio(self.sim_config)
        
        # State
        self._running = False
        self._crypto_markets: List[CryptoMarket] = []
        self._markets_by_id: Dict[str, CryptoMarket] = {}
        self._last_market_scan: Optional[datetime] = None
        self._opportunities_seen = 0
        
        # Cooldown tracking to avoid duplicate trades
        self._last_trade_time: Dict[str, datetime] = {}  # market_id -> last trade time
        self._trade_cooldown_seconds = 60  # Don't trade same market within 60 seconds
        
        # Setup callbacks
        self.binance_feed.add_callback(self._on_price_update)
        self.arb_detector.add_callback(self._on_opportunity)
    
    def _on_price_update(self, price: CryptoPrice):
        """Called on each price update from Binance."""
        # Check for opportunities
        symbol = price.symbol.replace('USDT', '')
        relevant_markets = [m for m in self._crypto_markets if m.crypto_symbol == symbol]
        
        for market in relevant_markets:
            self.arb_detector.check_opportunity(market, price)
        
        # Check position resolutions
        if self.portfolio.open_positions:
            prices = self.binance_feed.get_all_prices()
            self.portfolio.check_resolutions(prices, self._markets_by_id)
    
    def _on_opportunity(self, opp: ArbOpportunity):
        """Called when an arbitrage opportunity is detected."""
        self._opportunities_seen += 1
        
        # Check if we should take this trade
        if opp.confidence < self.sim_config.min_confidence:
            return  # Skip silently
        
        if opp.edge < self.sim_config.min_edge_to_trade:
            return  # Skip silently
        
        # Check cooldown - don't trade same market too frequently
        market_id = opp.market.market_id
        if market_id in self._last_trade_time:
            time_since_last = (datetime.now() - self._last_trade_time[market_id]).total_seconds()
            if time_since_last < self._trade_cooldown_seconds:
                return  # Still in cooldown
        
        # Check if we already have a position in this market
        for pos in self.portfolio.open_positions:
            if pos.market_id == market_id:
                return  # Already have position
        
        # Record trade time
        self._last_trade_time[market_id] = datetime.now()
        
        # Open position
        position = self.portfolio.open_position(opp)
        
        if position:
            direction = "[LONG]" if position.side == "YES" else "[SHORT]"
            log.info(f"""
+====================================================================+
| {direction} NEW SIMULATED POSITION OPENED
+--------------------------------------------------------------------+
|  ID: {position.id}
|  Market: {position.market_question[:50]}...
|  
|  Action: BUY {position.side}
|  Entry: {position.entry_price:.4f}
|  Size: ${position.size:.2f} ({position.shares:.2f} shares)
|  
|  {position.crypto_symbol}: ${opp.crypto_price.price:,.2f}
|  Threshold: ${position.threshold_price:,.0f} ({position.market_type.value})
|  
|  Expected Edge: {opp.edge:.1%}
|  Confidence: {opp.confidence:.1%}
|  
|  Portfolio: ${self.portfolio.total_value:.2f} ({self.portfolio.total_return_pct:+.1f}%)
+====================================================================+
""")
    
    async def _market_scan_loop(self):
        """Periodically scan Polymarket for crypto markets."""
        while self._running:
            try:
                markets = self.poly_scanner.scan_markets(
                    min_liquidity=self.arb_config.min_liquidity
                )
                self._crypto_markets = markets
                self._markets_by_id = {m.market_id: m for m in markets}
                self._last_market_scan = datetime.now()
                
            except Exception as e:
                log.error(f"[Sim] Market scan error: {e}")
            
            await asyncio.sleep(self.arb_config.market_scan_interval)
    
    async def _status_loop(self):
        """Print periodic status updates."""
        while self._running:
            await asyncio.sleep(15)  # Every 15 seconds
            
            prices = self.binance_feed.get_all_prices()
            stats = self.portfolio.get_stats()
            
            # Format prices
            price_strs = []
            for symbol, price in prices.items():
                price_strs.append(f"{symbol.replace('USDT', '')}: ${price.price:,.0f}")
            
            # Open positions summary
            positions_str = ""
            for pos in self.portfolio.open_positions[:3]:
                positions_str += f"\n|    - {pos.id}: {pos.side} {pos.crypto_symbol} ${pos.threshold_price:,.0f} @ {pos.entry_price:.2f}"
            
            if len(self.portfolio.open_positions) > 3:
                positions_str += f"\n|    ... +{len(self.portfolio.open_positions) - 3} more"
            
            log.info(f"""
+====================================================================+
| [STATUS] SIMULATION - Runtime: {stats['runtime_formatted']}
+--------------------------------------------------------------------+
| PORTFOLIO
|   Initial: ${stats['initial_capital']:.2f}
|   Current: ${stats['total_value']:.2f} ({stats['total_return_pct']:+.1f}%)
|   Cash: ${stats['current_cash']:.2f} | Exposure: ${stats['total_exposure']:.2f}
|
| PERFORMANCE
|   P&L: ${stats['total_pnl']:+.2f} | Trades: {stats['trade_count']}
|   Win Rate: {stats['win_rate']:.0f}% ({stats['wins']}W / {stats['losses']}L)
|
| MARKETS
|   Crypto: {', '.join(price_strs) if price_strs else 'Loading...'}
|   Monitoring: {len(self._crypto_markets)} markets
|   Opportunities: {self._opportunities_seen}
|
| POSITIONS ({len(self.portfolio.open_positions)}){positions_str}
+====================================================================+
""")
            
            # Save state
            self.portfolio.save_state(self.sim_config.state_file)
    
    async def _resolution_loop(self):
        """Periodically check for position resolutions."""
        while self._running:
            await asyncio.sleep(self.sim_config.resolution_check_interval)
            
            if self.portfolio.open_positions:
                prices = self.binance_feed.get_all_prices()
                self.portfolio.check_resolutions(prices, self._markets_by_id)
    
    async def _demo_trade_loop(self):
        """Generate demo trades for testing the system."""
        if not self.sim_config.demo_mode:
            return
        
        log.info("[Demo] Demo mode enabled - will generate test trades")
        
        # Wait for Binance feed to connect and get prices
        wait_count = 0
        while self._running and wait_count < 30:  # Max 30 seconds wait
            await asyncio.sleep(1)
            wait_count += 1
            prices = self.binance_feed.get_all_prices()
            if prices:
                log.info(f"[Demo] Binance connected with {len(prices)} symbols")
                break
        
        if not self.binance_feed.get_all_prices():
            log.warning("[Demo] Binance feed not connected, demo trades may not work")
        
        while self._running:
            await asyncio.sleep(self.sim_config.demo_trade_interval)
            
            # Check if we can open more positions
            if len(self.portfolio.open_positions) >= self.sim_config.max_open_positions:
                continue
            
            # Pick a random market
            if not self._crypto_markets:
                continue
            
            market = random.choice(self._crypto_markets)
            
            # Get current crypto price
            symbol = f"{market.crypto_symbol}USDT"
            crypto_price = self.binance_feed.get_price(symbol)
            
            if not crypto_price:
                continue
            
            # Create a fake opportunity with random edge
            fake_edge = random.uniform(0.10, 0.35)
            fake_confidence = random.uniform(0.60, 0.95)
            
            # Randomly choose YES or NO
            side = random.choice(["YES", "NO"])
            
            # Create fake opportunity
            fake_opp = ArbOpportunity(
                market=market,
                crypto_price=crypto_price,
                action=f"BUY_{side}",
                edge=fake_edge,
                confidence=fake_confidence,
            )
            
            # Simulate lower edge threshold for demo
            old_min_edge = self.sim_config.min_edge_to_trade
            old_min_conf = self.sim_config.min_confidence
            self.sim_config.min_edge_to_trade = 0.05
            self.sim_config.min_confidence = 0.50
            
            # Open position
            position = self.portfolio.open_position(fake_opp)
            
            # Restore thresholds
            self.sim_config.min_edge_to_trade = old_min_edge
            self.sim_config.min_confidence = old_min_conf
            
            if position:
                log.info(f"""
+====================================================================+
| [DEMO] NEW TEST POSITION OPENED
+--------------------------------------------------------------------+
|  ID: {position.id}
|  Market: {position.market_question[:50]}...
|  Side: {position.side} @ {position.entry_price:.4f}
|  Size: ${position.size:.2f}
|  
|  {market.crypto_symbol}: ${crypto_price.price:,.2f}
|  Threshold: ${market.threshold_price:,.0f}
|  
|  Demo Edge: {fake_edge:.1%} | Confidence: {fake_confidence:.1%}
+====================================================================+
""")
    
    async def _demo_resolution_loop(self):
        """Resolve demo positions after some time."""
        if not self.sim_config.demo_mode:
            return
        
        while self._running:
            await asyncio.sleep(15)  # Check every 15 seconds
            
            for pos_id, position in list(self.portfolio.positions.items()):
                # Resolve after 30-90 seconds
                age = (datetime.now() - position.opened_at).total_seconds()
                min_age = 30
                max_age = 90
                
                if age < min_age:
                    continue
                
                # Probability of resolution increases with age
                resolve_prob = (age - min_age) / (max_age - min_age)
                resolve_prob = min(1.0, resolve_prob)
                
                if random.random() > resolve_prob:
                    continue
                
                # 60% chance of winning (simulated edge)
                win = random.random() < 0.60
                
                if win:
                    exit_price = 0.90 + random.uniform(0, 0.09)  # 90-99 cents
                else:
                    exit_price = 0.01 + random.uniform(0, 0.10)  # 1-11 cents
                
                self.portfolio.close_position(
                    pos_id, 
                    exit_price, 
                    f"Demo resolution after {age:.0f}s"
                )
                
                result_str = "[WIN]" if win else "[LOSS]"
                log.info(f"""
{result_str} DEMO POSITION RESOLVED
   ID: {position.id}
   Side: {position.side} @ {position.entry_price:.4f}
   Exit: {exit_price:.4f}
   P&L: ${position.pnl:+.2f} ({position.pnl_pct:+.1%})
""")
    
    async def start(self):
        """Start the simulation."""
        log.info(f"""
+====================================================================+
|        CRYPTO ARBITRAGE SIMULATOR STARTING                        |
+====================================================================+
|  Mode: SIMULATION (no real trades)
|
|  Capital: ${self.sim_config.initial_capital:.2f}
|  Max Position: ${self.sim_config.max_position_size:.2f}
|  Max Positions: {self.sim_config.max_open_positions}
|  Min Edge: {self.sim_config.min_edge_to_trade:.0%}
|  Min Confidence: {self.sim_config.min_confidence:.0%}
|
|  Symbols: {', '.join(self.arb_config.symbols)}
|
|  Resume: {len(self.portfolio.positions)} open positions
|  Previous P&L: ${self.portfolio.total_pnl:+.2f}
+====================================================================+
""")
        
        self._running = True
        
        # Initial market scan
        markets = self.poly_scanner.scan_markets(min_liquidity=self.arb_config.min_liquidity)
        self._crypto_markets = markets
        self._markets_by_id = {m.market_id: m for m in markets}
        
        # Connect to Binance first
        log.info("[Sim] Connecting to Binance...")
        connected = await self.binance_feed.connect()
        if connected:
            log.info("[Sim] Binance connected!")
        else:
            log.warning("[Sim] Binance connection failed, will retry...")
        
        # Start tasks
        tasks = [
            asyncio.create_task(self.binance_feed.listen()),
            asyncio.create_task(self._market_scan_loop()),
            asyncio.create_task(self._status_loop()),
            asyncio.create_task(self._resolution_loop()),
        ]
        
        # Add demo mode tasks if enabled
        if self.sim_config.demo_mode:
            tasks.append(asyncio.create_task(self._demo_trade_loop()))
            tasks.append(asyncio.create_task(self._demo_resolution_loop()))
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("[Sim] Shutting down...")
        finally:
            await self.stop()
    
    async def stop(self):
        """Stop the simulation."""
        self._running = False
        await self.binance_feed.disconnect()
        
        # Save final state
        self.portfolio.save_state(self.sim_config.state_file)
        
        stats = self.portfolio.get_stats()
        log.info(f"""
+====================================================================+
|        SIMULATION ENDED                                           |
+====================================================================+
|  Runtime: {stats['runtime_formatted']}
|  Final Value: ${stats['total_value']:.2f}
|  Total Return: {stats['total_return_pct']:+.1f}%
|  Total P&L: ${stats['total_pnl']:+.2f}
|  Trades: {stats['trade_count']}
|  Win Rate: {stats['win_rate']:.0f}%
|
|  State saved to: {self.sim_config.state_file}
+====================================================================+
""")


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

async def main():
    """Main entry point for simulation."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Crypto Arbitrage Simulator')
    parser.add_argument('--capital', type=float, default=100.0,
                        help='Starting capital in USDC (default: $100)')
    parser.add_argument('--max-position', type=float, default=10.0,
                        help='Maximum position size (default: $10)')
    parser.add_argument('--max-positions', type=int, default=5,
                        help='Maximum concurrent positions (default: 5)')
    parser.add_argument('--min-edge', type=float, default=0.08,
                        help='Minimum edge to trade (default: 0.08 = 8%%)')
    parser.add_argument('--min-confidence', type=float, default=0.70,
                        help='Minimum confidence (default: 0.70 = 70%%)')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'ETHUSDT'],
                        help='Crypto symbols to monitor')
    parser.add_argument('--reset', action='store_true',
                        help='Reset simulation state')
    parser.add_argument('--state-file', type=str, default='crypto_arb_sim_state.json',
                        help='State file path')
    parser.add_argument('--demo', action='store_true',
                        help='Demo mode - generates test trades automatically')
    
    args = parser.parse_args()
    
    # Reset state if requested
    if args.reset:
        state_path = Path(args.state_file)
        if state_path.exists():
            state_path.unlink()
            log.info(f"Removed state file: {args.state_file}")
    
    # Build configs
    sim_config = SimConfig(
        initial_capital=args.capital,
        max_position_size=args.max_position,
        max_open_positions=args.max_positions,
        min_edge_to_trade=args.min_edge,
        min_confidence=args.min_confidence,
        state_file=args.state_file,
        demo_mode=args.demo,
    )
    
    arb_config = ArbConfig(
        symbols=args.symbols,
        min_edge=args.min_edge,
        min_liquidity=500,  # Lower for more opportunities
    )
    
    # Start simulator
    simulator = CryptoArbSimulator(sim_config, arb_config)
    
    try:
        await simulator.start()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    finally:
        await simulator.stop()


if __name__ == "__main__":
    asyncio.run(main())
