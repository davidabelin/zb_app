param(
  [string]$ProjectId = "zenbot-434517",
  [string]$ServiceAccount = "zenbot-sa@zenbot-434517.iam.gserviceaccount.com",
  [string]$Account = "",
  [switch]$SkipPrompts
)

$ErrorActionPreference = "Stop"

function Invoke-Gcloud {
  param([string[]]$Args)
  & gcloud @Args
  if ($LASTEXITCODE -ne 0) {
    throw "gcloud command failed: gcloud $($Args -join ' ')"
  }
}

if ($Account) {
  Invoke-Gcloud @("config", "set", "account", $Account)
}
Invoke-Gcloud @("config", "set", "project", $ProjectId)
Invoke-Gcloud @(
  "services", "enable",
  "secretmanager.googleapis.com",
  "run.googleapis.com",
  "cloudbuild.googleapis.com",
  "artifactregistry.googleapis.com"
)

$secrets = @(
  "zb-openai-api-key",
  "zb-flask-secret-key",
  "zb-action-api-token"
)

foreach ($name in $secrets) {
  & gcloud secrets describe $name *> $null
  if ($LASTEXITCODE -ne 0) {
    Invoke-Gcloud @("secrets", "create", $name, "--replication-policy=automatic")
  }

  if (-not $SkipPrompts) {
    Write-Host "Enter value for secret '$name' (input hidden):"
    $secret = Read-Host -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
      $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    $plain | gcloud secrets versions add $name --data-file=- *> $null
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to add secret version for $name"
    }
  }

  & gcloud secrets add-iam-policy-binding $name `
    --member="serviceAccount:$ServiceAccount" `
    --role="roles/secretmanager.secretAccessor"
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to add IAM binding for $name"
  }
}

Write-Host "Secret setup completed."
