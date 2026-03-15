# ZB App Developer Guide

## Modify Routes Safely

### When editing `main.py`
- keep request validation close to the route boundary
- keep storage/model logic in `utilities.py` unless the behavior is purely HTTP
- document auth requirements and response shape changes immediately
- if a route is used by GPT Actions, update `../zenbot_knowledge/action_schemas.yaml`
  in the same change

### When editing `utilities.py`
- treat it as the side-effect layer
- document storage touched, cloud/local behavior, and failure modes
- preserve the distinction between:
  - Firestore live state
  - GCS archived transcripts
  - canonical memory logbook storage

## Schema Alignment Rules

- runtime truth wins; the schema must describe actual request/response behavior
- keep the OpenAPI version independent from the app release version
- for GPT-facing memory access, prefer:
  1. `/zb_api/load_memory_logbook`
  2. `/zb_api/load_memory_entry/<serial_number>`
- do not expose the full memory-logbook route to GPT Actions unless payload size
  constraints change materially
- preserve the dokusan verbatim-channeling instruction in schema text

## Model and Config Notes

- model registry lives in `models.py`
- `config.py` narrows the active Zenbot model list to the latest entries plus a
  fallback base model
- each new conversation gets a randomized model/profile pair that is stored in
  metadata and then reused for continuation turns
- secret resolution order is:
  1. Secret Manager
  2. plain env fallback
  3. local dotenv only outside managed runtimes

## Review Workflow

The training-review admin flow is tied directly to
`../training/trainset04/review.csv`.

Relevant assumptions:
- `review.csv` must include `id` and `keep`
- each row must include `source_path`
- `source_path` must resolve under `../collected_sessions`
- the admin UI only edits the `keep` column; it does not regenerate the review
  dataset

## Deploy and Verify

Use the scripts in `scripts/`:

- `setup_gcp_secrets.ps1`
- `deploy_cloudrun_chat.ps1`
- `deploy_appengine_web.ps1`
- `get_admin_token.ps1`

Use [`RECOVERY_RUNBOOK.md`](../RECOVERY_RUNBOOK.md) for the short operational
sequence.

After deploy:
- verify `gcloud meta list-files-for-upload` excludes `venv/` and `config/`
- smoke-test `/zb_api/chat`
- smoke-test memory index and single-entry retrieval
- confirm admin login and archive listing still work

## Troubleshooting

### Chat works locally but not in cloud
- inspect secret names and active secret versions
- confirm Cloud Run/App Engine env vars match the expected hybrid topology
- confirm Firestore and GCS clients initialize for the target project

### GPT Action fails but direct API works
- check schema/runtime mismatch first
- check auth mode in Actions UI
- confirm the route is still represented accurately in `action_schemas.yaml`

### Memory lookups fail
- use the index endpoint first to discover current live serials
- remember the index is newest-first, while the full logbook remains stored in
  chronological order

### Review admin fails
- confirm `../training/trainset04/review.csv` exists
- confirm the CSV columns still include `id`, `keep`, and `source_path`
- confirm referenced session files still exist under `../collected_sessions`
