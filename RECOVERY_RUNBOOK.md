# ZB App Recovery Runbook

Use this file as the short operational sequence. For the fuller system map and
maintainer guidance, see:

- [`README.md`](README.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)
- [`scripts/WHAT_TO_RUN.md`](scripts/WHAT_TO_RUN.md)

## 1) Bootstrap or Rotate Secrets

```cmd
scripts\rotate_keys.bat
```

## 2) Deploy the App Engine Service

```cmd
scripts\deploy.bat
```

The script deploys `app.yaml` to the single public App Engine service.

## 3) Optional: Refresh Docs/Search Context

If File Search is enabled for the runtime, refresh the curated context set:

```cmd
scripts\refresh_context.bat --create
```

Record the returned store ID and set `OPENAI_VECTOR_STORE_IDS`
accordingly.

## 4) Verify Deploy Payload Hygiene

```cmd
gcloud meta list-files-for-upload
```

Expected: no `venv/` payloads and no local `config/` artifacts.

## 5) Run the Quality Gates

```cmd
python -m pytest -q
python -m flake8
python -m mypy .
```

## 6) Smoke-Test the Live Contract

Minimum checks after deploy:

- `/zb_api/chat`
- `/zb_api/load_memory_logbook`
- `/zb_api/load_memory_entry/<serial_number>`
- `/zb_api/session-evaluations/summary`
- `/zb_api/session-evaluations/next?after=-1&evaluation=needs_cm_review`
- `/admin/login` -> `/admin/conversations`

If background critic mode is enabled, confirm the save flow still succeeds even
when the critic request is unavailable.

## 7) Auth Contract Reminder

All `/zb_api/*` routes require the action/admin token in the `Authorization`
header. Preferred format:

```text
Authorization: Bearer <ACTION_API_TOKEN>
```

The runtime also accepts the raw token value alone in `Authorization` for GPT
Actions compatibility.
