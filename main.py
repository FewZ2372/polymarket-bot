import time
import os
import requests
from scanner import get_top_markets, calculate_inefficiency_score, fetch_kalshi_price
from insider_tracker import track_insiders
from sentiment_analyzer import SentimentAnalyzer

# Instanciar el analizador
analyzer = SentimentAnalyzer()

def main_loop():
    print("🚀 Iniciando el Bot de Trading de Polymarket...")
    while True:
        try:
            print("\n--- Ejecutando ciclo de escaneo ---")
            markets = get_top_markets()
            
            for m in markets[:10]:
                title = m.get('question', 'Unknown')
                price = m.get('yes', 0)
                vol = m.get('vol24h', 0)
                score = m.get('score', 0)
                
                # 1. Análisis de Sentimiento
                try:
                    sentiment_data = analyzer.analyze(title)
                except Exception as e:
                    print(f"Error analizando sentimiento: {e}")
                    sentiment_data = {'sentiment': 'UNKNOWN', 'buzz_score': 0}
                
                # 2. Si el score es alto, profundizar
                if score >= 80:
                    print(f"\n[!] Oportunidad Detectada: {title}")
                    print(f"[*] Score: {score} | Precio: {price} | Vol 24h: ${vol:,.2f}")
                    print(f"[*] Sentimiento: {sentiment_data['sentiment']} | Buzz: {sentiment_data['buzz_score']}")
                    
                    # 3. Si el sentimiento confirma la anomalía, mandar alerta
                    if score == 100 or (score >= 80 and sentiment_data.get('buzz_score', 0) > 70):
                        print("[ALERT] Enviando alerta a WhatsApp...")
                        try:
                            msg = (
                                f"🚀 *ALERTA DE TRADING*\n\n"
                                f"📈 *Mercado:* {title}\n"
                                f"💰 *Precio:* ${price:.4f}\n"
                                f"🔥 *Score Ineficiencia:* {score}/100\n"
                                f"📊 *Vol 24h:* ${vol:,.2f}\n"
                                f"🗣️ *Sentimiento:* {sentiment_data['sentiment']} ({sentiment_data['buzz_score']})\n\n"
                                f"🔗 *Operar:* https://polymarket.com/event/{title.lower().replace(' ', '-')}"
                            )
                            
                            # Enviar via Gateway usando el Bridge personalizado
                            requests.post("http://localhost:3000/req", json={
                                "type": "req",
                                "id": f"alert-{int(time.time())}",
                                "method": "whatsapp.sendMessage",
                                "params": {
                                    "target": "+5491164079874",
                                    "message": msg
                                }
                            }, timeout=5)
                        except Exception as e:
                            print(f"Error enviando WhatsApp: {e}")
            
            print("\nCiclo completado. Esperando 10 minutos...")
            time.sleep(600) # Chequeamos cada 10 minutos
            
        except Exception as e:
            print(f"Error en el loop: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main_loop()
