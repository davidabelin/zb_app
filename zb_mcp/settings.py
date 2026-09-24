"""MCP-only configuration; production secrets are resolved from Secret Manager."""

from dataclasses import dataclass
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    """Required connection identity and persistent OAuth configuration."""

    project: str
    base_url: str
    allowed_emails: frozenset[str]
    google_client_id: str
    google_client_secret: str
    signing_key: str
    encryption_key: str
    namespace: str = "zenbot_mcp"

    @classmethod
    def from_environment(cls):
        """Fail closed for missing secrets or an unsafe public URL."""
        from google.cloud import secretmanager

        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "zenbot-434517")
        cloud = os.environ.get("GAE_ENV", "").startswith("standard")

        def required(name):
            secret = os.environ.get(f"{name}_SECRET_NAME", "")
            if secret:
                client = secretmanager.SecretManagerServiceClient()
                response = client.access_secret_version(
                    request={
                        "name": f"projects/{project}/secrets/{secret}/versions/latest"
                    }
                )
                value = response.payload.data.decode("utf-8").strip()
                if not value:
                    raise ValueError(f"The Secret Manager value for {name} is empty.")
                return value
            value = os.environ.get(name, "").strip()
            if not value or cloud:
                raise ValueError(
                    f"Configure {name}_SECRET_NAME (or {name} for local development)."
                )
            return value

        base_url = os.environ.get("MCP_BASE_URL", "").rstrip("/")
        parsed = urlsplit(base_url)
        local = parsed.hostname in {"localhost", "127.0.0.1"} and not cloud
        if (
            parsed.scheme != "https" and not (local and parsed.scheme == "http")
        ) or not parsed.hostname:
            raise ValueError(
                "MCP_BASE_URL must be HTTPS, or loopback HTTP for development."
            )
        if (
            parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "MCP_BASE_URL must be a root origin without credentials, path, or query."
            )
        return cls(
            project=project,
            base_url=base_url,
            allowed_emails=frozenset(
                item.strip().lower()
                for item in os.environ.get("MCP_ALLOWED_EMAILS", "").split(",")
                if item.strip()
            ),
            google_client_id=required("MCP_GOOGLE_CLIENT_ID"),
            google_client_secret=required("MCP_GOOGLE_CLIENT_SECRET"),
            signing_key=required("MCP_SIGNING_KEY"),
            encryption_key=required("MCP_ENCRYPTION_KEY"),
        )
