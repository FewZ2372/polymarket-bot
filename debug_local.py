"""Debug why local is losing"""
import json

with open('simulation_data.json') as f:
    data = json.load(f)

trades = data.get('trades', [])
closed = [t for t in trades if t['status'] != 'OPEN']
open_trades = [t for t in trades if t['status'] == 'OPEN']

print("=" * 60)
print("DEBUG: POR QUE EL LOCAL PIERDE")
print("=" * 60)

print(f"\nTotal trades: {len(trades)}")
print(f"Open: {len(open_trades)}")
print(f"Closed: {len(closed)}")

# Analyze closed trades
print("\n" + "=" * 60)
print("TRADES CERRADOS (todos perdedores?):")
print("=" * 60)
for t in closed:
    print(f"\n  Market: {t.get('market', 'N/A')[:50]}")
    print(f"  Status: {t.get('status')}")
    print(f"  Side: {t.get('side')} {t.get('outcome', 'YES')}")
    print(f"  Entry: {t.get('entry_price', 0):.4f}")
    print(f"  Exit: {t.get('exit_price', 0):.4f}")
    print(f"  P&L: ${t.get('pnl_usd', 0):.2f}")
    print(f"  Score: {t.get('score', 'N/A')}")

# Check unrealized P&L distribution
print("\n" + "=" * 60)
print("TRADES ABIERTOS - DISTRIBUCION P&L:")
print("=" * 60)

positive = [t for t in open_trades if t.get('pnl_usd', 0) > 0]
negative = [t for t in open_trades if t.get('pnl_usd', 0) < 0]
neutral = [t for t in open_trades if t.get('pnl_usd', 0) == 0]

print(f"  Positivos: {len(positive)}")
print(f"  Negativos: {len(negative)}")
print(f"  Neutral (no calculado): {len(neutral)}")

total_unrealized = sum(t.get('pnl_usd', 0) for t in open_trades)
print(f"  Total unrealized: ${total_unrealized:.2f}")
print(f"  NOTA: Si todos son neutral, el P&L no se está calculando!")

# Check entry prices
print("\n" + "=" * 60)
print("DISTRIBUCION DE PRECIOS DE ENTRADA:")
print("=" * 60)

prices = [t.get('entry_price', 0) for t in trades if t.get('entry_price')]
if prices:
    low = len([p for p in prices if p < 0.15])
    mid = len([p for p in prices if 0.15 <= p < 0.50])
    high = len([p for p in prices if p >= 0.50])
    
    print(f"  <15c (bajo): {low} ({low/len(prices)*100:.1f}%)")
    print(f"  15-50c (medio): {mid} ({mid/len(prices)*100:.1f}%)")
    print(f"  >50c (alto): {high} ({high/len(prices)*100:.1f}%)")

# Top 5 best unrealized
print("\n[TOP 5 MEJORES UNREALIZED]")
sorted_by_pnl = sorted(open_trades, key=lambda x: x.get('pnl_usd', 0), reverse=True)
for t in sorted_by_pnl[:5]:
    print(f"  {t.get('market', 'N/A')[:40]} | ${t.get('pnl_usd', 0):.2f} | Entry: {t.get('entry_price', 0):.2f}")

# Top 5 worst unrealized
print("\n[TOP 5 PEORES UNREALIZED]")
for t in sorted_by_pnl[-5:]:
    print(f"  {t.get('market', 'N/A')[:40]} | ${t.get('pnl_usd', 0):.2f} | Entry: {t.get('entry_price', 0):.2f}")

# Check unique markets
print("\n" + "=" * 60)
print("MERCADOS UNICOS:")
print("=" * 60)
markets = set(t.get('market', 'N/A')[:50] for t in trades)
print(f"Total mercados unicos: {len(markets)}")
for m in list(markets)[:10]:
    count = len([t for t in trades if t.get('market', '')[:50] == m])
    print(f"  {m} (x{count})")
