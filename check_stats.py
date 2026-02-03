import json

with open('local_stats.json') as f:
    d = json.load(f)['stats']

print("LOCAL BOT:")
print(f"  Trades: {d['total_trades']}")
print(f"  Open: {d['open_positions']}")
print(f"  Closed: {d['closed_trades']}")
print(f"  Wins: {d['wins']} / Losses: {d['losses']}")
print(f"  WinRate: {d['win_rate']:.1f}%")
print(f"  Invested: ${d['total_invested']:.2f}")
print(f"  Realized PnL: ${d['realized_pnl']:.2f}")
print(f"  Unrealized PnL: ${d['unrealized_pnl']:.2f}")
print(f"  Total PnL: ${d['total_pnl']:.2f}")
print(f"  ROI: {d['pnl_pct']:.1f}%")
