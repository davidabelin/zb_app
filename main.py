"""Flask application composition root for the Zenbot App Engine service.

The application is assembled from dedicated public-web, GPT-facing API, and
admin/review blueprints. Shared HTTP policy lives in :mod:`app_support`; this
module intentionally remains the stable ``main:app`` deployment entry point.
"""

from __future__ import annotations

import logging

from flask import Flask

from admin_routes import admin_bp
from app_support import register_app_handlers
from web_routes import web_bp
from zb_api import zb_api_bp
import review_sync
import session_reviews
import utilities as utipy

__all__ = ["app", "review_sync", "session_reviews", "utipy"]


logging.basicConfig(level=utipy.config.LOG_LEVEL)
app = Flask(__name__, static_url_path="/static")
app.secret_key = utipy.config.FLASK_SECRET_KEY
app.config["SESSION_COOKIE_SECURE"] = utipy.config.SESSION_COOKIE_SECURE
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

register_app_handlers(app)
app.register_blueprint(web_bp)
app.register_blueprint(zb_api_bp)
app.register_blueprint(admin_bp)


if __name__ == "__main__":
    if utipy.config.LOCAL:
        app.run(debug=True)
    else:
        app.run(host="0.0.0.0", port=8080, debug=False)
