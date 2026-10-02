"""Production ASGI factory; OAuth is required and cannot be disabled by an env flag."""

import anyio
from starlette.responses import JSONResponse

import utilities
from .auth import provider
from .operations import OperationStore
from .server import create_server
from .settings import Settings


def create_app():
    """Create the independently deployed MCP app with OAuth and health endpoints."""
    settings = Settings.from_environment()
    if utilities.DB is None or utilities.BUCKET is None:
        raise RuntimeError("MCP requires configured Firestore and GCS clients.")
    if not settings.allowed_emails:
        raise ValueError(
            "MCP_ALLOWED_EMAILS must explicitly authorize the Chief Monk's account."
        )
    operations = OperationStore(utilities.DB, namespace=settings.namespace)
    server = create_server(operations, settings.allowed_emails, auth=provider(settings))

    @server.custom_route("/healthz", methods=["GET"])
    async def health(request):
        return JSONResponse({"status": "ok"})

    @server.custom_route("/readyz", methods=["GET"])
    async def ready(request):
        def check():
            operations.records.document("_healthcheck").get(timeout=5)
            utilities.BUCKET.blob(utilities.config.MEMORY_LOGBOOK).exists(timeout=5)
            if utilities.config.HOT_STATE_BACKEND == "redis":
                if utilities.REDIS is None:
                    raise RuntimeError("Configured Redis unavailable")
                utilities.REDIS.ping()

        try:
            await anyio.to_thread.run_sync(check)
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse({"status": "ready"})

    return server.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=[settings.base_url.split("://", 1)[1]],
        allowed_origins=[settings.base_url],
    )
