# ZB App Architecture

## Overview

`zb_app` is the operational web/API layer of Zenbot. It exposes:

- browser pages for public chat and reference browsing
- authenticated API routes for GPT Actions and other clients
- admin/archive tooling
- local training-review tooling

The app is intentionally split into a thin orchestration layer (`main.py`) and
an operational utility layer (`utilities.py`).

## Component Responsibilities

### `main.py`
- owns Flask app creation
- applies auth, rate limiting, and CORS policy
- renders templates and static-facing pages
- defines authenticated API routes
- coordinates admin and review workflows

### `utilities.py`
- conversation lifecycle and ID generation
- Firestore live-session persistence
- koan lookup from `static/mmnk.json`
- OpenAI request/stream handling
- GCS archive read/write
- memory-logbook normalization, resequencing, indexing, and persistence

### `config.py`
- determines local vs managed-cloud runtime
- loads `.env` for local development only
- resolves secrets from Secret Manager
- exposes shared config defaults and model parameter presets

### `models.py`
- stores the curated model registries
- stores training-loss metadata used in selection/admin context

### `chat_api.py`
- exposes `main.app` under a Cloud Run-oriented entrypoint name

## Deployment Topology

### App Engine
- serves the browser shell and template/static routes
- injects `CHAT_API_BASE_URL` into the browser bundle
- uses the same secret indirection env vars as the API service

### Cloud Run
- serves the chat/API workload
- handles authenticated `/zb_api/*` routes
- handles browser `/chat`, `/chat_case/*`, and `/save_chat` when called through
  the hybrid front-end path

## Storage and External Services

### Firestore
- stores live conversation state for active sessions
- keyed by `conversation_id`
- contains both message list and conversation metadata

### Cloud Storage
- stores archived chat transcripts under `zbchats/*.jsonl`
- stores the canonical memory logbook object

### Secret Manager
- stores OpenAI API key
- stores Flask secret key
- stores action/admin token

### OpenAI
- used for streaming and non-streaming chat completion requests
- model/profile selection is randomized per new conversation and then kept in
  conversation metadata

## Sibling-Directory Dependencies

`zb_app` reads or depends on the following repo-adjacent assets:

- `../zenbot_knowledge/action_schemas.yaml`
  - GPT Actions/OpenAPI contract
- `../training/trainset04/review.csv`
  - source of truth for admin review decisions
- `static/mmnk.json`
  - runtime koan dataset bundled with the app

The app does not attempt to document or own all of `training/` or
`zenbot_knowledge/`; it documents only the pieces that materially affect app
behavior.

## Request/Data Flows

### Browser Chat
1. Browser loads `/chatter`.
2. `base.html` injects `CHAT_API_BASE_URL` for the front-end bundle.
3. `static/scripts.js` sends `/chat` or `/chat_case/<case_id>`.
4. `main.py` validates request and auth/cookie context.
5. `utilities.py` loads or creates the conversation state.
6. OpenAI is called in streaming or non-streaming mode.
7. Firestore is updated with live transcript state.
8. `/save_chat` archives the session to GCS and removes live Firestore state.

### Authenticated API Chat
1. Client sends `Authorization` header.
2. `/zb_api/chat` or `/zb_api/chat_case/<case_id>` validates auth.
3. Conversation state is created or continued in Firestore.
4. Response returns `conversation_id` and status, plus assistant text for
   ordinary chat.

### Memory Workflow
1. Client calls `/zb_api/load_memory_logbook` for a compact newest-first index.
2. Client chooses a `serial_number`.
3. Client calls `/zb_api/load_memory_entry/<serial_number>` for one full record.
4. Memory write endpoints append or replace the logbook and re-sequence serials.

### Admin Archive Flow
1. Browser signs in via `/admin/login`.
2. `/admin/conversations` lists archived transcripts from GCS.
3. `/admin/conversations/<conversation_id>` loads one archived conversation.
4. `/download_chats` downloads all GCS archives into the local dev tree.

### Admin Review Flow
1. `/admin/review` first syncs local session files into the generated
   review datasets, then redirects into `/review`.
2. `/review` renders the dual-review browser UI backed by
   `../training/generated/collected_sessions_with_evaluations.jsonl`.
3. Browser and helper-GPT callers can both use the shared
   `/api/session-evaluations/*` or `/zb_api/session-evaluations/*` endpoints.
4. The older CSV-backed keep/discard queue remains available at
   `/admin/review/legacy`, and `/admin/review/legacy/view/<record_id>` reads the
   referenced session JSONL to display parsed metadata plus messages.

## Notable Operational Constraints

- In dokusan/channeling mode, message forwarding must be verbatim.
- Cloud runtimes must not silently fall back to local memory-logbook state.
- The full memory logbook is intentionally kept out of GPT Actions because the
  payload is too large; the compact index + single-entry fetch pattern is the
  supported route.
- The OpenAPI/schema version is independent from the app/repo release version.
