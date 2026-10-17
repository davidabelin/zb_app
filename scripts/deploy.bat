@echo off
setlocal ENABLEEXTENSIONS

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

set "PROJECT_ID=%~1"
if not defined PROJECT_ID set "PROJECT_ID=zenbot-434517"

echo.
echo Zenbot v3.2.5 App Engine deploy
echo Run this for normal Zenbot code deploys.
echo   project: %PROJECT_ID%
echo   manifest: app.yaml
echo.

echo [1/2] Setting gcloud project...
call gcloud config set project "%PROJECT_ID%"
if errorlevel 1 goto :error

echo [2/2] Deploying App Engine service...
call gcloud app deploy app.yaml --project "%PROJECT_ID%" --quiet
if errorlevel 1 goto :error

echo.
echo Deploy complete.
echo Optional next steps:
echo   scripts\update_context.bat      only if docs/search context changed
echo   scripts\get_token.bat           if you need the admin/API token
echo   python -m pytest -q
echo   python -m flake8
echo   python -m mypy .
echo.
exit /b 0

:usage
echo Usage:
echo   deploy.bat [PROJECT_ID]
echo.
echo Most people should just run:
echo   deploy.bat
echo.
echo Example:
echo   deploy.bat zenbot-434517
echo.
exit /b 0

:error
echo.
echo Deploy failed.
exit /b 1
