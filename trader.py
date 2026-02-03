"""
Auto-trading module for Polymarket.
Executes trades based on detected opportunities.
VERSION: v22 (production working version)
"""
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, date
from dataclasses import dataclass, field
from enum import Enum

from config import config
from logger import log
from risk_manager import risk_manager

# Import Polymarket CLOB client
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from eth_account import Account
    CLOB_AVAILABLE = True
except ImportError:
    CLOB_AVAILABLE = False
    log.warning("py-clob-client not installed. Trading will be disabled.")


class TradeSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStrategy(Enum):
    MARKET = "market"
    LIMIT = "limit"
    LIMIT_IOC = "limit_ioc"


@dataclass
class TradeResult:
    """Result of a trade attempt."""
    success: bool
    order_id: Optional[str] = None
    side: Optional[TradeSide] = None
    amount: float = 0.0
    price: float = 0.0
    limit_price: Optional[float] = None
    market: str = ""
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    is_dry_run: bool = False
    order_strategy: str = "market"


@dataclass
class DailyStats:
    """Track daily trading statistics."""
    date: date = field(default_factory=date.today)
    total_exposure: float = 0.0
    trades_count: int = 0
    successful_trades: int = 0
    total_pnl: float = 0.0
    
    def can_trade(self, amount: float, max_daily: float) -> bool:
        if self.date != date.today():
            self.date = date.today()
            self.total_exposure = 0.0
            self.trades_count = 0
            self.successful_trades = 0
            self.total_pnl = 0.0
        
        return (self.total_exposure + amount) <= max_daily


