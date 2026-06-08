@echo off
title BoloDB Setup
echo.
echo  BoloDB Setup
echo  ============
echo.
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python not found. Install from https://python.org (check "Add to PATH")
    pause & exit /b 1
)
python -m pip install --upgrade pip --quiet
python -m pip install fastapi "uvicorn[standard]" httpx sqlalchemy --quiet
echo.
echo  Which database driver do you need?
echo    1  SQLite only (no driver needed)
echo    2  PostgreSQL
echo    3  MySQL / MariaDB
echo    4  SQL Server
echo    5  All of the above
echo.
set /p d="  Enter number (default 1): "
if "%d%"=="2" python -m pip install psycopg2-binary --quiet
if "%d%"=="3" python -m pip install pymysql --quiet
if "%d%"=="4" python -m pip install pyodbc --quiet
if "%d%"=="5" python -m pip install psycopg2-binary pymysql pyodbc --quiet
echo.
echo  Done. Run: run.bat
echo.
pause
