"""
Mock data generator para testing de detectores.
"""

import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from models.market import Market, MarketCategory
from models.opportunity import Opportunity, OpportunityType, Action


def generate_random_market(
    yes_price: Optional[float] = None,
    days_to_resolution: Optional[int] = None,
    volume_24h: Optional[float] = None,
    price_change_1h: Optional[float] = None,
    category: Optional[MarketCategory] = None,
    question: Optional[str] = None,
) -> Market:
    """
    Genera un mercado aleatorio para testing.
    
    Args:
        yes_price: Precio YES (0-1), random si None
        days_to_resolution: Días hasta resolución, random si None
        volume_24h: Volumen 24h, random si None
        price_change_1h: Cambio de precio 1h, random si None
        category: Categoría, random si None
        question: Pregunta del mercado, generada si None
    
    Returns:
        Market con datos mock
    """
    if yes_price is None:
        yes_price = random.uniform(0.05, 0.95)
    
    if days_to_resolution is None:
        days_to_resolution = random.randint(1, 60)
    
    if volume_24h is None:
        volume_24h = random.uniform(1000, 1000000)
    
    if price_change_1h is None:
        price_change_1h = random.uniform(-0.15, 0.15)
    
    if category is None:
        category = random.choice(list(MarketCategory))
    
    if question is None:
        templates = [
            "Will {} happen before {}?",
            "Will {} be announced by {}?",
            "Will {} win {}?",
        ]
        subjects = ['Fed rate cut', 'Bitcoin rally', 'Trump', 'Apple earnings', 'SpaceX launch']
        question = random.choice(templates).format(
            random.choice(subjects),
            f"March {random.randint(1, 31)}"
        )
    
    market_id = f"mock_{random.randint(10000, 99999)}"
    end_date = datetime.now() + timedelta(days=days_to_resolution)
    
    return Market(
        id=market_id,
        condition_id=f"cond_{market_id}",
        slug=f"mock-market-{market_id}",
        question=question,
        yes_price=yes_price,
        no_price=1 - yes_price,
        volume_24h=volume_24h,
        volume_total=volume_24h * random.uniform(5, 20),
        liquidity=volume_24h * random.uniform(0.1, 0.5),
        price_change_1h=price_change_1h,
        price_change_24h=price_change_1h * random.uniform(1, 5),
        end_date=end_date,
        created_at=datetime.now() - timedelta(days=random.randint(1, 30)),
        category=category,
        is_active=True,
        is_closed=False,
        token_id_yes=f"token_yes_{market_id}",
        token_id_no=f"token_no_{market_id}",
    )


def generate_markets(count: int = 50, **kwargs) -> List[Market]:
    """Genera múltiples mercados aleatorios."""
    return [generate_random_market(**kwargs) for _ in range(count)]


# ============ ESCENARIOS ESPECÍFICOS ============

def generate_multi_outcome_arbitrage_scenario(
    profit_margin: float = 0.05
) -> Dict[str, Any]:
    """
    Genera un escenario de arbitraje multi-outcome.
    
    Args:
        profit_margin: Margen de ganancia (ej: 0.05 = 5%)
    
    Returns:
        Dict con 'event' y 'expected_profit'
    """
    # Generar precios que sumen más de 100%
    num_outcomes = random.randint(3, 5)
    base_prices = [random.uniform(0.15, 0.40) for _ in range(num_outcomes)]
    
    # Normalizar y agregar margen
    total = sum(base_prices)
    normalized = [p / total for p in base_prices]
    
    # Agregar el profit margin (suma > 100%)
    adjusted = [p * (1 + profit_margin) for p in normalized]
    
    markets = []
    for i, price in enumerate(adjusted):
        markets.append({
            'id': f'outcome_{i}',
            'question': f'Option {chr(65 + i)} wins?',
            'yes_price': min(0.95, price),
            'no_price': max(0.05, 1 - price),
        })
    
    return {
        'event_id': f'arb_event_{random.randint(1000, 9999)}',
        'event_question': 'Who will win the election?',
        'markets': markets,
        'expected_profit': (sum(m['yes_price'] for m in markets) - 1) * 100,
        'total_yes_sum': sum(m['yes_price'] for m in markets),
    }


def generate_yes_no_mismatch_scenario(
    mismatch_pct: float = 0.03
) -> Market:
    """
    Genera un mercado con YES + NO != 100%.
    
    Args:
        mismatch_pct: Porcentaje de mismatch (ej: 0.03 = 3%)
    
    Returns:
        Market con mismatch
    """
    yes_price = random.uniform(0.30, 0.70)
    # NO no es exactamente 1 - YES
    no_price = (1 - yes_price) - mismatch_pct
    
    market = generate_random_market(yes_price=yes_price)
    market.no_price = no_price
    
    return market


