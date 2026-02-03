"""Script para verificar estado de salud del mercado."""
from simulation_tracker import simulation_tracker
from market_health import market_health

# Load simulation data
simulation_tracker.load()
stats = simulation_tracker.get_stats()

print('='*60)
print(' ESTADISTICAS ACTUALES')
print('='*60)
invested = stats.get('total_invested', 0)
roi = (stats.get('total_pnl', 0) / invested * 100) if invested > 0 else 0
print(f" Total Trades: {stats.get('total_trades', 0)}")
print(f" Open:         {stats.get('open_positions', 0)}")
print(f" Closed:       {stats.get('closed_trades', 0)}")
print(f" Win Rate:     {stats.get('win_rate', 0):.1f}%")
print(f" Realized PnL: ${stats.get('realized_pnl', 0):.2f}")
print(f" Unrealized:   ${stats.get('unrealized_pnl', 0):.2f}")
print(f" Total PnL:    ${stats.get('total_pnl', 0):.2f}")
print(f" Invested:     ${invested:.2f}")
print(f" ROI:          {roi:.1f}%")
print('='*60)

# Calculate market health from trades
trades_for_health = [
    {
        'timestamp': t.timestamp,
        'pnl_pct': t.pnl_pct,
        'status': t.status,
        'spread': getattr(t, 'spread', 0),
        'exit_time': getattr(t, 'exit_time', None),
    }
    for t in simulation_tracker.trades
]

print()
print('Calculando Market Health...')
metrics = market_health.calculate_metrics(trades_for_health)

# emoji = market_health.get_status_emoji()  # Windows encoding issue
status_symbols = {"HEALTHY": "[OK]", "CAUTION": "[!]", "WARNING": "[!!]", "CRITICAL": "[X]"}
symbol = status_symbols.get(metrics.status, "[?]")
print()
print('='*60)
print(f' MARKET HEALTH: {symbol} {metrics.status}')
print('='*60)
print(f' Health Score:      {metrics.health_score}/100')
print(f' ROI Promedio:      {metrics.avg_roi_pct:+.1f}%')
print(f' Win Rate:          {metrics.win_rate*100:.0f}%')
print(f' Oportunidades/dia: {metrics.opportunities_per_day:.1f}')
print(f' Trades analizados: {metrics.trades_analyzed}')
print()
print(' [TENDENCIAS vs semana anterior]')
if metrics.roi_trend:
    print(f'   ROI:           {metrics.roi_trend*100:+.1f}%')
else:
    print('   ROI:           N/A (sin datos previos)')
if metrics.opportunities_trend:
    print(f'   Oportunidades: {metrics.opportunities_trend*100:+.1f}%')
else:
    print('   Oportunidades: N/A')
if metrics.win_rate_trend:
    print(f'   Win Rate:      {metrics.win_rate_trend*100:+.1f}%')
else:
    print('   Win Rate:      N/A')
print()
adj = market_health.get_adjustments()
print(' [AJUSTES AL BOT]')
print(f'   Position Size: {adj.position_size_multiplier*100:.0f}%')
print(f'   Score Minimo:  {adj.min_score_threshold}')
print(f'   Max Trades:    {adj.max_concurrent_trades}')
print(f'   Take Profit:   {adj.take_profit_multiplier*100:.0f}% del normal')
trading_status = "Permitido" if adj.is_trading_allowed else "Pausado"
print(f'   Trading:       {trading_status}')
print()
print(f' Razon: {adj.reason}')
print('='*60)
