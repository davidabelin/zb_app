<#
.SYNOPSIS
Deploy the full Zenbot web/API service to Cloud Run.

.DESCRIPTION
Builds the current `zb_app` source with Cloud Build, deploys it as a single
public Cloud Run service, then updates `WEB_APP_ORIGIN` and
`CHAT_API_BASE_URL` to the resolved service URL so the browser shell and API
stay on the same host.
#>

param(
  [string]$ProjectId = "zenbot-434517",
  [string]$Region = "us-central1",
  [string]$ServiceName = "zb-chat-api",
  [string]$ServiceAccount = "zenbot-sa@zenbot-434517.iam.gserviceaccount.com"
)

$ErrorActionPreference = "Stop"

gcloud config set project $ProjectId
if ($LASTEXITCODE -ne 0) {
  throw "Failed to set gcloud project."
}

$envVars = @(
  "GOOGLE_CLOUD_PROJECT=$ProjectId",
  "STREAMING_ENABLED=true",
  "ZB_API_STRICT_AUTH=true",
  "SESSION_COOKIE_SECURE=true",
  "OPENAI_API_KEY_SECRET_NAME=zb-openai-api-key",
  "FLASK_SECRET_KEY_SECRET_NAME=zb-flask-secret-key",
  "ACTION_API_TOKEN_SECRET_NAME=zb-action-api-token",
  "OPENAI_LIVE_MODEL=gpt-5.4-mini",
  "OPENAI_JUDGE_MODEL=gpt-5.4",
  "OPENAI_POST_TRAINING_MODEL=gpt-4.1",
  "OPENAI_ENABLE_FUNCTION_TOOLS=true",
  "OPENAI_PROMPT_CACHE_RETENTION=24h"
) -join ","

& gcloud run deploy $ServiceName `
  --source . `
  --region $Region `
  --quiet `
  --allow-unauthenticated `
  --service-account $ServiceAccount `
  --concurrency 8 `
  --min-instances 1 `
  --set-env-vars $envVars
if ($LASTEXITCODE -ne 0) {
  throw "Cloud Run deploy failed."
}

$url = (gcloud run services describe $ServiceName --region $Region --format "value(status.url)").Trim()
if (-not $url) {
  throw "Unable to fetch Cloud Run service URL."
}

& gcloud run services update $ServiceName `
  --region $Region `
  --quiet `
  --update-env-vars "WEB_APP_ORIGIN=$url,CHAT_API_BASE_URL=$url"
if ($LASTEXITCODE -ne 0) {
  throw "Cloud Run origin update failed."
}

Write-Host "Cloud Run service URL: $url"
