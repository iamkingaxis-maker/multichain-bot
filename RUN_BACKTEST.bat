@echo off
title Backtest - Multi-Chain Memecoin Bot v10
color 0B

echo.
echo  ============================================
echo   Backtest Runner
echo   Tests your strategy before going live
echo  ============================================
echo.

cd /d "%~dp0"

echo  Running backtest on all chains...
echo  Finding optimal score threshold...
echo.

python backtest\run_backtest.py --all-chains --find-optimal --days 30

echo.
echo  Backtest complete. Results saved to JSON files.
echo.
pause
