# ZB App Developer Guide

## Route Safety Rules

### When editing `main.py`

- keep validation and auth close to the route boundary
- keep OpenAI/storage side effects in `utilities.py`
- preserve existing browser and `zb_api` route names unless a breaking change is
  explicitly intended
- if a GPT-facing route changes materially, update
  `../zenbot_knowledge/action_schemas.yaml` in the same change

### When editing `utilities.py`

- treat it as the side-effect layer
- document storage touched, cloud/local behavior, and failure modes
- preserve the distinction between:
  - hot session state
  - archived transcripts/review artifacts
  - canonical memory logbook state

## Responses Runtime Rules

- use the OpenAI Responses API, not Chat Completions
- keep browser streaming on the low-latency path
- use `previous_response_id` when available, but clear it and retry if the
  provider rejects stale session context
- keep prompt-cache keys stable by persona + case + mode
- keep web search off for dokusan/koan dialogue
- treat strict function tools as sync/background helpers, not an excuse to add
  a slow multi-agent chain to the hot path

## Tooling and Schema Alignment

- the live tool catalog is exported from `utilities.response_tool_definitions()`
- `scripts/export_openai_tool_manifest.py` writes the current catalog to
  `../zenbot_knowledge/openai_response_tools.json`
- `action_schemas.yaml` must reflect the live runtime, including
  `needs_cm_review` in session-evaluation filters
- prefer typed contracts from `contracts.py` over new free-form dict shapes

## Storage Notes

- set `REDIS_URL` to move active session state to Redis/Memorystore
- if `REDIS_URL` is unset, the runtime falls back to Firestore, then local memory
- archived transcripts and review manifests remain GCS-backed
- queued memory candidates and review requests also land in GCS when available

## Vector Store Workflow

Use the bundled sync helper:

```cmd
python scripts/sync_openai_vector_store.py --create
```

By default it uploads:

- `static/mmnk.json`
- runtime/operator docs
- `action_schemas.yaml`
- the current project todo and `trainset04` curation notes

This is deliberately narrow. Bulk training/session corpus sync should be handled
as a separate data job once `set05` policy is settled.

## Deploy and Verify

Use the simple scripts in `scripts/`:

1. `one_time_only_DELETE_ME.bat` for the first deploy in a project
2. `deploy.bat` for code deploys
3. `update.bat` for docs/settings/search-context refreshes
4. `rotate_keys.bat` when bootstrapping or rotating secrets
5. `refresh_context.bat` as the explicit equivalent of `update.bat`
6. `get_token.bat` when you need the auth token after deploy

`WHAT_TO_RUN.md` is the short operator-facing explanation of what each script
does and when to run it.

After deploy:

- smoke-test `/zb_api/chat`
- smoke-test `/zb_api/load_memory_logbook`
- smoke-test `/zb_api/session-evaluations/summary`
- confirm `/admin/login` and `/admin/conversations` still work
- if File Search is enabled, confirm the configured vector store IDs are valid

## Quality Gates

```cmd
python -m pytest -q
python -m flake8
python -m mypy .
```

Additional checks worth keeping:

- export the tool manifest after tool changes
- verify `action_schemas.yaml` reflects the live route behavior
- inspect `zenbot_knowledge/openai_response_tools.json` after tool edits

## Troubleshooting

### Chat works locally but not in cloud

- inspect secret names and active secret versions
- confirm the Cloud Run service has the expected env vars
- confirm Redis/Firestore/GCS clients initialize for the target project

### Streaming regressed badly

- check whether the request is falling back from stream mode into sync tool mode
- check whether stale `previous_response_id` values are being retried repeatedly
- confirm File Search is not enabled against an empty or overly broad vector store

### GPT Action fails but direct API works

- check schema/runtime mismatch first
- check auth mode in the Actions UI
- confirm the route is represented accurately in `action_schemas.yaml`

### Review admin fails

- confirm the bucket client initializes and the runtime can read `zbchats/`
- confirm `session_reviews/index.jsonl` exists after a saved session or backfill
- confirm the review dashboard is not trying to rely on retired local bridges
