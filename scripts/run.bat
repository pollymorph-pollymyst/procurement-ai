@echo off
rem Общий запуск: готовит Python-окружение и выполняет python -m procurement <аргументы>.
rem Вызывается из «Обновить анализ.bat», «Собрать архив для отправки.bat» и из Планировщика Windows.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "QUIET="
for %%a in (%*) do if /i "%%~a"=="--no-open" set QUIET=1

if exist ".venv\Scripts\python.exe" goto :deps
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul || goto :nopython
echo Первый запуск: готовлю программу, это 1-2 минуты...
%PY% -m venv .venv || goto :err

:deps
fc /b requirements.txt ".venv\installed.txt" >nul 2>nul && goto :run
echo Устанавливаю нужные библиотеки...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt || goto :err
copy /y requirements.txt ".venv\installed.txt" >nul

:run
".venv\Scripts\python.exe" -m procurement %*
if errorlevel 1 goto :err
exit /b 0

:nopython
echo.
echo Не найден Python 3.11 или новее.
echo Откроется страница загрузки: скачайте Python, при установке поставьте галочку
echo «Add python.exe to PATH», затем запустите этот файл ещё раз.
if not defined QUIET start "" https://www.python.org/downloads/windows/
if not defined QUIET pause
exit /b 1

:err
echo.
echo Что-то пошло не так. Сообщение выше можно переслать разработчику.
if not defined QUIET pause
exit /b 1
