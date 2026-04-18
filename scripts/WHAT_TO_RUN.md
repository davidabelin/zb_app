# What To Run

Run these commands from the `zb_app` directory, which is the active Git root
for the deployed app. The parent `zenbot` folder holds project notes, training
data, and historical assets.

## Normal deploy

Run:

```cmd
scripts\deploy.bat
```

Use this for App Engine code changes.

## Update docs/settings/search context

Run:

```cmd
scripts\update_context.bat
```

This is not a code deploy. It refreshes docs/search context and related runtime
inputs.

## Rotate secrets only

Run:

```cmd
scripts\rotate_keys.bat
```

## Create search context

If you do not already have a search-context store, use:

```cmd
scripts\update_context.bat --create
```

## Print the admin/API token

Run:

```cmd
scripts\get_token.bat
```

## Smoke-test active routes

After a local change, run the focused tests that exercise public rendering,
admin/review rendering, and GPT-facing API contracts:

```cmd
python -m pytest tests/test_session_settings.py -q
python -m pytest tests/test_admin_archives.py -q
python -m pytest tests/test_session_review_bridge.py -q
python -m pytest tests/test_api_contract_cleanup.py -q
```

For a manual browser check, start Flask locally and visit:

```text
/
/chatter
/admin/conversations
/review
```
