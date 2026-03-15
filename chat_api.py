"""Cloud Run entrypoint shim for the API/chat workload.

The real Flask application lives in ``main.py``. This module exists so Cloud
Run can point at a lightweight entrypoint that clearly communicates which
runtime surface is being deployed when the API/chat service is built
independently from the App Engine web shell.

Deploy example:
    gcloud run deploy zb-chat-api --source . --entry-point chat_api:app
"""

from main import app

__all__ = ["app"]
