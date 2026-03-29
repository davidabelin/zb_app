<#
.SYNOPSIS
Deprecated App Engine deploy entrypoint.

.DESCRIPTION
Zenbot v3 is Cloud Run-only. The browser shell and API now deploy together via
`deploy_cloudrun_service.ps1`.
#>

param()

Write-Error "App Engine deployment is retired in v3. Use .\scripts\deploy_cloudrun_service.ps1 instead."
exit 1
