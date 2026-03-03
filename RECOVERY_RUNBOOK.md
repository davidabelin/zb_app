# ZB App Recovery Runbook

## 1) Rotate Secrets and Configure Access
Run:

```powershell
.\scripts\setup_gcp_secrets.ps1
```

This enables required APIs, creates required secrets, adds secret versions (interactive), and grants Secret Manager access to the runtime service account.

## 2) Deploy Chat/API Service to Cloud Run
Run:

```powershell
.\scripts\deploy_cloudrun_chat.ps1
```

Copy the printed Cloud Run URL.

## 3) Deploy Web Shell to App Engine
Run:

```powershell
.\scripts\deploy_appengine_web.ps1 -ChatApiBaseUrl "https://YOUR-CLOUD-RUN-URL"
```

## 4) Verify Deploy Payload Hygiene
Run:

```powershell
gcloud meta list-files-for-upload
```

Expected: no `venv/` and no `config/` files.

## 5) API Auth Contract
All `/zb_api/*` routes and `/appendMemoryLogbookEntry` require:

```text
Authorization: Bearer <ACTION_API_TOKEN>
```
