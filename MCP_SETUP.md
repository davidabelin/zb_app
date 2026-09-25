# Private Zenbot MCP service

The `mcp` App Engine service exposes `/mcp` alongside the existing default Flask
service. Both transports call `zb_services`; the MCP service never calls REST.
The current GPT Actions URLs, bearer token, browser routes, prompts, and default
deployment command remain available.

## Install and test

Run from this `zb_app` repository with Python 3.14:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest -q
.\venv\Scripts\python.exe -m flake8
.\venv\Scripts\python.exe -m mypy .
```

`requirements-mcp.txt` pins the MCP server, protocol SDK, OAuth storage adapter,
and ASGI server. App Engine installs the root requirements, which includes this
file for both services. Flask does not import MCP during normal startup.

The tests exercise all 23 API operations through REST and MCP with isolated
Firestore/GCS substitutes and a deterministic model response. They also cover
HTTP initialization/discovery, OAuth challenges, tool annotations, schema
validation, exact text forwarding, duplicate/concurrent writes, and interrupted
operations. They do not perform real Google sign-in or call OpenAI.

Implementation validation on Python 3.14.2: 148 tests pass, including the original
83 tests, and flake8 and formatting checks pass. The existing environment still
has three unrelated mypy errors and old-wheel platform warnings from `pip check`;
a fresh runtime dependency resolution succeeds. Cloud deployment and actual
Google/ChatGPT connection tests remain pending the OAuth client and secrets.

## Configure Google identity

1. In Google Cloud project `zenbot-434517`, configure an OAuth consent screen
   for this private integration. When using External/Testing, add your account
   as a test user. Request only `openid` and user email, not Drive or Gmail scopes.
2. Create a **Web application** OAuth client. Register this exact redirect URI:
   `https://mcp-dot-zenbot-434517.uw.r.appspot.com/auth/callback`.
   For local OAuth testing, also register `http://localhost:8000/auth/callback`.
3. Store the client ID and secret in Secret Manager as
   `zb-mcp-google-client-id` and `zb-mcp-google-client-secret`.
4. Generate a persistent random signing key (at least 32 random bytes, encoded
   as a string) and a Fernet encryption key. Store them as `zb-mcp-signing-key`
   and `zb-mcp-encryption-key`. Use your secret management UI or secure file
   input; do not put values in committed YAML or shell command arguments.
5. Grant the deployed service account access to those four secrets and the
   existing model API secret. It also needs access to the existing GCS objects
   and Firestore data. This service uses the attached service account; do not
   ship a service-account key file.

Copy `mcp_app.yaml` to the ignored `mcp_app.local.yaml`. Set `MCP_ALLOWED_EMAILS`
to your verified Google account email. A local overlay may already exist from
implementation. The committed template deliberately contains no allowed user.
An empty allowlist refuses startup; an unverified or different identity cannot
execute a tool. The old Actions bearer token is not an MCP credential.

`MCP_BASE_URL` is the service's canonical root HTTPS origin, without `/mcp`.
Give clients the complete URL ending in `/mcp`. OAuth resource discovery uses
that endpoint's audience; do not substitute an unrelated host in the connection.

OAuth state is encrypted before storage and kept under separately prefixed
Firestore collections (`zenbot_mcp_oauth_...`). Stable signing and encryption
keys must be shared by all deployed versions. Do not casually regenerate them
on each deployment: existing connections would stop working.

For local use, set the same `MCP_*` names without `_SECRET_NAME` in the process
environment, along with `MCP_BASE_URL=http://localhost:8000`. Application Default
Credentials must target an isolated development project/bucket if you intend to
write test data. Then run:

