<#
.SYNOPSIS
PowerShell wrapper for the cmd-first Zenbot v3 Cloud Run deploy.

.DESCRIPTION
The primary deploy implementation now lives in `deploy_cloudrun_service.cmd` so
the deployment flow is easy to run from a normal Windows cmd shell. This
wrapper keeps the old PowerShell entrypoint working.
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
