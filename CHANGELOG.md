# Changelog

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
