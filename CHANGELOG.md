# Changelog

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
