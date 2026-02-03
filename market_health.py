"""
Market Health Monitor - Detecta deterioro de oportunidades de trading.

Este módulo trackea métricas clave y ajusta dinámicamente los parámetros
del bot según la "salud" del mercado. La idea es detectar ANTES de que
sea demasiado tarde cuando la oportunidad se está agotando.

Métricas trackeadas:
1. ROI promedio por trade (¿siguen siendo rentables?)
2. Oportunidades por día (¿hay menos para elegir?)
3. Win rate (¿la estrategia sigue funcionando?)
4. Spread promedio (¿el mercado se volvió más eficiente?)
5. Slippage (¿hay más competencia?)

Estados de salud:
- HEALTHY (80-100): Operar normal
- CAUTION (60-79): Reducir exposición, ser más selectivo
- WARNING (40-59): Exposición mínima, solo mejores oportunidades
- CRITICAL (0-39): Pausar trading, la oportunidad se agotó
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from pathlib import Path
from enum import Enum
import statistics

from logger import log


class HealthStatus(Enum):
    """Estados de salud del mercado."""
    HEALTHY = "HEALTHY"      # 80-100
    CAUTION = "CAUTION"      # 60-79  
    WARNING = "WARNING"      # 40-59
    CRITICAL = "CRITICAL"    # 0-39


@dataclass
class HealthMetrics:
    """Métricas de salud calculadas."""
    # Métricas principales
    avg_roi_pct: float = 0.0           # ROI promedio por trade
    opportunities_per_day: float = 0.0  # Oportunidades detectadas por día
    win_rate: float = 0.0               # Porcentaje de trades ganadores
    avg_spread: float = 0.0             # Spread promedio disponible
    avg_hold_hours: float = 0.0         # Horas promedio de hold
    
    # Métricas de tendencia (comparación con período anterior)
    roi_trend: float = 0.0              # Cambio % en ROI vs período anterior
    opportunities_trend: float = 0.0    # Cambio % en oportunidades
    win_rate_trend: float = 0.0         # Cambio % en win rate
    
    # Score final
    health_score: int = 100
    status: str = "HEALTHY"
    
    # Metadata
    period_days: int = 7
    trades_analyzed: int = 0
    last_updated: str = ""


@dataclass 
class TradingAdjustments:
    """Ajustes al comportamiento del bot según salud del mercado."""
    position_size_multiplier: float = 1.0   # Multiplicador de position size
    min_score_threshold: int = 85           # Score mínimo para tradear
    max_concurrent_trades: int = 10         # Máximo de trades abiertos
    take_profit_multiplier: float = 1.0     # Multiplicador de take profit (< 1 = más agresivo)
    stop_loss_multiplier: float = 1.0       # Multiplicador de stop loss
    is_trading_allowed: bool = True         # Si se permite tradear
    reason: str = ""                        # Razón del estado actual


@dataclass
class HealthDataPoint:
    """Un punto de datos histórico para análisis de tendencias."""
    date: str
    avg_roi_pct: float
    opportunities_count: int
    win_rate: float
    avg_spread: float
    trades_count: int


class MarketHealthMonitor:
    """
    Monitor de salud del mercado que ajusta dinámicamente el comportamiento del bot.
    """
    
    # Pesos para calcular el health score
    METRIC_WEIGHTS = {
        'roi': 0.30,           # ROI es muy importante
        'win_rate': 0.25,      # Win rate indica si la estrategia funciona
        'opportunities': 0.20,  # Cantidad de oportunidades
        'trends': 0.25,        # Tendencias (deterioro)
    }
    
    # Umbrales para cada métrica (qué se considera "bueno")
    THRESHOLDS = {
        'min_roi_pct': 5.0,           # Mínimo 5% ROI promedio
        'healthy_roi_pct': 15.0,      # ROI saludable
        'min_win_rate': 0.45,         # Mínimo 45% win rate
        'healthy_win_rate': 0.60,     # Win rate saludable
        'min_opportunities': 3,        # Mínimo 3 oportunidades/día
        'healthy_opportunities': 10,   # Oportunidades saludables
        'max_decline_pct': -0.20,     # Máximo declive aceptable en tendencias
    }
    
    # Ajustes por estado de salud - OPTIMIZED FOR HIGH FREQUENCY
    ADJUSTMENTS_BY_STATUS = {
        HealthStatus.HEALTHY: TradingAdjustments(
            position_size_multiplier=1.0,
            min_score_threshold=35,  # VERY LOW for high frequency
            max_concurrent_trades=50,  # Many positions
            take_profit_multiplier=1.0,
            stop_loss_multiplier=1.0,
            is_trading_allowed=True,
            reason="Alta frecuencia activa"
        ),
        HealthStatus.CAUTION: TradingAdjustments(
            position_size_multiplier=1.0,  # Keep same size
            min_score_threshold=40,
            max_concurrent_trades=40,
            take_profit_multiplier=1.0,
            stop_loss_multiplier=1.0,
            is_trading_allowed=True,
            reason="Caution pero seguimos"
        ),
        HealthStatus.WARNING: TradingAdjustments(
            position_size_multiplier=0.8,
            min_score_threshold=45,
            max_concurrent_trades=30,
            take_profit_multiplier=0.9,
            stop_loss_multiplier=0.95,
            is_trading_allowed=True,
            reason="Warning - reduciendo un poco"
        ),
        HealthStatus.CRITICAL: TradingAdjustments(
            position_size_multiplier=0.0,
            min_score_threshold=100,  # Imposible alcanzar
            max_concurrent_trades=0,
            take_profit_multiplier=0.5,
            stop_loss_multiplier=0.7,
            is_trading_allowed=False,
            reason="Oportunidad agotada - trading pausado"
        ),
    }
    
    def __init__(self, data_file: str = "market_health_data.json"):
        self.data_file = Path(data_file)
        self.history: List[HealthDataPoint] = []
        self.current_metrics: Optional[HealthMetrics] = None
        self.current_adjustments: TradingAdjustments = self.ADJUSTMENTS_BY_STATUS[HealthStatus.HEALTHY]
        self._load_history()
    
    def _load_history(self):
        """Cargar historial de métricas."""
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r') as f:
                    data = json.load(f)
                    self.history = [
                        HealthDataPoint(**dp) for dp in data.get('history', [])
                    ]
                    if data.get('current_metrics'):
                        self.current_metrics = HealthMetrics(**data['current_metrics'])
                    log.info(f"[HEALTH] Loaded {len(self.history)} historical data points")
            except Exception as e:
                log.error(f"[HEALTH] Error loading history: {e}")
    
    def _save_history(self):
        """Guardar historial de métricas."""
        try:
            data = {
                'history': [asdict(dp) for dp in self.history],
                'current_metrics': asdict(self.current_metrics) if self.current_metrics else None,
                'last_updated': datetime.now().isoformat(),
            }
            with open(self.data_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error(f"[HEALTH] Error saving history: {e}")
    
    def calculate_metrics(self, trades: List[Dict], opportunities_log: List[Dict] = None) -> HealthMetrics:
        """
        Calcular métricas de salud basadas en trades recientes.
        
        Args:
            trades: Lista de trades con campos: pnl_pct, status, timestamp, spread, etc.
            opportunities_log: Lista de oportunidades detectadas (opcional)
        """
        # If no trades yet, return HEALTHY to allow trading to start
        if not trades or len(trades) < 3:
            metrics = HealthMetrics(
                avg_roi_pct=0,
                opportunities_per_day=10,  # Assume good
                win_rate=0.5,  # Neutral
                avg_spread=0,
                avg_hold_hours=24,
                roi_trend=0,
                opportunities_trend=0,
                win_rate_trend=0,
                health_score=85,  # Start healthy to allow trading
                status="HEALTHY",
                period_days=7,
                trades_analyzed=len(trades),
                last_updated=datetime.now().isoformat()
            )
            self.current_metrics = metrics
            self.current_adjustments = self.ADJUSTMENTS_BY_STATUS[HealthStatus.HEALTHY]
            return metrics
        
        now = datetime.now()
        week_ago = now - timedelta(days=7)
        two_weeks_ago = now - timedelta(days=14)
        
        # Filtrar trades de la última semana
        recent_trades = []
        previous_trades = []
        
        for trade in trades:
            try:
                ts = trade.get('timestamp', '')
                if isinstance(ts, str):
                    trade_time = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if trade_time.tzinfo:
                        trade_time = trade_time.replace(tzinfo=None)
                else:
                    continue
                
                if trade_time >= week_ago:
                    recent_trades.append(trade)
                elif trade_time >= two_weeks_ago:
                    previous_trades.append(trade)
            except:
                continue
        
        # === Calcular métricas del período actual ===
        
        # ROI promedio
        closed_trades = [t for t in recent_trades if t.get('status') in ['EXITED', 'RESOLVED', 'CLOSED']]
        if closed_trades:
            roi_values = [t.get('pnl_pct', 0) for t in closed_trades]
            avg_roi = statistics.mean(roi_values) if roi_values else 0
        else:
            avg_roi = 0
        
        # Win rate
        if closed_trades:
            wins = sum(1 for t in closed_trades if t.get('pnl_pct', 0) > 0)
            win_rate = wins / len(closed_trades)
        else:
            win_rate = 0.5  # Neutral si no hay datos
        
        # Oportunidades por día (estimado desde trades si no hay log)
        if opportunities_log:
            recent_opps = [o for o in opportunities_log 
                         if datetime.fromisoformat(o.get('timestamp', '').replace('Z', '')) >= week_ago]
            opportunities_per_day = len(recent_opps) / 7
        else:
            # Estimamos desde trades (trades = oportunidades tomadas)
            opportunities_per_day = len(recent_trades) / 7
        
        # Spread promedio
        spreads = [t.get('spread', 0) for t in recent_trades if t.get('spread', 0) > 0]
        avg_spread = statistics.mean(spreads) if spreads else 0
        
        # Hold time promedio
        hold_times = []
        for trade in closed_trades:
            if trade.get('timestamp') and trade.get('exit_time'):
                try:
                    entry = datetime.fromisoformat(trade['timestamp'].replace('Z', '+00:00'))
                    exit_t = datetime.fromisoformat(trade['exit_time'].replace('Z', '+00:00'))
                    hold_hours = (exit_t - entry).total_seconds() / 3600
                    hold_times.append(hold_hours)
                except:
                    pass
        avg_hold_hours = statistics.mean(hold_times) if hold_times else 24
        
        # === Calcular tendencias (comparación con período anterior) ===
        
        # ROI trend
        if previous_trades:
            prev_closed = [t for t in previous_trades if t.get('status') in ['EXITED', 'RESOLVED', 'CLOSED']]
            if prev_closed:
                prev_roi = statistics.mean([t.get('pnl_pct', 0) for t in prev_closed])
                roi_trend = (avg_roi - prev_roi) / abs(prev_roi) if prev_roi != 0 else 0
            else:
                roi_trend = 0
        else:
            roi_trend = 0
        
        # Opportunities trend
        prev_opp_per_day = len(previous_trades) / 7 if previous_trades else opportunities_per_day
        if prev_opp_per_day > 0:
            opportunities_trend = (opportunities_per_day - prev_opp_per_day) / prev_opp_per_day
        else:
            opportunities_trend = 0
        
        # Win rate trend  
        if previous_trades:
            prev_closed = [t for t in previous_trades if t.get('status') in ['EXITED', 'RESOLVED', 'CLOSED']]
            if prev_closed:
                prev_wins = sum(1 for t in prev_closed if t.get('pnl_pct', 0) > 0)
                prev_win_rate = prev_wins / len(prev_closed)
                win_rate_trend = (win_rate - prev_win_rate) / prev_win_rate if prev_win_rate > 0 else 0
            else:
                win_rate_trend = 0
        else:
            win_rate_trend = 0
        
        # === Calcular health score ===
        health_score = self._calculate_health_score(
            avg_roi=avg_roi,
            win_rate=win_rate,
            opportunities_per_day=opportunities_per_day,
            roi_trend=roi_trend,
            opportunities_trend=opportunities_trend,
            win_rate_trend=win_rate_trend
        )
        
        # Determinar status
        if health_score >= 80:
            status = HealthStatus.HEALTHY
        elif health_score >= 60:
            status = HealthStatus.CAUTION
        elif health_score >= 40:
            status = HealthStatus.WARNING
        else:
            status = HealthStatus.CRITICAL
        
        metrics = HealthMetrics(
            avg_roi_pct=round(avg_roi, 2),
            opportunities_per_day=round(opportunities_per_day, 1),
            win_rate=round(win_rate, 3),
            avg_spread=round(avg_spread, 4),
            avg_hold_hours=round(avg_hold_hours, 1),
            roi_trend=round(roi_trend, 3),
            opportunities_trend=round(opportunities_trend, 3),
            win_rate_trend=round(win_rate_trend, 3),
            health_score=health_score,
            status=status.value,
            period_days=7,
            trades_analyzed=len(recent_trades),
            last_updated=datetime.now().isoformat()
        )
        
        self.current_metrics = metrics
        self.current_adjustments = self.ADJUSTMENTS_BY_STATUS[status]
        
        # Guardar punto de datos histórico
        self._record_data_point(metrics)
        self._save_history()
        
        return metrics
    
    def _calculate_health_score(
        self,
        avg_roi: float,
        win_rate: float,
        opportunities_per_day: float,
        roi_trend: float,
        opportunities_trend: float,
        win_rate_trend: float
    ) -> int:
        """Calcular score de salud 0-100."""
        
        # Score de ROI (0-100)
        if avg_roi >= self.THRESHOLDS['healthy_roi_pct']:
            roi_score = 100
        elif avg_roi >= self.THRESHOLDS['min_roi_pct']:
            roi_score = 50 + 50 * (avg_roi - self.THRESHOLDS['min_roi_pct']) / \
                       (self.THRESHOLDS['healthy_roi_pct'] - self.THRESHOLDS['min_roi_pct'])
        elif avg_roi > 0:
            roi_score = 50 * avg_roi / self.THRESHOLDS['min_roi_pct']
        else:
            roi_score = max(0, 25 + avg_roi)  # Negativo reduce score
        
        # Score de win rate (0-100)
        if win_rate >= self.THRESHOLDS['healthy_win_rate']:
            wr_score = 100
        elif win_rate >= self.THRESHOLDS['min_win_rate']:
            wr_score = 50 + 50 * (win_rate - self.THRESHOLDS['min_win_rate']) / \
                      (self.THRESHOLDS['healthy_win_rate'] - self.THRESHOLDS['min_win_rate'])
        else:
            wr_score = 100 * win_rate / self.THRESHOLDS['min_win_rate']
        
        # Score de oportunidades (0-100)
        if opportunities_per_day >= self.THRESHOLDS['healthy_opportunities']:
            opp_score = 100
        elif opportunities_per_day >= self.THRESHOLDS['min_opportunities']:
            opp_score = 50 + 50 * (opportunities_per_day - self.THRESHOLDS['min_opportunities']) / \
                       (self.THRESHOLDS['healthy_opportunities'] - self.THRESHOLDS['min_opportunities'])
        else:
            opp_score = 100 * opportunities_per_day / self.THRESHOLDS['min_opportunities']
        
        # Score de tendencias (0-100) - penaliza deterioro
        trend_scores = []
        for trend in [roi_trend, opportunities_trend, win_rate_trend]:
            if trend >= 0:
                trend_scores.append(100)  # Mejorando o estable
            elif trend >= self.THRESHOLDS['max_decline_pct']:
                # Deterioro leve
                trend_scores.append(50 + 50 * (1 + trend / abs(self.THRESHOLDS['max_decline_pct'])))
            else:
                # Deterioro severo
                trend_scores.append(max(0, 50 * (1 + trend)))
        
        trend_score = statistics.mean(trend_scores)
        
        # Weighted average
        final_score = (
            roi_score * self.METRIC_WEIGHTS['roi'] +
            wr_score * self.METRIC_WEIGHTS['win_rate'] +
            opp_score * self.METRIC_WEIGHTS['opportunities'] +
            trend_score * self.METRIC_WEIGHTS['trends']
        )
        
        return int(max(0, min(100, final_score)))
    
    def _record_data_point(self, metrics: HealthMetrics):
        """Guardar punto de datos para análisis histórico."""
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Evitar duplicados del mismo día
        self.history = [dp for dp in self.history if dp.date != today]
        
        self.history.append(HealthDataPoint(
            date=today,
            avg_roi_pct=metrics.avg_roi_pct,
            opportunities_count=int(metrics.opportunities_per_day * 7),
            win_rate=metrics.win_rate,
            avg_spread=metrics.avg_spread,
            trades_count=metrics.trades_analyzed
        ))
        
        # Mantener solo últimos 90 días
        self.history = self.history[-90:]
    
    def get_adjustments(self) -> TradingAdjustments:
        """Obtener ajustes actuales para el bot."""
        return self.current_adjustments
    
    def get_status_emoji(self) -> str:
        """Obtener emoji del estado actual."""
        if not self.current_metrics:
            return "⚪"
        
        status_map = {
            "HEALTHY": "🟢",
            "CAUTION": "🟡", 
            "WARNING": "🟠",
            "CRITICAL": "🔴"
        }
        return status_map.get(self.current_metrics.status, "⚪")
    
    def get_dashboard(self) -> Dict[str, Any]:
        """Obtener dashboard completo de salud."""
        if not self.current_metrics:
            return {"status": "NO_DATA", "message": "Sin datos suficientes"}
        
        m = self.current_metrics
        adj = self.current_adjustments
        
        return {
            "status": m.status,
            "emoji": self.get_status_emoji(),
            "health_score": m.health_score,
            "metrics": {
                "roi_promedio": f"{m.avg_roi_pct:+.1f}%",
                "win_rate": f"{m.win_rate*100:.1f}%",
                "oportunidades_dia": f"{m.opportunities_per_day:.1f}",
                "spread_promedio": f"{m.avg_spread*100:.2f}%",
                "hold_promedio": f"{m.avg_hold_hours:.1f}h",
            },
            "trends": {
                "roi": f"{m.roi_trend*100:+.1f}%" if m.roi_trend else "N/A",
                "oportunidades": f"{m.opportunities_trend*100:+.1f}%" if m.opportunities_trend else "N/A",
                "win_rate": f"{m.win_rate_trend*100:+.1f}%" if m.win_rate_trend else "N/A",
            },
            "adjustments": {
                "position_size": f"{adj.position_size_multiplier*100:.0f}%",
                "score_minimo": adj.min_score_threshold,
                "max_trades": adj.max_concurrent_trades,
                "take_profit": f"{adj.take_profit_multiplier*100:.0f}% del normal",
                "trading_permitido": adj.is_trading_allowed,
            },
            "reason": adj.reason,
            "last_updated": m.last_updated,
            "trades_analyzed": m.trades_analyzed,
        }
    
    def get_trend_analysis(self) -> Dict[str, Any]:
        """Analizar tendencias históricas para predicción."""
        if len(self.history) < 7:
            return {"status": "INSUFFICIENT_DATA", "message": "Necesito al menos 7 días de datos"}
        
        # Últimos 7 días vs 7 días anteriores
        recent = self.history[-7:]
        previous = self.history[-14:-7] if len(self.history) >= 14 else []
        
        analysis = {
            "period": f"{recent[0].date} - {recent[-1].date}",
            "days_of_data": len(self.history),
        }
        
        # Tendencia de ROI
        recent_roi = statistics.mean([dp.avg_roi_pct for dp in recent])
        if previous:
            prev_roi = statistics.mean([dp.avg_roi_pct for dp in previous])
            roi_change = ((recent_roi - prev_roi) / abs(prev_roi) * 100) if prev_roi != 0 else 0
            analysis["roi_trend"] = {
                "current": f"{recent_roi:.1f}%",
                "previous": f"{prev_roi:.1f}%",
                "change": f"{roi_change:+.1f}%",
                "direction": "📈" if roi_change > 5 else "📉" if roi_change < -5 else "➡️"
            }
        
        # Tendencia de oportunidades
        recent_opps = statistics.mean([dp.opportunities_count for dp in recent])
        if previous:
            prev_opps = statistics.mean([dp.opportunities_count for dp in previous])
            opp_change = ((recent_opps - prev_opps) / prev_opps * 100) if prev_opps > 0 else 0
            analysis["opportunities_trend"] = {
                "current": f"{recent_opps:.0f}/semana",
                "previous": f"{prev_opps:.0f}/semana", 
                "change": f"{opp_change:+.1f}%",
                "direction": "📈" if opp_change > 10 else "📉" if opp_change < -10 else "➡️"
            }
        
        # Proyección simple (si la tendencia continúa)
        if len(self.history) >= 14 and previous:
            weeks_until_critical = None
            if roi_change < -10:  # ROI cayendo >10% por semana
                weeks_until_zero = abs(recent_roi / (prev_roi - recent_roi)) if prev_roi != recent_roi else float('inf')
                weeks_until_critical = min(weeks_until_zero, 12)  # Cap en 12 semanas
            
            analysis["projection"] = {
                "warning": f"⚠️ Si la tendencia continúa, ROI podría llegar a 0 en ~{weeks_until_critical:.0f} semanas" 
                          if weeks_until_critical and weeks_until_critical < 12 else None,
                "recommendation": self._get_recommendation(roi_change, opp_change if previous else 0)
            }
        
        return analysis
    
    def _get_recommendation(self, roi_change: float, opp_change: float) -> str:
        """Generar recomendación basada en tendencias."""
        if roi_change > 10 and opp_change > 10:
            return "🟢 Mercado mejorando - considerar aumentar exposición"
        elif roi_change > 0 and opp_change > 0:
            return "🟢 Mercado estable - mantener estrategia actual"
        elif roi_change > -10 and opp_change > -10:
            return "🟡 Deterioro leve - monitorear de cerca"
        elif roi_change > -20 and opp_change > -20:
            return "🟠 Deterioro moderado - reducir exposición, preparar exit strategy"
        else:
            return "🔴 Deterioro severo - considerar pausar operaciones"
    
    def should_alert(self) -> Tuple[bool, Optional[str]]:
        """Determinar si hay que enviar alerta."""
        if not self.current_metrics:
            return False, None
        
        m = self.current_metrics
        
        # Alerta si pasamos de HEALTHY a algo peor
        if m.status in ["WARNING", "CRITICAL"]:
            return True, f"⚠️ Market Health: {self.get_status_emoji()} {m.status} (Score: {m.health_score})\n{self.current_adjustments.reason}"
        
        # Alerta si hay deterioro significativo en tendencias
        if m.roi_trend < -0.20 or m.opportunities_trend < -0.30:
            return True, f"📉 Tendencia negativa detectada:\nROI: {m.roi_trend*100:+.1f}%\nOportunidades: {m.opportunities_trend*100:+.1f}%"
        
        return False, None


# Global instance
market_health = MarketHealthMonitor()


def integrate_with_smart_trader(smart_trader_instance, opportunity: Dict) -> Tuple[bool, str, float]:
    """
    Integrar health monitor con smart_trader.
    Retorna (should_trade, reason, adjusted_position_size)
    """
    adj = market_health.get_adjustments()
    
    # Si trading no está permitido
    if not adj.is_trading_allowed:
        return False, f"Trading pausado: {adj.reason}", 0
    
    # Ajustar score threshold
    score = opportunity.get('score', 0)
    if score < adj.min_score_threshold:
        return False, f"Score {score} < {adj.min_score_threshold} (ajustado por salud del mercado)", 0
    
    # Verificar max concurrent trades
    current_positions = smart_trader_instance.get_position_count()
    if current_positions >= adj.max_concurrent_trades:
        return False, f"Max trades alcanzado: {current_positions}/{adj.max_concurrent_trades}", 0
    
    # Calcular position size ajustado
    base_size = opportunity.get('suggested_size', 5.0)
    adjusted_size = base_size * adj.position_size_multiplier
    
    return True, "OK", adjusted_size


def integrate_with_resolver(take_profit: float, stop_loss: float) -> Tuple[float, float]:
    """
    Ajustar take profit y stop loss según salud del mercado.
    En mercados deteriorados, tomamos ganancias más rápido.
    """
    adj = market_health.get_adjustments()
    
    adjusted_tp = take_profit * adj.take_profit_multiplier
    adjusted_sl = stop_loss * adj.stop_loss_multiplier
    
    return adjusted_tp, adjusted_sl
