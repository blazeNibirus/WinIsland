@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% island.py
echo.
echo Программа остановилась. Если выше есть красный текст с ошибкой - скопируй его и пришли мне.
pause
