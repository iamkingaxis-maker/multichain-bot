@echo off
REM Wrapper for Windows Task Scheduler to run the live forward test cycle.
REM Resolves any 2.5h+ old snapshots, takes a new snapshot, updates aggregate.
cd /d C:\Users\jcole\multichain-bot
set PYTHONIOENCODING=utf-8
python scripts\live_forward_test.py >> .live_forward_test\_cron.log 2>&1
exit /b 0
