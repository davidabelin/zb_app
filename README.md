# ZB App

Current app/repo release: `v3.0.0`

`zb_app` is the web, API, and operator surface for Zenbot. In v3 it is a
single Cloud Run application with an OpenAI-native runtime underneath:

- browser chat and reference pages
- authenticated `zb_api` routes used by GPT Actions and other clients
- review/admin pages
- Responses API chat runtime with prompt caching and provider conversation state
- optional File Search, strict function tools, and background session critic

## What Lives Here

- `main.py`: Flask routes for browser, API, admin, and review flows
- `utilities.py`: hot-state storage, Responses API adapter, koan lookup, GCS
  archive helpers, memory logbook helpers, and tool handlers
- `contracts.py`: typed runtime contracts and tool schemas
- `config.py`: runtime/env/secret resolution
- `models.py`: active model registry plus legacy fine-tune catalog
- `scripts/`: deploy, vector-store sync, and tooling export helpers
- `templates/` and `static/`: browser UI assets

## Runtime Topology

The v3 runtime is Cloud Run-only:

- one public Cloud Run service serves browser routes, SSE chat, admin pages,
  and authenticated API routes
- if a browser shell is still served from another origin during migration,
  allow it with `CHAT_ALLOWED_ORIGINS`
- active conversation state uses Redis/Memorystore when `REDIS_URL` is set,
  then falls back to Firestore, then local memory for development
- archived transcripts and generated review/training artifacts stay in GCS
- review manifests and memory workflows remain GCS-backed today, with the new
  runtime ready for stricter structured backends later

## OpenAI-Native Runtime

The live Mumonbot path now uses the current OpenAI Python SDK and the
Responses API rather than Chat Completions.

Implemented v3 runtime pieces:

- deterministic live model defaults: `gpt-5.4-mini` for chat, `gpt-5.4` for
  critic/judging, `gpt-4.1` reserved for post-training work
- prompt caching via stable `prompt_cache_key` values
- provider-side conversation continuation via `previous_response_id`
- optional File Search through configured vector stores
- strict internal function tools:
  - `load_case_context`
  - `search_exemplars`
  - `load_memory_summaries`
  - `load_memory_entry`
  - `save_memory_candidate`
  - `archive_session`
  - `enqueue_review`
  - `report_ui_status`
- optional background session critic submissions

The tool catalog can be exported with:

```cmd
python scripts/export_openai_tool_manifest.py
```

The default vector-store sync helper is:

```cmd
python scripts/sync_openai_vector_store.py --create
```

## Local Setup

1. Create or activate a Python environment.
2. Install runtime dependencies:

   ```cmd
   pip install -r requirements.txt
   ```

3. Install dev tooling when needed:

   ```cmd
   pip install -r requirements-dev.txt
   ```

4. Copy `.env.example` to `.env` and fill in the values you actually need.
5. Run the app from `zb_app`:

   ```cmd
   python main.py
   ```

## Key Env Vars

- `OPENAI_API_KEY_SECRET_NAME`
- `FLASK_SECRET_KEY_SECRET_NAME`
- `ACTION_API_TOKEN_SECRET_NAME`
- `OPENAI_LIVE_MODEL`
- `OPENAI_JUDGE_MODEL`
- `OPENAI_ENABLE_FILE_SEARCH`
- `OPENAI_VECTOR_STORE_IDS`
- `OPENAI_ENABLE_BACKGROUND_CRITIC`
- `REDIS_URL`
- `SESSION_TTL_SECONDS`
- `STREAMING_ENABLED`
- `CHAT_API_BASE_URL`
- `WEB_APP_ORIGIN`
- `CHAT_ALLOWED_ORIGINS`

## Key Workflows

- Browser chat: `/chatter` -> `/chat` -> hot session state -> `/save_chat` ->
  GCS archive + review manifest upsert + optional background critic
- API chat: `/zb_api/chat` and `/zb_api/chat_case/<case_id>` ->
  same live runtime, no browser cookies required
- Memory selection: `/zb_api/load_memory_logbook` ->
  `/zb_api/load_memory_entry/<serial_number>`
- Review/admin: `/admin/conversations` and `/review`

## Documentation Map

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DEVELOPER_GUIDE.md`](docs/DEVELOPER_GUIDE.md)
- [`RECOVERY_RUNBOOK.md`](RECOVERY_RUNBOOK.md)
- [`scripts/WHAT_TO_RUN.md`](scripts/WHAT_TO_RUN.md)
- [`CHANGELOG.md`](CHANGELOG.md)

## Deploying v3

Use the simple scripts in `scripts\`:

1. First time only: `scripts\one_time_only_DELETE_ME.bat`
2. Code deploy: `scripts\deploy.bat`
3. Docs/settings/search-context update: `scripts\update.bat`
4. Rotate secrets only: `scripts\rotate_keys.bat`
5. Same as update, explicit name: `scripts\refresh_context.bat`
6. Print the admin/API token: `scripts\get_token.bat`

If you want the short explanation, read `scripts\WHAT_TO_RUN.md`.

## Validation

```cmd
python -m pytest -q
python -m flake8
python -m mypy .
```
