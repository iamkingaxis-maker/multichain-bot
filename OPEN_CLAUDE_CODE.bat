@echo off
title Claude Code - Multi-Chain Memecoin Bot v10
color 0E

echo.
echo  ============================================
echo   Opening Claude Code in Bot Folder
echo  ============================================
echo.

cd /d "%~dp0"

echo  Claude Code will open in this folder.
echo  All 49 bot files are available to Claude.
echo.
echo  Useful prompts:
echo    "Fix the error in bot.log"
echo    "Explain what signal_evaluator.py does"
echo    "Why isn't the pyramid logic triggering?"
echo    "Change stall detection to 45 minutes"
echo.

claude

pause
