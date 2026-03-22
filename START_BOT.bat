@echo off
title Multi-Chain Memecoin Bot v10
color 0A

echo.
echo  ============================================
echo   Multi-Chain Memecoin Bot v10
echo   Solana ^| Base ^| BNB Chain
echo  ============================================
echo.

cd /d "%~dp0"

echo  Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo  ERROR: Python not found. Install from python.org
    pause
    exit /b 1
)

echo  Checking dependencies...
python -c "import aiohttp, solders" 2>nul
if errorlevel 1 (
    echo  Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo  ERROR: Failed to install dependencies
        pause
        exit /b 1
    )
)

echo.
echo  Starting bot...
echo  Dashboard: http://localhost:8080
echo  Press Ctrl+C to stop
echo.

python main.py

echo.
echo  Bot stopped.
pause
