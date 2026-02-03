import json

data = json.load(open(r'C:\Users\Feli\.cursor\projects\c-Users-Feli-Desktop-polymarket-bot\agent-tools\b6d7c1b5-f191-4f44-a01d-a06638f98bc8.txt'))
open_trades = data.get('open_trades', [])
recent_trades = data.get('recent_trades', [])
stats = data.get('stats', {})

print("=" * 60)
print("ANALISIS DE POSICIONES - pm-scanner-felipe.fly.dev (v22)")
print("=" * 60)

print(f"\nESTADISTICAS GENERALES:")
print(f"  Total trades: {stats.get('total_trades', 0)}")
print(f"  Posiciones abiertas: {stats.get('open_positions', 0)}")
print(f"  Trades cerrados: {stats.get('closed_trades', 0)}")
print(f"  Win Rate: {stats.get('win_rate', 0):.1f}%")
print(f"  Total invertido: ${stats.get('total_invested', 0):.2f}")
print(f"  Realized P&L: ${stats.get('realized_pnl', 0):.2f}")
print(f"  Unrealized P&L: ${stats.get('unrealized_pnl', 0):.2f}")

print(f"\n\nTRADES CERRADOS (recent_trades): {len(recent_trades)}")
wins = [t for t in recent_trades if t.get('pnl', 0) > 0]
losses = [t for t in recent_trades if t.get('pnl', 0) < 0]
breakeven = [t for t in recent_trades if t.get('pnl', 0) == 0]

print(f"  Ganadores: {len(wins)}")
print(f"  Perdedores: {len(losses)}")
print(f"  Breakeven: {len(breakeven)}")

total_win_pnl = sum(t.get('pnl', 0) for t in wins)
total_loss_pnl = sum(t.get('pnl', 0) for t in losses)
print(f"\n  Total ganancias: ${total_win_pnl:.2f}")
print(f"  Total perdidas: ${total_loss_pnl:.2f}")

print("\n\n=== TOP 15 PERDIDAS ===")
sorted_losses = sorted(losses, key=lambda x: x.get('pnl', 0))[:15]
for i, t in enumerate(sorted_losses, 1):
    market = t.get('market', 'Unknown')[:55]
    pnl = t.get('pnl', 0)
    entry = t.get('entry_price', 0)
    exit_price = t.get('exit_price', 0)
    print(f"  {i:2}. ${pnl:+.2f} | Entry: {entry:.2f} -> Exit: {exit_price:.2f} | {market}")

print("\n\n=== POSICIONES ABIERTAS (sample) ===")
# Group by entry price ranges
low_entry = [t for t in open_trades if t.get('entry_price', 0) < 0.20]
mid_entry = [t for t in open_trades if 0.20 <= t.get('entry_price', 0) < 0.50]
high_entry = [t for t in open_trades if t.get('entry_price', 0) >= 0.50]

print(f"  Entry < 20c: {len(low_entry)} posiciones")
print(f"  Entry 20-50c: {len(mid_entry)} posiciones")
print(f"  Entry >= 50c: {len(high_entry)} posiciones")

# Los open_trades no tienen current_price, entonces no podemos calcular unrealized
print(f"\nNOTA: Los open_trades no incluyen precio actual,")
print(f"      el unrealized P&L total es ${stats.get('unrealized_pnl', 0):.2f}")
