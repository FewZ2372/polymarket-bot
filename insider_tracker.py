"""
Insider/Whale tracker for Polymarket.
Monitors top traders and their recent activity.
"""
import requests
from typing import List, Dict, Any, Optional
from datetime import datetime

from logger import log


class InsiderTracker:
    """
    Tracks top traders from Polymarket leaderboard and their activity.
    """
    
    LEADERBOARD_URL = "https://gamma-api.polymarket.com/users"
    
    def __init__(self, limit: int = 20):
        self.limit = limit
        self._traders: List[Dict] = []
        self._last_fetch: Optional[datetime] = None
    
    def fetch_top_traders(self) -> List[Dict[str, Any]]:
        """
        Obtiene los mejores usuarios del leaderboard de Polymarket.
        """
        url = f"{self.LEADERBOARD_URL}?order=profit&ascending=false&limit={self.limit}"
        
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                self._traders = response.json()
                self._last_fetch = datetime.now()
                log.info(f"Fetched {len(self._traders)} top traders from leaderboard")
                return self._traders
        except Exception as e:
            log.warning(f"Error fetching leaderboard: {e}")
        
        return []
    
    def get_trader_summary(self) -> List[Dict[str, Any]]:
        """
        Returns a summary of tracked traders.
        """
        if not self._traders:
            self.fetch_top_traders()
        
        summaries = []
        for trader in self._traders:
            summaries.append({
                'name': trader.get('displayName', 'Anonymous'),
                'address': trader.get('proxyAddress', '0x...'),
                'profit': trader.get('profit', 0),
                'volume': trader.get('volume', 0),
                'positions_count': trader.get('positionsCount', 0),
            })
        
        return summaries
    
    def get_whale_addresses(self) -> List[str]:
        """
        Returns list of whale wallet addresses to monitor.
        """
        if not self._traders:
            self.fetch_top_traders()
        
        return [
            trader.get('proxyAddress') 
            for trader in self._traders 
            if trader.get('proxyAddress')
        ]
    
    def check_whale_activity(self, market_id: str) -> Dict[str, Any]:
        """
        Check if any whales have recent activity in a specific market.
        This would require additional API calls to check positions.
        
        For now, returns a placeholder - would need to implement
        position tracking per whale.
        """
        # TODO: Implement actual whale position tracking
        # This would require polling each whale's positions
        # or using websockets for real-time updates
        
        return {
            'market_id': market_id,
            'whale_activity': False,
            'whales_in_market': [],
            'note': 'Full implementation requires position tracking'
        }
    
    def print_leaderboard(self):
        """Print formatted leaderboard to console."""
        if not self._traders:
            self.fetch_top_traders()
        
        if not self._traders:
            print("No traders found.")
            return
        
        print(f"\n{'='*70}")
        print(f" POLYMARKET TOP TRADERS LEADERBOARD")
        print(f" Updated: {self._last_fetch.strftime('%Y-%m-%d %H:%M:%S') if self._last_fetch else 'Never'}")
        print(f"{'='*70}\n")
        
        print(f"{'#':<4} | {'NAME':<20} | {'PROFIT':<15} | {'VOLUME':<15}")
        print("-" * 70)
        
        for i, trader in enumerate(self._traders[:20], 1):
            name = trader.get('displayName', 'Anon')[:18]
            profit = trader.get('profit', 0)
            volume = trader.get('volume', 0)
            
            print(f"{i:<4} | {name:<20} | ${profit:>12,.2f} | ${volume:>12,.2f}")
        
        print(f"\n{'='*70}\n")


# Global instance
insider_tracker = InsiderTracker()


if __name__ == "__main__":
    tracker = InsiderTracker()
    tracker.print_leaderboard()
