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

:: Pri prvnim spusteni vytvor config.ini ze sablony
if not exist config.ini (
    if exist config.ini.example (
        echo Vytvarim config.ini ze sablony config.ini.example...
        copy /Y config.ini.example config.ini >nul
        echo.
        echo [!] UPRAVTE config.ini - nastavte data_dir na vasi cestu, pak spustte znovu.
        notepad config.ini
        pause
        exit /b
    )
)

echo Instaluji zavislosti (to muze chvili trvat, pokud je to poprve)...
python -m pip install -r requirements.txt -q

echo.
echo Nacitam konfiguraci z config.ini...
for /f "delims=" %%i in ('python -c "import config; print(config.HOST)"') do set INTEBO_HOST=%%i
for /f "delims=" %%i in ('python -c "import config; print(config.PORT)"') do set INTEBO_PORT=%%i
for /f "delims=" %%i in ('python -c "import config; print(config.DATA_DIR)"') do set INTEBO_DATA=%%i

:: 0.0.0.0 je validni jen jako bind adresa, ne jako URL v prohlizeci.
:: Pro otevreni prohlizece pouzij localhost, kdyz server posloucha na vsech rozhranich.
set INTEBO_CLIENT_HOST=%INTEBO_HOST%
if "%INTEBO_HOST%"=="0.0.0.0" set INTEBO_CLIENT_HOST=localhost

echo.
echo   Host:     %INTEBO_HOST%
echo   Port:     %INTEBO_PORT%
echo   Data dir: %INTEBO_DATA%
echo.
echo Aplikace bude dostupna na http://%INTEBO_CLIENT_HOST%:%INTEBO_PORT%
echo Prohlizec se otevre automaticky za par sekund.
echo (Toto okno nechte otevrene. Zavrenim okna se server zastavi.)
echo.

:: Otevri prohlizec na pozadi (timeout pocka, az server nabehne)
start "" /B cmd /c "timeout /t 3 /nobreak >nul && start http://%INTEBO_CLIENT_HOST%:%INTEBO_PORT%"

python -m uvicorn app:app --host %INTEBO_HOST% --port %INTEBO_PORT%

pause
