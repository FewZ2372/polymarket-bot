"""
Tests para ArbitrageDetector.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors.arbitrage_detector import ArbitrageDetector, calculate_multi_outcome_profit
from models.market import Market
from tests.mock_data import (
    generate_random_market,
    generate_multi_outcome_arbitrage_scenario,
    generate_yes_no_mismatch_scenario,
    generate_cross_platform_arbitrage_scenario,
)


class TestYesNoMismatch:
    """Tests para detección de YES/NO mismatch."""
    
    def test_detects_mismatch_under_100(self):
        """Debe detectar cuando YES + NO < 100%."""
        detector = ArbitrageDetector({'min_yes_no_mismatch': 0.02})
        
        # Crear mercado con mismatch (97%)
        market = generate_random_market(yes_price=0.52)
        market.no_price = 0.45  # Total = 97%
        
        opps = detector.detect([market])
        
        assert len(opps) == 1
        assert opps[0].type.value == 'yes_no_mismatch'
        assert opps[0].action.value == 'buy_both'
        assert opps[0].expected_profit > 2.5  # ~3%
        assert opps[0].confidence >= 95
        
        print(f"[OK] Detected YES/NO mismatch: {opps[0]}")
    
    def test_ignores_fair_pricing(self):
        """No debe detectar cuando YES + NO ≈ 100%."""
        detector = ArbitrageDetector({'min_yes_no_mismatch': 0.02})
        
        # Mercado con pricing correcto
        market = generate_random_market(yes_price=0.60)
        market.no_price = 0.40  # Total = 100%
        
        opps = detector.detect([market])
        
        assert len(opps) == 0
        print("[OK] Correctly ignored fair-priced market")
    
    def test_ignores_small_mismatch(self):
        """No debe detectar mismatch muy pequeño."""
        detector = ArbitrageDetector({'min_yes_no_mismatch': 0.02})
        
        # Mismatch de solo 1%
        market = generate_random_market(yes_price=0.50)
        market.no_price = 0.49  # Total = 99%
        
        opps = detector.detect([market])
        
        assert len(opps) == 0
        print("[OK] Correctly ignored small mismatch (1%)")


class TestMultiOutcomeArbitrage:
    """Tests para detección de arbitraje multi-outcome."""
    
    def test_detects_overpriced_outcomes(self):
        """Debe detectar cuando suma de YES > 100%."""
        detector = ArbitrageDetector({'min_multi_outcome_profit': 2.0})
        
        # Escenario con 5% de profit
        scenario = generate_multi_outcome_arbitrage_scenario(profit_margin=0.05)
        
        opps = detector.detect([], events=[scenario])
        
        assert len(opps) == 1
        assert opps[0].type.value == 'multi_outcome_arb'
        assert opps[0].action.value == 'buy_all_no'
        assert opps[0].expected_profit > 4.5
        assert opps[0].confidence >= 95
        
        print(f"[OK] Detected multi-outcome arb: {opps[0]}")
        print(f"  Sum of YES: {scenario['total_yes_sum']:.2%}")
        print(f"  Profit: {opps[0].expected_profit:.1f}%")
    
    def test_detects_underpriced_outcomes(self):
        """Debe detectar cuando suma de YES < 100%."""
        detector = ArbitrageDetector({'min_multi_outcome_profit': 2.0})
        
        # Crear evento con suma < 100%
        event = {
            'event_id': 'test_under',
            'event_question': 'Who wins?',
            'markets': [
                {'yes_price': 0.30},
                {'yes_price': 0.30},
                {'yes_price': 0.30},
            ]
        }
        # Total = 90%
        
        opps = detector.detect([], events=[event])
        
        assert len(opps) == 1
        assert opps[0].action.value == 'buy_all_yes'
        assert opps[0].expected_profit > 9.0  # ~10%
        
        print(f"[OK] Detected underpriced outcomes: profit={opps[0].expected_profit:.1f}%")
    
    def test_ignores_fair_outcomes(self):
        """No debe detectar cuando suma ≈ 100%."""
        detector = ArbitrageDetector({'min_multi_outcome_profit': 2.0})
        
        # Evento con suma exacta
        event = {
            'event_id': 'test_fair',
            'event_question': 'Who wins?',
            'markets': [
                {'yes_price': 0.50},
                {'yes_price': 0.30},
                {'yes_price': 0.20},
            ]
        }
        # Total = 100%
        
        opps = detector.detect([], events=[event])
        
        assert len(opps) == 0
        print("[OK] Correctly ignored fair-priced event")


class TestCrossPlatformArbitrage:
    """Tests para detección de arbitraje cross-platform."""
    
    def test_detects_spread(self):
        """Debe detectar spread significativo entre plataformas."""
        detector = ArbitrageDetector({'min_cross_platform_spread': 0.03})
        
        scenario = generate_cross_platform_arbitrage_scenario(spread_pct=0.07)
        
        opps = detector.detect(
            [scenario['pm_market']],
            kalshi_markets=[scenario['kalshi_market']]
        )
        
        assert len(opps) == 1
        assert opps[0].type.value == 'cross_platform_arb'
        assert opps[0].expected_profit > 6.0  # ~7%
        
        print(f"[OK] Detected cross-platform arb: {opps[0]}")
        print(f"  Spread: {scenario['spread']:.1%}")
    
    def test_ignores_small_spread(self):
        """No debe detectar spread muy pequeño."""
        detector = ArbitrageDetector({'min_cross_platform_spread': 0.03})
        
        scenario = generate_cross_platform_arbitrage_scenario(spread_pct=0.02)
        
        opps = detector.detect(
            [scenario['pm_market']],
            kalshi_markets=[scenario['kalshi_market']]
        )
        
        assert len(opps) == 0
        print("[OK] Correctly ignored small spread (2%)")


class TestHelperFunctions:
    """Tests para funciones auxiliares."""
    
    def test_calculate_multi_outcome_profit(self):
        """Test de cálculo de profit."""
        # Suma > 100%
        profit, strategy = calculate_multi_outcome_profit([0.40, 0.35, 0.30])
        assert profit > 4.0  # 105% - 100% = 5%
        assert strategy == "BUY_ALL_NO"
        
        # Suma < 100%
        profit, strategy = calculate_multi_outcome_profit([0.30, 0.30, 0.30])
        assert profit > 9.0  # 100% - 90% = 10%
        assert strategy == "BUY_ALL_YES"
        
        # Suma = 100%
        profit, strategy = calculate_multi_outcome_profit([0.50, 0.30, 0.20])
        assert profit == 0
        assert strategy == "NO_ARBITRAGE"
        
        print("[OK] Helper functions work correctly")


def run_all_tests():
    """Ejecuta todos los tests."""
    print("\n" + "="*60)
    print("ARBITRAGE DETECTOR TESTS")
    print("="*60 + "\n")
    
    # YES/NO Mismatch
    print("--- YES/NO Mismatch Tests ---")
    test_yes_no = TestYesNoMismatch()
    test_yes_no.test_detects_mismatch_under_100()
    test_yes_no.test_ignores_fair_pricing()
    test_yes_no.test_ignores_small_mismatch()
    
    # Multi-Outcome
    print("\n--- Multi-Outcome Arbitrage Tests ---")
    test_multi = TestMultiOutcomeArbitrage()
    test_multi.test_detects_overpriced_outcomes()
    test_multi.test_detects_underpriced_outcomes()
    test_multi.test_ignores_fair_outcomes()
    
    # Cross-Platform
    print("\n--- Cross-Platform Arbitrage Tests ---")
    test_cross = TestCrossPlatformArbitrage()
    test_cross.test_detects_spread()
    test_cross.test_ignores_small_spread()
    
    # Helpers
    print("\n--- Helper Function Tests ---")
    test_helpers = TestHelperFunctions()
    test_helpers.test_calculate_multi_outcome_profit()
    
    print("\n" + "="*60)
    print("ALL ARBITRAGE TESTS PASSED!")
    print("="*60 + "\n")


if __name__ == "__main__":
    run_all_tests()
