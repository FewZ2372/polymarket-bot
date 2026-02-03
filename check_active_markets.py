"""Ver mercados activos y futuros (no cerrados)."""
import requests
import json
from datetime import datetime
from collections import defaultdict

print('='*70)
print(' MERCADOS ACTIVOS Y FUTUROS (excluyendo cerrados)')
print('='*70)

# Obtener mercados - usar endpoint de eventos que tiene mercados mas recientes
all_markets = []

# Primero obtener de gamma
print('\nDescargando de Gamma API...')
offset = 0
while offset < 1000:
    response = requests.get(
        'https://gamma-api.polymarket.com/markets',
        params={'limit': 100, 'offset': offset, 'closed': 'false'},
        timeout=30
    )
    markets = response.json()
    if not markets:
        break
    all_markets.extend(markets)
    offset += 100
    if len(markets) < 100:
        break

print(f'Obtenidos {len(all_markets)} mercados de Gamma')

now = datetime.now()
by_timeframe = defaultdict(list)
categories = defaultdict(int)

for m in all_markets:
    end_date = m.get('endDate', '')
    cat = m.get('category', 'Unknown') or 'Unknown'
    categories[cat] += 1
    
    if not end_date:
        by_timeframe['sin_fecha'].append(m)
        continue
    
    try:
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        if end_dt.tzinfo:
            end_dt = end_dt.replace(tzinfo=None)
        
        days = (end_dt - now).days
        
        if days < 0:
            by_timeframe['pasado'].append(m)  # Ya cerrado
        elif days <= 1:
            by_timeframe['hoy'].append(m)
        elif days <= 7:
            by_timeframe['esta_semana'].append(m)
        elif days <= 30:
            by_timeframe['este_mes'].append(m)
        elif days <= 90:
            by_timeframe['1-3_meses'].append(m)
        else:
            by_timeframe['mas_3_meses'].append(m)
    except:
        by_timeframe['sin_fecha'].append(m)

print(f'\nTotal mercados: {len(all_markets)}')
print()
print('[DISTRIBUCION POR FECHA DE RESOLUCION]')
print(f"  Ya pasados:     {len(by_timeframe['pasado']):>4} mercados (API incluye cerrados)")
print(f"  Hoy/Manana:     {len(by_timeframe['hoy']):>4} mercados")
print(f"  Esta semana:    {len(by_timeframe['esta_semana']):>4} mercados")
print(f"  Este mes:       {len(by_timeframe['este_mes']):>4} mercados")
print(f"  1-3 meses:      {len(by_timeframe['1-3_meses']):>4} mercados")
print(f"  Mas de 3 meses: {len(by_timeframe['mas_3_meses']):>4} mercados")
print(f"  Sin fecha:      {len(by_timeframe['sin_fecha']):>4} mercados")

corto = len(by_timeframe['hoy']) + len(by_timeframe['esta_semana']) + len(by_timeframe['este_mes'])
futuro_total = corto + len(by_timeframe['1-3_meses']) + len(by_timeframe['mas_3_meses'])
print(f'\n  TOTAL CORTO PLAZO (< 30 dias): {corto} mercados')
print(f'  TOTAL FUTUROS (no pasados):    {futuro_total} mercados')

# Categorias
print('\n[CATEGORIAS]')
for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:10]:
    print(f"  {cat:<25} {count:>4} mercados")

# Mostrar ejemplos de mercados FUTUROS con volumen alto
print('\n' + '='*70)
print(' MERCADOS FUTUROS POR VOLUMEN (oportunidades)')
print('='*70)

future_markets = (by_timeframe['hoy'] + by_timeframe['esta_semana'] + 
                  by_timeframe['este_mes'] + by_timeframe['1-3_meses'] +
                  by_timeframe['mas_3_meses'])

# Sort by volume
future_markets_sorted = sorted(
    future_markets, 
    key=lambda x: float(x.get('volume', 0) or 0), 
    reverse=True
)

print(f"\n{'Mercado':<50} {'Dias':>5} {'Volumen':>12} {'Precio':>8}")
print('-'*80)

for m in future_markets_sorted[:30]:
    q = m.get('question', '')[:48]
    vol = float(m.get('volume', 0) or 0)
    vol_str = f'${vol/1000:.0f}k' if vol >= 1000 else f'${vol:.0f}'
    
    # Get days
    end_date = m.get('endDate', '')
    days = 999
    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            if end_dt.tzinfo:
                end_dt = end_dt.replace(tzinfo=None)
            days = (end_dt - now).days
        except:
            pass
    
    # Get price
    prices_str = m.get('outcomePrices', '')
    yes_price = 0.5
    if prices_str:
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            yes_price = float(prices[0])
        except:
            pass
    
    print(f"  {q:<48} {days:>4}d {vol_str:>11} {yes_price:>7.0%}")

# Comparar con lo que el bot ve
print('\n' + '='*70)
print(' COMPARACION CON EL BOT')
print('='*70)

bot_limit = 75
print(f'''
  Mercados FUTUROS disponibles:  {futuro_total}
  Mercados CORTO PLAZO (< 30d):  {corto}
  
  El bot analiza:                {bot_limit} por ciclo
  Cobertura actual:              {bot_limit/futuro_total*100:.1f}% del total
                                 {bot_limit/corto*100:.1f}% del corto plazo (si filtrara)
  
  PROBLEMA:
  - El bot mezcla mercados de corto y largo plazo
  - Termina con capital atrapado en mercados de 2027
  
  SOLUCION:
  - Filtrar solo mercados que se resuelvan en < 30 dias
  - De esos {corto} mercados, priorizar por volumen
  - Aumentar el limite de analisis de 75 a 200+
''')
