import json

# V1 Analysis
with open('mega_bot_state.json', 'r') as f:
    v1 = json.load(f)

print("=== V1 Closed Positions ===")
v1_wins = 0
v1_losses = 0
for p in v1['closed_positions']:
    pnl = p.get('pnl', 0)
    if pnl > 0:
        v1_wins += 1
        status = "WIN"
    else:
        v1_losses += 1
        status = "LOSS"
    q = p['market_question'][:45]
    print(f"  {q}... PnL: ${pnl:.2f} [{status}]")

print(f"\nV1 Actual: {v1_wins}W / {v1_losses}L = {v1_wins/(v1_wins+v1_losses)*100:.0f}% WR")
print(f"V1 Stats in JSON: {v1['stats']['wins']}W / {v1['stats']['losses']}L")

# V2 Analysis  
with open('mega_bot_v2_state.json', 'r') as f:
    v2 = json.load(f)

print("\n=== V2 Closed Positions ===")
v2_wins = 0
v2_losses = 0
for p in v2['closed_positions']:
    pnl = p.get('pnl', 0)
    if pnl > 0:
        v2_wins += 1
        status = "WIN"
    else:
        v2_losses += 1
        status = "LOSS"
    q = p['market_question'][:45]
    print(f"  {q}... PnL: ${pnl:.2f} [{status}]")

print(f"\nV2 Actual: {v2_wins}W / {v2_losses}L = {v2_wins/(v2_wins+v2_losses)*100:.0f}% WR")
print(f"V2 Stats in JSON: {v2['stats']['wins']}W / {v2['stats']['losses']}L")
