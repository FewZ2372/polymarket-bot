import json

with open('simulation_data.json') as f:
    data = json.load(f)

closed = [t for t in data['trades'] if t['status'] in ['WIN', 'LOSS', 'EXITED']]
wins = [t for t in closed if t['status'] == 'WIN' or (t['status'] == 'EXITED' and t['pnl_usd'] > 0)]
losses = [t for t in closed if t['status'] == 'LOSS' or (t['status'] == 'EXITED' and t['pnl_usd'] <= 0)]

print(f"Closed trades: {len(closed)}")
print(f"Wins: {len(wins)} | Losses: {len(losses)}")
print(f"Win rate: {len(wins)/len(closed)*100:.1f}%" if closed else "N/A")
print()
print("=== WINS ===")
for t in wins[:10]:
    print(f"  +${t['pnl_usd']:.2f} | {t['entry_price']:.4f} -> {t.get('exit_price', 'N/A')} | {t['market'][:45]}")
print()
print("=== LOSSES ===")
for t in losses[:10]:
    print(f"  ${t['pnl_usd']:.2f} | {t['entry_price']:.4f} -> {t.get('exit_price', 'N/A')} | {t['market'][:45]}")
