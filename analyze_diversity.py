import json
from collections import Counter

# V1
with open('mega_bot_state.json', 'r') as f:
    v1 = json.load(f)

all_v1 = v1['positions'] + v1['closed_positions']
v1_markets = Counter(p['market_question'][:50] for p in all_v1)

print("=== V1: Market Distribution ===")
print(f"Total trades: {len(all_v1)}")
print(f"Unique markets: {len(v1_markets)}")
print(f"Avg trades per market: {len(all_v1)/len(v1_markets):.1f}")
print("\nTop markets by # of trades:")
for market, count in v1_markets.most_common(10):
    print(f"  {count}x - {market}")

# V2
with open('mega_bot_v2_state.json', 'r') as f:
    v2 = json.load(f)

all_v2 = v2['positions'] + v2['closed_positions']
v2_markets = Counter(p['market_question'][:50] for p in all_v2)

print("\n=== V2: Market Distribution ===")
print(f"Total trades: {len(all_v2)}")
print(f"Unique markets: {len(v2_markets)}")
print(f"Avg trades per market: {len(all_v2)/len(v2_markets):.1f}")
print("\nTop markets by # of trades:")
for market, count in v2_markets.most_common(10):
    print(f"  {count}x - {market}")

# Opportunities found vs traded
print("\n=== Opportunity Flow ===")
print(f"V1: {v1['stats']['total_opportunities']} opportunities found -> {v1['stats']['total_trades']} trades")
print(f"V2: {v2['stats']['total_opportunities']} opportunities found -> {v2['stats']['total_trades']} trades")
print(f"V1 conversion: {v1['stats']['total_trades']/v1['stats']['total_opportunities']*100:.2f}%")
print(f"V2 conversion: {v2['stats']['total_trades']/v2['stats']['total_opportunities']*100:.2f}%")
