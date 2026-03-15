<#
.SYNOPSIS
Print the current admin/API token from Secret Manager.

.DESCRIPTION
Fetches the latest version of `zb-action-api-token` so maintainers can sign in
to browser admin routes or configure GPT Actions authentication.
#>

param(
  [string]$ProjectId = "zenbot-434517",
  [string]$Account = "davidabelin96@gmail.com",
  [string]$SecretName = "zb-action-api-token"
)

$ErrorActionPreference = "Stop"

& gcloud --account=$Account secrets versions access latest `
  --secret=$SecretName `
  --project=$ProjectId

if ($LASTEXITCODE -ne 0) {
  throw "Failed to read secret '$SecretName' from project '$ProjectId'."
}
