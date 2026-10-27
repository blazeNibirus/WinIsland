@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   WinIsland - установка компонентов
echo ================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% --version
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Python не найден. Установи Python с python.org
  echo и обязательно поставь галочку "Add python.exe to PATH".
  pause
  exit /b 1
)
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
echo.
echo ================================================
echo   Готово! Теперь запускай файл run.bat
echo   (если выше есть красные ошибки - пришли их мне)
echo ================================================
pause
