<#
.SYNOPSIS
Deploy the browser-facing web shell to App Engine.

.DESCRIPTION
Resolves or accepts the Cloud Run chat/API base URL, patches `app.yaml` for the
deploy, and then deploys the App Engine service that renders templates, static
assets, and the browser-facing shell around the chat/API backend.
#>

param(
  [string]$ProjectId = "zenbot-434517",
  [string]$ChatApiBaseUrl,
  [string]$ChatServiceName = "zb-chat-api",
  [string]$Region = "us-central1"
)

$ErrorActionPreference = "Stop"

if (-not $ChatApiBaseUrl) {
  $ChatApiBaseUrl = (& gcloud run services describe $ChatServiceName --region $Region --project $ProjectId --format "value(status.url)" 2>$null).Trim()
  if (-not $ChatApiBaseUrl) {
    throw "Could not resolve Cloud Run URL. Provide -ChatApiBaseUrl explicitly."
  }
  Write-Host "Resolved Chat API URL from Cloud Run: $ChatApiBaseUrl"
}

gcloud config set project $ProjectId
if ($LASTEXITCODE -ne 0) {
  throw "Failed to set gcloud project."
}

# Patch app.yaml CHAT_API_BASE_URL in-memory for deploy.
$content = Get-Content app.yaml -Raw
$updated = $content -replace "CHAT_API_BASE_URL: .*", "CHAT_API_BASE_URL: $ChatApiBaseUrl"
Set-Content app.yaml -Value $updated -NoNewline

gcloud app deploy app.yaml --quiet
if ($LASTEXITCODE -ne 0) {
  throw "App Engine deploy failed."
}
Write-Host "App Engine deploy complete."
