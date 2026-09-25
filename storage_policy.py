"""Opt-in durable storage guarantees for MCP without changing legacy defaults."""

from contextlib import contextmanager
from contextvars import ContextVar

_strict = ContextVar("zenbot_strict_storage", default=False)


class StorageUnavailable(RuntimeError):
    """A durable backend failed or would fall back to process-local state."""


def is_strict() -> bool:
    """Return whether this workflow requires confirmed durable persistence."""
    return _strict.get()


def require(condition: bool, message: str) -> None:
    """Fail only for strict workflows when a backend is unavailable."""
    if is_strict() and not condition:
        raise StorageUnavailable(message)


@contextmanager
def durable_storage():
    """Scope strict behavior to one workflow, including its internal model tools."""
    token = _strict.set(True)
    try:
        yield
    finally:
        _strict.reset(token)
