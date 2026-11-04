@echo off
chcp 65001 >nul
rem Двойной щелчок: собрать zip проекта в папку dist (без лишних файлов и без ключа из .env).
call "%~dp0scripts\run.bat" package
if errorlevel 1 exit /b 1
start "" "%~dp0dist"
pause
