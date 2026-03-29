# Deploy Order

If you are deploying Zenbot v3 from a normal Windows `cmd` prompt, use the
scripts in this order:

## 1. `setup_gcp_secrets.cmd`

Use this only when:

- setting up a new project
- rotating `zb-openai-api-key`
- rotating `zb-flask-secret-key`
- rotating `zb-action-api-token`

This launches the PowerShell secret helper because hidden secret prompts are
much cleaner there.

## 2. `deploy_cloudrun_service.cmd`

Use this for every real app deploy.

What it does:

- sets the active gcloud project
- deploys the single Cloud Run service from the current `zb_app` source
- sets the main v3 runtime env vars
- resolves the deployed service URL
- updates `WEB_APP_ORIGIN` and `CHAT_API_BASE_URL` to that same URL
- defaults the service account to `zenbot-sa@<PROJECT_ID>.iam.gserviceaccount.com`

This is the main v3 deploy script.

## 3. `deploy_cloudrun_chat.cmd`

Compatibility alias only.

It just forwards to `deploy_cloudrun_service.cmd`. Use it only if old notes or
muscle memory still point you at the old name.

## 4. `deploy_appengine_web.ps1`

Do not use this for v3.

It is only a deprecation stub that tells you App Engine is no longer part of
the active deploy path.

## 5. `sync_openai_vector_store.py`

Use this only when File Search is enabled or the curated reference documents
changed.

Typical use:

```cmd
python scripts\sync_openai_vector_store.py --create
```

Then set `OPENAI_VECTOR_STORE_IDS` to the returned vector store ID.

## 6. `get_admin_token.cmd`

Use this after deploy when you need the action/admin token for:

- browser admin login testing
- Postman
- GPT Actions auth configuration

By default it uses the active `gcloud` account. Pass an account explicitly only
when you need to override that.
