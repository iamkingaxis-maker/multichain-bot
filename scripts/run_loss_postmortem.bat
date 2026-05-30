@echo off
REM Wrapper for Windows Task Scheduler to auto-postmortem new losses.
REM Pulls /api/trades, processes any new $20-era losses since last run.
cd /d C:\Users\jcole\multichain-bot
set PYTHONIOENCODING=utf-8
python scripts\loss_postmortem.py >> .live_forward_test\_postmortem_cron.log 2>&1
exit /b 0
