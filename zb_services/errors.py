"""Application failures without a dependency on Flask or MCP."""

from typing import Any


class ServiceError(Exception):
    """Carry the established API error code, status, and structured details."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        *,
        effects_possible: bool = False,
        **details: Any,
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.effects_possible = effects_possible
        self.details = {
            key: value for key, value in details.items() if value is not None
        }

    def payload(self) -> dict[str, Any]:
        """Return the existing JSON failure envelope."""
        return {
            "status": "failure",
            "error": self.code,
            "message": str(self),
            **self.details,
        }


def require_archive_storage(
    message: str = "Archive storage is unavailable or unconfigured.",
) -> None:
    """Require cloud storage before an archive or review operation."""
    import utilities

    if not utilities.BUCKET:
        raise ServiceError("storage_unavailable", message, 503)
