# Polymarket Bot - Advanced Strategies

## Estado Actual

### Producción (Fly.io - v22)
El bot de producción está corriendo **v22** que funciona correctamente:
- ✅ Trading automático basado en score
- ✅ Comparación cross-platform con Kalshi
- ✅ Swing trading con take profit / stop loss dinámicos
- ✅ Resolución automática de trades
- ✅ 70%+ win rate demostrado

**NO TOCAR** - Dejar corriendo mientras acumula datos.

### Local Development
Los archivos locales ahora incluyen:
- Código base de v22 (sincronizado)
- Módulo de estrategias avanzadas (`advanced_strategies.py`)

## Nuevas Estrategias (Aditivas)

El archivo `advanced_strategies.py` implementa 6 estrategias adicionales **sin modificar** el código principal:

### 1. Multi-Outcome Arbitrage
Detecta mercados donde la suma de probabilidades ≠ 100%
- Si suma > 100%: OVERPRICED - se puede shortear todo
- Si suma < 100%: UNDERPRICED - se puede comprar todo

### 2. Resolution Arbitrage
Encuentra mercados donde el resultado es conocido pero el precio no se ajustó:
- Fechas vencidas
- Patrones de hoy/ayer en el título
- Alta probabilidad que debería ser 99%

### 3. Time Decay (Theta Plays)
Mercados de alta probabilidad expirando pronto:
- Calcula theta diario = (expected - current) / days
- Ideal para "collect premium"

### 4. Correlated Markets
Pares de mercados que deberían moverse juntos pero están mal preciadoss:
- Mismo evento con diferente framing
- Entidades relacionadas (Trump nomination vs election)

### 5. Insider Detection
Volumen inusual sin movimiento de precio:
- Alta actividad + bajo cambio = acumulación informada
- Señales de ACCUMULATION o DISTRIBUTION

### 6. Sports Mispricing
Sesgo de fans en mercados deportivos:
- Equipos populares tienden a estar overvalued
- Detecta Lakers, Cowboys, Yankees, etc.

## Uso

### Correr localmente con estrategias avanzadas:
```bash
python run_local.py
```

Esto ejecuta el bot normal + escanea las estrategias avanzadas cada ciclo.

### Solo escanear estrategias (sin trading):
```python
from advanced_strategies import advanced_scanner

results = advanced_scanner.scan_all()
data = advanced_scanner.get_dashboard_data()

# Ver oportunidades
print(data['resolution'])  # Resolution arbitrage
print(data['time_decay'])  # Theta plays
print(data['insider'])     # Insider signals
```

## Archivos

| Archivo | Descripción |
|---------|-------------|
| `trader.py` | Trading principal (v22) |
| `simulation_tracker.py` | Tracking de trades (v22) |
| `trade_resolver.py` | Resolución y swing trading (v22) |
| `advanced_strategies.py` | **NUEVO** - 6 estrategias adicionales |
| `run_local.py` | **NUEVO** - Runner local con todo integrado |
| `trader_v22.py` | Backup del trader de producción |

## Próximos Pasos

1. **Correr localmente** y verificar que las estrategias detectan oportunidades reales
2. **Validar** que los profits estimados son correctos
3. **Decidir** cuáles estrategias agregar al trading automático
4. **Integrar** gradualmente sin romper v22

## Notas Importantes

- Los archivos de v22 son **idénticos** a producción
- Las estrategias avanzadas son **read-only** (solo muestran, no tradean)
- Para habilitar trading de estrategias avanzadas, se requiere integración explícita
