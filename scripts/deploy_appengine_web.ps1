param(
  [string]$ProjectId = "zenbot-434517",
  [string]$ChatApiBaseUrl
)

$ErrorActionPreference = "Stop"

if (-not $ChatApiBaseUrl) {
  throw "Provide -ChatApiBaseUrl from deployed Cloud Run chat service."
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
