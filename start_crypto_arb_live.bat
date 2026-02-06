@echo off
echo ============================================
echo   CRYPTO LATENCY ARBITRAGE BOT - LIVE MODE
echo ============================================
echo.
echo WARNING: THIS WILL EXECUTE REAL TRADES!
echo.

REM Check if venv exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else (
    echo [WARNING] Virtual environment not found, using system Python
)

set /p CONFIRM="Type 'YES' to confirm live trading: "
if not "%CONFIRM%"=="YES" (
    echo Aborted.
    pause
    exit /b
)

echo Starting in LIVE mode...
echo.

python crypto_latency_arb.py --symbols BTCUSDT ETHUSDT --min-edge 0.10 --min-liquidity 1000 --auto-trade --live --max-trade 5.0

pause
