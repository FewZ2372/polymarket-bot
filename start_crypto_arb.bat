@echo off
echo ============================================
echo   CRYPTO LATENCY ARBITRAGE BOT
echo ============================================
echo.

REM Check if venv exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment not found, using system Python
)

echo Starting in DRY RUN mode (no real trades)...
echo Press Ctrl+C to stop
echo.

python crypto_latency_arb.py --symbols BTCUSDT ETHUSDT --min-edge 0.10 --min-liquidity 1000

pause
