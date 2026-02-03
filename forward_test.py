"""
Forward Test - Acelera la evaluación del bot rastreando trades REALES
sin arriesgar dinero verdadero.

En lugar de backtest (pasado) o esperar meses (futuro),
este script:
1. Toma tus trades simulados actuales
2. Los monitorea cada hora
3. Proyecta cuándo se resolverán
4. Te da métricas de progreso

Es como "fast-forward" tu simulación actual.
"""
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
import requests

def load_simulation():
    """Cargar trades de simulación."""
    try:
        with open('simulation_data.json', 'r') as f:
            data = json.load(f)
        return data.get('trades', [])
    except Exception as e:
        print(f"Error: {e}")
        return []

def analyze_resolution_timeline(trades: List[Dict]) -> Dict[str, Any]:
    """
    Analizar cuándo se espera que los trades se resuelvan.
    """
    open_trades = [t for t in trades if t.get('status') == 'OPEN']
    closed_trades = [t for t in trades if t.get('status') != 'OPEN']
    
    print(f"\nAnalizando {len(open_trades)} trades abiertos...")
    
    # Agrupar por mercado
    by_market = {}
    for trade in open_trades:
        market = trade.get('market', '')
        slug = trade.get('market_slug', '')
        if market not in by_market:
            by_market[market] = {
                'trades': [],
                'slug': slug,
                'total_invested': 0,
                'current_pnl': 0,
            }
        by_market[market]['trades'].append(trade)
        by_market[market]['total_invested'] += trade.get('amount_usd', 0)
        by_market[market]['current_pnl'] += trade.get('pnl_usd', 0)
    
    print(f"Agrupados en {len(by_market)} mercados unicos")
    
    # Obtener info actual de cada mercado
    print("\nObteniendo info actual de Polymarket...")
    
    market_info = []
    for market_name, data in by_market.items():
        slug = data['slug']
        
        # Fetch current market data
        try:
            # Use gamma API
            response = requests.get(
                f"https://gamma-api.polymarket.com/markets",
                params={'slug': slug},
                timeout=10
            )
            
            if response.status_code == 200:
                markets = response.json()
                if markets:
                    m = markets[0]
                    end_date = m.get('endDate', '')
                    current_price = 0.5
                    
                    # Parse price
                    prices_str = m.get('outcomePrices', '')
                    if prices_str:
                        try:
                            prices = json.loads(prices_str) if isinstance(prices_str, str) else prices_str
                            current_price = float(prices[0])
                        except:
                            pass
                    
                    # Parse end date
                    days_to_resolution = 999
                    if end_date:
                        try:
                            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                            if end_dt.tzinfo:
                                end_dt = end_dt.replace(tzinfo=None)
                            days_to_resolution = (end_dt - datetime.now()).days
                        except:
                            pass
                    
                    market_info.append({
                        'name': market_name[:50],
                        'slug': slug,
                        'invested': data['total_invested'],
                        'current_pnl': data['current_pnl'],
                        'trade_count': len(data['trades']),
                        'current_price': current_price,
                        'days_to_resolution': days_to_resolution,
                        'end_date': end_date[:10] if end_date else 'Unknown',
                    })
        except Exception as e:
            print(f"  Error fetching {slug}: {e}")
    
    # Sort by days to resolution
    market_info.sort(key=lambda x: x['days_to_resolution'])
    
    return {
        'open_trades': len(open_trades),
        'closed_trades': len(closed_trades),
        'unique_markets': len(by_market),
        'total_invested': sum(t.get('amount_usd', 0) for t in open_trades),
        'total_unrealized_pnl': sum(t.get('pnl_usd', 0) for t in open_trades),
        'markets': market_info,
    }

def print_timeline(analysis: Dict):
    """Imprimir timeline de resoluciones."""
    print("\n" + "="*80)
    print(" TIMELINE DE RESOLUCIONES - Trades Abiertos")
    print("="*80)
    
    print(f"\n[RESUMEN]")
    print(f"  Trades abiertos:    {analysis['open_trades']}")
    print(f"  Trades cerrados:    {analysis['closed_trades']}")
    print(f"  Mercados unicos:    {analysis['unique_markets']}")
    print(f"  Total invertido:    ${analysis['total_invested']:.2f}")
    print(f"  P&L no realizado:   ${analysis['total_unrealized_pnl']:.2f}")
    
    print("\n[MERCADOS POR FECHA DE RESOLUCION]")
    print("-"*80)
    print(f"{'Mercado':<40} {'Dias':>6} {'Inv':>8} {'P&L':>10} {'Precio':>8}")
    print("-"*80)
    
    # Group by timeframe
    this_week = []
    this_month = []
    later = []
    
    for m in analysis['markets']:
        days = m['days_to_resolution']
        if days <= 7:
            this_week.append(m)
        elif days <= 30:
            this_month.append(m)
        else:
            later.append(m)
    
    if this_week:
        print("\n>>> ESTA SEMANA (prioridad alta)")
        for m in this_week:
            pnl_str = f"${m['current_pnl']:+.2f}"
            print(f"  {m['name']:<38} {m['days_to_resolution']:>4}d ${m['invested']:>6.0f} {pnl_str:>10} {m['current_price']:>7.0%}")
    
    if this_month:
        print("\n>>> ESTE MES")
        for m in this_month:
            pnl_str = f"${m['current_pnl']:+.2f}"
            print(f"  {m['name']:<38} {m['days_to_resolution']:>4}d ${m['invested']:>6.0f} {pnl_str:>10} {m['current_price']:>7.0%}")
    
    if later:
        print("\n>>> MAS ADELANTE")
        for m in later[:5]:  # Solo primeros 5
            pnl_str = f"${m['current_pnl']:+.2f}"
            print(f"  {m['name']:<38} {m['days_to_resolution']:>4}d ${m['invested']:>6.0f} {pnl_str:>10} {m['current_price']:>7.0%}")
        if len(later) > 5:
            print(f"  ... y {len(later) - 5} mercados mas")
    
    print("\n" + "="*80)
    
    # Proyección
    print("\n[PROYECCION]")
    
    week_invested = sum(m['invested'] for m in this_week)
    week_pnl = sum(m['current_pnl'] for m in this_week)
    
    month_invested = sum(m['invested'] for m in this_month)
    month_pnl = sum(m['current_pnl'] for m in this_month)
    
    print(f"  Esta semana podrias resolver: ${week_invested:.0f} invertidos ({len(this_week)} mercados)")
    print(f"    P&L proyectado si mantienen precios: ${week_pnl:+.2f}")
    
    print(f"  Este mes:                     ${week_invested + month_invested:.0f} ({len(this_week) + len(this_month)} mercados)")
    print(f"    P&L proyectado:                      ${week_pnl + month_pnl:+.2f}")
    
    # Warning
    if len(later) > len(this_week) + len(this_month):
        print(f"\n  [ATENCION] {len(later)} mercados se resuelven en >30 dias")
        print(f"  Capital atrapado: ${sum(m['invested'] for m in later):.0f}")


def main():
    print("="*80)
    print(" FORWARD TEST - Proyeccion de Resoluciones")
    print("="*80)
    
    trades = load_simulation()
    if not trades:
        print("No hay trades para analizar")
        return
    
    analysis = analyze_resolution_timeline(trades)
    print_timeline(analysis)


if __name__ == "__main__":
    main()
