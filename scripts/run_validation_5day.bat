@echo off
REM 5-day forward validation of filter_turn — fires Sun May 10 1:17 PM CT
cd /d C:\Users\jcole\multichain-bot
set PYTHONIOENCODING=utf-8
python scripts\forward_validation.py 5day_final 2026-05-05T17:00:00 >> .live_forward_test\_cron.log 2>&1
exit /b 0
