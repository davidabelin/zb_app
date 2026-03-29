@echo off
setlocal ENABLEEXTENSIONS

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

set "PROJECT_ID=%~1"
if not defined PROJECT_ID set "PROJECT_ID=zenbot-434517"

set "ACCOUNT=%~2"

set "SECRET_NAME=%~3"
if not defined SECRET_NAME set "SECRET_NAME=zb-action-api-token"

echo.
echo Zenbot v3 admin token lookup
echo   project : %PROJECT_ID%
if defined ACCOUNT (
echo   account : %ACCOUNT%
) else (
echo   account : active gcloud account
)
echo   secret  : %SECRET_NAME%
echo.
echo Use this after deploy when you need the token for admin login, Postman, or GPT Actions.
echo.
if defined ACCOUNT (
  powershell -ExecutionPolicy Bypass -File "%~dp0get_admin_token.ps1" -ProjectId "%PROJECT_ID%" -Account "%ACCOUNT%" -SecretName "%SECRET_NAME%"
) else (
  powershell -ExecutionPolicy Bypass -File "%~dp0get_admin_token.ps1" -ProjectId "%PROJECT_ID%" -SecretName "%SECRET_NAME%"
)
exit /b %ERRORLEVEL%

:usage
echo Usage:
echo   get_admin_token.cmd [PROJECT_ID] [ACCOUNT] [SECRET_NAME]
echo.
echo Example:
echo   get_admin_token.cmd zenbot-434517 my-user@example.com zb-action-api-token
echo.
exit /b 0
