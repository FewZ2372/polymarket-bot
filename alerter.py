"""
Alert system for sending notifications via WhatsApp and other channels.
"""
import time
import requests
from typing import Dict, Any, Optional
from datetime import datetime

from config import config
from logger import log


class Alerter:
    """
    Handles sending alerts through various channels.
    """
    
    def __init__(self):
        self.alerts_sent = 0
        self.last_alert_time: Optional[datetime] = None
        self._rate_limit_seconds = 60  # Minimum seconds between alerts
    
    def _can_send_alert(self) -> bool:
        """Check rate limiting."""
        if self.last_alert_time is None:
            return True
        
        elapsed = (datetime.now() - self.last_alert_time).total_seconds()
        return elapsed >= self._rate_limit_seconds
    
    def send_whatsapp(self, message: str) -> bool:
        """
        Send alert via WhatsApp using the configured gateway.
        """
        if not config.alerts.whatsapp_target:
            log.warning("WhatsApp target not configured")
            return False
        
        if not self._can_send_alert():
            log.debug("Rate limited, skipping WhatsApp alert")
            return False
        
        try:
            payload = {
                "type": "req",
                "id": f"alert-{int(time.time())}",
                "method": "whatsapp.sendMessage",
                "params": {
                    "target": config.alerts.whatsapp_target,
                    "message": message
                }
            }
            
            response = requests.post(
                config.alerts.whatsapp_gateway_url,
                json=payload,
                timeout=10
            )
            
            if response.status_code == 200:
                self.alerts_sent += 1
                self.last_alert_time = datetime.now()
                log.info("WhatsApp alert sent successfully")
                return True
            else:
                log.warning(f"WhatsApp gateway returned {response.status_code}")
                return False
                
        except requests.exceptions.ConnectionError:
            log.debug("WhatsApp gateway not available (localhost)")
            return False
        except Exception as e:
            log.error(f"Error sending WhatsApp alert: {e}")
            return False
    
    def format_opportunity_alert(self, opportunity: Dict[str, Any], trade_result=None) -> str:
        """
        Format an opportunity into a WhatsApp message.
        """
        title = opportunity.get('question', 'Unknown Market')
        price = opportunity.get('yes', 0)
        score = opportunity.get('score', 0)
        vol = opportunity.get('vol24h', 0)
        spread = opportunity.get('spread', 0)
        sentiment = opportunity.get('sentiment', 'N/A')
        buzz = opportunity.get('buzz_score', 0)
        k_yes = opportunity.get('k_yes')
        
        # Build the alert message
        lines = [
            "🚀 *TRADING ALERT*",
            "",
            f"📈 *Market:* {title}",
            f"💰 *Price:* ${price:.4f}",
            f"🔥 *Score:* {score}/100",
            f"📊 *Vol 24h:* ${vol:,.2f}",
        ]
        
        if k_yes:
            lines.append(f"📉 *Kalshi:* ${k_yes:.4f} (Spread: {spread*100:.1f}%)")
        
        if sentiment:
            lines.append(f"🗣️ *Sentiment:* {sentiment} ({buzz})")
        
        # Add trade info if executed
        if trade_result:
            lines.append("")
            side_str = trade_result.side.value if trade_result.side else "UNKNOWN"
            if trade_result.is_dry_run and trade_result.success:
                lines.append(f"🧪 *[DRY RUN]* Would {side_str} ${trade_result.amount:.2f}")
            elif trade_result.success:
                lines.append(f"✅ *TRADED:* {side_str} ${trade_result.amount:.2f}")
            else:
                lines.append(f"❌ *Trade skipped:* {trade_result.error}")
        
        # Add link
        slug = opportunity.get('slug', title.lower().replace(' ', '-'))
        lines.append("")
        lines.append(f"🔗 https://polymarket.com/event/{slug}")
        
        return "\n".join(lines)
    
    def alert_opportunity(self, opportunity: Dict[str, Any], trade_result=None) -> bool:
        """
        Send an alert for a detected opportunity.
        """
        message = self.format_opportunity_alert(opportunity, trade_result)
        return self.send_whatsapp(message)
    
    def send_raw_message(self, message: str) -> bool:
        """
        Send a raw message alert (for health warnings, etc).
        """
        return self.send_whatsapp(message)


# Global alerter instance
alerter = Alerter()
