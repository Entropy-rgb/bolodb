@echo off
setlocal enabledelayedexpansion
title BoloDB
cd /d "%~dp0"
echo.
echo  BoloDB - Ask your data. Trust the answer.
echo  ==========================================
echo.
echo  DATABASE URL examples:
echo    sqlite:///C:\path\to\mydb.db
echo    postgresql://user:pass@localhost/mydb
echo    mysql+pymysql://user:pass@localhost/mydb
echo.
echo  Or press Enter to use the built-in sample database (no setup needed).
echo.
set /p DB="  Database URL (Enter for sample): "
echo.
echo  Starting BoloDB at http://localhost:4321
echo  (Your browser will open automatically)
echo  Press Ctrl+C to stop.
echo.
if "!DB!"=="" (
    python main.py
) else (
    python main.py --db "!DB!"
)
if errorlevel 1 pause
