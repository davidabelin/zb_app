"""Runtime configuration for the Zenbot web application.

This module is the configuration spine for the v3 runtime. It resolves local
dotenv files for development, reads secrets from Google Secret Manager in cloud
runtimes, and exposes a single dataclass consumed by the Flask routes,
Responses-based model adapter, storage helpers, and operator scripts.

Key precedence rules:
1. In local development, read `.env` and `config/.env` if python-dotenv exists.
2. In managed runtimes, do not load dotenv files.
3. For secrets, prefer Secret Manager and fall back to environment values.
4. Keep model/runtime defaults deterministic; do not randomize production model
   or profile selection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Optional
import logging
import os

from models import MODELS_IN_USE


def _default_models_in_use() -> Dict[str, str]:
    """Return the active model registry with a modern base-model fallback."""

    return dict(MODELS_IN_USE) if MODELS_IN_USE else {"gpt-5.4-mini": "gpt-5.4-mini"}


def _default_model_name() -> str:
    """Return the default active model key."""

    return next(iter(_default_models_in_use()), "gpt-5.4-mini")


def _default_botling_presets() -> Dict[str, Dict[str, Any]]:
    """Return the first-pass named Mumonbot-ling preset registry. """

    return {
        "balanced_mumon": {
            "label": "Mumon",
            "description": "You are an LLM chatbot post-trained and fine-tuned on the classic commentaries by Zen Master Mumon Ekai's on the canonical Chinese koans, as collected in the 13thC compilation 'The Gateless Gate', or *Mumonkan*.",
            "instruction": (
                "Your training has made you a truly faithful emulation of Mumon, his Zen essence, his presence and style, in a lively and accessible way for today's students of Zen."
                "Now, as if centuries have passed while you were sitting zazen, here you are holding a Dokusan session with your students at the Zendo."
            ),
            "opening_cue": "(smiles)",
            "settings": {
                "model_name": _default_model_name(),
                "reasoning_effort": "low",
                "max_output_tokens": 1000,
                "enable_function_tools": True,
                "enable_file_search": False,
                "enable_web_search": False,
                "enable_background_critic": False,
            },
        },
        "austere_abbot": {
            "label": "Austere Abbot",
            "description": "Sparse, disciplined replies with very little explanation.",
            "instruction": (
                "Adopt the austere abbot voice: sparse, severe, and economical. "
                "Prefer a short challenge over a warm explanation."
            ),
            "opening_cue": "(sits upright)",
            "settings": {
                "model_name": _default_model_name(),
                "reasoning_effort": "low",
                "max_output_tokens": 1000,
                "enable_function_tools": True,
                "enable_file_search": False,
                "enable_web_search": False,
                "enable_background_critic": False,
            },
        },
        "fierce_barrier": {
            "label": "Fierce Barrier",
            "description": "Sharper, more forceful challenge for hard koan pressure.",
            "instruction": (
                "Adopt the fierce barrier voice: sharper, more forceful, and less accommodating. Intensify the pressure if the student tries to cling to preconceived ideas."
            ),
            "opening_cue": "(meets your gaze without speaking)",
            "settings": {
                "model_name": _default_model_name(),
                "reasoning_effort": "medium",
                "max_output_tokens": 1000,
                "enable_function_tools": True,
                "enable_file_search": False,
                "enable_web_search": False,
                "enable_background_critic": False,
            },
        },
        "explanatory_guide": {
            "label": "Mumonbotlinger",
            "description": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a custom-made chatbot fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in a 13thC compilation. Now, centuries later, here you are holding a Dokusan session with the students; centering yourself on each koan they bring to you, and on the barriers they bring with them to it.",
            "instruction": (
                "Your training has made you a truly faithful emulation of Mumon, his Zen essence, his presence and style, in a lively and accessible way for today's students of Zen."
                "Now, as if centuries have passed while you were sitting zazen, here you are holding a Dokusan session with your students at the Zendo."
            ),
            "opening_cue": "(nods once)",
            "settings": {
                "model_name": _default_model_name(),
                "reasoning_effort": "medium",
                "max_output_tokens": 1200,
                "enable_function_tools": True,
                "enable_file_search": False,
                "enable_web_search": False,
                "enable_background_critic": False,
            },
        },
    }


def _csv_list(value: str | None) -> list[str]:
    """Split a comma-delimited environment variable into trimmed values."""

    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _intenv(value: str | None, default: int) -> int:
    """Parse an integer environment variable with a safe fallback."""

    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


_load_dotenv: Optional[Callable[..., bool]]
try:
    from dotenv import load_dotenv as _dotenv_load

    _load_dotenv = _dotenv_load
except Exception:
    _load_dotenv = None


def load_dotenv(*args: Any, **kwargs: Any) -> bool:
    """Proxy to python-dotenv when installed."""

    if _load_dotenv is None:
        return False
    return bool(_load_dotenv(*args, **kwargs))


def _strtobool(value: str | None, default: bool = False) -> bool:
    """Parse permissive truthy environment values."""

    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sanitize_secret(value: str | None) -> str:
    """Normalize secret payloads before they are exposed to the app."""

    if value is None:
        return ""
    return value.strip().lstrip("\ufeff")


def _is_cloud_runtime() -> bool:
    """Detect whether the current process is running in a managed GCP runtime."""

    if os.getenv("K_SERVICE"):
        return True
    return os.getenv("GAE_ENV", "").startswith("standard")


def _load_local_env() -> None:
    """Load local dotenv files only outside managed cloud runtimes."""

    if _is_cloud_runtime():
        return
    load_dotenv()
    load_dotenv("config/.env")


_load_local_env()


@lru_cache(maxsize=64)
def _read_secret(project_id: str, secret_name: str) -> str:
    """Read and cache the latest version of a named Secret Manager secret."""

    if not project_id or not secret_name:
        return ""
    try:
        from google.cloud import secretmanager
    except Exception:
        return ""

    try:
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return _sanitize_secret(response.payload.data.decode("utf-8"))
    except Exception as exc:
        logging.warning("Unable to read secret '%s': %s", secret_name, exc)
        return ""


@dataclass
class Config:
    """Resolved application configuration shared across the Zenbot runtime."""

    GOOGLE_CLOUD_PROJECT: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "zenbot-434517")
    )
    LOCAL: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("LOCAL"), default=not _is_cloud_runtime()
        )
    )

    OPENAI_API_KEY_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY_SECRET_NAME", "")
    )
    FLASK_SECRET_KEY_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("FLASK_SECRET_KEY_SECRET_NAME", "")
    )
    ACTION_API_TOKEN_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("ACTION_API_TOKEN_SECRET_NAME", "")
    )

    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    FLASK_SECRET_KEY: str = field(
        default_factory=lambda: os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())
    )
    ACTION_API_TOKEN: str = field(
        default_factory=lambda: os.getenv("ACTION_API_TOKEN", "")
    )

    GOOGLE_API_KEY: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    CHAT_API_BASE_URL: str = field(
        default_factory=lambda: os.getenv("CHAT_API_BASE_URL", "")
    )
    WEB_APP_ORIGIN: str = field(
        default_factory=lambda: os.getenv(
            "WEB_APP_ORIGIN", "https://zenbot-434517.uw.r.appspot.com"
        )
    )
    CHAT_ALLOWED_ORIGINS: list[str] = field(
        default_factory=lambda: _csv_list(os.getenv("CHAT_ALLOWED_ORIGINS", ""))
    )
    ZB_API_STRICT_AUTH: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("ZB_API_STRICT_AUTH"), default=True
        )
    )
    SESSION_COOKIE_SECURE: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("SESSION_COOKIE_SECURE"), default=False
        )
    )

    BUCKET_NAME: str = field(
        default_factory=lambda: os.getenv("BUCKET_NAME", "zenbot_cloudstore")
    )
    MEMORY_LOGBOOK: str = field(
        default_factory=lambda: os.getenv("MEMORY_LOGBOOK", "memory_logbook.json")
    )
    MEMORY_ARCHIVES: str = field(
        default_factory=lambda: os.getenv(
            "MEMORY_ARCHIVES", "memory_logbook_archives.json"
        )
    )
    MEMORY_CANDIDATE_QUEUE: str = field(
        default_factory=lambda: os.getenv(
            "MEMORY_CANDIDATE_QUEUE", "memory/memory_candidates.jsonl"
        )
    )
    REVIEW_REQUESTS_BLOB: str = field(
        default_factory=lambda: os.getenv(
            "REVIEW_REQUESTS_BLOB", "session_reviews/review_requests.jsonl"
        )
    )

    STUDENT: str | None = None
    CASE_ID: int = 0
    KOAN: Dict[str, Any] = field(default_factory=dict)
    MODEL_NAME: str = field(default_factory=_default_model_name)
    TRAINING_LOSS: float = 0.0
    STREAMING: bool = field(
        default_factory=lambda: _strtobool(os.getenv("STREAMING_ENABLED"), default=True)
    )
    LOG_LEVEL: int = logging.INFO

    OPENAI_LIVE_MODEL: str = field(
        default_factory=lambda: os.getenv("OPENAI_LIVE_MODEL", "gpt-5.4-mini")
    )
    OPENAI_JUDGE_MODEL: str = field(
        default_factory=lambda: os.getenv("OPENAI_JUDGE_MODEL", "gpt-5.4")
    )
    OPENAI_POST_TRAINING_MODEL: str = field(
        default_factory=lambda: os.getenv("OPENAI_POST_TRAINING_MODEL", "gpt-4.1")
    )
    OPENAI_REASONING_EFFORT: str = field(
        default_factory=lambda: os.getenv("OPENAI_REASONING_EFFORT", "low")
    )
    OPENAI_REASONING_SUMMARY: str = field(
        default_factory=lambda: os.getenv("OPENAI_REASONING_SUMMARY", "auto")
    )
    OPENAI_PROMPT_CACHE_PREFIX: str = field(
        default_factory=lambda: os.getenv("OPENAI_PROMPT_CACHE_PREFIX", "zenbot-v3")
    )
    OPENAI_PROMPT_CACHE_RETENTION: str = field(
        default_factory=lambda: os.getenv("OPENAI_PROMPT_CACHE_RETENTION", "24h")
    )
    OPENAI_ENABLE_FILE_SEARCH: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("OPENAI_ENABLE_FILE_SEARCH"), default=False
        )
    )
    OPENAI_ENABLE_WEB_SEARCH: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("OPENAI_ENABLE_WEB_SEARCH"), default=False
        )
    )
    OPENAI_ENABLE_FUNCTION_TOOLS: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("OPENAI_ENABLE_FUNCTION_TOOLS"), default=True
        )
    )
    OPENAI_ENABLE_BACKGROUND_CRITIC: bool = field(
        default_factory=lambda: _strtobool(
            os.getenv("OPENAI_ENABLE_BACKGROUND_CRITIC"), default=False
        )
    )
    OPENAI_VECTOR_STORE_IDS: list[str] = field(
        default_factory=lambda: _csv_list(os.getenv("OPENAI_VECTOR_STORE_IDS", ""))
    )
    DEFAULT_RESPONSE_PROFILE: str = field(
        default_factory=lambda: os.getenv("DEFAULT_RESPONSE_PROFILE", "live")
    )
    SESSION_SETTINGS_VERSION: str = field(
        default_factory=lambda: os.getenv("SESSION_SETTINGS_VERSION", "v3.1")
    )
    DEFAULT_BOTLING_ID: str = field(
        default_factory=lambda: os.getenv("DEFAULT_BOTLING_ID", "balanced_mumon")
    )

    REDIS_URL: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))
    HOT_STATE_BACKEND: str = field(
        default_factory=lambda: os.getenv("HOT_STATE_BACKEND", "")
    )
    SESSION_TTL_SECONDS: int = field(
        default_factory=lambda: _intenv(os.getenv("SESSION_TTL_SECONDS"), 86400)
    )

    MODELS_IN_USE: Dict[str, str] = field(default_factory=_default_models_in_use)
    BOTLING_PRESETS: Dict[str, Dict[str, Any]] = field(
        default_factory=_default_botling_presets
    )

    MODEL_ARGS: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "live": {
                "temperature": 0.9,
                "max_output_tokens": 900,
                "top_p": 1.0,
                "reasoning_effort": "low",
            },
            "balanced": {
                "temperature": 0.7,
                "max_output_tokens": 700,
                "top_p": 1.0,
                "reasoning_effort": "low",
            },
            "judge": {
                "temperature": 0.2,
                "max_output_tokens": 1000,
                "top_p": 1.0,
                "reasoning_effort": "medium",
            },
        }
    )

    START_CHATS = {
        "smiles": [
            {
                "role": "system",
                "content": (
                    "You are Mumonbot, a disciplined Zen teacher voice shaped by "
                    "the Mumonkan and related Zen training records. Speak with "
                    "clarity, brevity, and grounded koan attention. Avoid modern "
                    "AI meta-commentary unless directly asked."
                ),
            },
            {"role": "user", "content": "(student enters, bows, sits)"},
            {"role": "assistant", "content": "(smiles)"},
        ]
    }

    def __post_init__(self) -> None:
        """Finalize derived config after dataclass field initialization."""

        self.OPENAI_API_KEY = self._resolve_secret(
            self.OPENAI_API_KEY_SECRET_NAME, self.OPENAI_API_KEY
        )
        self.FLASK_SECRET_KEY = self._resolve_secret(
            self.FLASK_SECRET_KEY_SECRET_NAME, self.FLASK_SECRET_KEY
        )
        self.ACTION_API_TOKEN = self._resolve_secret(
            self.ACTION_API_TOKEN_SECRET_NAME, self.ACTION_API_TOKEN
        )

        if not self.HOT_STATE_BACKEND:
            self.HOT_STATE_BACKEND = "redis" if self.REDIS_URL else "firestore"

        if self.MODEL_NAME not in self.MODELS_IN_USE:
            self.MODEL_NAME = self.OPENAI_LIVE_MODEL
        if self.DEFAULT_BOTLING_ID not in self.BOTLING_PRESETS:
            self.DEFAULT_BOTLING_ID = next(iter(self.BOTLING_PRESETS), "balanced_mumon")

    def _resolve_secret(self, secret_name: str, fallback: str) -> str:
        """Resolve one secret with Secret Manager first and env fallback second."""

        value = _read_secret(self.GOOGLE_CLOUD_PROJECT, secret_name)
        return value or _sanitize_secret(fallback)

    @staticmethod
    def _supports_reasoning(model_name: str) -> bool:
        """Return whether a model name plausibly accepts reasoning options."""

        prefixes = ("gpt-5", "o1", "o3", "o4")
        return model_name.startswith(prefixes)

    @staticmethod
    def _supports_sampling_controls(model_name: str) -> bool:
        """Return whether temperature/top-p style controls should be sent."""

        prefixes = ("gpt-5", "o1", "o3", "o4")
        return not model_name.startswith(prefixes)

    @staticmethod
    def reasoning_effort_choices(model_name: str) -> list[str]:
        """Return the supported reasoning-effort values for one model."""

        if Config._supports_reasoning(model_name):
            return ["none", "low", "medium", "high", "xhigh"]
        return []

    def tool_caps(self) -> Dict[str, bool]:
        """Return deployment-level caps for per-session tool toggles."""

        return {
            "enable_function_tools": self.OPENAI_ENABLE_FUNCTION_TOOLS,
            "enable_file_search": bool(
                self.OPENAI_ENABLE_FILE_SEARCH and self.OPENAI_VECTOR_STORE_IDS
            ),
            "enable_web_search": self.OPENAI_ENABLE_WEB_SEARCH,
            "enable_background_critic": self.OPENAI_ENABLE_BACKGROUND_CRITIC,
        }

    def model_capabilities(self, model_name: str) -> Dict[str, Any]:
        """Return capability flags for one configured model key."""

        resolved_model = self.MODELS_IN_USE.get(model_name, model_name)
        return {
            "id": model_name,
            "label": model_name,
            "supports_reasoning": self._supports_reasoning(resolved_model),
            "reasoning_efforts": self.reasoning_effort_choices(resolved_model),
            "supports_sampling_controls": self._supports_sampling_controls(resolved_model),
        }

    @staticmethod
    def _normalize_reasoning_effort(effort: str | None) -> str:
        """Map legacy reasoning aliases onto currently supported API values."""

        value = str(effort or "").strip().lower()
        if value == "minimal":
            return "low"
        return value

    def make_params(
        self, profile: str, model_name: str | None = None
    ) -> Dict[str, Any]:
        """Build a Responses-API parameter dict for a named profile."""

        selected_name = model_name or self.MODEL_NAME or self.OPENAI_LIVE_MODEL
        resolved_model = self.MODELS_IN_USE.get(selected_name, selected_name)
        profile_config = self.MODEL_ARGS.get(
            profile, self.MODEL_ARGS.get(self.DEFAULT_RESPONSE_PROFILE, {})
        )

        params: Dict[str, Any] = {
            "model": resolved_model,
            "store": True,
            "truncation": "auto",
        }
        if self._supports_sampling_controls(resolved_model):
            if "temperature" in profile_config:
                params["temperature"] = profile_config["temperature"]
            if "top_p" in profile_config:
                params["top_p"] = profile_config["top_p"]
        if "max_output_tokens" in profile_config:
            params["max_output_tokens"] = profile_config["max_output_tokens"]

        effort = self._normalize_reasoning_effort(
            str(profile_config.get("reasoning_effort", self.OPENAI_REASONING_EFFORT))
        )
        if effort and self._supports_reasoning(resolved_model):
            params["reasoning"] = {
                "effort": effort,
                "summary": self.OPENAI_REASONING_SUMMARY,
            }

        return params
