@echo off
REM ===== Draft War Room - daily data refresher =====
REM Double-click this file to start. Leave the window open all day.
REM It refreshes ADP + projections + injuries and pushes updates to your live site.
cd /d "%~dp0"
echo Starting Draft War Room refresher...
echo Leave this window open. Press Ctrl+C to stop.
echo.
python refresh.py
echo.
echo Refresher stopped. Press any key to close.
pause >nul
