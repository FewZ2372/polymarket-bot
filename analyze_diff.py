"""Analyze why local performs worse than v22"""
import json

with open('simulation_data.json') as f:
    trades = json.load(f)['trades']

closed = [t for t in trades if t['status'] != 'OPEN']

print("ANALISIS DE TRADES CERRADOS (LOCAL)")
print("=" * 50)

# Por side
yes_trades = [t for t in closed if t.get('side') == 'YES']
no_trades = [t for t in closed if t.get('side') == 'NO']

yes_wins = len([t for t in yes_trades if t.get('pnl_usd', 0) > 0])
no_wins = len([t for t in no_trades if t.get('pnl_usd', 0) > 0])

print(f"YES trades: {len(yes_trades)}, wins: {yes_wins} ({yes_wins/len(yes_trades)*100 if yes_trades else 0:.0f}%)")
print(f"NO trades: {len(no_trades)}, wins: {no_wins} ({no_wins/len(no_trades)*100 if no_trades else 0:.0f}%)")

# Por precio de entrada
low_price = [t for t in closed if t.get('entry_price', 1) < 0.15]
mid_price = [t for t in closed if 0.15 <= t.get('entry_price', 1) < 0.50]
high_price = [t for t in closed if t.get('entry_price', 1) >= 0.50]

low_wins = len([t for t in low_price if t.get('pnl_usd', 0) > 0])
mid_wins = len([t for t in mid_price if t.get('pnl_usd', 0) > 0])
high_wins = len([t for t in high_price if t.get('pnl_usd', 0) > 0])

print(f"\nPrecio <15c: {len(low_price)} trades, {low_wins} wins ({low_wins/len(low_price)*100 if low_price else 0:.0f}%)")
print(f"Precio 15-50c: {len(mid_price)} trades, {mid_wins} wins ({mid_wins/len(mid_price)*100 if mid_price else 0:.0f}%)")
print(f"Precio >50c: {len(high_price)} trades, {high_wins} wins ({high_wins/len(high_price)*100 if high_price else 0:.0f}%)")

# Ver si hay duplicados (mismo mercado)
print("\n" + "=" * 50)
print("MERCADOS MAS TRADEADOS (cerrados)")
market_counts = {}
for t in closed:
    m = t.get('market_title', 'unknown')[:50]
    if m not in market_counts:
        market_counts[m] = {'count': 0, 'pnl': 0, 'wins': 0}
    market_counts[m]['count'] += 1
    market_counts[m]['pnl'] += t.get('pnl_usd', 0)
    if t.get('pnl_usd', 0) > 0:
        market_counts[m]['wins'] += 1

sorted_markets = sorted(market_counts.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
for m, data in sorted_markets:
    wr = data['wins'] / data['count'] * 100 if data['count'] > 0 else 0
    print(f"  {m[:40]:<40} | x{data['count']} | WR:{wr:.0f}% | ${data['pnl']:.2f}")

# Comparar con open trades
open_trades = [t for t in trades if t['status'] == 'OPEN']
print(f"\n\nOPEN TRADES: {len(open_trades)}")
print("Mercados unicos abiertos:", len(set(t.get('market_title', '')[:50] for t in open_trades)))
