@echo off
echo ==========================================
echo Spouštím Intebo Aplikaci lokálně...
echo ==========================================
echo.

:: Check if python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python není nainstalován nebo není v PATH.
    echo Stáhněte a nainstalujte Python z https://www.python.org/downloads/
    echo DŮLEŽITÉ: Během instalace zaškrtněte políčko "Add python.exe to PATH"!
    pause
    exit /b
)

echo Instaluji závislosti (to může chvíli trvat, pokud je to poprvé)...
pip install -r requirements.txt

echo.
echo Spouštím aplikační server...
echo Aplikace bude dostupná na http://localhost:8000
echo (Tento okno můžete nechat otevřené během testování. Zavřete ho pro zastavení serveru.)
echo.

:: Spustím aplikaci na bezpečném portu (8000 místo 80, aby se předešlo chybám s právy administrátora)
python -m uvicorn app:app --host localhost --port 8000

pause
