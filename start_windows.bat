@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py --open
  goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python app.py --open
  goto end
)
echo Python не найден. Установите Python 3 с сайта https://www.python.org/downloads/
echo При установке отметьте галочку "Add python.exe to PATH".
:end
pause
