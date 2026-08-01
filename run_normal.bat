@echo off
cd /d "%~dp0"

if exist venv goto skip_create
echo Setting up (first run only)...
python -m venv venv

:skip_create
call venv\Scripts\activate.bat
pip install -r requirements.txt

echo Starting TSW Hud with native window...
python app.py

pause
