@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   Выпуск версии (появится в разделе Releases)
echo ================================================
if not exist ".git" (
  echo [ОШИБКА] Папка не подключена к GitHub. Сначала git_publish.bat
  pause
  exit /b 1
)
echo Сначала убедись, что запускал git_update.bat (всё загружено).
echo.
set /p V=Номер версии (например 2.1.0): 
if "!V!"=="" (
  echo Номер не введён.
  pause
  exit /b 1
)
git tag v!V!
if errorlevel 1 (
  echo [ОШИБКА] Такая версия уже есть. Введи другой номер.
  pause
  exit /b 1
)
git push origin v!V!
if errorlevel 1 (
  echo [ОШИБКА] Не получилось отправить. Пришли мне текст выше.
  pause
  exit /b 1
)
echo.
echo Готово! Через 1-5 минут версия появится в Releases на GitHub
echo (смотри вкладку Actions: зелёная галочка = готово).
pause
