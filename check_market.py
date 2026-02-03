import json
with open('simulation_data.json') as f:
    trades = json.load(f)['trades']

open_trades = [t for t in trades if t['status'] == 'OPEN']
if open_trades:
    # Get unique markets
    markets = {}
    for t in open_trades:
        m = t.get('market_title', 'unknown')
        if m not in markets:
            markets[m] = []
        markets[m].append(t)
    
    print(f"Total open: {len(open_trades)}")
    print(f"Unique markets: {len(markets)}")
    print("\nTop markets by trade count:")
    for m, trades_list in sorted(markets.items(), key=lambda x: len(x[1]), reverse=True)[:5]:
        print(f"  {m[:50]} | {len(trades_list)} trades")
        if trades_list:
            print(f"    Entry price: {trades_list[0].get('entry_price', 'N/A')}")
            print(f"    Side: {trades_list[0].get('side', 'N/A')}")
