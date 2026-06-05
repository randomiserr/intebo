@echo off
echo ==========================================
echo Spoustim Intebo Aplikaci...
echo ==========================================
echo.

:: Check if python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python neni nainstalovan nebo neni v PATH.
    echo Stahnete a nainstalujte Python z https://www.python.org/downloads/
    echo DULEZITE: Behem instalace zaskrtnete policko "Add python.exe to PATH"!
    pause
    exit /b
)

echo Instaluji zavislosti (to muze chvili trvat, pokud je to poprve)...
python -m pip install -r requirements.txt -q

echo.
echo Nacitam konfiguraci z config.ini...
for /f "delims=" %%i in ('python -c "import config; print(config.HOST)"') do set INTEBO_HOST=%%i
for /f "delims=" %%i in ('python -c "import config; print(config.PORT)"') do set INTEBO_PORT=%%i
for /f "delims=" %%i in ('python -c "import config; print(config.DATA_DIR)"') do set INTEBO_DATA=%%i

echo.
echo   Host:     %INTEBO_HOST%
echo   Port:     %INTEBO_PORT%
echo   Data dir: %INTEBO_DATA%
echo.
echo Aplikace bude dostupna na http://%INTEBO_HOST%:%INTEBO_PORT%
echo (Toto okno nechte otevrene. Zavrenim okna se server zastavi.)
echo.

python -m uvicorn app:app --host %INTEBO_HOST% --port %INTEBO_PORT%

pause
