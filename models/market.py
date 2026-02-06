"""
Market model - Representa un mercado de Polymarket.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum


class MarketCategory(Enum):
    """Categorías de mercados."""
    POLITICS = "politics"
    CRYPTO = "crypto"
    SPORTS = "sports"
    SCIENCE = "science"
    BUSINESS = "business"
    ENTERTAINMENT = "entertainment"
    OTHER = "other"


@dataclass
class Market:
    """Representa un mercado de Polymarket."""
    
    # Identificadores
    id: str = ""
    condition_id: str = ""
    slug: str = ""
    question: str = ""
    
    # Precios
    yes_price: float = 0.0
    no_price: float = 0.0
    
    # Volumen y liquidez
    volume_24h: float = 0.0
    volume_total: float = 0.0
    liquidity: float = 0.0
    
    # Cambios de precio
    price_change_1h: float = 0.0
    price_change_24h: float = 0.0
    
    # Timing
    end_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    
    # Metadata
    category: MarketCategory = MarketCategory.OTHER
    is_active: bool = True
    is_closed: bool = False
    
    # Token IDs para trading
    token_id_yes: Optional[str] = None
    token_id_no: Optional[str] = None
    
    # Datos raw de la API
    raw_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def days_to_resolution(self) -> Optional[float]:
        """Días hasta la resolución del mercado."""
        if not self.end_date:
            return None
        
        delta = self.end_date - datetime.now()
        return delta.total_seconds() / 86400
    
    @property
    def is_short_term(self) -> bool:
        """¿Es un mercado de corto plazo (<14 días)?"""
        days = self.days_to_resolution
        return days is not None and 0 < days <= 14
    
    @property
    def is_medium_term(self) -> bool:
        """¿Es un mercado de mediano plazo (14-30 días)?"""
        days = self.days_to_resolution
        return days is not None and 14 < days <= 30
    
    @property
    def is_long_term(self) -> bool:
        """¿Es un mercado de largo plazo (>30 días)?"""
        days = self.days_to_resolution
        return days is not None and days > 30
    
    @property
    def has_good_liquidity(self) -> bool:
        """¿Tiene buena liquidez (>$10K volume 24h)?"""
        return self.volume_24h >= 10000
    
    @property
    def is_volatile(self) -> bool:
        """¿Es un mercado volátil (>5% cambio en 24h)?"""
        return abs(self.price_change_24h) > 0.05
    
    @property
    def price_sum(self) -> float:
        """Suma de precios YES + NO (debería ser ~1.0)."""
        return self.yes_price + self.no_price
    
    @property
    def has_price_mismatch(self) -> bool:
        """¿Hay mismatch en precios (YES + NO != 100%)?"""
        total = self.price_sum
        return total < 0.98 or total > 1.02
    
    @property
    def spread(self) -> float:
        """Spread entre YES y NO (para mercados 50/50)."""
        return abs(self.yes_price - self.no_price)
    
    def is_technical_category(self) -> bool:
        """¿Es una categoría técnica (más predecible)?"""
        technical = [
            MarketCategory.POLITICS,
            MarketCategory.CRYPTO,
            MarketCategory.SCIENCE,
            MarketCategory.BUSINESS,
        ]
        return self.category in technical
    
    def is_noise_category(self) -> bool:
        """¿Es una categoría ruidosa (menos predecible)?"""
        noise = [
            MarketCategory.SPORTS,
            MarketCategory.ENTERTAINMENT,
        ]
        return self.category in noise
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'Market':
        """
        Crea un Market desde la respuesta de la API de Polymarket.
        """
        import json
        
        # Parse prices
        prices_str = data.get('outcomePrices', '[]')
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            yes_price = float(prices[0]) if len(prices) > 0 else 0.0
            no_price = float(prices[1]) if len(prices) > 1 else 0.0
        except (json.JSONDecodeError, ValueError, IndexError):
            yes_price = 0.0
            no_price = 0.0
        
        # Parse end date
        end_date = None
        if data.get('endDate'):
            try:
                end_str = data['endDate'].replace('Z', '+00:00')
                end_date = datetime.fromisoformat(end_str)
                if end_date.tzinfo is not None:
                    end_date = end_date.replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass
        
        # Parse created date
        created_at = None
        if data.get('createdAt'):
            try:
                created_str = data['createdAt'].replace('Z', '+00:00')
                created_at = datetime.fromisoformat(created_str)
                if created_at.tzinfo is not None:
                    created_at = created_at.replace(tzinfo=None)
            except (ValueError, AttributeError):
                pass
        
        # Parse category
        cat_str = data.get('category', '').lower()
        category = MarketCategory.OTHER
        for cat in MarketCategory:
            if cat.value in cat_str:
                category = cat
                break
        
        # Token IDs
        clob_ids = data.get('clobTokenIds', [])
        token_id_yes = clob_ids[0] if len(clob_ids) > 0 else None
        token_id_no = clob_ids[1] if len(clob_ids) > 1 else None
        
        return cls(
            id=data.get('id', ''),
            condition_id=data.get('conditionId', ''),
            slug=data.get('slug', ''),
            question=data.get('question', ''),
            yes_price=yes_price,
            no_price=no_price,
            volume_24h=float(data.get('volume24hr', 0) or 0),
            volume_total=float(data.get('volume', 0) or 0),
            liquidity=float(data.get('liquidity', 0) or 0),
            price_change_1h=float(data.get('oneHourPriceChange', 0) or 0),
            price_change_24h=float(data.get('oneDayPriceChange', 0) or 0),
            end_date=end_date,
            created_at=created_at,
            category=category,
            is_active=data.get('active', True),
            is_closed=data.get('closed', False),
            token_id_yes=token_id_yes,
            token_id_no=token_id_no,
            raw_data=data,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario."""
        return {
            'id': self.id,
            'condition_id': self.condition_id,
            'slug': self.slug,
            'question': self.question,
            'yes_price': self.yes_price,
            'no_price': self.no_price,
            'volume_24h': self.volume_24h,
            'price_change_1h': self.price_change_1h,
            'price_change_24h': self.price_change_24h,
            'days_to_resolution': self.days_to_resolution,
            'category': self.category.value,
            'is_active': self.is_active,
        }
    
    def __repr__(self) -> str:
        return (
            f"Market('{self.question[:40]}...', "
            f"YES={self.yes_price:.0%}, "
            f"vol=${self.volume_24h:,.0f})"
        )
