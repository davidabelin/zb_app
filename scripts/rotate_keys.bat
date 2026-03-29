@echo off
setlocal ENABLEEXTENSIONS

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

set "PROJECT_ID=%~1"
if not defined PROJECT_ID set "PROJECT_ID=zenbot-434517"

set "SERVICE_ACCOUNT=%~2"
if not defined SERVICE_ACCOUNT set "SERVICE_ACCOUNT=zenbot-sa@%PROJECT_ID%.iam.gserviceaccount.com"

echo.
echo Zenbot v3 key rotation
echo Run this only for first-time setup or when secrets change.
echo   project        : %PROJECT_ID%
echo   service account: %SERVICE_ACCOUNT%
echo.
echo This will create or update:
echo   - zb-openai-api-key
echo   - zb-flask-secret-key
echo   - zb-action-api-token
echo.
echo Launching the PowerShell helper for hidden secret prompts...
powershell -ExecutionPolicy Bypass -File "%~dp0rotate_keys.ps1" -ProjectId "%PROJECT_ID%" -ServiceAccount "%SERVICE_ACCOUNT%" %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:usage
echo Usage:
echo   rotate_keys.bat [PROJECT_ID] [SERVICE_ACCOUNT]
echo.
echo Most people should just run:
echo   rotate_keys.bat
echo.
echo Example:
echo   rotate_keys.bat zenbot-434517 zenbot-sa@zenbot-434517.iam.gserviceaccount.com
echo.
exit /b 0
