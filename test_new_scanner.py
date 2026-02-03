"""Test the new short-term focused scanner."""
from scanner import get_top_markets, print_market_report

print('Probando nuevo scanner con filtro de corto plazo...')
print()

# Test with default 30 day filter
markets = get_top_markets(limit=50, max_days_to_resolution=30)
print_market_report(markets)

# Show summary
print()
print('='*70)
print(' RESUMEN DEL NUEVO SCANNER')
print('='*70)
print(f'  Mercados encontrados: {len(markets)}')

if markets:
    avg_days = sum(m.get('days_to_resolution', 999) for m in markets) / len(markets)
    short_term = sum(1 for m in markets if m.get('days_to_resolution', 999) <= 7)
    this_month = sum(1 for m in markets if 7 < m.get('days_to_resolution', 999) <= 30)
    
    print(f'  Promedio dias a resolucion: {avg_days:.1f}')
    print(f'  Mercados < 7 dias:          {short_term}')
    print(f'  Mercados 7-30 dias:         {this_month}')
    print(f'  Score promedio:             {sum(m["score"] for m in markets) / len(markets):.0f}')
    print(f'  Movement potential prom:    {sum(m.get("movement_potential", 0) for m in markets) / len(markets):.0f}')
    
    # Show by category
    print()
    print('[POR CATEGORIA]')
    categories = {}
    for m in markets:
        cat = m.get('category', 'Unknown') or 'Unknown'
        categories[cat] = categories.get(cat, 0) + 1
    
    for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:10]:
        print(f'  {cat:<25} {count:>3} mercados')
    
    # Best opportunities
    print()
    print('[TOP 5 OPORTUNIDADES]')
    for i, m in enumerate(markets[:5], 1):
        days = m.get('days_to_resolution', 999)
        print(f'  {i}. [{m["score"]}] {m["question"][:50]}')
        print(f'     Precio: {m["yes"]:.0%} | Dias: {days:.0f} | Movimiento: {m.get("movement_potential", 0)}')

print()
print('='*70)
print(' COMPARACION: ANTES vs AHORA')
print('='*70)
print('''
  ANTES:
  - Solo miraba 75 mercados
  - Ordenaba por volumen (favorecia largo plazo)
  - Sin filtro de fecha
  - Resultado: Capital atrapado en mercados de 2027

  AHORA:
  - Descarga 500 mercados
  - FILTRA: solo < 30 dias de resolucion
  - Ordena por: 50% inefficiency + 50% movement potential
  - Bonus para mercados < 7 dias
  - Resultado: Feedback rapido, capital liquido
''')
