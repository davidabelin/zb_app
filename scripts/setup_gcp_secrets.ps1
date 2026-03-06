param(
  [string]$ProjectId = "zenbot-434517",
  [string]$ServiceAccount = "zenbot-sa@zenbot-434517.iam.gserviceaccount.com",
  [string]$Account = "",
  [switch]$SkipPrompts,
  [switch]$SkipIamBinding
)

$ErrorActionPreference = "Stop"

function Invoke-Gcloud {
  param([Parameter(Mandatory = $true)][string[]]$GcloudArgs)
  & gcloud @GcloudArgs
  if ($LASTEXITCODE -ne 0) {
    throw "gcloud command failed: gcloud $($GcloudArgs -join ' ')"
  }
}

if ($Account) {
  Invoke-Gcloud @("config", "set", "account", $Account)
}

$activeAccount = (& gcloud config get-value account 2>$null).Trim()
if (-not $SkipIamBinding -and $activeAccount -like "*gserviceaccount.com") {
  throw @"
Active gcloud account is a service account ($activeAccount), which commonly lacks permission to set IAM policy bindings.
Use a user account with sufficient IAM rights (e.g. Owner, Secret Manager Admin), or rerun with -SkipIamBinding if bindings are already in place.
"@
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
    $tmp = New-TemporaryFile
    try {
      $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
      [System.IO.File]::WriteAllText($tmp, $plain, $utf8NoBom)
      & gcloud secrets versions add $name --data-file=$tmp *> $null
      if ($LASTEXITCODE -ne 0) {
        throw "Failed to add secret version for $name"
      }
    } finally {
      Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
  }

  if (-not $SkipIamBinding) {
    & gcloud secrets add-iam-policy-binding $name `
      --member="serviceAccount:$ServiceAccount" `
      --role="roles/secretmanager.secretAccessor"
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to add IAM binding for $name"
    }
  }
}

Write-Host "Secret setup completed."
