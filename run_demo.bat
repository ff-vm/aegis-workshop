@echo off
rem Double-click: runs the spin-and-wave sequence in the simulator and opens the live view.
cd /d "%~dp0"
chcp 65001 >nul
echo Aegis workshop folder: %cd%
echo.
set FF_SIM_OPEN=1
if exist .venv\Scripts\python.exe (.venv\Scripts\python spin_and_wave.py) else (py spin_and_wave.py)
echo.
echo Replay saved: %cd%\run.html
pause
