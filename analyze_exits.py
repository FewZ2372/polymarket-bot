"""Analizar por qué los trades no están saliendo."""
import json
from datetime import datetime

# Cargar trades
with open('simulation_data.json', 'r') as f:
    data = json.load(f)

trades = data.get('trades', [])
open_trades = [t for t in trades if t.get('status') == 'OPEN']
closed_trades = [t for t in trades if t.get('status') != 'OPEN']

print('ANALISIS DE TRADES - Por que no salen?')
print('='*80)

# Definir thresholds del resolver (de smart_trader.py)
TP_SETTINGS = {'low': 0.40, 'medium': 0.20, 'high': 0.08}
SL_SETTINGS = {
    'low': {'initial': -0.30, 'time': -0.40},
    'medium': {'initial': -0.25, 'time': -0.35},
    'high': {'initial': -0.15, 'time': -0.25},
}

def get_tier(price):
    if price < 0.30:
        return 'low'
    elif price < 0.70:
        return 'medium'
    return 'high'

print(f'\n{"Market":<40} {"Entry":>7} {"Curr":>7} {"PnL":>8} {"TP":>6} {"SL":>6} {"Status":>12}')
print('-'*90)

should_tp_list = []
should_sl_list = []
waiting_list = []

for t in open_trades:
    market = t.get('market', '')[:38]
    entry = t.get('entry_price', 0)
    current = t.get('current_price', entry)
    pnl_pct = t.get('pnl_pct', 0)
    
    tier = get_tier(entry)
    tp = TP_SETTINGS[tier] * 100
    sl = SL_SETTINGS[tier]['initial'] * 100
    
    # Determinar status
    if pnl_pct >= tp:
        status = 'DEBERIA TP!'
        should_tp_list.append(t)
    elif pnl_pct <= sl:
        status = 'DEBERIA SL!'
        should_sl_list.append(t)
    elif pnl_pct > 0:
        pct_to_tp = pnl_pct / tp * 100 if tp > 0 else 0
        status = f'{pct_to_tp:.0f}pct al TP'
        waiting_list.append(t)
    else:
        pct_to_sl = abs(pnl_pct / sl) * 100 if sl < 0 else 0
        status = f'{pct_to_sl:.0f}pct al SL'
        waiting_list.append(t)
    
    print(f'{market:<40} {entry:>6.1%} {current:>6.1%} {pnl_pct:>+7.1f}% {tp:>+5.0f}% {sl:>+5.0f}% {status:>12}')

# Resumen
print('\n' + '='*80)
print('\n[RESUMEN]')
print(f'  Trades que DEBERIAN haber salido por TP: {len(should_tp_list)}')
print(f'  Trades que DEBERIAN haber salido por SL: {len(should_sl_list)}')
print(f'  Trades dentro de rango (esperando):      {len(waiting_list)}')

# Si hay trades que deberían haber salido, analizar por qué
if should_tp_list:
    print('\n[PROBLEMA] Hay trades que deberían haber tomado profit pero no lo hicieron:')
    for t in should_tp_list:
        print(f'  - {t.get("market", "")[:50]}')
        print(f'    Entry: {t.get("entry_price", 0):.1%}, Current: {t.get("current_price", 0):.1%}, PnL: {t.get("pnl_pct", 0):+.1f}%')

if should_sl_list:
    print('\n[PROBLEMA] Hay trades que deberían haber salido por stop loss:')
    for t in should_sl_list:
        print(f'  - {t.get("market", "")[:50]}')
        print(f'    Entry: {t.get("entry_price", 0):.1%}, Current: {t.get("current_price", 0):.1%}, PnL: {t.get("pnl_pct", 0):+.1f}%')

# Ver trades cerrados para entender qué SÍ funcionó
print('\n' + '='*80)
print('\n[TRADES CERRADOS - Como salieron?]')
print('-'*80)
for t in closed_trades:
    market = t.get('market', '')[:40]
    entry = t.get('entry_price', 0)
    exit_p = t.get('exit_price', 0)
    pnl = t.get('pnl_usd', 0)
    pnl_pct = t.get('pnl_pct', 0)
    status = t.get('status', '')
    
    print(f'  {market:<40} | Entry: {entry:.1%} Exit: {exit_p:.1%} | PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%) | {status}')

# Conclusión
print('\n' + '='*80)
print('\n[CONCLUSION]')
if len(should_tp_list) == 0 and len(should_sl_list) == 0:
    print('  Ningun trade ha alcanzado el threshold de TP o SL.')
    print('  Los trades estan dentro del rango de espera.')
    print('  ESTO ES NORMAL - simplemente los precios no se movieron lo suficiente.')
else:
    print(f'  HAY UN BUG: {len(should_tp_list) + len(should_sl_list)} trades deberian haber salido pero no lo hicieron.')
    print('  Revisar el trade_resolver.py o la frecuencia de chequeo.')