class PolymarketTrader:
    """
    Handles automatic trading on Polymarket using the CLOB API.
    """
    
    CLOB_HOST = "https://clob.polymarket.com"
    CHAIN_ID = 137
    
    def __init__(self):
        self.client: Optional[ClobClient] = None
        self.daily_stats = DailyStats()
        self._initialized = False
        
        if not CLOB_AVAILABLE:
            log.error("CLOB client not available. Install py-clob-client.")
            return
        
        if not config.wallet.is_configured:
            log.warning("Wallet not configured. Trading disabled.")
            return
        
        self._initialize_client()
    
    def _initialize_client(self):
        try:
            self.account = Account.from_key(config.wallet.private_key)
            
            self.client = ClobClient(
                self.CLOB_HOST,
                key=config.wallet.private_key,
                chain_id=self.CHAIN_ID,
            )
            
            if not config.polymarket_api.is_configured:
                log.info("Deriving API credentials from wallet...")
                self.client.set_api_creds(self.client.create_or_derive_api_creds())
            else:
                from py_clob_client.clob_types import ApiCreds
                self.client.set_api_creds(ApiCreds(
                    api_key=config.polymarket_api.api_key,
                    api_secret=config.polymarket_api.api_secret,
                    api_passphrase=config.polymarket_api.api_passphrase,
                ))
            
            self._initialized = True
            log.info(f"Trader initialized. Wallet: {self.account.address}")
            
        except Exception as e:
            log.error(f"Failed to initialize trader: {e}")
            self._initialized = False
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and self.client is not None
    
    def get_balance(self) -> Optional[float]:
        if not self.is_ready:
            return None
        
        try:
            balance_info = self.client.get_balance_allowance()
            return float(balance_info.get('balance', 0)) / 1e6
        except Exception as e:
            log.error(f"Error getting balance: {e}")
            return None
    
    def should_trade(self, opportunity: Dict[str, Any]) -> Tuple[bool, str]:
        if not config.trading.auto_trade_enabled:
            return False, "Auto-trading disabled"
        
        # In dry_run mode, we don't need the CLOB client
        if not config.trading.dry_run and not self.is_ready:
            return False, "Trader not initialized"
        
        # SMART TRADER: Check for duplicates first
        try:
            from smart_trader import smart_trader
            # Support both suggested_side and forced_outcome
            outcome = opportunity.get('forced_outcome') or opportunity.get('suggested_side', 'YES')
            can_trade, reason = smart_trader.should_trade(opportunity, outcome)
            if not can_trade:
                return False, f"[SMART] {reason}"
        except ImportError:
            pass  # smart_trader not available, continue
        
        score = opportunity.get('score', 0)
        spread = opportunity.get('spread', 0)
        
        if score < config.trading.min_score_to_trade:
            return False, f"Score {score} below threshold {config.trading.min_score_to_trade}"
        
        if spread > 0 and spread < config.trading.min_spread_to_trade:
            return False, f"Spread {spread:.2%} below threshold {config.trading.min_spread_to_trade:.2%}"
        
        trade_amount = min(config.trading.max_trade_amount, opportunity.get('suggested_amount', 10.0))
        if not config.trading.dry_run:
            if not self.daily_stats.can_trade(trade_amount, config.trading.max_daily_exposure):
                return False, f"Daily exposure limit reached ({config.trading.max_daily_exposure})"
        
        return True, "All checks passed"
    
    def calculate_trade_params(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        poly_yes = opportunity.get('yes', 0)
        poly_no = opportunity.get('no', 1 - poly_yes)
        kalshi_price = opportunity.get('k_yes')
        spread = opportunity.get('spread', 0)
        
        side = TradeSide.BUY
        outcome = "YES"
        target_price = poly_yes
        confidence_boost = 0
        
        if kalshi_price and spread > 0.02:
            if poly_yes < kalshi_price:
                side = TradeSide.BUY
                outcome = "YES"
                target_price = poly_yes
                confidence_boost = spread * 5
                log.debug(f"Arbitrage signal: BUY YES (Poly {poly_yes:.2f} < Kalshi {kalshi_price:.2f})")
            else:
                side = TradeSide.BUY
                outcome = "NO"
                target_price = poly_no
                confidence_boost = spread * 5
                log.debug(f"Arbitrage signal: BUY NO (Poly YES {poly_yes:.2f} > Kalshi {kalshi_price:.2f})")
        else:
            sentiment = opportunity.get('sentiment', 'NEUTRAL')
            social_sentiment = opportunity.get('social_sentiment', 0)
            has_momentum = opportunity.get('has_momentum', False)
            
            if sentiment == 'BEARISH' or social_sentiment < -0.3:
                outcome = "NO"
                target_price = poly_no
            elif poly_yes > 0.85:
                outcome = "YES" if sentiment == 'BULLISH' else "NO"
                target_price = poly_yes if outcome == "YES" else poly_no
            else:
                outcome = "YES"
                target_price = poly_yes
            
            if has_momentum:
                confidence_boost = 0.1
        
        try:
            portfolio_balance = 100.0
            if config.trading.dry_run:
                from simulation_tracker import simulation_tracker
                stats = simulation_tracker.get_stats()
                portfolio_balance = stats.get('total_invested', 100) + 100
            
            kelly_amount, kelly_meta = risk_manager.calculate_position_size(
                opportunity, 
                portfolio_balance
            )
            
            amount = kelly_amount if kelly_amount > 0 else 2.0
            
            win_prob = kelly_meta.get('estimated_win_prob', 0)
            kelly_raw = kelly_meta.get('kelly_raw', 0)
            log.debug(f"Kelly sizing: ${amount:.2f} (win_prob: {win_prob:.1%}, raw_kelly: {kelly_raw:.3f})")
        except Exception as e:
            log.debug(f"Kelly calculation failed, using default: {e}")
            amount = 2.0
        
        limit_discount = 0.015
        if target_price > 0.10:
            limit_price = round(target_price * (1 - limit_discount), 4)
        else:
            limit_price = round(target_price - 0.005, 4)
        
        limit_price = max(0.001, limit_price)
        
        return {
            'side': side,
            'outcome': outcome,
            'price': target_price,
            'limit_price': limit_price,
            'amount': max(1.0, round(amount, 2)),
            'token_id': opportunity.get('token_id'),
            'condition_id': opportunity.get('condition_id'),
            'arbitrage_signal': spread > 0.02 and kalshi_price is not None,
            'order_strategy': 'limit',
        }
    
    def execute_trade(self, opportunity: Dict[str, Any]) -> TradeResult:
        market_title = opportunity.get('question', 'Unknown')[:50]
        
        should, reason = self.should_trade(opportunity)
        if not should:
            log.info(f"Skipping trade: {reason} | Market: {market_title}")
            return TradeResult(
                success=False,
                side=TradeSide.BUY,
                error=reason,
                market=market_title,
                is_dry_run=config.trading.dry_run
            )
        
        params = self.calculate_trade_params(opportunity)
        
        if config.trading.dry_run:
            outcome = params.get('outcome', 'YES')
            arb_tag = " [ARB]" if params.get('arbitrage_signal') else ""
            limit_price = params.get('limit_price', params['price'])
            strategy = params.get('order_strategy', 'market')
            
            log.info(f"[DRY RUN]{arb_tag} Would {params['side'].value} {outcome} ${params['amount']:.2f} @ LIMIT {limit_price:.4f} (mkt: {params['price']:.4f}) | {market_title}")
            self.daily_stats.trades_count += 1
            self.daily_stats.total_exposure += params['amount']
            
            try:
                from simulation_tracker import simulation_tracker
                opp_copy = opportunity.copy()
                if outcome == "YES":
                    opp_copy['yes'] = limit_price
                else:
                    opp_copy['no'] = limit_price
                
                simulation_tracker.record_trade(
                    opp_copy,
                    side=params['side'].value,
                    outcome=outcome
                )
                
                # SMART TRADER: Record position to prevent duplicates
                try:
                    from smart_trader import smart_trader
                    smart_trader.record_trade(opportunity, outcome)
                except ImportError:
                    pass
                    
            except Exception as e:
                log.debug(f"Could not record simulation: {e}")
            
            return TradeResult(
                success=True,
                side=params['side'],
                amount=params['amount'],
                price=params['price'],
                market=market_title,
                is_dry_run=True
            )
        
        try:
            token_id = params.get('token_id')
            if not token_id:
                return TradeResult(
                    success=False,
                    error="No token_id available for market",
                    market=market_title
                )
            
            order_args = OrderArgs(
                token_id=token_id,
                price=params['price'],
                size=params['amount'],
                side=params['side'].value,
            )
            
            log.info(f"Submitting order: {params['side'].value} ${params['amount']:.2f} @ {params['price']:.4f}")
            
            signed_order = self.client.create_order(order_args)
            response = self.client.post_order(signed_order, OrderType.GTC)
            
            order_id = response.get('orderID')
            
            self.daily_stats.trades_count += 1
            self.daily_stats.successful_trades += 1
            self.daily_stats.total_exposure += params['amount']
            
            log.info(f"Order submitted successfully: {order_id}")
            
            return TradeResult(
                success=True,
                order_id=order_id,
                side=params['side'],
                amount=params['amount'],
                price=params['price'],
                market=market_title
            )
            
        except Exception as e:
            log.error(f"Trade execution failed: {e}")
            self.daily_stats.trades_count += 1
            
            return TradeResult(
                success=False,
                error=str(e),
                market=market_title
            )
    
    def get_open_positions(self) -> list:
        if not self.is_ready:
            return []
        
        try:
            positions = self.client.get_positions()
            return positions if positions else []
        except Exception as e:
            log.error(f"Error fetching positions: {e}")
            return []
    
    def get_order_history(self, limit: int = 10) -> list:
        if not self.is_ready:
            return []
        
        try:
            orders = self.client.get_orders()
            return orders[:limit] if orders else []
        except Exception as e:
            log.error(f"Error fetching orders: {e}")
            return []


# Global trader instance
trader = PolymarketTrader()
