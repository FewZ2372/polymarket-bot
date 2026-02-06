"""
Tests para TimeDecayDetector.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.time_decay_detector import TimeDecayDetector, calculate_theta, is_deadline_market
from models.market import Market
from tests.mock_data import generate_time_decay_scenario, generate_random_market
from datetime import datetime, timedelta


class TestDeadlineApproaching:
    """Tests para detección de deadline approaching."""
    
    def test_detects_deadline_market(self):
        """Debe detectar mercado con deadline cercano."""
        detector = TimeDecayDetector({
            'max_days_deadline': 14,
            'min_daily_theta': 0.01
        })
        
        # Mercado con deadline en 3 días
        market = generate_time_decay_scenario(days_left=3, event_unlikely=False)
        market.question = "Will government shutdown happen before Feb 10?"
        market.yes_price = 0.25
        
        opps = detector.detect([market])
        
        assert len(opps) >= 1
        opp = opps[0]
        assert opp.type.value == 'time_decay'
        assert opp.action.value == 'buy_no'
        assert opp.confidence >= 70
        
        print(f"[OK] Detected deadline approaching: {opp}")
        print(f"  Days left: {market.days_to_resolution:.1f}")
        print(f"  Daily theta: {opp.extra_data.get('daily_theta', 0):.2%}")
    
    def test_ignores_far_deadline(self):
        """No debe detectar deadline muy lejano."""
        detector = TimeDecayDetector({'max_days_deadline': 14})
        
        market = generate_random_market(
            days_to_resolution=30,
            yes_price=0.20
        )
        market.question = "Will X happen before March 15?"
        
        opps = detector.detect([market])
        
        # No debería detectar (>14 días)
        assert len(opps) == 0
        print("[OK] Correctly ignored far deadline (30 days)")
    
    def test_ignores_high_yes_price(self):
        """No debe detectar si YES es muy alto (evento probable)."""
        detector = TimeDecayDetector()
        
        market = generate_random_market(
            days_to_resolution=5,
            yes_price=0.80  # Evento muy probable
        )
        market.question = "Will X happen before Feb 10?"
        
        opps = detector.detect([market])
        
        # No debería detectar (YES > 70%)
        time_decay_opps = [o for o in opps if o.type.value == 'time_decay']
        assert len(time_decay_opps) == 0
        print("[OK] Correctly ignored high-YES market (80%)")
    
    def test_ignores_non_deadline_market(self):
        """No debe detectar mercados sin keyword de deadline."""
        detector = TimeDecayDetector()
        
        market = generate_random_market(
            days_to_resolution=5,
            yes_price=0.20
        )
        market.question = "Will Bitcoin reach $100K?"  # Sin "before/by"
        
        opps = detector.detect([market])
        
        # Puede detectar improbable pero no time_decay
        time_decay_opps = [o for o in opps if o.type.value == 'time_decay']
        assert len(time_decay_opps) == 0
        print("[OK] Correctly ignored non-deadline market")


class TestImprobableExpiring:
    """Tests para detección de eventos improbables expirando."""
    
    def test_detects_improbable_event(self):
        """Debe detectar evento claramente improbable."""
        detector = TimeDecayDetector({'max_yes_price_improbable': 0.15})
        
        market = generate_time_decay_scenario(days_left=10, event_unlikely=True)
        market.question = "Will alien contact be confirmed by Feb 28?"
        market.yes_price = 0.04
        
        opps = detector.detect([market])
        
        improbable_opps = [o for o in opps if o.type.value == 'improbable_expiring']
        assert len(improbable_opps) >= 1
        
        opp = improbable_opps[0]
        assert opp.action.value == 'buy_no'
        assert opp.confidence >= 90  # Alto para eventos claramente imposibles
        
        print(f"[OK] Detected improbable event: {opp}")
        print(f"  Confidence: {opp.confidence}%")
    
    def test_detects_very_low_yes(self):
        """Debe detectar cualquier YES muy bajo como improbable."""
        detector = TimeDecayDetector()
        
        market = generate_random_market(
            days_to_resolution=15,
            yes_price=0.02  # 2% = muy improbable
        )
        market.question = "Will something random happen?"
        
        opps = detector.detect([market])
        
        improbable_opps = [o for o in opps if o.type.value == 'improbable_expiring']
        assert len(improbable_opps) >= 1
        
        print("[OK] Detected very low YES as improbable (2%)")
    
    def test_ignores_probable_event(self):
        """No debe detectar evento con YES alto."""
        detector = TimeDecayDetector({'max_yes_price_improbable': 0.15})
        
        market = generate_random_market(
            days_to_resolution=10,
            yes_price=0.40  # 40% = no improbable
        )
        
        opps = detector.detect([market])
        
        improbable_opps = [o for o in opps if o.type.value == 'improbable_expiring']
        assert len(improbable_opps) == 0
        print("[OK] Correctly ignored probable event (40%)")


class TestHelperFunctions:
    """Tests para funciones auxiliares."""
    
    def test_calculate_theta(self):
        """Test de cálculo de theta."""
        # 20% / 5 días = 4%/día
        theta = calculate_theta(0.20, 5)
        assert abs(theta - 0.04) < 0.001
        
        # Edge case: 0 días
        theta = calculate_theta(0.20, 0)
        assert theta == 0
        
        print("[OK] Theta calculation works")
    
    def test_is_deadline_market(self):
        """Test de detección de mercados deadline."""
        assert is_deadline_market("Will X happen before March?") == True
        assert is_deadline_market("Will Y happen by end of February?") == True
        assert is_deadline_market("Will Z reach $100?") == False
        
        print("[OK] Deadline market detection works")


def run_all_tests():
    """Ejecuta todos los tests."""
    print("\n" + "="*60)
    print("TIME DECAY DETECTOR TESTS")
    print("="*60 + "\n")
    
    # Deadline Approaching
    print("--- Deadline Approaching Tests ---")
    test_deadline = TestDeadlineApproaching()
    test_deadline.test_detects_deadline_market()
    test_deadline.test_ignores_far_deadline()
    test_deadline.test_ignores_high_yes_price()
    test_deadline.test_ignores_non_deadline_market()
    
    # Improbable Expiring
    print("\n--- Improbable Expiring Tests ---")
    test_improbable = TestImprobableExpiring()
    test_improbable.test_detects_improbable_event()
    test_improbable.test_detects_very_low_yes()
    test_improbable.test_ignores_probable_event()
    
    # Helpers
    print("\n--- Helper Function Tests ---")
    test_helpers = TestHelperFunctions()
    test_helpers.test_calculate_theta()
    test_helpers.test_is_deadline_market()
    
    print("\n" + "="*60)
    print("ALL TIME DECAY TESTS PASSED!")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_all_tests()
