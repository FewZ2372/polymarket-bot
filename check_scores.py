"""Check scores of filtered markets."""
from scanner import get_top_markets
from risk_manager import risk_manager

markets = get_top_markets(limit=30, max_days_to_resolution=30)
filtered = risk_manager.get_filtered_markets(markets)

print(f'Mercados que pasaron filtro: {len(filtered)}')
print()
print('[SCORES DE MERCADOS FILTRADOS]')
for m in filtered[:15]:
    score = m.get('score', 0)
    question = m.get('question', '')[:50]
    days = m.get('days_to_resolution', 999)
    price = m.get('yes', 0)
    move = m.get('movement_potential', 0)
    
    marker = " <-- TRADEABLE" if score >= 85 else ""
    print(f'  Score: {score:>3} | {question}')
    print(f'        Dias: {days:.0f} | Precio: {price:.0%} | Move: {move}{marker}')
    print()

# Count how many are tradeable
tradeable = [m for m in filtered if m.get('score', 0) >= 85]
print(f'Mercados con score >= 85: {len(tradeable)}')
if not tradeable:
    print('\nNinguno alcanza el score minimo de 85.')
    print('Posibles soluciones:')
    print('1. Bajar MIN_SCORE_TO_TRADE en config.py')
    print('2. Mejorar el calculo de score en scanner.py')
