@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 windows_app.py %* & goto :done)
where python >nul 2>nul && (python windows_app.py %* & goto :done)
echo Python 3를 찾을 수 없습니다. https://www.python.org/downloads/ 에서 설치한 뒤 다시 실행해 주세요.
:done
pause
