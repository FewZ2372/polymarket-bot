# POLYMARKET MEGA BOT - Plan de Desarrollo

## Objetivo
Crear un bot unificado que detecte y aproveche TODAS las oportunidades de profit en Polymarket, priorizando por expected value y manteniendo un win rate >85%.

**Meta**: 20-30% profit diario sostenible.

---

## Tabla de Contenidos
1. [Arquitectura General](#1-arquitectura-general)
2. [Oportunidades a Detectar](#2-oportunidades-a-detectar)
3. [Implementación por Módulo](#3-implementación-por-módulo)
4. [Sistema de Priorización](#4-sistema-de-priorización)
5. [Testing Strategy](#5-testing-strategy)
6. [To-Do List](#6-to-do-list)

---

## 1. Arquitectura General

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         POLYMARKET MEGA BOT                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    OPPORTUNITY DETECTORS                         │    │
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐        │    │
│  │  │ Arbitrage │ │ TimeDecay │ │  Whale    │ │ Momentum  │        │    │
│  │  │ Detector  │ │ Detector  │ │ Detector  │ │ Detector  │        │    │
│  │  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘        │    │
│  │        │             │             │             │               │    │
│  │  ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐ ┌─────┴─────┐        │    │
│  │  │Resolution │ │ Mispricing│ │   News    │ │ Correlation│       │    │
│  │  │ Detector  │ │ Detector  │ │ Detector  │ │ Detector  │        │    │
│  │  └─────┬─────┘ └─────┬─────┘ └─────┬─────┘ └─────┬─────┘        │    │
│  └────────┴─────────────┴─────────────┴─────────────┴──────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                   OPPORTUNITY AGGREGATOR                         │    │
│  │  - Deduplica oportunidades (mismo mercado, diferente detector)  │    │
│  │  - Combina scores de múltiples señales                          │    │
│  │  - Elimina conflictos (no comprar YES y NO del mismo mercado)   │    │
│  └──────────────────────────────┬──────────────────────────────────┘    │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                   OPPORTUNITY RANKER                             │    │
│  │  - Calcula Expected Value = probability * profit - (1-p) * loss │    │
│  │  - Ordena por EV / tiempo_exposición                            │    │
│  │  - Filtra por umbrales mínimos                                  │    │
│  └──────────────────────────────┬──────────────────────────────────┘    │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     RISK MANAGER                                 │    │
│  │  - Límite de exposición por mercado                             │    │
│  │  - Límite de exposición total                                   │    │
│  │  - Diversificación (no >20% en un solo mercado)                 │    │
│  │  - Drawdown protection                                          │    │
│  └──────────────────────────────┬──────────────────────────────────┘    │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    TRADE EXECUTOR                                │    │
│  │  - Ejecuta trades (real o simulado)                             │    │
│  │  - Calcula tamaño de posición (Kelly Criterion)                 │    │
│  │  - Maneja errores y reintentos                                  │    │
│  └──────────────────────────────┬──────────────────────────────────┘    │
│                                 ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                   POSITION MANAGER                               │    │
│  │  - Monitorea posiciones abiertas                                │    │
│  │  - Ejecuta take profit / stop loss                              │    │
│  │  - Time-based exits                                             │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Archivos del Proyecto

```
polymarket-bot/
├── main.py                    # Entry point, orquestador principal
├── config.py                  # Configuración centralizada
├── opportunity_detector.py    # Clase principal que coordina detectores
├── detectors/                 # Módulos de detección
│   ├── __init__.py
│   ├── base_detector.py       # Clase base abstracta
│   ├── arbitrage_detector.py  # Arbitraje (cross-platform, multi-outcome)
│   ├── time_decay_detector.py # Theta decay, deadlines
│   ├── whale_detector.py      # Whale activity, smart money
│   ├── momentum_detector.py   # Price momentum, trends
│   ├── resolution_detector.py # Eventos resueltos, casi seguros
│   ├── mispricing_detector.py # Mercados mal priceados
│   ├── news_detector.py       # Event-driven, noticias
│   └── correlation_detector.py# Mercados correlacionados
├── ranker.py                  # Priorización de oportunidades
├── risk_manager.py            # Gestión de riesgo
├── trader.py                  # Ejecución de trades
├── position_manager.py        # Gestión de posiciones abiertas
├── api/                       # Clientes de APIs
│   ├── polymarket_api.py
│   ├── kalshi_api.py
│   └── news_api.py
├── models/                    # Dataclasses y tipos
│   ├── opportunity.py
│   ├── position.py
│   └── market.py
├── utils/                     # Utilidades
│   ├── logger.py
│   └── helpers.py
├── tests/                     # Tests unitarios
│   ├── test_arbitrage.py
│   ├── test_time_decay.py
│   └── ...
└── simulation_data.json       # Datos de simulación
```

---

## 2. Oportunidades a Detectar

### 2.1 ARBITRAJE (Prioridad 1 - Ganancia Garantizada)

#### 2.1.1 Multi-Outcome Arbitrage
**Descripción**: Cuando la suma de probabilidades de todos los outcomes > 100%, hay arbitraje.

**Ejemplo**:
```
Mercado: "¿Quién ganará las elecciones?"
- Candidato A: 45%
- Candidato B: 40%
- Candidato C: 20%
- SUMA: 105%

Estrategia: Comprar NO en todos = ganancia de 5%
```

**Detección**:
```python
def detect_multi_outcome_arbitrage(event):
    """
    Detecta arbitraje en eventos con múltiples outcomes.
    
    Args:
        event: Evento con múltiples mercados (outcomes)
    
    Returns:
        Opportunity si suma > 100%, None si no hay arbitraje
    """
    outcomes = event.get('markets', [])
    if len(outcomes) < 2:
        return None
    
    total_yes = sum(m.get('yes_price', 0) for m in outcomes)
    total_no = sum(m.get('no_price', 0) for m in outcomes)
    
    # Arbitraje en YES: si suma < 100%, comprar YES en todos
    if total_yes < 0.98:  # 2% margen mínimo
        profit_pct = (1.0 - total_yes) * 100
        return Opportunity(
            type='MULTI_OUTCOME_ARB',
            action='BUY_ALL_YES',
            expected_profit=profit_pct,
            confidence=99,  # Matemáticamente seguro
            markets=outcomes
        )
    
    # Arbitraje en NO: si suma > 100%, comprar NO en todos
    if total_yes > 1.02:  # suma de YES > 102%
        profit_pct = (total_yes - 1.0) * 100
        return Opportunity(
            type='MULTI_OUTCOME_ARB',
            action='BUY_ALL_NO',
            expected_profit=profit_pct,
            confidence=99,
            markets=outcomes
        )
    
    return None
```

**API Endpoint**: `GET /events` → cada evento tiene múltiples markets

#### 2.1.2 Cross-Platform Arbitrage
**Descripción**: Mismo mercado con precio diferente en Polymarket vs Kalshi.

**Ejemplo**:
```
Mercado: "Fed sube tasas en Marzo"
- Polymarket: YES = 85%
- Kalshi: YES = 78%
- Spread: 7%

Estrategia: Comprar YES en Kalshi, NO en Polymarket
```

**Detección**:
```python
def detect_cross_platform_arbitrage(pm_market, kalshi_markets):
    """
    Busca el mismo mercado en Kalshi y compara precios.
    """
    # Matching por similitud de texto
    best_match = find_best_kalshi_match(pm_market['question'], kalshi_markets)
    
    if not best_match or best_match['similarity'] < 0.7:
        return None
    
    pm_yes = pm_market['yes_price']
    k_yes = best_match['yes_price']
    spread = abs(pm_yes - k_yes)
    
    if spread < 0.03:  # Menos de 3% no vale la pena
        return None
    
    # Determinar dirección
    if pm_yes > k_yes:
        # PM más caro → comprar en Kalshi, vender en PM
        action = 'BUY_KALSHI_SELL_PM'
    else:
        # Kalshi más caro → comprar en PM, vender en Kalshi
        action = 'BUY_PM_SELL_KALSHI'
    
    return Opportunity(
        type='CROSS_PLATFORM_ARB',
        action=action,
        expected_profit=spread * 100,
        confidence=95,
        pm_market=pm_market,
        kalshi_market=best_match
    )
```

#### 2.1.3 YES/NO Mismatch
**Descripción**: En un solo mercado, YES + NO ≠ 100%.

**Ejemplo**:
```
Mercado: "Bitcoin > $100K"
- YES: 52%
- NO: 45%
- SUMA: 97%

Estrategia: Comprar ambos, ganancia garantizada de 3%
```

**Detección**:
```python
def detect_yes_no_mismatch(market):
    """
    Detecta cuando YES + NO != 100%
    """
    yes_price = market.get('yes_price', 0)
    no_price = market.get('no_price', 0)
    total = yes_price + no_price
    
    if total < 0.98:  # Suma < 98%
        profit = (1.0 - total) * 100
        return Opportunity(
            type='YES_NO_MISMATCH',
            action='BUY_BOTH',
            expected_profit=profit,
            confidence=99,
            market=market
        )
    
    if total > 1.02:  # Suma > 102%
        # Esto es raro pero posible con fees
        return None
    
    return None
```

---

### 2.2 TIME DECAY / THETA (Prioridad 2)

#### 2.2.1 Deadline Approaching
**Descripción**: Mercados con deadline cercano donde el evento NO ha ocurrido.

**Ejemplo**:
```
Mercado: "Government shutdown antes del 15 Feb"
Hoy: 10 Feb
Precio NO: 60%

Si no hay shutdown en 5 días → NO gana
Cada día sin shutdown = NO más probable
```

**Detección**:
```python
def detect_deadline_theta(market):
    """
    Detecta oportunidades de time decay en mercados con deadline.
    """
    end_date = parse_date(market.get('endDate'))
    if not end_date:
        return None
    
    days_left = (end_date - datetime.now()).days
    
    # Solo mercados con deadline < 14 días
    if days_left > 14 or days_left < 0:
        return None
    
    yes_price = market.get('yes_price', 0.5)
    question = market.get('question', '').lower()
    
    # Detectar mercados tipo "X antes de Y"
    is_before_market = any(w in question for w in ['before', 'by', 'antes de', 'prior to'])
    
    if not is_before_market:
        return None
    
    # Si el evento NO ha ocurrido y quedan pocos días...
    # Calcular theta (decay por día)
    if yes_price < 0.3:  # Evento improbable
        # NO está a 70%+, poco upside
        return None
    
    # Theta = cuánto debería bajar YES por día si no pasa nada
    # Fórmula simplificada: theta = yes_price / days_left
    daily_theta = yes_price / max(days_left, 1)
    
    if daily_theta < 0.02:  # Menos de 2%/día no vale
        return None
    
    return Opportunity(
        type='TIME_DECAY',
        action='BUY_NO',
        expected_profit=yes_price * 100,  # Si NO gana, profit = precio de YES
        confidence=70 + (14 - days_left) * 2,  # Más confianza si menos días
        market=market,
        days_left=days_left,
        daily_theta=daily_theta
    )
```

#### 2.2.2 Improbable Events Expiring
**Descripción**: Eventos muy improbables con YES > 3%.

**Ejemplo**:
```
Mercado: "Alien contact confirmado en Febrero"
YES: 4%
Días restantes: 24

Estrategia: Comprar NO a 96%, esperar a que expire
Profit: 4% en 24 días = 0.17%/día (61% anualizado)
```

**Detección**:
```python
def detect_improbable_expiring(market):
    """
    Detecta eventos muy improbables que expiran pronto.
    """
    yes_price = market.get('yes_price', 0)
    end_date = parse_date(market.get('endDate'))
    
    if not end_date:
        return None
    
    days_left = (end_date - datetime.now()).days
    
    # YES muy bajo (evento improbable) pero no cero
    if yes_price < 0.02 or yes_price > 0.15:
        return None
    
    # Expira en menos de 30 días
    if days_left > 30 or days_left < 1:
        return None
    
    # Verificar que no sea algo que pueda pasar
    question = market.get('question', '').lower()
    likely_impossible = any(w in question for w in [
        'alien', 'ufo', 'asteroid', 'apocalypse', 'end of world',
        'zombie', 'vampire', 'unicorn'
    ])
    
    daily_return = yes_price / days_left
    
    return Opportunity(
        type='IMPROBABLE_EXPIRING',
        action='BUY_NO',
        expected_profit=yes_price * 100,
        confidence=90 if likely_impossible else 75,
        market=market,
        days_left=days_left,
        daily_return=daily_return
    )
```

---

### 2.3 RESOLUTION PREDICTABLE (Prioridad 3)

#### 2.3.1 Event Already Occurred
**Descripción**: El evento ya ocurrió pero el mercado no se actualizó.

**Ejemplo**:
```
Mercado: "Eagles ganan vs Cowboys el 2 Feb"
Fecha: 4 Feb (juego ya ocurrió)
Resultado real: Eagles ganaron
Precio actual: YES = 85%

Estrategia: Comprar YES a 85%, esperar resolución → 100%
Profit: 15%
```

**Detección**:
```python
def detect_already_resolved(market, news_feed):
    """
    Detecta mercados cuyo evento ya ocurrió.
    
    Requiere:
    - Feed de noticias/resultados
    - Parsing de la pregunta para extraer evento
    """
    question = market.get('question', '')
    end_date = parse_date(market.get('endDate'))
    
    # Buscar en noticias si el evento ya pasó
    event_keywords = extract_event_keywords(question)
    recent_news = news_feed.search(event_keywords, days=3)
    
    for news in recent_news:
        # Verificar si la noticia confirma el resultado
        if news.confirms_outcome(question):
            outcome = news.get_outcome()  # 'YES' o 'NO'
            current_price = market.get(f'{outcome.lower()}_price', 0)
            
            if current_price < 0.95:  # No ha llegado a 95%
                profit = (1.0 - current_price) * 100
                return Opportunity(
                    type='ALREADY_RESOLVED',
                    action=f'BUY_{outcome}',
                    expected_profit=profit,
                    confidence=95,
                    market=market,
                    evidence=news
                )
    
    return None
```

#### 2.3.2 Near-Certain Outcomes
**Descripción**: Mercados donde el resultado es casi seguro pero el precio no refleja eso.

**Ejemplo**:
```
Mercado: "Habrá Super Bowl en 2026"
Precio YES: 92%

Es 100% seguro que habrá Super Bowl.
Profit: 8%
```

**Detección**:
```python
def detect_near_certain(market):
    """
    Detecta mercados con outcomes casi seguros.
    """
    question = market.get('question', '').lower()
    yes_price = market.get('yes_price', 0)
    
    # Patrones de eventos casi seguros
    certain_yes_patterns = [
        ('super bowl', 0.99),
        ('world series', 0.99),
        ('nba finals', 0.99),
        ('sun rise tomorrow', 0.9999),
        ('january have 31 days', 0.9999),
    ]
    
    certain_no_patterns = [
        ('alien contact', 0.01),
        ('world end', 0.01),
        ('moon explode', 0.001),
    ]
    
    for pattern, expected_prob in certain_yes_patterns:
        if pattern in question:
            if yes_price < expected_prob - 0.05:  # 5% margen
                profit = (expected_prob - yes_price) * 100
                return Opportunity(
                    type='NEAR_CERTAIN',
                    action='BUY_YES',
                    expected_profit=profit,
                    confidence=int(expected_prob * 100),
                    market=market
                )
    
    for pattern, expected_prob in certain_no_patterns:
        if pattern in question:
            if yes_price > expected_prob + 0.05:
                profit = (yes_price - expected_prob) * 100
                return Opportunity(
                    type='NEAR_CERTAIN',
                    action='BUY_NO',
                    expected_profit=profit,
                    confidence=int((1 - expected_prob) * 100),
                    market=market
                )
    
    return None
```

---

### 2.4 WHALE / SMART MONEY (Prioridad 4)

#### 2.4.1 Whale Activity Detection
**Descripción**: Detectar cuando wallets grandes compran/venden.

**Ejemplo**:
```
Wallet 0xABC... (histórico 85% WR) compró $50K en YES
Precio actual: 45%

Estrategia: Copiar al whale, comprar YES
```

**Detección**:
```python
def detect_whale_activity(market, blockchain_data):
    """
    Detecta actividad de wallets grandes.
    """
    recent_txs = blockchain_data.get_recent_transactions(
        market['condition_id'],
        hours=4
    )
    
    # Filtrar transacciones grandes (>$5K)
    whale_txs = [tx for tx in recent_txs if tx['amount_usd'] > 5000]
    
    if not whale_txs:
        return None
    
    # Analizar dirección del smart money
    total_yes_volume = sum(tx['amount'] for tx in whale_txs if tx['side'] == 'YES')
    total_no_volume = sum(tx['amount'] for tx in whale_txs if tx['side'] == 'NO')
    
    # Si hay consenso entre whales (>70% en una dirección)
    total = total_yes_volume + total_no_volume
    yes_ratio = total_yes_volume / total if total > 0 else 0.5
    
    if yes_ratio > 0.7:
        return Opportunity(
            type='WHALE_ACTIVITY',
            action='BUY_YES',
            expected_profit=15,  # Estimado
            confidence=int(yes_ratio * 100),
            market=market,
            whale_txs=whale_txs
        )
    elif yes_ratio < 0.3:
        return Opportunity(
            type='WHALE_ACTIVITY',
            action='BUY_NO',
            expected_profit=15,
            confidence=int((1 - yes_ratio) * 100),
            market=market,
            whale_txs=whale_txs
        )
    
    return None
```

#### 2.4.2 Abnormal Volume Detection
**Descripción**: Volumen anormalmente alto sin noticias = alguien sabe algo.

```python
def detect_abnormal_volume(market, historical_data):
    """
    Detecta volumen anormal que puede indicar insider activity.
    """
    current_volume = market.get('volume_1h', 0)
    avg_volume = historical_data.get_avg_hourly_volume(market['id'], days=7)
    
    if avg_volume == 0:
        return None
    
    volume_ratio = current_volume / avg_volume
    
    # Volumen 5x+ el promedio
    if volume_ratio < 5:
        return None
    
    # Verificar que no hay noticias (si hay noticias, es esperado)
    has_recent_news = news_api.has_news(market['question'], hours=2)
    
    if has_recent_news:
        return None
    
    # Determinar dirección por price change
    price_change = market.get('price_change_1h', 0)
    
    if price_change > 0.03:
        action = 'BUY_YES'
    elif price_change < -0.03:
        action = 'BUY_NO'
    else:
        return None  # Volumen alto pero sin dirección clara
    
    return Opportunity(
        type='ABNORMAL_VOLUME',
        action=action,
        expected_profit=10,
        confidence=70,
        market=market,
        volume_ratio=volume_ratio
    )
```

---

### 2.5 MOMENTUM (Prioridad 5)

#### 2.5.1 Price Momentum
**Descripción**: Precio moviéndose fuerte en una dirección.

```python
def detect_momentum(market):
    """
    Detecta momentum de precio.
    """
    change_1h = market.get('price_change_1h', 0)
    change_24h = market.get('price_change_24h', 0)
    
    # Momentum fuerte: >5% en 1h o >15% en 24h
    if abs(change_1h) > 0.05:
        # Momentum de corto plazo
        direction = 'YES' if change_1h > 0 else 'NO'
        return Opportunity(
            type='MOMENTUM_SHORT',
            action=f'BUY_{direction}',
            expected_profit=abs(change_1h) * 50,  # Esperar que continúe
            confidence=65,
            market=market
        )
    
    if abs(change_24h) > 0.15:
        # Momentum de largo plazo (más confiable)
        direction = 'YES' if change_24h > 0 else 'NO'
        return Opportunity(
            type='MOMENTUM_LONG',
            action=f'BUY_{direction}',
            expected_profit=abs(change_24h) * 30,
            confidence=70,
            market=market
        )
    
    return None
```

#### 2.5.2 Contrarian / Mean Reversion
**Descripción**: Precio cayó mucho sin razón → posible rebote.

```python
def detect_contrarian(market, news_feed):
    """
    Detecta oportunidades contrarian cuando hay pánico irracional.
    """
    change_1h = market.get('price_change_1h', 0)
    
    # Caída fuerte (>10% en 1h)
    if change_1h > -0.10:
        return None
    
    # Verificar si hay noticias que justifiquen
    has_bad_news = news_feed.has_negative_news(market['question'], hours=2)
    
    if has_bad_news:
        return None  # Caída justificada
    
    # Caída sin noticias = posible pánico irracional
    return Opportunity(
        type='CONTRARIAN',
        action='BUY_YES',  # Comprar el dip
        expected_profit=abs(change_1h) * 50,  # Esperar rebote parcial
        confidence=60,
        market=market
    )
```

---

### 2.6 MISPRICING (Prioridad 6)

#### 2.6.1 New Market Mispricing
**Descripción**: Mercados recién creados suelen estar mal priceados.

```python
def detect_new_market_mispricing(market):
    """
    Detecta mercados nuevos que pueden estar mal priceados.
    """
    created_at = parse_date(market.get('createdAt'))
    if not created_at:
        return None
    
    hours_since_creation = (datetime.now() - created_at).total_seconds() / 3600
    
    # Solo mercados de menos de 24h
    if hours_since_creation > 24:
        return None
    
    # Bajo volumen = precio no estabilizado
    volume = market.get('volume', 0)
    if volume > 50000:  # Ya tiene liquidez
        return None
    
    # Analizar si el precio tiene sentido
    fair_value = estimate_fair_value(market)  # Usar modelo propio
    current_price = market.get('yes_price', 0.5)
    
    mispricing = abs(fair_value - current_price)
    
    if mispricing > 0.10:  # >10% mispricing
        action = 'BUY_YES' if fair_value > current_price else 'BUY_NO'
        return Opportunity(
            type='NEW_MARKET_MISPRICING',
            action=action,
            expected_profit=mispricing * 100,
            confidence=60,
            market=market,
            fair_value=fair_value
        )
    
    return None
```

#### 2.6.2 Low Liquidity Mispricing
**Descripción**: Mercados con poca liquidez tienen precios menos eficientes.

```python
def detect_low_liquidity_mispricing(market):
    """
    Detecta mispricing en mercados de baja liquidez.
    
    Cuidado: difícil entrar/salir
    """
    volume_24h = market.get('volume_24h', 0)
    
    # Solo mercados de baja liquidez
    if volume_24h > 10000:
        return None
    
    # Pero con algo de actividad (no muertos)
    if volume_24h < 100:
        return None
    
    # Comparar con mercados similares
    similar_markets = find_similar_markets(market)
    avg_price = sum(m['yes_price'] for m in similar_markets) / len(similar_markets)
    
    current_price = market.get('yes_price', 0.5)
    mispricing = abs(avg_price - current_price)
    
    if mispricing > 0.15:  # >15% diferencia
        action = 'BUY_YES' if avg_price > current_price else 'BUY_NO'
        return Opportunity(
            type='LOW_LIQUIDITY_MISPRICING',
            action=action,
            expected_profit=mispricing * 100,
            confidence=55,  # Menor confianza por baja liquidez
            market=market
        )
    
    return None
```

---

### 2.7 NEWS / EVENT-DRIVEN (Prioridad 7)

#### 2.7.1 Pre-Event Positioning
**Descripción**: Posicionarse antes de eventos conocidos (earnings, meetings, etc).

```python
def detect_pre_event_opportunity(market, calendar):
    """
    Detecta oportunidades antes de eventos programados.
    """
    question = market.get('question', '').lower()
    
    # Buscar eventos relacionados en calendario
    related_events = calendar.find_related_events(question, days_ahead=7)
    
    for event in related_events:
        days_until = (event['date'] - datetime.now()).days
        
        if days_until < 1 or days_until > 7:
            continue
        
        # Antes de earnings: alta volatilidad esperada
        if 'earnings' in event['type']:
            # Verificar si el precio refleja la expectativa
            # ...
            pass
        
        # Antes de Fed meeting
        if 'fed' in event['type'] or 'fomc' in event['type']:
            return Opportunity(
                type='PRE_FED_MEETING',
                action='ANALYZE',  # Requiere análisis manual
                expected_profit=5,
                confidence=65,
                market=market,
                event=event
            )
    
    return None
```

#### 2.7.2 News Reaction Speed
**Descripción**: Reaccionar a noticias más rápido que el mercado.

```python
def detect_news_opportunity(market, news_feed):
    """
    Detecta noticias que aún no se reflejaron en el precio.
    """
    question = market.get('question', '')
    keywords = extract_keywords(question)
    
    # Buscar noticias muy recientes (<30 min)
    recent_news = news_feed.search(keywords, minutes=30)
    
    if not recent_news:
        return None
    
    for news in recent_news:
        sentiment = news.get_sentiment()  # POSITIVE, NEGATIVE, NEUTRAL
        impact = news.get_impact_score()  # 0-100
        
        if impact < 50:
            continue
        
        # Verificar si el precio ya se movió
        price_change_30m = market.get('price_change_30m', 0)
        
        if sentiment == 'POSITIVE' and price_change_30m < impact / 200:
            # Noticia positiva pero precio no subió suficiente
            return Opportunity(
                type='NEWS_LAG',
                action='BUY_YES',
                expected_profit=impact / 10,
                confidence=70,
                market=market,
                news=news
            )
        elif sentiment == 'NEGATIVE' and price_change_30m > -impact / 200:
            return Opportunity(
                type='NEWS_LAG',
                action='BUY_NO',
                expected_profit=impact / 10,
                confidence=70,
                market=market,
                news=news
            )
    
    return None
```

---

### 2.8 CORRELATION (Prioridad 8)

#### 2.8.1 Correlated Markets Divergence
**Descripción**: Mercados que deberían moverse juntos pero divergen.

```python
def detect_correlation_divergence(markets):
    """
    Detecta divergencias entre mercados correlacionados.
    """
    # Definir pares correlacionados
    correlation_pairs = [
        ('trump wins', 'republican wins', 0.95),
        ('bitcoin 100k', 'crypto bull market', 0.80),
        ('fed raises rates', 'inflation high', 0.70),
    ]
    
    opportunities = []
    
    for pattern1, pattern2, expected_corr in correlation_pairs:
        market1 = find_market_by_pattern(markets, pattern1)
        market2 = find_market_by_pattern(markets, pattern2)
        
        if not market1 or not market2:
            continue
        
        price1 = market1.get('yes_price', 0.5)
        price2 = market2.get('yes_price', 0.5)
        
        # Calcular divergencia
        expected_price2 = price1 * expected_corr  # Simplificado
        divergence = abs(price2 - expected_price2)
        
        if divergence > 0.10:  # >10% divergencia
            # El más barato debería subir
            if price2 < expected_price2:
                opportunities.append(Opportunity(
                    type='CORRELATION_DIVERGENCE',
                    action='BUY_YES',
                    expected_profit=divergence * 50,
                    confidence=65,
                    market=market2,
                    correlated_with=market1
                ))
    
    return opportunities
```

---

## 3. Implementación por Módulo

### 3.1 Base Detector (Clase Abstracta)

```python
# detectors/base_detector.py

from abc import ABC, abstractmethod
from typing import List, Optional
from models.opportunity import Opportunity
from models.market import Market

class BaseDetector(ABC):
    """Clase base para todos los detectores de oportunidades."""
    
    def __init__(self, config: dict):
        self.config = config
        self.name = self.__class__.__name__
    
    @abstractmethod
    def detect(self, markets: List[Market], **kwargs) -> List[Opportunity]:
        """
        Detecta oportunidades en una lista de mercados.
        
        Args:
            markets: Lista de mercados a analizar
            **kwargs: Datos adicionales (news, whale_data, etc)
        
        Returns:
            Lista de oportunidades detectadas
        """
        pass
    
    def filter_valid_markets(self, markets: List[Market]) -> List[Market]:
        """Filtra mercados inválidos (cerrados, sin precio, etc)."""
        return [
            m for m in markets
            if m.is_active and m.yes_price > 0 and m.yes_price < 1
        ]
    
    def log_detection(self, opportunity: Opportunity):
        """Log de oportunidad detectada."""
        print(f"[{self.name}] Found: {opportunity.type} | "
              f"Profit: {opportunity.expected_profit:.1f}% | "
              f"Conf: {opportunity.confidence}%")
```

### 3.2 Opportunity Model

```python
# models/opportunity.py

from dataclasses import dataclass, field
from typing import Optional, List, Any
from enum import Enum
from datetime import datetime

class OpportunityType(Enum):
    # Arbitraje
    MULTI_OUTCOME_ARB = "multi_outcome_arb"
    CROSS_PLATFORM_ARB = "cross_platform_arb"
    YES_NO_MISMATCH = "yes_no_mismatch"
    
    # Time Decay
    TIME_DECAY = "time_decay"
    IMPROBABLE_EXPIRING = "improbable_expiring"
    
    # Resolution
    ALREADY_RESOLVED = "already_resolved"
    NEAR_CERTAIN = "near_certain"
    
    # Whale
    WHALE_ACTIVITY = "whale_activity"
    ABNORMAL_VOLUME = "abnormal_volume"
    
    # Momentum
    MOMENTUM_SHORT = "momentum_short"
    MOMENTUM_LONG = "momentum_long"
    CONTRARIAN = "contrarian"
    
    # Mispricing
    NEW_MARKET_MISPRICING = "new_market_mispricing"
    LOW_LIQUIDITY_MISPRICING = "low_liquidity_mispricing"
    
    # News
    NEWS_LAG = "news_lag"
    PRE_EVENT = "pre_event"
    
    # Correlation
    CORRELATION_DIVERGENCE = "correlation_divergence"

class Action(Enum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"
    BUY_BOTH = "buy_both"
    BUY_ALL_YES = "buy_all_yes"
    BUY_ALL_NO = "buy_all_no"
    SELL_YES = "sell_yes"
    SELL_NO = "sell_no"

@dataclass
class Opportunity:
    """Representa una oportunidad de trading detectada."""
    
    type: OpportunityType
    action: Action
    expected_profit: float  # Porcentaje esperado
    confidence: int  # 0-100
    
    # Market info
    market_id: str = ""
    market_question: str = ""
    current_price: float = 0.0
    
    # Metadata
    detected_at: datetime = field(default_factory=datetime.now)
    detector_name: str = ""
    
    # Additional data
    extra_data: dict = field(default_factory=dict)
    
    @property
    def expected_value(self) -> float:
        """Calcula el Expected Value."""
        prob_win = self.confidence / 100
        return (prob_win * self.expected_profit) - ((1 - prob_win) * 100)
    
    @property
    def risk_reward_ratio(self) -> float:
        """Ratio riesgo/recompensa."""
        if self.expected_profit == 0:
            return 0
        return self.expected_profit / (100 - self.confidence)
    
    def __repr__(self):
        return (f"Opportunity({self.type.value}, {self.action.value}, "
                f"profit={self.expected_profit:.1f}%, conf={self.confidence}%)")
```

### 3.3 Opportunity Ranker

```python
# ranker.py

from typing import List
from models.opportunity import Opportunity, OpportunityType

# Prioridad base por tipo (mayor = mejor)
TYPE_PRIORITY = {
    # Arbitraje = máxima prioridad (ganancia garantizada)
    OpportunityType.MULTI_OUTCOME_ARB: 100,
    OpportunityType.YES_NO_MISMATCH: 95,
    OpportunityType.CROSS_PLATFORM_ARB: 90,
    
    # Resolution predecible
    OpportunityType.ALREADY_RESOLVED: 85,
    OpportunityType.NEAR_CERTAIN: 80,
    
    # Time decay
    OpportunityType.TIME_DECAY: 75,
    OpportunityType.IMPROBABLE_EXPIRING: 70,
    
    # Whale/Smart money
    OpportunityType.WHALE_ACTIVITY: 65,
    OpportunityType.ABNORMAL_VOLUME: 60,
    
    # Momentum
    OpportunityType.MOMENTUM_LONG: 55,
    OpportunityType.MOMENTUM_SHORT: 50,
    OpportunityType.CONTRARIAN: 45,
    
    # Mispricing
    OpportunityType.NEW_MARKET_MISPRICING: 40,
    OpportunityType.LOW_LIQUIDITY_MISPRICING: 35,
    
    # News/Event
    OpportunityType.NEWS_LAG: 30,
    OpportunityType.PRE_EVENT: 25,
    
    # Correlation
    OpportunityType.CORRELATION_DIVERGENCE: 20,
}

class OpportunityRanker:
    """Ordena oportunidades por expected value y prioridad."""
    
    def __init__(self, config: dict):
        self.min_confidence = config.get('min_confidence', 60)
        self.min_profit = config.get('min_profit', 3.0)
    
    def rank(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        """
        Ordena oportunidades de mejor a peor.
        
        Criterios:
        1. Filtrar por umbrales mínimos
        2. Calcular score compuesto
        3. Ordenar por score
        """
        # Filtrar
        valid = [
            o for o in opportunities
            if o.confidence >= self.min_confidence
            and o.expected_profit >= self.min_profit
        ]
        
        # Calcular score
        for opp in valid:
            opp.rank_score = self._calculate_score(opp)
        
        # Ordenar
        valid.sort(key=lambda x: x.rank_score, reverse=True)
        
        return valid
    
    def _calculate_score(self, opp: Opportunity) -> float:
        """
        Calcula score compuesto para ranking.
        
        Score = (type_priority * 0.3) + (expected_value * 0.4) + (confidence * 0.3)
        """
        type_priority = TYPE_PRIORITY.get(opp.type, 10) / 100
        ev_normalized = min(opp.expected_value / 50, 1.0)  # Normalizar a 0-1
        conf_normalized = opp.confidence / 100
        
        score = (
            type_priority * 0.3 +
            ev_normalized * 0.4 +
            conf_normalized * 0.3
        )
        
        return score * 100
```

---

## 4. Sistema de Priorización

### 4.1 Orden de Ejecución

Cuando hay múltiples oportunidades:

```
1. ARBITRAJE (SIEMPRE PRIMERO)
   - Multi-outcome: suma != 100%
   - Cross-platform: spread > 3%
   - YES/NO mismatch
   
   → Ejecutar TODAS las de arbitraje (ganancia segura)

2. ALTA CONFIANZA (>85%)
   - Already resolved
   - Near certain
   - Time decay <3 días
   
   → Ejecutar hasta límite de exposición

3. SMART MONEY (>75% confianza)
   - Whale activity consenso
   - Abnormal volume
   
   → Ejecutar con tamaño reducido

4. MOMENTUM (>65% confianza)
   - Momentum confirmado
   - Contrarian con evidencia
   
   → Ejecutar con stop loss tight

5. ESPECULATIVO (<65% confianza)
   - Mispricing estimado
   - News lag
   - Correlation
   
   → Solo si hay capital disponible
```

### 4.2 Conflictos

```python
def resolve_conflicts(opportunities: List[Opportunity]) -> List[Opportunity]:
    """
    Resuelve conflictos entre oportunidades.
    
    Conflictos posibles:
    - Mismo mercado, diferentes acciones (BUY_YES vs BUY_NO)
    - Mercados correlacionados con acciones opuestas
    """
    # Agrupar por mercado
    by_market = defaultdict(list)
    for opp in opportunities:
        by_market[opp.market_id].append(opp)
    
    resolved = []
    
    for market_id, opps in by_market.items():
        if len(opps) == 1:
            resolved.append(opps[0])
            continue
        
        # Múltiples señales para mismo mercado
        yes_signals = [o for o in opps if 'YES' in o.action.value.upper()]
        no_signals = [o for o in opps if 'NO' in o.action.value.upper()]
        
        if yes_signals and no_signals:
            # Conflicto: elegir el de mayor confianza
            all_signals = yes_signals + no_signals
            best = max(all_signals, key=lambda x: x.confidence)
            resolved.append(best)
        else:
            # Sin conflicto: agregar todos (se combinan las señales)
            # Tomar el de mayor EV
            best = max(opps, key=lambda x: x.expected_value)
            # Boost de confianza por múltiples señales
            best.confidence = min(95, best.confidence + len(opps) * 5)
            resolved.append(best)
    
    return resolved
```

---

## 5. Testing Strategy

### 5.1 Unit Tests por Detector

Cada detector tiene tests individuales:

```python
# tests/test_arbitrage.py

import pytest
from detectors.arbitrage_detector import ArbitrageDetector

class TestMultiOutcomeArbitrage:
    
    def test_detects_overpriced_outcomes(self):
        """Suma > 100% debe detectar arbitraje."""
        detector = ArbitrageDetector({})
        
        mock_event = {
            'markets': [
                {'yes_price': 0.45},
                {'yes_price': 0.40},
                {'yes_price': 0.20},
            ]
        }
        # Suma = 105%
        
        result = detector.detect_multi_outcome([mock_event])
        
        assert len(result) == 1
        assert result[0].type.value == 'multi_outcome_arb'
        assert result[0].expected_profit == pytest.approx(5.0, rel=0.1)
    
    def test_ignores_fair_outcomes(self):
        """Suma = 100% no debe detectar arbitraje."""
        detector = ArbitrageDetector({})
        
        mock_event = {
            'markets': [
                {'yes_price': 0.50},
                {'yes_price': 0.30},
                {'yes_price': 0.20},
            ]
        }
        # Suma = 100%
        
        result = detector.detect_multi_outcome([mock_event])
        
        assert len(result) == 0
```

### 5.2 Mock Data Generator

```python
# tests/mock_data.py

import random
from datetime import datetime, timedelta

def generate_mock_market(
    yes_price: float = None,
    days_to_resolution: int = None,
    volume_24h: float = None,
    price_change_1h: float = None,
):
    """Genera un mercado mock para testing."""
    
    if yes_price is None:
        yes_price = random.uniform(0.05, 0.95)
    
    if days_to_resolution is None:
        days_to_resolution = random.randint(1, 60)
    
    if volume_24h is None:
        volume_24h = random.uniform(1000, 1000000)
    
    if price_change_1h is None:
        price_change_1h = random.uniform(-0.15, 0.15)
    
    end_date = datetime.now() + timedelta(days=days_to_resolution)
    
    return {
        'id': f'mock_{random.randint(1000, 9999)}',
        'question': f'Mock market question {random.randint(1, 100)}',
        'yes_price': yes_price,
        'no_price': 1 - yes_price,
        'endDate': end_date.isoformat(),
        'volume24hr': volume_24h,
        'price_change_1h': price_change_1h,
        'price_change_24h': price_change_1h * random.uniform(1, 5),
        'category': random.choice(['politics', 'crypto', 'sports', 'science']),
    }

def generate_arbitrage_scenario():
    """Genera un escenario con arbitraje garantizado."""
    # Multi-outcome con suma > 100%
    prices = [0.35, 0.40, 0.30]  # Suma = 105%
    return {
        'id': 'arb_event_1',
        'markets': [
            {'yes_price': p, 'no_price': 1-p, 'question': f'Option {i}'}
            for i, p in enumerate(prices)
        ]
    }

def generate_time_decay_scenario():
    """Genera un escenario de time decay."""
    return generate_mock_market(
        yes_price=0.25,  # Evento improbable
        days_to_resolution=3,  # Muy cerca
        volume_24h=50000,
    )
```

### 5.3 Integration Test

```python
# tests/test_integration.py

from opportunity_detector import OpportunityDetector
from ranker import OpportunityRanker
from tests.mock_data import *

def test_full_pipeline():
    """Test completo del pipeline."""
    
    # 1. Generar datos mock
    markets = [generate_mock_market() for _ in range(50)]
    arb_events = [generate_arbitrage_scenario()]
    
    # 2. Detectar oportunidades
    detector = OpportunityDetector()
    opportunities = detector.scan_all(
        markets=markets,
        events=arb_events
    )
    
    # 3. Rankear
    ranker = OpportunityRanker({'min_confidence': 50})
    ranked = ranker.rank(opportunities)
    
    # 4. Verificar
    assert len(ranked) > 0
    
    # El arbitraje debe estar primero
    if any(o.type.value.endswith('_arb') for o in ranked):
        assert ranked[0].type.value.endswith('_arb')
    
    # Debe estar ordenado por score
    scores = [o.rank_score for o in ranked]
    assert scores == sorted(scores, reverse=True)
```

---

## 6. To-Do List

### Fase 1: Estructura Base
- [ ] 1.1 Crear estructura de carpetas
- [ ] 1.2 Implementar models (Opportunity, Market, Position)
- [ ] 1.3 Implementar BaseDetector
- [ ] 1.4 Implementar OpportunityRanker
- [ ] 1.5 Crear mock data generator

### Fase 2: Detectores de Arbitraje (Prioridad Alta)
- [ ] 2.1 Multi-Outcome Arbitrage Detector
- [ ] 2.2 Cross-Platform Arbitrage Detector (Kalshi)
- [ ] 2.3 YES/NO Mismatch Detector
- [ ] 2.4 Tests para arbitraje

### Fase 3: Detectores de Time Decay
- [ ] 3.1 Deadline Approaching Detector
- [ ] 3.2 Improbable Events Detector
- [ ] 3.3 Tests para time decay

### Fase 4: Detectores de Resolution
- [ ] 4.1 Already Resolved Detector
- [ ] 4.2 Near Certain Outcomes Detector
- [ ] 4.3 Tests para resolution

### Fase 5: Detectores de Smart Money
- [ ] 5.1 Whale Activity Detector
- [ ] 5.2 Abnormal Volume Detector
- [ ] 5.3 Tests para smart money

### Fase 6: Detectores de Momentum
- [ ] 6.1 Price Momentum Detector
- [ ] 6.2 Contrarian Detector
- [ ] 6.3 Tests para momentum

### Fase 7: Detectores de Mispricing
- [ ] 7.1 New Market Mispricing Detector
- [ ] 7.2 Low Liquidity Mispricing Detector
- [ ] 7.3 Tests para mispricing

### Fase 8: Detectores de News/Correlation
- [ ] 8.1 News Lag Detector
- [ ] 8.2 Pre-Event Detector
- [ ] 8.3 Correlation Divergence Detector
- [ ] 8.4 Tests para news/correlation

### Fase 9: Integración
- [ ] 9.1 Opportunity Aggregator (deduplicación)
- [ ] 9.2 Conflict Resolution
- [ ] 9.3 Integration con Risk Manager existente
- [ ] 9.4 Integration con Trade Executor existente

### Fase 10: Testing Final
- [ ] 10.1 Unit tests completos
- [ ] 10.2 Integration tests
- [ ] 10.3 Backtest con datos históricos
- [ ] 10.4 Paper trading por 24h
- [ ] 10.5 Análisis de resultados

---

## Métricas de Éxito

| Métrica | Objetivo | Mínimo Aceptable |
|---------|----------|------------------|
| Win Rate | >85% | >75% |
| Daily ROI | >20% | >10% |
| Max Drawdown | <10% | <20% |
| Trades/día | >30 | >15 |
| Sharpe Ratio | >2.0 | >1.0 |

---

## Notas Adicionales

1. **APIs Necesarias**:
   - Polymarket Gamma API (ya tenemos)
   - Kalshi API (ya tenemos)
   - News API (NewsAPI.org o similar)
   - Blockchain data (Polygonscan o Alchemy)

2. **Rate Limits**:
   - Polymarket: ~100 req/min
   - Kalshi: ~60 req/min
   - Implementar caching agresivo

3. **Monitoreo**:
   - Dashboard en tiempo real
   - Alertas por WhatsApp (ya tenemos)
   - Logs detallados por detector

