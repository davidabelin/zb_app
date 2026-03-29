@echo off
setlocal ENABLEEXTENSIONS

if /I "%~1"=="--help" goto :usage
if /I "%~1"=="/?" goto :usage

set "PROJECT_ID=%~1"
if not defined PROJECT_ID set "PROJECT_ID=zenbot-434517"

set "REGION=%~2"
if not defined REGION set "REGION=us-central1"

set "SERVICE_NAME=%~3"
if not defined SERVICE_NAME set "SERVICE_NAME=zb-chat-api"

set "SERVICE_ACCOUNT=%~4"
if not defined SERVICE_ACCOUNT set "SERVICE_ACCOUNT=zenbot-sa@%PROJECT_ID%.iam.gserviceaccount.com"

set "ENV_VARS=GOOGLE_CLOUD_PROJECT=%PROJECT_ID%,STREAMING_ENABLED=true,ZB_API_STRICT_AUTH=true,SESSION_COOKIE_SECURE=true,OPENAI_API_KEY_SECRET_NAME=zb-openai-api-key,FLASK_SECRET_KEY_SECRET_NAME=zb-flask-secret-key,ACTION_API_TOKEN_SECRET_NAME=zb-action-api-token,OPENAI_LIVE_MODEL=gpt-5.4-mini,OPENAI_JUDGE_MODEL=gpt-5.4,OPENAI_POST_TRAINING_MODEL=gpt-4.1,OPENAI_ENABLE_FUNCTION_TOOLS=true,OPENAI_PROMPT_CACHE_RETENTION=24h"

echo.
echo Zenbot v3 Cloud Run deploy
echo   project        : %PROJECT_ID%
echo   region         : %REGION%
echo   service        : %SERVICE_NAME%
echo   service account: %SERVICE_ACCOUNT%
echo.
echo Order of use:
echo   1. Run setup_gcp_secrets.cmd once per project or whenever secrets rotate.
echo   2. Run this script for each app deploy.
echo   3. Run sync_openai_vector_store.py only if File Search is enabled or docs changed.
echo   4. Follow RECOVERY_RUNBOOK.md smoke tests.
echo.

echo [1/4] Setting gcloud project...
gcloud config set project "%PROJECT_ID%"
if errorlevel 1 goto :error

echo [2/4] Deploying Cloud Run service...
gcloud run deploy "%SERVICE_NAME%" ^
  --source . ^
  --region "%REGION%" ^
  --quiet ^
  --allow-unauthenticated ^
  --service-account "%SERVICE_ACCOUNT%" ^
  --concurrency 8 ^
  --min-instances 1 ^
  --set-env-vars "%ENV_VARS%"
if errorlevel 1 goto :error

echo [3/4] Reading service URL...
set "SERVICE_URL="
for /f "usebackq delims=" %%I in (`gcloud run services describe "%SERVICE_NAME%" --region "%REGION%" --format "value(status.url)"`) do set "SERVICE_URL=%%I"
if not defined SERVICE_URL (
  echo Failed to resolve the Cloud Run service URL.
  goto :error
)
echo Resolved URL: %SERVICE_URL%

echo [4/4] Syncing WEB_APP_ORIGIN and CHAT_API_BASE_URL...
gcloud run services update "%SERVICE_NAME%" ^
  --region "%REGION%" ^
  --quiet ^
  --update-env-vars "WEB_APP_ORIGIN=%SERVICE_URL%,CHAT_API_BASE_URL=%SERVICE_URL%"
if errorlevel 1 goto :error

echo.
echo Deploy complete.
echo Next:
echo   - Optional File Search sync:
echo       python scripts\sync_openai_vector_store.py --create
echo   - Quality gates:
echo       python -m pytest -q
echo       python -m flake8
echo       python -m mypy .
echo   - Smoke tests: see RECOVERY_RUNBOOK.md
echo.
exit /b 0

:usage
echo Usage:
echo   deploy_cloudrun_service.cmd [PROJECT_ID] [REGION] [SERVICE_NAME] [SERVICE_ACCOUNT]
echo.
echo Example:
echo   deploy_cloudrun_service.cmd zenbot-434517 us-central1 zb-chat-api zenbot-sa@zenbot-434517.iam.gserviceaccount.com
echo.
exit /b 0

:error
echo.
echo Deploy failed.
exit /b 1
