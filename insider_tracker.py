import requests
import time

def fetch_top_traders():
    """
    Obtiene los mejores usuarios del leaderboard de Polymarket.
    """
    url = "https://gamma-api.polymarket.com/users?order=profit&ascending=false&limit=20"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
    return []

def track_insiders():
    """
    Función principal de rastreo de movimientos de billeteras.
    """
    print("🕵️ Iniciando Insider Tracker...")
    traders = fetch_top_traders()
    
    if not traders:
        print("No se encontraron traders en el leaderboard.")
        return

    print(f"Vigilando a {len(traders)} top traders...")
    
    # Aquí iría la lógica de polling de transacciones en la blockchain
    # Por ahora marcamos presencia en el loop principal
    for trader in traders:
        name = trader.get('displayName', 'Anon')
        address = trader.get('proxyAddress', '0x...')
        profit = trader.get('profit', 0)
        print(f"[*] Vigilando trader: {name} ({address}) | Profit: ${profit:,.2f}")

if __name__ == "__main__":
    track_insiders()
