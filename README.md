# ZB App

Current app/repo release: `v3.2.6`

`zb_app` is the web, API, and operator surface for Zenbot. In v3 it is a
single App Engine application with an OpenAI-native runtime underneath:

- browser chat and reference pages
- authenticated `zb_api` routes used by GPT Actions and other clients
- review/admin pages
- public, anonymized [Sampled Sessions](#sampled-sessions) from the full archive
- Responses API chat runtime with prompt caching and provider conversation state
- optional File Search, strict function tools, and background session critic

## What Lives Here

- `main.py`: Flask application composition and the stable App Engine entry point
- `app_support.py`: shared HTTP contracts, auth, CORS, request guards, and errors
- `web_routes.py`: public pages and browser-session chat routes
- `zb_api.py`: authenticated JSON endpoints used by GPT Actions and operators
- `zb_services/`: shared conversation, memory, koan, and review workflows
- `zb_mcp/`: independent private MCP transport, OAuth, and durable write receipts
- `admin_routes.py`: browser-facing admin and review workflows
- `utilities.py`: hot-state storage, Responses API adapter, koan lookup, GCS
  archive helpers, memory logbook helpers, and tool handlers
- `contracts.py`: typed runtime contracts and tool schemas
- `config.py`: runtime/env/secret resolution
- `models.py`: active model registry plus legacy fine-tune catalog
- `scripts/`: deploy, vector-store sync, and tooling export helpers
- `templates/` and `static/`: browser UI assets

## Runtime Topology

The v3.2 runtime is App Engine-only:

- one public App Engine service serves browser routes, SSE chat, admin pages,
  and authenticated API routes from the same host
- an independently deployed private `mcp` service exposes the shared API workflows
  through authenticated Streamable HTTP; see [MCP_SETUP.md](MCP_SETUP.md)
- `CHAT_ALLOWED_ORIGINS` remains available only for explicit extra browser
  callers; it is no longer used for a split Zenbot shell/API topology
- active conversation state uses Redis/Memorystore when `REDIS_URL` is set,
  then falls back to Firestore, then local memory for development
- archived transcripts and generated review/training artifacts stay in GCS
- review manifests and memory workflows remain GCS-backed today, with the new
  runtime ready for stricter structured backends later

## Sampled Sessions

`/sampled-sessions` is public and displays five randomly drawn, distinct archived
Dokusan dialogues. Every recorded System, Student, and Mumonbot message is shown,
including introductions and case readings. Names identified from student
metadata, legacy session identifiers, and explicit student self-introductions
are replaced with `Student`; other recorded text is preserved, including legacy
text-block content. The page sends no archive identifiers, student metadata,
review data, or chat-settings scripts to the browser.

The sampler reads current `zbchats/` objects from `BUCKET_NAME`. If configured,
it also reads `sampled_sessions/historical/` from
`SAMPLED_HISTORICAL_BUCKET_NAME` (unset by default).
Historical originals require a private bucket; a prefix inherits its bucket's
access policy. The deployed service account needs `roles/storage.objectViewer`
on the historical bucket. Do not grant public storage access for this page: the
app reads originals privately and renders the anonymized view.

The cache lasts five minutes per worker. Refreshes reuse unchanged object
generations, add new saves, drop deleted objects, and deduplicate identical
dialogues without consulting review ratings. An unreadable recording is skipped
as a whole; a storage failure renders a temporary-unavailability page instead of
serving a stale sample. Browser/proxy page caching is disabled.

The October 1, 2026 live archive check found 484 readable cloud recordings,
including all 455 unique dialogues in the 472 historical files and local cache
copies. No import or additional bucket is required for that collection.

For future historical files absent from the current cloud archive, first create
a private destination and grant the web service account read access:

```cmd
gcloud storage buckets create gs://zenbot-434517-sampled-sessions --project=zenbot-434517 --location=us-east1 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets add-iam-policy-binding gs://zenbot-434517-sampled-sessions --member=serviceAccount:zenbot-sa@zenbot-434517.iam.gserviceaccount.com --role=roles/storage.objectViewer
```

Set `SAMPLED_HISTORICAL_BUCKET_NAME=zenbot-434517-sampled-sessions` in local
configuration and the web `app.yaml` environment before deploying. Then, from
`zb_app`, preview and import the historical collection using
application-default credentials (no credential values on the command line):

```cmd
venv\Scripts\python.exe scripts\import_sampled_sessions.py
venv\Scripts\python.exe scripts\import_sampled_sessions.py --apply
```

The importer reads `collected_sessions/local`, `web`, and `use`, plus the local
`zb_app/config/zbchats` cache. Use `--repo-root`, `--project`, or `--bucket` for a
different checkout or private destination; keep the runtime bucket setting in
agreement. The importer requires uniform bucket-level access and enforced
public access prevention, and refuses public bucket IAM grants.
Reports show scanned, invalid, duplicate, existing, pending, and
uploaded counts. Hash-addressed conditional uploads make repeat runs safe.
Original files, current cloud archives, and administrative review data are not
modified. Imported object metadata retains the recording filename for dates;
it is never sent to the browser. Missing dates/models are labelled unavailable,
and times without a recorded timezone are displayed without an assumed one.

Newly saved sessions automatically join the public pool after cache refresh.
Name detection is based on recorded identities and explicit introductions; it
does not attempt to identify every person mentioned in unrestricted prose.

## OpenAI-Native Runtime

The live Mumonbot path now uses the current OpenAI Python SDK and the
Responses API rather than Chat Completions.

Implemented v3 runtime pieces:

- deterministic live botling defaults sourced from `models.py`, including the
  generic `gpt-5.5` baseline and the retained fine-tuned botlings
- `gpt-5.6-sol` for asynchronous critic/judging, `gpt-4.1` reserved for
  post-training work
- prompt caching via stable `prompt_cache_key` values
- Mumon exemplar context for non-fine-tuned live models: the Responses
  `instructions` block for generic models such as `gpt-5.5` opens with the
  trainset03a dokusan transcripts rendered as `Student:` / `Mumon:` lines
  (`static/mumon_exemplars.jsonl`, a snapshot of
  `../training/trainset03/trainset03a.jsonl` to re-copy when set05 lands),
  plus reading instructions and the `(bows)` closing rule; fine-tuned
  botlings get the closing rule only. The block never enters the stored
  transcript, archives, or training exports. Controlled by
  `MUMON_EXEMPLARS_ENABLED` and `MUMON_EXEMPLARS_PATH`
- the startup system prompt is the preset `description` followed by its
  `instruction`, matching the system prompt the fine-tunes were trained with
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

### Background critic

The optional session critic is a separate `gpt-5.6-sol` Responses API job. It
submits after an archived chat is saved, uses background mode so it never holds
up the browser/API save response, and returns a `response_id` when queued.
Poll that ID with the Responses API if an operator needs the completed
structured assessment; Zenbot does not currently write a completed critic
result back into the review manifest automatically.

Critic submission requires both `OPENAI_ENABLE_BACKGROUND_CRITIC=true` for the
deployment and `enable_background_critic=true` in the session's locked
settings. The browser session settings panel and API session settings payload
expose the per-session choice. `OPENAI_JUDGE_MODEL` defaults to `gpt-5.6-sol`;
leave its `judge` profile at medium reasoning for the initial baseline, then
evaluate a lower effort against representative archived sessions before tuning.
`OPENAI_PROMPT_CACHE_RETENTION` supplies the critic request's cache TTL.

For internal runtime diagnostics, the Responses function-tool catalog can be
exported to `generated/openai_response_tools.json` with:

```cmd
python scripts/export_openai_tool_manifest.py
```

Inspect the live botling presets, model capabilities, and resolved session
settings from the shell with:

```cmd
python scripts/inspect_botlings.py --help
python scripts/inspect_botlings.py
python scripts/inspect_botlings.py --preset fierce_barrier --model set03-bs2lr05e7 --reasoning-effort medium
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

Run app commands from this `zb_app` directory. It is the active Git root for
the deployed Flask/App Engine service; the parent `zenbot` directory contains
project notes, training data, and historical assets.

## Key Env Vars

- `OPENAI_API_KEY_SECRET_NAME`
- `FLASK_SECRET_KEY_SECRET_NAME`
- `ACTION_API_TOKEN_SECRET_NAME`
- `OPENAI_LIVE_MODEL`
- `OPENAI_JUDGE_MODEL`
- `OPENAI_ENABLE_FILE_SEARCH`
- `OPENAI_VECTOR_STORE_IDS`
- `OPENAI_ENABLE_BACKGROUND_CRITIC`
- `OPENAI_PROMPT_CACHE_RETENTION`
- `MUMON_EXEMPLARS_ENABLED`
- `MUMON_EXEMPLARS_PATH`
- `REDIS_URL`
- `SESSION_TTL_SECONDS`
- `STREAMING_ENABLED`
- `WEB_APP_ORIGIN`
- `CHAT_ALLOWED_ORIGINS`

## Private MCP Service

The independent `mcp` App Engine service exposes the existing API workflows as
typed MCP tools using shared Python services. It adds Google account authorization,
encrypted OAuth state, durable write receipts, and an explicit logbook-replacement
tool. See [MCP_SETUP.md](MCP_SETUP.md) for setup, testing, deployment, and recovery.

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

From the `zb_app` directory, use the scripts that exist in `scripts\`:

1. Code deploy to App Engine: `scripts\deploy.bat`
2. Docs/settings/search-context update: `scripts\update_context.bat`
3. First-time search-context creation: `scripts\update_context.bat --create`
4. Rotate secrets only: `scripts\rotate_keys.bat`
5. Print the admin/API token: `scripts\get_token.bat`

If you want the short explanation, read `scripts\WHAT_TO_RUN.md`.

## Validation

```cmd
python -m pytest -q
python -m flake8
python -m mypy .
```
