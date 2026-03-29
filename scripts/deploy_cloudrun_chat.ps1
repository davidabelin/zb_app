<#
.SYNOPSIS
Backward-compatible wrapper for the Cloud Run-only Zenbot deploy.

.DESCRIPTION
The v3 runtime no longer deploys a separate App Engine web shell. This script
remains as a stable alias and forwards to `deploy_cloudrun_service.cmd`.
#>

param(
  [string]$ProjectId = "zenbot-434517",
  [string]$Region = "us-central1",
  [string]$ServiceName = "zb-chat-api",
  [string]$ServiceAccount = ""
)

if (-not $ServiceAccount) {
  $ServiceAccount = "zenbot-sa@$ProjectId.iam.gserviceaccount.com"
}

$script = Join-Path $PSScriptRoot "deploy_cloudrun_service.cmd"
& cmd /c `"$script`" $ProjectId $Region $ServiceName $ServiceAccount
exit $LASTEXITCODE
