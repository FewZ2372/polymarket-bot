@echo off
echo ============================================
echo   CRYPTO ARBITRAGE SIMULATOR
echo ============================================
echo.
echo Modo: SIMULACION (sin trades reales)
echo Capital inicial: $100 USDC
echo.
echo Opciones:
echo   1. Modo REAL - Busca oportunidades reales de arbitraje
echo   2. Modo DEMO - Genera trades automaticos para testing
echo.

set /p MODE="Elegir modo (1 o 2): "

REM Check if venv exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo.
echo Iniciando simulador...
echo Press Ctrl+C to stop
echo.

if "%MODE%"=="2" (
    echo [DEMO MODE] Generando trades de prueba cada 15 segundos...
    python crypto_arb_simulator.py --capital 100 --max-position 10 --max-positions 5 --min-edge 0.05 --symbols BTCUSDT ETHUSDT --demo
) else (
    echo [REAL MODE] Buscando oportunidades reales de arbitraje...
    python crypto_arb_simulator.py --capital 100 --max-position 10 --max-positions 5 --min-edge 0.08 --symbols BTCUSDT ETHUSDT
)

pause
