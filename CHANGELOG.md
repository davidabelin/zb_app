# Changelog

## v3.2.3 - 2026-04-17

Admin/review workflow and operator-maintenance release for `zb_app`.

- added compact backend status cards to `/admin/conversations` for archive
  storage, review manifest, memory logbook, vector-store configuration, and
  hot-state backend
- expanded review dashboard filters for Need ZB Review, Not Yet Touched,
  Awaiting Other Review, CM Reviewed, and ZB Reviewed while preserving the
  existing final-evaluation filters
- added local-only admin controls to download the full memory logbook JSON and
  run local/remote dokusan sync across archived sessions, review manifest
  metadata, review status, and local review/training artifacts
- added GPT-facing review API helpers for ZB-needed lists, random ZB review,
  and random review sampling, with matching `action_schemas.yaml` updates
- refreshed operator docs, smoke-test commands, stale public/admin copy,
  `trainset04` notes, and the rolling project to-do list
- pinned the OpenAI/httpx transport stack used by Python 3.14 local runtimes
- formalized the app/repo release marker as `v3.2.3`

## v3.2.0 - 2026-04-10

App Engine-only runtime cleanup for `zb_app`.

- removed the active Cloud Run bootstrap and same-repo Cloud Run entrypoint shim
- collapsed the browser chat bootstrap to same-origin `/chat`, `/chat_case/*`, and `/save_chat`
- made `app.yaml` the canonical Zenbot deploy manifest and repointed `scripts\deploy.bat` to App Engine
- updated maintainer docs, runbooks, and schemas to treat App Engine as the only active serving path
- removed Zenbot-specific Cloud Run assumptions from shared operator tooling
- formalized the app/repo release marker as `v3.2.0`

## v3.0.0 - 2026-03-28

OpenAI-native runtime rebuild for `zb_app`.

- replaced Chat Completions usage with the OpenAI Responses API
- made live model/profile selection deterministic
- added prompt caching and provider-side continuation via `previous_response_id`
- added strict internal Responses function tools and exported tool manifest support
- added optional File Search/vector-store sync tooling
- added optional background session critic submission
- added Redis/Memorystore-compatible hot session storage with Firestore fallback
- updated the session-evaluation schema to document `needs_cm_review`
- switched maintainer docs and deploy scripts to a Cloud Run-only runtime story
- formalized the app/repo release marker as `v3.0.0`

## v2.0.0 - 2026-03-15

Documentation-complete release for `zb_app`.

- added dense module, class, and function docstrings across the core app code
- documented browser/API/admin flows, storage layers, and cloud runtime topology
- added maintainer-facing docs:
  - `README.md`
  - `docs/ARCHITECTURE.md`
  - `docs/DEVELOPER_GUIDE.md`
- updated the recovery runbook to point at the fuller maintainer docs
- added doc-coverage enforcement for the core Python modules
- formalized the app/repo release marker as `v2.0.0`
- kept OpenAPI/schema versioning independent from the app release version

This release does not intentionally redesign the runtime API surface. It is a
documentation and maintainability release over the stabilized hybrid deployment,
memory-index workflow, and authenticated API surface already in place.

## Pre-v2 Preparation

Add archive download+delete admin flow and deduped review sync

- add bulk admin archive actions for download, delete, and download+delete
- support hosted downloads as zip output instead of local-only file writes
- add GCS archive helper functions for fetch, write, and delete operations
- add local review sync logic to dedupe collected sessions by transcript hash
- quarantine duplicate session files under collected_sessions/_duplicates
- rebuild training/trainset04/review.csv from the deduped session set
- preserve existing generated review evaluations across dedupe/sync
- regenerate generated review datasets and sessions_to_train from canonical sessions
- update admin conversations UI to expose maintenance actions and sync status
- add tests covering archive maintenance and review-sync preservation
