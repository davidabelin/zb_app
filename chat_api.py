"""
Cloud Run entrypoint for chat/API workloads.

Deploy with:
  gcloud run deploy zb-chat-api --source . --entry-point chat_api:app
"""

from main import app

__all__ = ["app"]
