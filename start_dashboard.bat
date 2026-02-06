@echo off
echo ============================================
echo   CRYPTO ARBITRAGE DASHBOARD
echo ============================================
echo.
echo Dashboard URL: http://localhost:8085
echo.
echo El simulador corre automaticamente en modo REAL
echo Capital: $100 | Max Position: $10 | Min Edge: 8%%
echo.

REM Check if venv exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo Iniciando dashboard + simulador...
echo.

python crypto_arb_dashboard.py

pause
