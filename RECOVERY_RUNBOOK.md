# ZB App Recovery Runbook

Use this file as the short operational sequence. For the full system map and
maintainer guidance, see:

- [`README.md`](README.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)

## 1) Bootstrap or Rotate Secrets

```powershell
.\scripts\setup_gcp_secrets.ps1
```

This script enables required APIs, creates required secrets when missing, adds
interactive secret versions, and grants Secret Manager access to the runtime
service account.

## 2) Deploy Cloud Run Chat/API

```powershell
.\scripts\deploy_cloudrun_chat.ps1
```

Record the printed Cloud Run URL. The App Engine deploy script can resolve it
automatically, but keeping the URL handy makes smoke tests simpler.

## 3) Deploy App Engine Web Shell

```powershell
.\scripts\deploy_appengine_web.ps1
```

If automatic Cloud Run URL lookup fails, pass it explicitly:

```powershell
.\scripts\deploy_appengine_web.ps1 -ChatApiBaseUrl "https://YOUR-CLOUD-RUN-URL"
```

## 4) Verify Deploy Payload Hygiene

```powershell
gcloud meta list-files-for-upload
```

Expected: no `venv/` files and no `config/` payloads. The deployable source set
should match the `.gcloudignore` and `.gitignore` intent.

## 5) Smoke-Test the Live Contract

Minimum checks after deploy:

```powershell
python -m pytest -q
python -m flake8
python -m mypy .
```

Then test the live routes that matter most:

- `/zb_api/chat`
- `/zb_api/load_memory_logbook`
- `/zb_api/load_memory_entry/<serial_number>`
- `/zb_api/session-evaluations/summary`
- `/zb_api/session-evaluations/next?after=-1&evaluation=unreviewed`
- `/admin/login` -> `/admin/conversations`

## 6) Auth Contract Reminder

All `/zb_api/*` routes and `/appendMemoryLogbookEntry` require the action/admin
token in the `Authorization` header. Preferred wire format:

```text
Authorization: Bearer <ACTION_API_TOKEN>
```

The live service also accepts the raw token value alone in `Authorization` for
GPT Actions compatibility.