```powershell
.\venv\Scripts\python.exe -m uvicorn zb_mcp.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

There is no production no-auth switch. Local HTTP also uses Google OAuth.

## Storage and retries

The default MCP manifest selects Firestore for active conversations, matching
the existing manifest's default. If the web service actually uses Redis, copy
that same backend configuration and network connectivity to the MCP overlay
before testing continuity. Both services must point to the same authoritative
conversation backend and archive bucket.

Every MCP write requires an `operation_id` containing 1–128 letters, digits,
periods, underscores, colons, or hyphens. Use a fresh ID for each intentional
action and the same ID and arguments for a retry. JSON-RPC request IDs are not
used for deduplication.

Firestore transactions claim operations and locks; external calls never run
inside those transactions. Confirmed results, including safe application
rejections, are replayed without executing the action again. Changed arguments
with the same ID are rejected. Receipts are private to the Google subject.

This private release deliberately serializes all MCP writes through one shared
lock because the model's own function tools can also modify memory and review
data. Reads remain concurrent. Browser and REST writes do not use this lock:
avoid concurrent edits through another client during the pilot.

Completed operation documents have an `expires_at` field seven days after
completion. Enable a Firestore TTL policy on that field for collection group
`zenbot_mcp_operations`. TTL deletion is asynchronous; until a receipt is
removed it still prevents replay. IDs are not protected forever after deletion.
Unresolved records and locks have no TTL and must not be expired automatically.

If the service crashes or the outcome of a storage/model call is unknown,
the existing claim prevents blind re-execution. `getOperationStatus` returns
`running`, `completed`, or `uncertain`; running claims older than ten minutes
are presented as uncertain. They are **not** automatically stolen or retried.

Before manually reconciling an uncertain operation:

1. Stop or drain the affected MCP version so the original worker cannot still
   finish. Record the operation ID and Google subject from the operation record.
2. Inspect the actual conversation/archive, logbook, review manifest/export,
   and optional critic response. Determine which effects happened. Finish or
   repair partial work deliberately; never blindly rerun the original action.
3. Use `scripts/mcp_operation.py resolve` with a JSON result file representing
   the verified outcome and an operator note. Use a `status: failure` result
   if the action was confirmed not to have completed. The command releases
   only that operation's locks and preserves an audit note in its receipt.
4. Resume the service. Repeating the original operation ID returns the
   reconciled result. A new intentional attempt requires a new ID.

MCP strict-storage context prevents process-local fallback and propagates
conversation read/write/delete failures, including failures inside model function
tools. MCP also disables the runtime and OpenAI SDK's automatic model retries:
a failed follow-up response must not repeat an internal tool's earlier writes.
An enabled background critic submission failure is uncertain as well. Legacy
callers retain their existing retry and fallback behavior. Idempotency is not an
atomic transaction across OpenAI, GCS, and Firestore; ambiguous results require
reconciliation.

## Deploy and connect

```powershell
scripts\deploy_mcp.bat mcp_app.local.yaml
```

This deploys only the MCP service and uses `--no-promote`. Check the new version's
`/healthz` and `/readyz` first. Health is process liveness; readiness performs
read-only storage checks and exposes no configuration values. Promote the tested
version with App Engine traffic management for **service `mcp` only**. Complete
OAuth testing at the canonical service URL after routing it to that version.

Use MCP Inspector in Streamable HTTP mode with the complete `/mcp` URL. Verify
the OAuth login, initialization, 25 tools, schemas, read/write annotations, and
an invalid-token request. The service uses complete JSON responses and stateless
transport; it does not depend on sticky sessions or incremental SSE delivery.

In ChatGPT developer mode, create a personal plugin from the MCP URL, complete
Google sign-in, install it, and invoke it in a fresh Work conversation. Confirm
that your account can actually invoke write tools. Connecting a server does not
automatically attach it to an existing Custom GPT conversation.

Acceptance workflow:

- Load summaries, then one selected memory entry.
- Start a koan-anchored session, exchange exact turns, relay `(bows)` and the
  final response, and archive it.
- Retrieve the archived transcript and verify exact text and the closing turn.
- Retry the archive with the same operation ID and verify the receipt is replayed.
- In isolated test storage, exercise memory append/replacement and both reviewer
  decisions. Do not use full-logbook replacement as a production smoke test.

Tool logs contain names, operation IDs, duration, and status, not arguments or
full transcripts. OAuth credentials and receipts must not be copied into logs.
Uvicorn access logs are disabled to avoid recording OAuth callback query strings.
Check failed OAuth links, initialization errors, readiness failures, tool failures,
and unresolved operation records during the pilot. Roll back by routing the MCP
service to its previous version or stopping it; the default Actions service stays
available.

## Contract maintenance and boundaries

`zb_mcp/output_schemas.json` is a checked-in snapshot of the current Actions
output contracts. MCP converts the older OpenAPI `nullable` fields into actual
JSON Schema null branches. The deployed service does not read the sibling
knowledge repository. Regenerate the snapshot with
`python scripts/export_mcp_output_schemas.py` when intentionally updating the
Actions contract, and rerun parity tests.

The tools retain the existing operation IDs. MCP additionally exposes
`replaceMemoryLogbook` and `getOperationStatus`; review decisions require both
the index and conversation ID. Server instructions cover operational sequencing.
Zenbot persona, visitor permissions, public distribution, and custom UI are
outside this release.

References: [OpenAI MCP guide](https://developers.openai.com/plugins/build/mcp-server),
[personal plugin setup](https://developers.openai.com/plugins/quickstart),
[FastMCP Google OAuth](https://gofastmcp.com/integrations/google),
[App Engine response behavior](https://docs.cloud.google.com/appengine/docs/standard/how-requests-are-handled).
