@echo off
REM Runs YTGuy straight from source (needs Python 3.9+ on PATH). Opens in your browser.
cd /d "%~dp0"
python server.py
if errorlevel 1 pause
