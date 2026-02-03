"""
On-chain Whale Tracker - Monitors top trader wallets on Polygon.
Detects their trades in REAL-TIME before prices move significantly.
"""
import requests
import time
import json
from typing import Dict, List, Any, Optional, Set, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from collections import defaultdict
import hashlib

from logger import log
from config import config

try:
    from web3 import Web3
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    log.warning("web3 not installed. On-chain tracking limited.")


@dataclass
class WhaleTransaction:
    """A detected whale transaction."""
    tx_hash: str
    whale_address: str
    whale_name: str
    market_id: str
    market_title: str
    action: str  # 'BUY_YES', 'BUY_NO', 'SELL_YES', 'SELL_NO'
    amount_usd: float
    price: float
    block_number: int
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'tx_hash': self.tx_hash[:16] + '...',
            'whale': self.whale_name,
            'address': self.whale_address[:10] + '...',
            'action': self.action,
            'market': self.market_title[:50],
            'amount_usd': self.amount_usd,
            'price': self.price,
            'timestamp': self.timestamp.isoformat(),
        }


@dataclass
class WhaleProfile:
    """Profile of a tracked whale."""
    address: str
    name: str
    total_profit: float
    win_rate: float
    avg_position_size: float
    favorite_categories: List[str]
    recent_trades: List[WhaleTransaction] = field(default_factory=list)
    last_active: Optional[datetime] = None
    tx_count_24h: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'address': self.address[:10] + '...',
            'name': self.name,
            'total_profit': self.total_profit,
            'win_rate': self.win_rate,
            'avg_position_size': self.avg_position_size,
            'favorite_categories': self.favorite_categories[:3],
            'last_active': self.last_active.isoformat() if self.last_active else None,
            'recent_trades_count': len(self.recent_trades),
            'tx_count_24h': self.tx_count_24h,
        }


@dataclass
class CopyTradeSignal:
    """A signal to copy a whale's trade."""
    whale: WhaleProfile
    transaction: WhaleTransaction
    confidence: float  # 0-100
    reason: str
    suggested_amount: float
    urgency: str  # 'HIGH', 'MEDIUM', 'LOW'


