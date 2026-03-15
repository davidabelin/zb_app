<#
.SYNOPSIS
Deploy the authenticated chat/API service to Cloud Run.

.DESCRIPTION
Builds the current `zb_app` source with Cloud Build, deploys it as the
`zb-chat-api` Cloud Run service, and prints the resolved service URL. This is
the runtime that serves `/chat`, `/save_chat`, and `/zb_api/*` for the hybrid
App Engine + Cloud Run topology.
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

& gcloud run deploy $ServiceName `
  --source . `
  --region $Region `
  --quiet `
  --allow-unauthenticated `
  --service-account $ServiceAccount `
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$ProjectId,STREAMING_ENABLED=true,ZB_API_STRICT_AUTH=true,SESSION_COOKIE_SECURE=true,WEB_APP_ORIGIN=https://zenbot-434517.uw.r.appspot.com,OPENAI_API_KEY_SECRET_NAME=zb-openai-api-key,FLASK_SECRET_KEY_SECRET_NAME=zb-flask-secret-key,ACTION_API_TOKEN_SECRET_NAME=zb-action-api-token"
if ($LASTEXITCODE -ne 0) {
  throw "Cloud Run deploy failed."
}

$url = gcloud run services describe $ServiceName --region $Region --format="value(status.url)"
if (-not $url) {
  throw "Unable to fetch Cloud Run service URL."
}
Write-Host "Cloud Run chat service URL: $url"
