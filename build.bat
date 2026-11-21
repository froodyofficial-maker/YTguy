@echo off
REM Builds YTGuy.exe into the dist\ folder.
REM Needs Python 3.9+ from python.org (tick "Add python.exe to PATH" during install).
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller || goto :err
python -m PyInstaller --noconfirm --clean --onefile --noconsole --name YTGuy --icon app.ico ^
  --add-data "web;web" server.py || goto :err
echo.
echo Done! Your app is at: %~dp0dist\YTGuy.exe
pause
exit /b 0
:err
echo.
echo Build failed - see the messages above.
pause
exit /b 1
