# ZB App Architecture

## Overview

`zb_app` is the operational web/API layer of Zenbot. In v3 it is a single
Cloud Run service that serves:

- public browser pages
- streaming and non-streaming chat
- authenticated `zb_api` routes
- admin review tooling

The app is still intentionally split into a thin orchestration layer
(`main.py`) and an operational utility layer (`utilities.py`), but the runtime
below that orchestration is now OpenAI-native.

## Component Responsibilities

### `main.py`

- owns Flask app creation
- applies auth, rate limiting, and CORS policy
- renders templates and static-facing pages
- defines authenticated API routes
- coordinates admin and review workflows

### `utilities.py`

- conversation lifecycle and hot-state persistence
- koan lookup from `static/mmnk.json`
- OpenAI Responses requests, streaming, prompt caching, and tool dispatch
- GCS archive read/write
- memory-logbook normalization, resequencing, indexing, and persistence

### `contracts.py`

- typed runtime contracts
- strict tool argument schemas
- shared shape definitions for review, memory, and training objects

### `config.py`

- determines local vs managed-cloud runtime
- loads `.env` for local development only
- resolves secrets from Secret Manager
- exposes deterministic runtime/model defaults

### `models.py`

- stores the active general-model registry
- preserves legacy fine-tuned model identifiers for evaluation/reference

## Deployment Topology

### Cloud Run

- serves browser, API, SSE chat, and admin routes together
- uses one public base URL for `WEB_APP_ORIGIN` and `CHAT_API_BASE_URL`
- should run with `min instances = 1` and moderate concurrency
- is deployed via `scripts\deploy_cloudrun_service.cmd`; see
  `scripts/DEPLOY_ORDER.md` for operator sequencing

### App Engine

- no longer part of the active production path
- `app.yaml` remains only as archived reference material during migration cleanup

## Storage and External Services

### Redis / Memorystore

- preferred hot-state backend for active conversation sessions
- keyed by `conversation_id`
- stores the live transcript plus metadata, including the last Responses API ID

### Firestore

- current structured fallback when Redis is not configured
- still used for live state in compatibility mode

### Cloud Storage

- stores archived chat transcripts under `zbchats/*.jsonl`
- stores the canonical review manifest under `session_reviews/index.jsonl`
- stores the derived sessions-to-train export under
  `session_reviews/sessions_to_train.jsonl`
- stores the canonical memory logbook object
- stores queued memory/review helper artifacts when those queues are used

### Secret Manager

- stores OpenAI API key
- stores Flask secret key
- stores action/admin token

### OpenAI

- Responses API powers live chat
- prompt caching reduces repeated static prompt cost
- `previous_response_id` reduces per-turn transcript replay
- optional File Search is available when vector stores are configured
- strict function tools support Zenbot-specific retrieval and admin actions
- optional Background mode queues a non-blocking session critic

## Sibling-Directory Dependencies

- `../zenbot_knowledge/action_schemas.yaml`
- `../zenbot_knowledge/openai_response_tools.json`
- `../project/rolling_to_do_list.md`
- `../training/trainset04/readme_set04.md`
- `static/mmnk.json`

## Request/Data Flows

### Browser Chat

1. Browser loads `/chatter`.
2. `static/scripts.js` sends `/chat` or `/chat_case/<case_id>`.
3. `main.py` validates request and auth/cookie context.
4. `utilities.py` loads or creates the conversation state.
5. The Responses API is called with:
   - deterministic model/profile params
   - prompt caching
   - optional provider-side continuation via `previous_response_id`
   - built-in File Search when configured
6. Hot state is updated with transcript and latest provider response ID.
7. `/save_chat` archives the session to GCS, upserts the review manifest, and
   optionally queues a background critic.

### Authenticated API Chat

1. Client sends `Authorization`.
2. `/zb_api/chat` or `/zb_api/chat_case/<case_id>` validates auth.
3. Conversation state is created or continued in the same hot-state backend.
4. The response returns `conversation_id` plus one assistant reply.

### Tool-Enabled Sync Turns

For synchronous Responses requests, the runtime can expose strict function
tools. The model may call them to:

- load exact case context
- search approved exemplars
- load compact or full memory entries
- queue memory candidates or review requests
- archive a session

The runtime resolves those tool calls locally, then continues the Responses
turn until final text is produced.

### Memory Workflow

1. Client calls `/zb_api/load_memory_logbook` for a compact newest-first index.
2. Client chooses a `serial_number`.
3. Client calls `/zb_api/load_memory_entry/<serial_number>` for one full record.
4. Memory write endpoints append or replace the logbook and re-sequence serials.

### Admin Review Flow

1. `/admin/login` establishes browser admin auth.
2. `/admin/conversations` loads the GCS-backed review manifest.
3. `/review` renders one full transcript plus decision controls.
4. `/zb_api/session-evaluations/*` exposes the same review queue for GPT-side use.

## Notable Operational Constraints

- In dokusan/channeling mode, forwarded user text must remain verbatim.
- Cloud runtimes must not silently fall back to local memory-logbook state.
- The full memory logbook remains too large for GPT Actions; use summary index
  plus single-entry fetch.
- The action schema version is independent from the app release version.
- The current v3 repo still keeps review and memory canonical storage in GCS;
  Redis/File Search/Background mode are implemented first because they tighten
  latency and runtime behavior without requiring a full data-store rewrite.
