"""
Position model - Representa una posición abierta.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class PositionSide(Enum):
    """Lado de la posición."""
    YES = "yes"
    NO = "no"


class PositionStatus(Enum):
    """Estado de la posición."""
    OPEN = "open"
    CLOSED = "closed"
    RESOLVED = "resolved"


@dataclass
class Position:
    """Representa una posición abierta o cerrada."""
    
    # Identificación
    id: str = ""
    market_id: str = ""
    market_question: str = ""
    
    # Posición
    side: PositionSide = PositionSide.YES
    entry_price: float = 0.0
    amount: float = 0.0  # USD invertido
    shares: float = 0.0  # Cantidad de shares
    
    # Estado actual
    status: PositionStatus = PositionStatus.OPEN
    current_price: float = 0.0
    
    # Exit info (si cerrada)
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    
    # Timing
    opened_at: datetime = field(default_factory=datetime.now)
    closed_at: Optional[datetime] = None
    
    # PnL
    realized_pnl: float = 0.0  # Si cerrada
    
    # Metadata
    opportunity_type: str = ""  # Tipo de oportunidad que originó
    extra_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def unrealized_pnl(self) -> float:
        """PnL no realizado (basado en precio actual)."""
        if self.status != PositionStatus.OPEN:
            return 0.0
        
        if self.entry_price == 0:
            return 0.0
        
        # Calcular cambio de precio
        price_diff = self.current_price - self.entry_price
        
        # PnL = shares * price_diff
        return self.shares * price_diff
    
    @property
    def unrealized_pnl_pct(self) -> float:
        """PnL no realizado en porcentaje."""
        if self.amount == 0:
            return 0.0
        
        return (self.unrealized_pnl / self.amount) * 100
    
    @property
    def current_value(self) -> float:
        """Valor actual de la posición."""
        return self.shares * self.current_price
    
    @property
    def holding_time_hours(self) -> float:
        """Tiempo que ha estado abierta la posición en horas."""
        end = self.closed_at or datetime.now()
        delta = end - self.opened_at
        return delta.total_seconds() / 3600
    
    @property
    def holding_time_days(self) -> float:
        """Tiempo que ha estado abierta la posición en días."""
        return self.holding_time_hours / 24
    
    def should_take_profit(self, target_pct: float) -> bool:
        """¿Debería tomar ganancias?"""
        return self.unrealized_pnl_pct >= target_pct
    
    def should_stop_loss(self, stop_pct: float) -> bool:
        """¿Debería cortar pérdidas?"""
        return self.unrealized_pnl_pct <= -stop_pct
    
    def close(self, exit_price: float, reason: str = "manual") -> float:
        """
        Cierra la posición y calcula PnL realizado.
        
        Returns:
            PnL realizado
        """
        self.exit_price = exit_price
        self.exit_reason = reason
        self.closed_at = datetime.now()
        self.status = PositionStatus.CLOSED
        
        # Calcular PnL realizado
        price_diff = exit_price - self.entry_price
        self.realized_pnl = self.shares * price_diff
        
        return self.realized_pnl
    
    def resolve(self, won: bool) -> float:
        """
        Resuelve la posición (el mercado terminó).
        
        Args:
            won: Si la posición ganó (YES resolvió YES, NO resolvió NO)
        
        Returns:
            PnL realizado
        """
        self.status = PositionStatus.RESOLVED
        self.closed_at = datetime.now()
        
        if won:
            # Ganamos: recibimos $1 por share
            self.exit_price = 1.0
            self.realized_pnl = self.shares * (1.0 - self.entry_price)
        else:
            # Perdimos: recibimos $0 por share
            self.exit_price = 0.0
            self.realized_pnl = -self.amount
        
        self.exit_reason = "resolved_win" if won else "resolved_loss"
        
        return self.realized_pnl
    
    def update_price(self, new_price: float):
        """Actualiza el precio actual."""
        self.current_price = new_price
    
    def to_dict(self) -> Dict[str, Any]:
        """Convierte a diccionario."""
        return {
            'id': self.id,
            'market_id': self.market_id,
            'market_question': self.market_question,
            'side': self.side.value,
            'entry_price': self.entry_price,
            'amount': self.amount,
            'shares': self.shares,
            'status': self.status.value,
            'current_price': self.current_price,
            'unrealized_pnl': self.unrealized_pnl,
            'unrealized_pnl_pct': self.unrealized_pnl_pct,
            'realized_pnl': self.realized_pnl,
            'exit_price': self.exit_price,
            'exit_reason': self.exit_reason,
            'opened_at': self.opened_at.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'holding_time_hours': self.holding_time_hours,
            'opportunity_type': self.opportunity_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Position':
        """Crea desde diccionario."""
        pos = cls(
            id=data.get('id', ''),
            market_id=data.get('market_id', ''),
            market_question=data.get('market_question', ''),
            side=PositionSide(data.get('side', 'yes')),
            entry_price=data.get('entry_price', 0),
            amount=data.get('amount', 0),
            shares=data.get('shares', 0),
            status=PositionStatus(data.get('status', 'open')),
            current_price=data.get('current_price', 0),
            exit_price=data.get('exit_price'),
            exit_reason=data.get('exit_reason'),
            realized_pnl=data.get('realized_pnl', 0),
            opportunity_type=data.get('opportunity_type', ''),
            extra_data=data.get('extra_data', {}),
        )
        
        if data.get('opened_at'):
            pos.opened_at = datetime.fromisoformat(data['opened_at'])
        
        if data.get('closed_at'):
            pos.closed_at = datetime.fromisoformat(data['closed_at'])
        
        return pos
    
    def __repr__(self) -> str:
        pnl = self.unrealized_pnl if self.status == PositionStatus.OPEN else self.realized_pnl
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        return (
            f"Position({self.side.value.upper()}, "
            f"${self.amount:.2f} @ {self.entry_price:.2%}, "
            f"PnL: {pnl_str})"
        )
