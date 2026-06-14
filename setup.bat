@echo off
echo === Voice Automation Setup ===
echo.
echo Creating virtual environment...
python -m venv .venv
echo.
echo Activating virtual environment...
call .venv\Scripts\activate.bat
echo.
echo Installing dependencies...
pip install -e .
echo.
echo Downloading speech model...
python -m voice_automation download-model
echo.
echo === Setup complete! ===
echo Run 'run.bat' to start voice automation.
pause
