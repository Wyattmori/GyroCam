@echo off
title GyroCam bridge
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3 is required: https://www.python.org/downloads/ & pause & exit /b 1)
python gyrocam_bridge.py
pause
