@echo off
cd /d "%~dp0"

if exist venv goto skip_create
echo Setting up (first run only)...
python -m venv venv

:skip_create
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo Starting TSW Hud in headless server mode...
set TSW_HUD_NO_BROWSER=true
set TSW_HUD_PORT=5273
python app.py

pause