class OnchainWhaleTracker:
    """
    Tracks whale wallets on-chain for copy trading signals.
    Uses Polygonscan API for transaction history.
    """
    
    # Polymarket contract addresses on Polygon
    POLYMARKET_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
    POLYMARKET_NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
    USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    
    # Polygon RPC endpoints
    POLYGON_RPC = "https://polygon-rpc.com"
    POLYGONSCAN_API = "https://api.polygonscan.com/api"
    
    # Polymarket API for user data
    POLYMARKET_USERS_API = "https://gamma-api.polymarket.com/users"
    POLYMARKET_MARKETS_API = "https://gamma-api.polymarket.com/markets"
    
    # Method signatures for Polymarket trades
    # These are the first 4 bytes of keccak256 hash of function signatures
    TRADE_SIGNATURES = {
        '0x': 'UNKNOWN',
        # Common ERC1155/CTF methods
        '0xf242432a': 'safeTransferFrom',  # Single token transfer
        '0x2eb2c2d6': 'safeBatchTransferFrom',  # Batch transfer
        '0xa22cb465': 'setApprovalForAll',
    }
    
    def __init__(self, num_whales: int = 30):
        self.num_whales = num_whales
        self._whale_profiles: Dict[str, WhaleProfile] = {}
        self._whale_addresses: Set[str] = set()
        self._recent_transactions: List[WhaleTransaction] = []
        self._seen_tx_hashes: Set[str] = set()
        self._last_fetch = 0
        self._last_tx_check = 0
        self._fetch_interval = 300  # 5 minutes for whale list
        self._tx_check_interval = 60  # 1 minute for transactions
        self._last_block_checked: Dict[str, int] = {}
        
        # Cache for market info
        self._market_cache: Dict[str, Dict] = {}
        
        # Initialize Web3 if available
        self._web3 = None
        if WEB3_AVAILABLE:
            try:
                self._web3 = Web3(Web3.HTTPProvider(self.POLYGON_RPC))
                if self._web3.is_connected():
                    log.info("Connected to Polygon RPC for on-chain tracking")
            except Exception as e:
                log.warning(f"Failed to connect to Polygon: {e}")
    
    def fetch_top_whales(self) -> List[WhaleProfile]:
        """Fetch top traders from Polymarket leaderboard."""
        now = time.time()
        if now - self._last_fetch < self._fetch_interval and self._whale_profiles:
            return list(self._whale_profiles.values())
        
        try:
            url = f"{self.POLYMARKET_USERS_API}?order=profit&ascending=false&limit={self.num_whales}"
            response = requests.get(url, timeout=15)
            
            if response.status_code != 200:
                return list(self._whale_profiles.values())
            
            users = response.json()
            
            for user in users:
                address = user.get('proxyAddress', '')
                if not address:
                    continue
                
                profile = WhaleProfile(
                    address=address,
                    name=user.get('displayName', f"Whale_{address[:8]}"),
                    total_profit=float(user.get('profit', 0)),
                    win_rate=float(user.get('winRate', 0)) * 100 if user.get('winRate') else 0,
                    avg_position_size=float(user.get('avgPositionSize', 0)),
                    favorite_categories=user.get('topCategories', [])[:3] if user.get('topCategories') else [],
                )
                
                self._whale_profiles[address.lower()] = profile
                self._whale_addresses.add(address.lower())
            
            self._last_fetch = now
            log.info(f"Tracking {len(self._whale_profiles)} whale wallets")
            
        except Exception as e:
            log.error(f"Error fetching whales: {e}")
        
        return list(self._whale_profiles.values())
    
    def _get_polygonscan_api_key(self) -> Optional[str]:
        """Get Polygonscan API key from config if available."""
        try:
            return getattr(config, 'polygonscan_api_key', None)
        except Exception:
            return None
    
    def fetch_whale_transactions(self, address: str, blocks_back: int = 1000) -> List[Dict]:
        """
        Fetch recent transactions for a whale address using Polygonscan API.
        """
        transactions = []
        api_key = self._get_polygonscan_api_key()
        
        try:
            # Get internal transactions (contract calls)
            params = {
                'module': 'account',
                'action': 'txlist',
                'address': address,
                'startblock': 0,
                'endblock': 99999999,
                'page': 1,
                'offset': 50,  # Last 50 transactions
                'sort': 'desc',
            }
            
            if api_key:
                params['apikey'] = api_key
            
            response = requests.get(self.POLYGONSCAN_API, params=params, timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('status') == '1':
                    transactions = data.get('result', [])
        
        except Exception as e:
            log.debug(f"Error fetching transactions for {address[:10]}: {e}")
        
        return transactions
    
    def _is_polymarket_transaction(self, tx: Dict) -> bool:
        """Check if a transaction is related to Polymarket."""
        to_address = tx.get('to', '').lower()
        
        return to_address in [
            self.POLYMARKET_CTF_EXCHANGE.lower(),
            self.POLYMARKET_NEG_RISK_CTF_EXCHANGE.lower(),
        ]
    
    def _parse_polymarket_transaction(self, tx: Dict, whale_address: str) -> Optional[WhaleTransaction]:
        """Parse a Polymarket transaction to extract trade details."""
        try:
            tx_hash = tx.get('hash', '')
            
            # Skip if already seen
            if tx_hash in self._seen_tx_hashes:
                return None
            
            self._seen_tx_hashes.add(tx_hash)
            
            # Keep seen hashes manageable (remove oldest half when too large)
            if len(self._seen_tx_hashes) > 10000:
                # Convert to list, take last 5000, convert back to set
                hashes_list = list(self._seen_tx_hashes)
                self._seen_tx_hashes = set(hashes_list[-5000:])
            
            value_wei = int(tx.get('value', 0))
            gas_used = int(tx.get('gasUsed', 0))
            timestamp = int(tx.get('timeStamp', 0))
            block = int(tx.get('blockNumber', 0))
            
            # Parse input data to determine action
            input_data = tx.get('input', '')
            method_id = input_data[:10] if len(input_data) >= 10 else ''
            
            # Estimate USD value (this is approximate)
            # In reality, we'd need to decode the transaction input
            # to get exact token amounts
            amount_usd = 0
            if value_wei > 0:
                amount_usd = value_wei / 1e18 * 0.5  # Rough MATIC to USD
            
            # Use gas as proxy for transaction size
            if gas_used > 200000:
                amount_usd = max(amount_usd, 1000)  # Large transaction
            elif gas_used > 100000:
                amount_usd = max(amount_usd, 500)
            
            # Determine action based on method signature
            action = "TRADE"
            if method_id in ['0xf242432a', '0x2eb2c2d6']:  # safeTransferFrom, safeBatchTransferFrom
                action = "TRANSFER"
            elif method_id == '0xa22cb465':  # setApprovalForAll
                action = "APPROVAL"
            
            whale_profile = self._whale_profiles.get(whale_address.lower())
            whale_name = whale_profile.name if whale_profile else f"Whale_{whale_address[:8]}"
            
            # Parse timestamp safely (avoid 1970 date for timestamp=0)
            if timestamp and timestamp > 0:
                tx_time = datetime.fromtimestamp(timestamp)
            else:
                tx_time = datetime.now()
            
            return WhaleTransaction(
                tx_hash=tx_hash,
                whale_address=whale_address,
                whale_name=whale_name,
                market_id="",  # Would need to decode from input
                market_title="Polymarket Trade",
                action=action,
                amount_usd=amount_usd,
                price=0,
                block_number=block,
                timestamp=tx_time,
            )
            
        except Exception as e:
            log.debug(f"Error parsing transaction: {e}")
            return None
    
    def check_all_whale_activity(self) -> List[WhaleTransaction]:
        """
        Check recent activity for all tracked whales.
        Returns new transactions since last check.
        """
        now = time.time()
        if now - self._last_tx_check < self._tx_check_interval:
            return []
        
        self._last_tx_check = now
        
        if not self._whale_addresses:
            self.fetch_top_whales()
        
        new_transactions = []
        
        # Check top 10 most profitable whales (to avoid rate limits)
        sorted_whales = sorted(
            self._whale_profiles.values(),
            key=lambda w: w.total_profit,
            reverse=True
        )[:10]
        
        for whale in sorted_whales:
            try:
                txs = self.fetch_whale_transactions(whale.address)
                
                for tx in txs:
                    if self._is_polymarket_transaction(tx):
                        parsed = self._parse_polymarket_transaction(tx, whale.address)
                        if parsed:
                            new_transactions.append(parsed)
                            whale.recent_trades.append(parsed)
                            whale.last_active = parsed.timestamp
                            
                            # Keep recent trades limited
                            whale.recent_trades = whale.recent_trades[-20:]
                
                # Update 24h transaction count
                cutoff = datetime.now() - timedelta(hours=24)
                whale.tx_count_24h = len([
                    t for t in whale.recent_trades 
                    if t.timestamp > cutoff
                ])
                
                # Small delay to avoid rate limiting
                time.sleep(0.1)
                
            except Exception as e:
                log.debug(f"Error checking whale {whale.address[:10]}: {e}")
        
        if new_transactions:
            self._recent_transactions.extend(new_transactions)
            self._recent_transactions = self._recent_transactions[-100:]  # Keep last 100
            log.info(f"Detected {len(new_transactions)} new whale transactions")
        
        return new_transactions
    
    def get_copy_trade_signals(self, min_profit: float = 10000) -> List[Dict[str, Any]]:
        """
        Get actionable copy trade signals based on whale activity.
        Only follow whales with significant profit history.
        """
        # Check for new activity first
        self.check_all_whale_activity()
        
        signals = []
        
        # Filter for high-profit whales with recent activity
        profitable_whales = [
            p for p in self._whale_profiles.values()
            if p.total_profit >= min_profit and p.win_rate >= 55
        ]
        
        for whale in profitable_whales:
            if whale.recent_trades:
                # Check for trades in the last hour
                recent_cutoff = datetime.now() - timedelta(hours=1)
                recent_trades = [
                    t for t in whale.recent_trades 
                    if t.timestamp > recent_cutoff
                ]
                
                if recent_trades:
                    latest = recent_trades[-1]
                    
                    # Calculate confidence based on whale's track record
                    confidence = min(100, whale.win_rate + 20)
                    
                    # Boost confidence for very profitable whales
                    if whale.total_profit > 100000:
                        confidence = min(100, confidence + 10)
                    
                    # Determine urgency
                    time_since = datetime.now() - latest.timestamp
                    if time_since < timedelta(minutes=10):
                        urgency = 'HIGH'
                    elif time_since < timedelta(minutes=30):
                        urgency = 'MEDIUM'
                    else:
                        urgency = 'LOW'
                    
                    signals.append({
                        'whale': whale.name,
                        'address': whale.address[:10] + '...',
                        'profit_rank': whale.total_profit,
                        'win_rate': whale.win_rate,
                        'action': latest.action,
                        'market': latest.market_title,
                        'amount': latest.amount_usd,
                        'confidence': confidence,
                        'urgency': urgency,
                        'time_since_trade': str(time_since).split('.')[0],
                        'tx_hash': latest.tx_hash[:16] + '...',
                    })
        
        return sorted(signals, key=lambda x: (x['urgency'] == 'HIGH', x['confidence']), reverse=True)
    
    def get_active_whales(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get whales that have been active in the last N hours."""
        cutoff = datetime.now() - timedelta(hours=hours)
        
        active = []
        for whale in self._whale_profiles.values():
            if whale.last_active and whale.last_active > cutoff:
                active.append({
                    'name': whale.name,
                    'address': whale.address[:10] + '...',
                    'profit': whale.total_profit,
                    'win_rate': whale.win_rate,
                    'last_active': whale.last_active.isoformat(),
                    'tx_count_24h': whale.tx_count_24h,
                    'recent_trades': [t.to_dict() for t in whale.recent_trades[-5:]],
                })
        
        return sorted(active, key=lambda x: x['tx_count_24h'], reverse=True)
    
    def _check_web3_connected(self) -> bool:
        """Safely check if Web3 is connected."""
        try:
            return self._web3.is_connected() if self._web3 else False
        except Exception:
            return False
    
    def get_top_whale_picks(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get current top whale picks."""
        if not self._whale_profiles:
            self.fetch_top_whales()
        
        picks = []
        for profile in sorted(
            self._whale_profiles.values(),
            key=lambda p: p.total_profit,
            reverse=True
        )[:limit]:
            picks.append(profile.to_dict())
        
        return picks
    
    def get_recent_transactions(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent whale transactions."""
        return [t.to_dict() for t in self._recent_transactions[-limit:]]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get whale tracking statistics."""
        if not self._whale_profiles:
            self.fetch_top_whales()
        
        profiles = list(self._whale_profiles.values())
        active_24h = len([p for p in profiles if p.tx_count_24h > 0])
        
        return {
            'whales_tracked': len(profiles),
            'active_whales_24h': active_24h,
            'total_profit_tracked': sum(p.total_profit for p in profiles),
            'avg_win_rate': sum(p.win_rate for p in profiles) / len(profiles) if profiles else 0,
            'web3_connected': self._check_web3_connected(),
            'recent_transactions': len(self._recent_transactions),
            'seen_tx_hashes': len(self._seen_tx_hashes),
            'top_whale': profiles[0].name if profiles else 'N/A',
            'top_whale_profit': profiles[0].total_profit if profiles else 0,
        }


# Global instance
onchain_tracker = OnchainWhaleTracker()
