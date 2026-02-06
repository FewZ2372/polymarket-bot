# Detectors package
from .base_detector import BaseDetector, register_detector, DetectorRegistry
from .arbitrage_detector import ArbitrageDetector
from .time_decay_detector import TimeDecayDetector
from .resolution_detector import ResolutionDetector
from .whale_detector import WhaleDetector
from .momentum_detector import MomentumDetector
from .mispricing_detector import MispricingDetector
from .correlation_detector import CorrelationDetector

__all__ = [
    'BaseDetector',
    'register_detector',
    'DetectorRegistry',
    'ArbitrageDetector',
    'TimeDecayDetector',
    'ResolutionDetector',
    'WhaleDetector',
    'MomentumDetector',
    'MispricingDetector',
    'CorrelationDetector',
]
