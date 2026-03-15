# ZB App

Current app/repo release: `v2.0.0`

`zb_app` is the web application layer of the larger Zenbot project. It serves
the browser-facing shell, the authenticated JSON API used by GPT Actions and
other tools, the admin/review tooling, and the operational scripts that deploy
the hybrid App Engine + Cloud Run runtime.

## What Lives Here

- `main.py`: Flask routes for the web shell, authenticated API, admin pages, and
  training-review workflow
- `utilities.py`: conversation state, koan loading, OpenAI calls, GCS/Firestore
  persistence, memory-logbook normalization, and archive helpers
- `config.py`: runtime/env/secret resolution
- `models.py`: model registries and training-loss metadata
- `chat_api.py`: Cloud Run entrypoint shim
- `scripts/`: deployment and secret-management entrypoints
- `templates/` and `static/`: browser UI assets

## How It Fits Into Zenbot

`zb_app` is not the whole Zenbot repo. It directly depends on a few sibling
areas:

- `../zenbot_knowledge/action_schemas.yaml`: GPT Actions/OpenAPI contract
- `../zenbot_knowledge/*`: knowledge and reference material maintainers consult
- `../training/trainset04/review.csv`: local admin review queue
- `static/mmnk.json`: bundled koan source used at runtime

It also depends on external services:

- OpenAI API
- Google Secret Manager
- Google Cloud Firestore
- Google Cloud Storage
- App Engine
- Cloud Run

## Runtime Topology

The app is deployed in a hybrid shape:

- App Engine serves the web shell, templates, static assets, and browser-facing
  routes
- Cloud Run serves the chat/API workload
- both runtimes read secrets through Secret Manager and share backing data in
  Firestore and Cloud Storage

This separation exists because Cloud Run handles the chat/API workload and
streaming behavior more reliably than App Engine Standard.

## Local Setup

1. Create or activate a Python environment.
2. Install runtime dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Install dev tooling when needed:

   ```powershell
   pip install -r requirements-dev.txt
   ```

4. For local-only development, provide env values in `.env` or `config/.env`.
5. Run the app from `zb_app`:

   ```powershell
   python main.py
   ```

## Secrets and Env

In cloud runtimes, `zb_app` prefers Secret Manager and falls back to plain
environment variables only when needed.

Key secret indirection env vars:

- `OPENAI_API_KEY_SECRET_NAME`
- `FLASK_SECRET_KEY_SECRET_NAME`
- `ACTION_API_TOKEN_SECRET_NAME`

Key non-secret env vars:

- `GOOGLE_CLOUD_PROJECT`
- `CHAT_API_BASE_URL`
- `WEB_APP_ORIGIN`
- `STREAMING_ENABLED`
- `ZB_API_STRICT_AUTH`
- `SESSION_COOKIE_SECURE`

## Key Workflows

- Browser chat: `/chatter` -> `/chat` -> Firestore live state -> `/save_chat` ->
  GCS archive
- API chat: `/zb_api/chat` and `/zb_api/chat_case/<case_id>` -> Firestore live
  state -> `/zb_api/save_chat`
- Memory selection: `/zb_api/load_memory_logbook` -> `/zb_api/load_memory_entry/<serial_number>`
- Admin archive browsing: `/admin/conversations`
- Training review: `/admin/review` backed by `../training/trainset04/review.csv`

## Documentation Map

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): subsystem map, request/data
  flows, storage layout, and sibling-directory dependencies
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md): how to modify routes,
  schema, config, deployment, and troubleshooting safely
- [`RECOVERY_RUNBOOK.md`](RECOVERY_RUNBOOK.md): concise operational runbook for
  secrets, deploy, and verification
- [`CHANGELOG.md`](CHANGELOG.md): release history, starting with `v2.0.0`

## Validation

The normal maintenance checks are:

```powershell
python -m pytest -q
python -m flake8
python -m mypy .
```

The test suite includes a doc-coverage check for the core Python modules so
future edits do not immediately erode the documentation pass introduced in
`v2.0.0`.