def generate_cross_platform_arbitrage_scenario(
    spread_pct: float = 0.05
) -> Dict[str, Any]:
    """
    Genera escenario de arbitraje cross-platform.
    
    Args:
        spread_pct: Spread entre plataformas (ej: 0.05 = 5%)
    
    Returns:
        Dict con mercados de PM y Kalshi
    """
    base_price = random.uniform(0.30, 0.70)
    
    # Decidir cuál está más caro
    pm_higher = random.choice([True, False])
    
    if pm_higher:
        pm_price = base_price + spread_pct / 2
        kalshi_price = base_price - spread_pct / 2
    else:
        pm_price = base_price - spread_pct / 2
        kalshi_price = base_price + spread_pct / 2
    
    question = f"Will Fed cut rates by {random.choice(['25', '50'])} bps?"
    
    return {
        'pm_market': generate_random_market(
            yes_price=pm_price,
            question=question
        ),
        'kalshi_market': {
            'ticker': f'FED-{random.randint(1, 99)}',
            'title': question,
            'yes_price': kalshi_price,
            'yes_ask': kalshi_price * 100,  # Kalshi usa centavos
        },
        'spread': abs(pm_price - kalshi_price),
        'expected_profit': abs(pm_price - kalshi_price) * 100,
    }


def generate_time_decay_scenario(
    days_left: int = 5,
    event_unlikely: bool = True
) -> Market:
    """
    Genera escenario de time decay.
    
    Args:
        days_left: Días hasta resolución
        event_unlikely: Si el evento es improbable
    
    Returns:
        Market con oportunidad de time decay
    """
    if event_unlikely:
        yes_price = random.uniform(0.05, 0.25)
        question = f"Will {random.choice(['alien contact', 'asteroid hit', 'world peace'])} happen by {(datetime.now() + timedelta(days=days_left)).strftime('%b %d')}?"
    else:
        yes_price = random.uniform(0.20, 0.40)
        question = f"Will government shutdown happen by {(datetime.now() + timedelta(days=days_left)).strftime('%b %d')}?"
    
    return generate_random_market(
        yes_price=yes_price,
        days_to_resolution=days_left,
        question=question
    )


def generate_momentum_scenario(
    strong_momentum: bool = True,
    direction: str = 'up'
) -> Market:
    """
    Genera escenario de momentum.
    
    Args:
        strong_momentum: Si el momentum es fuerte
        direction: 'up' o 'down'
    
    Returns:
        Market con momentum
    """
    if strong_momentum:
        change = random.uniform(0.08, 0.15)
    else:
        change = random.uniform(0.03, 0.07)
    
    if direction == 'down':
        change = -change
    
    return generate_random_market(
        price_change_1h=change,
        yes_price=random.uniform(0.30, 0.70)
    )


def generate_whale_activity_scenario(
    whale_direction: str = 'yes',
    amount_usd: float = 50000
) -> Dict[str, Any]:
    """
    Genera escenario de whale activity.
    
    Args:
        whale_direction: 'yes' o 'no'
        amount_usd: Monto de la transacción
    
    Returns:
        Dict con market y transacciones
    """
    market = generate_random_market(volume_24h=100000)
    
    transactions = [
        {
            'wallet': f'0x{random.randbytes(20).hex()}',
            'side': whale_direction.upper(),
            'amount_usd': amount_usd,
            'timestamp': datetime.now() - timedelta(hours=random.uniform(0.5, 3)),
        }
        for _ in range(random.randint(2, 5))
    ]
    
    return {
        'market': market,
        'transactions': transactions,
        'total_volume': sum(t['amount_usd'] for t in transactions),
        'direction': whale_direction,
    }


def generate_near_certain_scenario(
    certainty: str = 'yes'
) -> Market:
    """
    Genera escenario de resultado casi seguro.
    
    Args:
        certainty: 'yes' (evento casi seguro) o 'no' (casi imposible)
    
    Returns:
        Market con resultado predecible
    """
    if certainty == 'yes':
        # Evento casi seguro (ej: Super Bowl will happen)
        yes_price = random.uniform(0.88, 0.95)
        questions = [
            "Will there be a Super Bowl in 2026?",
            "Will the NBA Finals happen?",
            "Will the sun rise tomorrow?",
        ]
    else:
        # Evento casi imposible
        yes_price = random.uniform(0.02, 0.08)
        questions = [
            "Will aliens make contact by end of month?",
            "Will Earth be hit by major asteroid?",
            "Will humans teleport by March?",
        ]
    
    return generate_random_market(
        yes_price=yes_price,
        question=random.choice(questions),
        days_to_resolution=random.randint(5, 30)
    )


