<#
.SYNOPSIS
Backward-compatible wrapper for the Cloud Run-only Zenbot deploy.

.DESCRIPTION
The v3 runtime no longer deploys a separate App Engine web shell. This script
remains as a stable alias and forwards to `deploy_cloudrun_service.ps1`.
#>

param(
  [string]$ProjectId = "zenbot-434517",
  [string]$Region = "us-central1",
  [string]$ServiceName = "zb-chat-api",
  [string]$ServiceAccount = "zenbot-sa@zenbot-434517.iam.gserviceaccount.com"
)

& "$PSScriptRoot\deploy_cloudrun_service.ps1" `
  -ProjectId $ProjectId `
  -Region $Region `
  -ServiceName $ServiceName `
  -ServiceAccount $ServiceAccount
