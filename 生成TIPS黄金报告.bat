@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"

if exist ".env.bat" (
  call ".env.bat"
)

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 "src\commodity_report.py" --open
) else (
  python "src\commodity_report.py" --open
)

pause
