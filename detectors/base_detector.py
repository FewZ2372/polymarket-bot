"""
BaseDetector - Clase base abstracta para todos los detectores de oportunidades.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime

from models.opportunity import Opportunity
from models.market import Market
from logger import log


class BaseDetector(ABC):
    """
    Clase base para todos los detectores de oportunidades.
    
    Cada detector implementa una estrategia específica para encontrar
    oportunidades de trading (arbitraje, time decay, momentum, etc).
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Inicializa el detector.
        
        Args:
            config: Configuración específica del detector
        """
        self.config = config or {}
        self.name = self.__class__.__name__
        self.enabled = self.config.get('enabled', True)
        
        # Estadísticas
        self.stats = {
            'opportunities_found': 0,
            'last_scan': None,
            'total_scans': 0,
        }
    
    @abstractmethod
    def detect(
        self, 
        markets: List[Market], 
        **kwargs
    ) -> List[Opportunity]:
        """
        Detecta oportunidades en una lista de mercados.
        
        Args:
            markets: Lista de mercados a analizar
            **kwargs: Datos adicionales (news, whale_data, events, etc)
        
        Returns:
            Lista de oportunidades detectadas
        """
        pass
    
    def scan(self, markets: List[Market], **kwargs) -> List[Opportunity]:
        """
        Wrapper que ejecuta detect() con logging y estadísticas.
        """
        if not self.enabled:
            return []
        
        self.stats['total_scans'] += 1
        self.stats['last_scan'] = datetime.now()
        
        try:
            # Filtrar mercados válidos
            valid_markets = self.filter_valid_markets(markets)
            
            # Detectar oportunidades
            opportunities = self.detect(valid_markets, **kwargs)
            
            # Agregar metadata
            for opp in opportunities:
                opp.detector_name = self.name
                opp.detected_at = datetime.now()
                opp.calculate_rank_score()
            
            self.stats['opportunities_found'] += len(opportunities)
            
            if opportunities:
                log.info(f"[{self.name}] Found {len(opportunities)} opportunities")
                for opp in opportunities[:3]:  # Log top 3
                    log.info(f"  {opp}")
            
            return opportunities
            
        except Exception as e:
            log.error(f"[{self.name}] Error during detection: {e}")
            return []
    
    def filter_valid_markets(self, markets: List[Market]) -> List[Market]:
        """
        Filtra mercados inválidos.
        
        Override en subclases para filtros específicos.
        """
        return [
            m for m in markets
            if m.is_active 
            and not m.is_closed
            and m.yes_price > 0 
            and m.yes_price < 1
        ]
    
    def get_min_confidence(self) -> int:
        """Retorna la confianza mínima configurada."""
        return self.config.get('min_confidence', 60)
    
    def get_min_profit(self) -> float:
        """Retorna el profit mínimo configurado."""
        return self.config.get('min_profit', 3.0)
    
    def log_opportunity(self, opportunity: Opportunity):
        """Log de oportunidad detectada."""
        log.info(
            f"[{self.name}] OPPORTUNITY: {opportunity.type.value} | "
            f"Action: {opportunity.action.value} | "
            f"Profit: {opportunity.expected_profit:.1f}% | "
            f"Conf: {opportunity.confidence}% | "
            f"EV: {opportunity.expected_value:.1f}"
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estadísticas del detector."""
        return {
            'name': self.name,
            'enabled': self.enabled,
            **self.stats
        }
    
    def __repr__(self) -> str:
        return f"{self.name}(enabled={self.enabled})"


class DetectorRegistry:
    """
    Registry de detectores disponibles.
    Permite registrar y obtener detectores dinámicamente.
    """
    
    _detectors: Dict[str, type] = {}
    
    @classmethod
    def register(cls, name: str, detector_class: type):
        """Registra un detector."""
        cls._detectors[name] = detector_class
    
    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """Obtiene una clase de detector por nombre."""
        return cls._detectors.get(name)
    
    @classmethod
    def get_all(cls) -> Dict[str, type]:
        """Retorna todos los detectores registrados."""
        return cls._detectors.copy()
    
    @classmethod
    def create_all(cls, config: Dict[str, Any]) -> List[BaseDetector]:
        """
        Crea instancias de todos los detectores registrados.
        
        Args:
            config: Configuración global (cada detector puede tener su sección)
        
        Returns:
            Lista de instancias de detectores
        """
        detectors = []
        for name, detector_class in cls._detectors.items():
            detector_config = config.get(name, {})
            try:
                detector = detector_class(detector_config)
                detectors.append(detector)
            except Exception as e:
                log.error(f"Error creating detector {name}: {e}")
        
        return detectors


def register_detector(name: str):
    """
    Decorator para registrar detectores automáticamente.
    
    Uso:
        @register_detector('arbitrage')
        class ArbitrageDetector(BaseDetector):
            ...
    """
    def decorator(cls):
        DetectorRegistry.register(name, cls)
        return cls
    return decorator
