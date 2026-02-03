"""
Análisis completo de todas las versiones del bot.
Compara rendimiento de simulation_data.json para entender qué versión usar con dinero real.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

def load_simulation_data():
    """Cargar datos de simulación."""
    try:
        with open('simulation_data.json', 'r') as f:
            data = json.load(f)
        return data.get('trades', [])
    except:
        return []

def analyze_trades(trades):
    """Analizar trades y generar estadísticas."""
    if not trades:
        return None
    
    stats = {
        'total_trades': len(trades),
        'open': 0,
        'closed': 0,
        'wins': 0,
        'losses': 0,
        'total_invested': 0,
        'realized_pnl': 0,
        'unrealized_pnl': 0,
        'by_strategy': defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0, 'invested': 0}),
        'by_market': defaultdict(lambda: {'trades': 0, 'pnl': 0}),
        'by_price_range': defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0}),
        'timeline': [],
    }
    
    for trade in trades:
        amount = trade.get('amount_usd', 0)
        pnl = trade.get('pnl_usd', 0)
        pnl_pct = trade.get('pnl_pct', 0)
        status = trade.get('status', 'OPEN')
        entry_price = trade.get('entry_price', 0.5)
        market = trade.get('market', '')[:50]
        
        # Determinar estrategia (puede estar en varios campos)
        strategy = 'UNKNOWN'
        if 'insider' in market.lower() or trade.get('category') == 'insider_signal':
            strategy = 'INSIDER'
        elif 'arb' in str(trade.get('category', '')).lower():
            strategy = 'ARBITRAGE'
        elif trade.get('has_momentum'):
            strategy = 'MOMENTUM'
        else:
            strategy = 'SWING'
        
        stats['total_invested'] += amount
        
        # Price range
        if entry_price < 0.15:
            price_range = 'bajo (<15%)'
        elif entry_price < 0.30:
            price_range = 'medio-bajo (15-30%)'
        elif entry_price < 0.50:
            price_range = 'medio (30-50%)'
        elif entry_price < 0.70:
            price_range = 'medio-alto (50-70%)'
        else:
            price_range = 'alto (>70%)'
        
        stats['by_price_range'][price_range]['trades'] += 1
        stats['by_strategy'][strategy]['trades'] += 1
        stats['by_strategy'][strategy]['invested'] += amount
        stats['by_market'][market]['trades'] += 1
        
        if status in ['EXITED', 'WIN', 'LOSS', 'RESOLVED', 'CLOSED']:
            stats['closed'] += 1
            stats['realized_pnl'] += pnl
            stats['by_strategy'][strategy]['pnl'] += pnl
            stats['by_market'][market]['pnl'] += pnl
            stats['by_price_range'][price_range]['pnl'] += pnl
            
            if pnl > 0:
                stats['wins'] += 1
                stats['by_strategy'][strategy]['wins'] += 1
                stats['by_price_range'][price_range]['wins'] += 1
            else:
                stats['losses'] += 1
        else:
            stats['open'] += 1
            stats['unrealized_pnl'] += pnl
    
    # Calcular métricas derivadas
    stats['total_pnl'] = stats['realized_pnl'] + stats['unrealized_pnl']
    stats['win_rate'] = (stats['wins'] / stats['closed'] * 100) if stats['closed'] > 0 else 0
    stats['roi'] = (stats['total_pnl'] / stats['total_invested'] * 100) if stats['total_invested'] > 0 else 0
    stats['roi_realized'] = (stats['realized_pnl'] / stats['total_invested'] * 100) if stats['total_invested'] > 0 else 0
    
    return stats

def print_analysis(stats):
    """Imprimir análisis detallado."""
    if not stats:
        print("No hay datos para analizar")
        return
    
    print("="*70)
    print(" ANALISIS COMPLETO DE LA SIMULACION")
    print("="*70)
    
    print("\n[RESUMEN GENERAL]")
    print(f"  Total Trades:     {stats['total_trades']}")
    print(f"  Abiertos:         {stats['open']}")
    print(f"  Cerrados:         {stats['closed']}")
    print(f"  Wins:             {stats['wins']}")
    print(f"  Losses:           {stats['losses']}")
    print(f"  Win Rate:         {stats['win_rate']:.1f}%")
    print(f"  Total Invertido:  ${stats['total_invested']:.2f}")
    print(f"  P&L Realizado:    ${stats['realized_pnl']:.2f}")
    print(f"  P&L No Realizado: ${stats['unrealized_pnl']:.2f}")
    print(f"  P&L Total:        ${stats['total_pnl']:.2f}")
    print(f"  ROI Total:        {stats['roi']:.1f}%")
    print(f"  ROI Realizado:    {stats['roi_realized']:.1f}%")
    
    print("\n[POR RANGO DE PRECIO DE ENTRADA]")
    print("-"*70)
    for price_range, data in sorted(stats['by_price_range'].items()):
        trades = data['trades']
        wins = data['wins']
        pnl = data['pnl']
        wr = (wins/trades*100) if trades > 0 else 0
        print(f"  {price_range:20s} | {trades:3d} trades | WR: {wr:5.1f}% | P&L: ${pnl:+8.2f}")
    
    print("\n[POR ESTRATEGIA]")
    print("-"*70)
    for strategy, data in sorted(stats['by_strategy'].items(), key=lambda x: -x[1]['pnl']):
        trades = data['trades']
        wins = data['wins']
        pnl = data['pnl']
        invested = data['invested']
        roi = (pnl/invested*100) if invested > 0 else 0
        print(f"  {strategy:15s} | {trades:3d} trades | ${invested:7.2f} inv | P&L: ${pnl:+8.2f} | ROI: {roi:+6.1f}%")
    
    print("\n[TOP 10 MERCADOS POR P&L]")
    print("-"*70)
    sorted_markets = sorted(stats['by_market'].items(), key=lambda x: -x[1]['pnl'])
    for market, data in sorted_markets[:10]:
        trades = data['trades']
        pnl = data['pnl']
        print(f"  {market[:45]:45s} | {trades:2d} trades | ${pnl:+7.2f}")
    
    print("\n[BOTTOM 5 MERCADOS (peor rendimiento)]")
    print("-"*70)
    for market, data in sorted_markets[-5:]:
        trades = data['trades']
        pnl = data['pnl']
        print(f"  {market[:45]:45s} | {trades:2d} trades | ${pnl:+7.2f}")

def main():
    print("\nCargando datos de simulacion...")
    trades = load_simulation_data()
    
    if not trades:
        print("No se encontraron trades en simulation_data.json")
        return
    
    print(f"Encontrados {len(trades)} trades")
    
    stats = analyze_trades(trades)
    print_analysis(stats)
    
    # Conclusiones
    print("\n" + "="*70)
    print(" CONCLUSIONES Y RECOMENDACIONES")
    print("="*70)
    
    # Analizar qué funciona
    best_price_range = max(stats['by_price_range'].items(), 
                          key=lambda x: x[1]['pnl'] if x[1]['trades'] >= 3 else -999)
    
    print(f"\n1. MEJOR RANGO DE PRECIO: {best_price_range[0]}")
    print(f"   -> {best_price_range[1]['trades']} trades, ${best_price_range[1]['pnl']:.2f} P&L")
    
    # Win rate analysis
    if stats['win_rate'] >= 60:
        print(f"\n2. WIN RATE ({stats['win_rate']:.0f}%): EXCELENTE")
        print("   -> La estrategia tiene edge positivo")
    elif stats['win_rate'] >= 50:
        print(f"\n2. WIN RATE ({stats['win_rate']:.0f}%): ACEPTABLE")
        print("   -> Revisar ratio riesgo/beneficio")
    else:
        print(f"\n2. WIN RATE ({stats['win_rate']:.0f}%): PREOCUPANTE")
        print("   -> Necesita ajustes significativos")
    
    # ROI analysis
    if stats['roi_realized'] > 5:
        print(f"\n3. ROI REALIZADO ({stats['roi_realized']:.1f}%): POSITIVO")
        print("   -> El bot esta generando ganancias reales")
    elif stats['roi_realized'] > 0:
        print(f"\n3. ROI REALIZADO ({stats['roi_realized']:.1f}%): MARGINAL")
        print("   -> Ganancias minimas, revisar fees y slippage")
    else:
        print(f"\n3. ROI REALIZADO ({stats['roi_realized']:.1f}%): NEGATIVO")
        print("   -> El bot esta perdiendo dinero")
    
    # Open positions
    if stats['open'] > stats['closed'] * 2:
        print(f"\n4. POSICIONES ABIERTAS ({stats['open']}): MUCHAS")
        print("   -> Riesgo alto de capital atrapado")
        print("   -> Considerar reducir trades o esperar resoluciones")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    main()
