@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
echo ================================================
echo   Публикация WinIsland на GitHub
echo ================================================
where git >nul 2>nul
if errorlevel 1 (
  echo [ОШИБКА] Git не установлен. Скачай: https://git-scm.com/download/win
  pause
  exit /b 1
)
git config --global user.name >nul 2>nul
if errorlevel 1 (
  set /p GN=Твоё имя для подписи коммитов: 
  git config --global user.name "!GN!"
)
git config --global user.email >nul 2>nul
if errorlevel 1 (
  set /p GE=Твой email от GitHub: 
  git config --global user.email "!GE!"
)
echo.
set /p URL=Вставь ссылку на пустой репозиторий (https://github.com/НИК/WinIsland.git): 
if not exist ".git" (
  git init
)
git branch -M main
git config commit.template .gitmessage
git add .
git commit -m "feat: первая версия WinIsland 2.0"
git remote remove origin >nul 2>nul
git remote add origin !URL!
git push -u origin main
echo.
echo Если выше нет красных ошибок — проект уже на GitHub!
pause
