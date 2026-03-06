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
