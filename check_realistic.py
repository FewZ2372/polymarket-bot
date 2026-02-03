"""Check realistic returns from local data"""
import json

print("=" * 60)
print("ANALISIS DE RETORNOS REALES")
print("=" * 60)

# Check local simulation data
try:
    with open('simulation_data.json') as f:
        data = json.load(f)
    trades = data.get('trades', [])
    
    # Get all trades with current_price to calculate unrealized P&L
    for t in trades:
        if t.get('status') == 'OPEN':
            entry = t.get('entry_price', 0)
            current = t.get('current_price', entry)
            if entry > 0:
                t['calc_pnl_pct'] = ((current - entry) / entry) * 100
            else:
                t['calc_pnl_pct'] = 0
    
    open_trades = [t for t in trades if t.get('status') == 'OPEN']
    closed_trades = [t for t in trades if t.get('status') != 'OPEN']
    
    print(f"\nTrades abiertos: {len(open_trades)}")
    print(f"Trades cerrados: {len(closed_trades)}")
    
    # Analyze open trades P&L
    if open_trades:
        pnls = [t.get('calc_pnl_pct', 0) for t in open_trades]
        positive = [p for p in pnls if p > 0]
        negative = [p for p in pnls if p < 0]
        
        print(f"\nTRADES ABIERTOS - Unrealized P&L:")
        print(f"  Positivos: {len(positive)}")
        print(f"  Negativos: {len(negative)}")
        
        if positive:
            print(f"\n  Mejor unrealized: +{max(positive):.1f}%")
            print(f"  Promedio positivos: +{sum(positive)/len(positive):.1f}%")
        
        if negative:
            print(f"  Peor unrealized: {min(negative):.1f}%")
        
        # Show current positions
        print("\n" + "-" * 60)
        print("POSICIONES ACTUALES (ordenadas por P&L):")
        print("-" * 60)
        sorted_trades = sorted(open_trades, key=lambda x: x.get('calc_pnl_pct', 0), reverse=True)
        for t in sorted_trades[:20]:
            market = t.get('market', 'N/A')[:40]
            entry = t.get('entry_price', 0)
            current = t.get('current_price', 0)
            pnl = t.get('calc_pnl_pct', 0)
            print(f"  {market} | Entry: {entry:.2f} | Now: {current:.2f} | {pnl:+.1f}%")

except Exception as e:
    print(f"Error: {e}")

# Theoretical analysis
print("\n" + "=" * 60)
print("ANALISIS TEORICO: QUE ES REALISTA?")
print("=" * 60)
print("""
En Polymarket, un movimiento de +30% en precio significa:
- Comprar a 10c, que suba a 13c (+3c)
- Comprar a 20c, que suba a 26c (+6c)

Esto sucede cuando:
1. HAY NOTICIAS - Un evento cambia las probabilidades
2. CERCA DEL CIERRE - El mercado converge a 0 o 100
3. VOLATILIDAD - Mercados de deportes en vivo

Para mercados "tranquilos" (politica, M&A, etc):
- Movimientos tipicos: 1-5% por dia
- +30% puede tomar SEMANAS sin noticias

CONCLUSION:
- +30% en 12h: SOLO con noticias o eventos
- +15% en 24h: Posible con buena seleccion
- +10% en 48h: Mas realista como objetivo base
""")