def generate_correlation_scenario() -> Dict[str, Market]:
    """
    Genera escenario de mercados correlacionados con divergencia.
    
    Returns:
        Dict con dos mercados que deberían estar correlacionados
    """
    # Dos mercados que deberían moverse juntos
    base_price = random.uniform(0.40, 0.60)
    
    # Uno está "correcto", otro divergió
    market1 = generate_random_market(
        yes_price=base_price,
        question="Will Trump win 2024 election?"
    )
    
    market2 = generate_random_market(
        yes_price=base_price - 0.15,  # Divergió 15%
        question="Will Republican candidate win 2024?"
    )
    
    return {
        'market1': market1,
        'market2': market2,
        'expected_correlation': 0.95,
        'actual_divergence': 0.15,
    }


# ============ TEST DATASETS ============

def generate_test_dataset(
    size: int = 100,
    include_arbitrage: bool = True,
    include_time_decay: bool = True,
    include_momentum: bool = True
) -> Dict[str, Any]:
    """
    Genera un dataset completo para testing.
    
    Returns:
        Dict con markets, events, y escenarios específicos
    """
    dataset = {
        'markets': [],
        'events': [],
        'whale_data': [],
        'expected_opportunities': [],
    }
    
    # Mercados normales
    dataset['markets'] = generate_markets(size)
    
    # Agregar escenarios específicos
    if include_arbitrage:
        # Multi-outcome arbitrage
        arb_scenario = generate_multi_outcome_arbitrage_scenario(profit_margin=0.06)
        dataset['events'].append(arb_scenario)
        dataset['expected_opportunities'].append({
            'type': 'MULTI_OUTCOME_ARB',
            'profit': arb_scenario['expected_profit']
        })
        
        # YES/NO mismatch
        mismatch_market = generate_yes_no_mismatch_scenario(mismatch_pct=0.04)
        dataset['markets'].append(mismatch_market)
        dataset['expected_opportunities'].append({
            'type': 'YES_NO_MISMATCH',
            'profit': 4.0
        })
    
    if include_time_decay:
        # Time decay scenarios
        for _ in range(3):
            td_market = generate_time_decay_scenario(
                days_left=random.randint(2, 7),
                event_unlikely=True
            )
            dataset['markets'].append(td_market)
            dataset['expected_opportunities'].append({
                'type': 'TIME_DECAY',
                'profit': td_market.yes_price * 100
            })
    
    if include_momentum:
        # Momentum scenarios
        for direction in ['up', 'down']:
            mom_market = generate_momentum_scenario(
                strong_momentum=True,
                direction=direction
            )
            dataset['markets'].append(mom_market)
            dataset['expected_opportunities'].append({
                'type': 'MOMENTUM_SHORT',
                'direction': direction
            })
    
    # Whale activity
    whale_scenario = generate_whale_activity_scenario(
        whale_direction='yes',
        amount_usd=75000
    )
    dataset['markets'].append(whale_scenario['market'])
    dataset['whale_data'].append(whale_scenario)
    
    return dataset


if __name__ == "__main__":
    # Test de generación
    print("=== Testing Mock Data Generator ===\n")
    
    # Mercado aleatorio
    market = generate_random_market()
    print(f"Random Market: {market}")
    print(f"  Days to resolution: {market.days_to_resolution:.1f}")
    print(f"  Has good liquidity: {market.has_good_liquidity}")
    
    # Escenario de arbitraje
    print("\n--- Multi-Outcome Arbitrage ---")
    arb = generate_multi_outcome_arbitrage_scenario(profit_margin=0.05)
    print(f"Event: {arb['event_question']}")
    print(f"Outcomes: {len(arb['markets'])}")
    print(f"Sum of YES prices: {arb['total_yes_sum']:.2%}")
    print(f"Expected profit: {arb['expected_profit']:.1f}%")
    
    # Time decay
    print("\n--- Time Decay ---")
    td = generate_time_decay_scenario(days_left=3)
    print(f"Market: {td.question}")
    print(f"YES price: {td.yes_price:.2%}")
    print(f"Days left: {td.days_to_resolution:.1f}")
    
    # Dataset completo
    print("\n--- Full Test Dataset ---")
    dataset = generate_test_dataset(size=50)
    print(f"Markets: {len(dataset['markets'])}")
    print(f"Events: {len(dataset['events'])}")
    print(f"Expected opportunities: {len(dataset['expected_opportunities'])}")
