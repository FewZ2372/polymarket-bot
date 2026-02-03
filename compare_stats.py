"""Compare local vs v22 stats"""
import json
import urllib.request

# Local stats
with open('simulation_data.json') as f:
    local = json.load(f)

local_trades = local.get('trades', [])
local_open = [t for t in local_trades if t['status'] == 'OPEN']
local_closed = [t for t in local_trades if t['status'] != 'OPEN']
local_wins = [t for t in local_closed if t.get('pnl_usd', 0) > 0]
local_losses = [t for t in local_closed if t.get('pnl_usd', 0) <= 0]
local_pnl = sum(t.get('pnl_usd', 0) for t in local_trades)
local_realized = sum(t.get('pnl_usd', 0) for t in local_closed)
local_invested = len(local_trades) * 2  # $2 per trade

print("=" * 60)
print("COMPARACION LOCAL vs V22")
print("=" * 60)
print(f"\n{'Metrica':<25} {'LOCAL':<15} {'V22':<15}")
print("-" * 55)

# V22 stats
try:
    with urllib.request.urlopen('https://pm-scanner-felipe.fly.dev/simulation', timeout=10) as resp:
        v22 = json.loads(resp.read())['stats']
    
    v22_trades = v22['total_trades']
    v22_open = v22['open_positions']
    v22_closed = v22['closed_trades']
    v22_winrate = v22['win_rate']
    v22_pnl = v22['total_pnl']
    v22_realized = v22['realized_pnl']
    
    local_winrate = (len(local_wins) / len(local_closed) * 100) if local_closed else 0
    
    print(f"{'Total Trades':<25} {len(local_trades):<15} {v22_trades:<15}")
    print(f"{'Open':<25} {len(local_open):<15} {v22_open:<15}")
    print(f"{'Closed':<25} {len(local_closed):<15} {v22_closed:<15}")
    print(f"{'Wins':<25} {len(local_wins):<15} {'N/A':<15}")
    print(f"{'Losses':<25} {len(local_losses):<15} {'N/A':<15}")
    print(f"{'Win Rate':<25} {local_winrate:.1f}%{'':<10} {v22_winrate:.1f}%")
    print(f"{'Realized P&L':<25} ${local_realized:.2f}{'':<10} ${v22_realized:.2f}")
    print(f"{'Total P&L':<25} ${local_pnl:.2f}{'':<10} ${v22_pnl:.2f}")
    print(f"{'Invested':<25} ${local_invested:.2f}")
    
    # Analyze why local is worse
    print("\n" + "=" * 60)
    print("ANALISIS DE DIFERENCIAS")
    print("=" * 60)
    
    if local_winrate < v22_winrate:
        print(f"\n[!] Win rate local ({local_winrate:.1f}%) es MENOR que v22 ({v22_winrate:.1f}%)")
    
    # Check strategy distribution
    strategies = {}
    for t in local_closed:
        strat = t.get('strategy', 'unknown')
        if strat not in strategies:
            strategies[strat] = {'wins': 0, 'losses': 0, 'pnl': 0}
        if t.get('pnl_usd', 0) > 0:
            strategies[strat]['wins'] += 1
        else:
            strategies[strat]['losses'] += 1
        strategies[strat]['pnl'] += t.get('pnl_usd', 0)
    
    print("\n[PERFORMANCE POR ESTRATEGIA - LOCAL]")
    for strat, data in sorted(strategies.items(), key=lambda x: x[1]['pnl'], reverse=True):
        total = data['wins'] + data['losses']
        wr = (data['wins'] / total * 100) if total > 0 else 0
        print(f"  {strat[:30]:<30} | W:{data['wins']:>3} L:{data['losses']:>3} | WR:{wr:>5.1f}% | PnL: ${data['pnl']:>7.2f}")
    
    # Check for problematic trades
    print("\n[TOP 5 PEORES TRADES - LOCAL]")
    worst = sorted(local_closed, key=lambda x: x.get('pnl_usd', 0))[:5]
    for t in worst:
        print(f"  {t.get('market_title', 'N/A')[:40]:<40} | ${t.get('pnl_usd', 0):.2f}")

except Exception as e:
    print(f"Error fetching v22: {e}")
    print(f"\nLOCAL SOLO:")
    print(f"  Trades: {len(local_trades)}")
    print(f"  Open: {len(local_open)}")
    print(f"  Closed: {len(local_closed)}")
    print(f"  Wins: {len(local_wins)}")
    print(f"  Losses: {len(local_losses)}")
    print(f"  PnL: ${local_pnl:.2f}")
