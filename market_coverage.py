"""Analizar cobertura de mercados - cuántos vemos vs cuántos hay."""
import requests
import json
from datetime import datetime, timedelta
from collections import defaultdict

print('='*70)
print(' ANALISIS DE COBERTURA DE MERCADOS')
print('='*70)

# 1. Cuantos mercados tiene Polymarket en total?
print('\n[1] MERCADOS TOTALES EN POLYMARKET')

# CLOB API - mercados activos
try:
    response = requests.get('https://clob.polymarket.com/markets', timeout=30)
    clob_data = response.json()
    total_clob = clob_data.get('count', 0)
    print(f'  CLOB API reporta: {total_clob:,} mercados totales')
except Exception as e:
    print(f'  CLOB API error: {e}')
    total_clob = 0

# Gamma API - mercados activos (paginar para obtener todos)
print('\n  Descargando mercados de Gamma API...')
all_gamma_markets = []
offset = 0
limit = 100

while True:
    try:
        response = requests.get(
            'https://gamma-api.polymarket.com/markets',
            params={'limit': limit, 'offset': offset, 'active': 'true'},
            timeout=30
        )
        markets = response.json()
        if not markets:
            break
        all_gamma_markets.extend(markets)
        offset += limit
        if len(markets) < limit:
            break
        if offset > 2000:  # Safety limit
            break
    except Exception as e:
        print(f'  Error at offset {offset}: {e}')
        break

print(f'  Gamma API (activos): {len(all_gamma_markets):,} mercados')

gamma_markets = all_gamma_markets

# 2. Analizar distribucion por fecha de resolucion
print('\n[2] DISTRIBUCION POR FECHA DE RESOLUCION')

now = datetime.now()
by_timeframe = {
    'hoy': 0,
    'esta_semana': 0,
    'este_mes': 0,
    '1-3_meses': 0,
    '3-12_meses': 0,
    'mas_1_ano': 0,
    'sin_fecha': 0,
}

short_term_markets = []

for m in gamma_markets:
    end_date = m.get('endDate', '')
    if not end_date:
        by_timeframe['sin_fecha'] += 1
        continue
    
    try:
        end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        if end_dt.tzinfo:
            end_dt = end_dt.replace(tzinfo=None)
        
        days = (end_dt - now).days
        
        if days <= 1:
            by_timeframe['hoy'] += 1
            short_term_markets.append((m, days))
        elif days <= 7:
            by_timeframe['esta_semana'] += 1
            short_term_markets.append((m, days))
        elif days <= 30:
            by_timeframe['este_mes'] += 1
            short_term_markets.append((m, days))
        elif days <= 90:
            by_timeframe['1-3_meses'] += 1
        elif days <= 365:
            by_timeframe['3-12_meses'] += 1
        else:
            by_timeframe['mas_1_ano'] += 1
    except:
        by_timeframe['sin_fecha'] += 1

print(f"  Hoy/Manana:     {by_timeframe['hoy']:>4} mercados")
print(f"  Esta semana:    {by_timeframe['esta_semana']:>4} mercados")
print(f"  Este mes:       {by_timeframe['este_mes']:>4} mercados")
print(f"  1-3 meses:      {by_timeframe['1-3_meses']:>4} mercados")
print(f"  3-12 meses:     {by_timeframe['3-12_meses']:>4} mercados")
print(f"  Mas de 1 ano:   {by_timeframe['mas_1_ano']:>4} mercados")
print(f"  Sin fecha:      {by_timeframe['sin_fecha']:>4} mercados")

corto_plazo = by_timeframe['hoy'] + by_timeframe['esta_semana'] + by_timeframe['este_mes']
print(f'\n  TOTAL CORTO PLAZO (< 30 dias): {corto_plazo} mercados')

# 3. Categorias de mercados
print('\n[3] CATEGORIAS DE MERCADOS')
categories = defaultdict(int)
for m in gamma_markets:
    cat = m.get('category', 'Unknown') or 'Unknown'
    categories[cat] += 1

for cat, count in sorted(categories.items(), key=lambda x: -x[1])[:15]:
    print(f'  {cat:<25} {count:>4} mercados')

# 4. Cuantos estamos viendo nosotros?
print('\n[4] COMPARACION: LO QUE VE EL BOT vs TOTAL')
print('-'*70)

# Cargar config del scanner
try:
    from scanner import get_top_markets
    print('  Scanner cargado correctamente')
except:
    print('  No se pudo cargar scanner')

# El bot usa limit=75 en get_top_markets
bot_limit = 75
print(f'\n  El bot analiza:        {bot_limit} mercados por ciclo')
print(f'  Polymarket tiene:      {len(gamma_markets):,} mercados activos')
print(f'  Cobertura:             {bot_limit/len(gamma_markets)*100:.1f}%')

print(f'\n  Mercados CORTO PLAZO:  {corto_plazo} disponibles')
print(f'  El bot podria cubrir:  {min(bot_limit, corto_plazo)} de ellos')

# 5. Ejemplos de mercados corto plazo que podriamos estar perdiendo
print('\n[5] MERCADOS CORTO PLAZO DISPONIBLES (< 30 dias)')
print('-'*70)

# Sort by volume
short_term_markets.sort(key=lambda x: x[0].get('volume', 0) or 0, reverse=True)

print(f"\n{'Mercado':<50} {'Dias':>5} {'Vol 24h':>12} {'Precio':>8}")
print('-'*80)

for m, days in short_term_markets[:25]:
    question = m.get('question', '')[:48]
    volume = m.get('volume', 0) or 0
    try:
        volume = float(volume)
    except:
        volume = 0
    
    # Get YES price
    prices_str = m.get('outcomePrices', '')
    yes_price = 0.5
    if prices_str:
        try:
            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
            yes_price = float(prices[0])
        except:
            pass
    
    vol_str = f"${volume/1000:.0f}k" if volume >= 1000 else f"${volume:.0f}"
    print(f"  {question:<48} {days:>4}d {vol_str:>11} {yes_price:>7.0%}")

print('\n' + '='*70)
print(' CONCLUSION')
print('='*70)
print(f'''
  1. Polymarket tiene {len(gamma_markets):,} mercados activos
  2. De esos, {corto_plazo} se resuelven en < 30 dias
  3. El bot solo mira {bot_limit} mercados por ciclo ({bot_limit/len(gamma_markets)*100:.1f}% cobertura)
  
  OPORTUNIDAD PERDIDA:
  - Hay {corto_plazo - bot_limit if corto_plazo > bot_limit else 0} mercados de corto plazo que NO estamos viendo
  - Estos son los que darian feedback rapido y capital liquido
  
  RECOMENDACION:
  - Aumentar cobertura a mas mercados
  - Filtrar por fecha de resolucion < 30 dias
  - Priorizar mercados con alto volumen
''')
