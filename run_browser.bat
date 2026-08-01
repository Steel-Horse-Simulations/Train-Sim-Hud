@echo off
cd /d "%~dp0"
if exist venv goto run
echo Setting up (first run only)...
python -m venv venv
call venv\Scripts\activate.bat
pip install -r requirements.txt
goto launch

:run
call venv\Scripts\activate.bat

:launch
echo Starting TSW Hud in browser mode (no native window)...
python app.py --browser
pause
