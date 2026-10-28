@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   Обновление проекта на GitHub
echo ================================================
where git >nul 2>nul
if errorlevel 1 (
  echo [ОШИБКА] Git не установлен. Скачай: https://git-scm.com/download/win
  pause
  exit /b 1
)
if not exist ".git" (
  echo [ОШИБКА] Эта папка не подключена к GitHub.
  echo Распакуй новый архив ПОВЕРХ старой папки проекта ^(той, откуда запускал git_publish.bat^)
  echo или загрузи файлы через сайт: Add file - Upload files.
  pause
  exit /b 1
)
echo Что изменилось:
git status --short
echo.
set /p MSG=Коротко: что нового? (например: feat: вкладки, таймер, заметки) 
if "!MSG!"=="" set MSG=chore: обновление
git add .
git commit -m "!MSG!"
if errorlevel 1 (
  echo.
  echo Нечего сохранять: файлы такие же, как уже на GitHub.
  pause
  exit /b 0
)
git push
if errorlevel 1 (
  echo.
  echo [ОШИБКА] Не получилось отправить. Скопируй текст выше и пришли мне.
  pause
  exit /b 1
)
echo.
echo Готово! Обновление на GitHub.
pause
