"""
Configuration management for Polymarket Bot.
Loads settings from environment variables with sensible defaults.
"""
import os
from pydantic import BaseModel, Field
from typing import Optional

# Load .env file if available (local development)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available in production, env vars set by platform
    pass


class WalletConfig(BaseModel):
    """Wallet and blockchain configuration."""
    private_key: Optional[str] = Field(default=None)
    
    @property
    def is_configured(self) -> bool:
        return self.private_key is not None and len(self.private_key) > 0


class TradingConfig(BaseModel):
    """Trading limits and parameters."""
    max_trade_amount: float = Field(default=10.0, ge=0)
    max_daily_exposure: float = Field(default=100.0, ge=0)
    min_score_to_trade: int = Field(default=85, ge=0, le=100)
    min_spread_to_trade: float = Field(default=0.05, ge=0, le=1)
    auto_trade_enabled: bool = Field(default=False)
    dry_run: bool = Field(default=True)


class AlertConfig(BaseModel):
    """Alert and notification settings."""
    whatsapp_target: str = Field(default="")
    whatsapp_gateway_url: str = Field(default="http://localhost:3000/req")


class ServerConfig(BaseModel):
    """HTTP server configuration."""
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8080)


class PolymarketAPIConfig(BaseModel):
    """Polymarket API credentials."""
    api_key: Optional[str] = Field(default=None)
    api_secret: Optional[str] = Field(default=None)
    api_passphrase: Optional[str] = Field(default=None)
    
    @property
    def is_configured(self) -> bool:
        return all([self.api_key, self.api_secret, self.api_passphrase])


class Config(BaseModel):
    """Main configuration container."""
    wallet: WalletConfig
    trading: TradingConfig
    alerts: AlertConfig
    server: ServerConfig
    polymarket_api: PolymarketAPIConfig
    scan_interval: int = Field(default=600)
    
    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        return cls(
            wallet=WalletConfig(
                private_key=os.getenv("PRIVATE_KEY")
            ),
            trading=TradingConfig(
                max_trade_amount=float(os.getenv("MAX_TRADE_AMOUNT", "10.0")),
                max_daily_exposure=float(os.getenv("MAX_DAILY_EXPOSURE", "100.0")),
                min_score_to_trade=int(os.getenv("MIN_SCORE_TO_TRADE", "85")),
                min_spread_to_trade=float(os.getenv("MIN_SPREAD_TO_TRADE", "0.05")),
                auto_trade_enabled=os.getenv("AUTO_TRADE_ENABLED", "false").lower() == "true",
                dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
            ),
            alerts=AlertConfig(
                whatsapp_target=os.getenv("WHATSAPP_TARGET", ""),
                whatsapp_gateway_url=os.getenv("WHATSAPP_GATEWAY_URL", "http://localhost:3000/req"),
            ),
            server=ServerConfig(
                host=os.getenv("HOST", "0.0.0.0"),
                port=int(os.getenv("PORT", "8080")),
            ),
            polymarket_api=PolymarketAPIConfig(
                api_key=os.getenv("POLYMARKET_API_KEY"),
                api_secret=os.getenv("POLYMARKET_API_SECRET"),
                api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE"),
            ),
            scan_interval=int(os.getenv("SCAN_INTERVAL", "600")),
        )


# Global config instance
config = Config.from_env()
