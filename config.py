# config.py
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Dict, Optional
import logging
import os

from models import ZB_MODELS

_load_dotenv: Optional[Callable[..., bool]]
try:
    from dotenv import load_dotenv as _dotenv_load

    _load_dotenv = _dotenv_load
except Exception:
    _load_dotenv = None


def load_dotenv(*args: Any, **kwargs: Any) -> bool:
    if _load_dotenv is None:
        return False
    return bool(_load_dotenv(*args, **kwargs))


def _strtobool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _load_local_env() -> None:
    """Load local dotenv files only for local/dev usage."""
    if os.getenv("GAE_ENV", "").startswith("standard"):
        return
    load_dotenv()
    load_dotenv("config/.env")


_load_local_env()


@lru_cache(maxsize=64)
def _read_secret(project_id: str, secret_name: str) -> str:
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
        return response.payload.data.decode("utf-8")
    except Exception as e:
        logging.warning("Unable to read secret '%s': %s", secret_name, e)
        return ""


@dataclass
class Config:
    # Project and runtime
    GOOGLE_CLOUD_PROJECT: str = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT", "zenbot-434517")
    )
    LOCAL: bool = field(
        default_factory=lambda: not os.getenv("GAE_ENV", "").startswith("standard")
    )

    # Secret indirection env variables
    OPENAI_API_KEY_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY_SECRET_NAME", "")
    )
    FLASK_SECRET_KEY_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("FLASK_SECRET_KEY_SECRET_NAME", "")
    )
    ACTION_API_TOKEN_SECRET_NAME: str = field(
        default_factory=lambda: os.getenv("ACTION_API_TOKEN_SECRET_NAME", "")
    )

    # Optional plain env fallback (local only).
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    FLASK_SECRET_KEY: str = field(
        default_factory=lambda: os.getenv("FLASK_SECRET_KEY", os.urandom(32).hex())
    )
    ACTION_API_TOKEN: str = field(
        default_factory=lambda: os.getenv("ACTION_API_TOKEN", "")
    )

    # Non-secret config
    GOOGLE_API_KEY: str = field(default_factory=lambda: os.getenv("GOOGLE_API_KEY", ""))
    CHAT_API_BASE_URL: str = field(
        default_factory=lambda: os.getenv("CHAT_API_BASE_URL", "")
    )
    WEB_APP_ORIGIN: str = field(
        default_factory=lambda: os.getenv(
            "WEB_APP_ORIGIN", "https://zenbot-434517.uw.r.appspot.com"
        )
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

    BUCKET_NAME: str = "zenbot_cloudstore"
    MEMORY_LOGBOOK: str = "memory_logbook.json"
    MEMORY_ARCHIVES: str = "memory_logbook_archives.json"

    # Dynamic chat defaults/state
    STUDENT: str | None = None
    CASE_ID: int = 0
    KOAN: Dict[str, Any] = field(default_factory=dict)
    MODEL_NAME: str = "gpt-4o"
    TRAINING_LOSS: float = 0.0
    STREAMING: bool = field(
        default_factory=lambda: _strtobool(os.getenv("STREAMING_ENABLED"), default=True)
    )
    LOG_LEVEL: int = logging.INFO

    # Model registries
    ZB_MODELS: Dict[str, str] = field(default_factory=lambda: ZB_MODELS)
    MODELS: Dict[str, str] = field(init=False)

    # Parameter defaults
    _PARAM_BASE: Dict[str, Any] = field(
        default_factory=lambda: {
            "temperature": 0,
            "max_tokens": 0,
            "top_p": 0,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "response_format": {"type": "text"},
        }
    )

    MODEL_ARGS: Dict[str, Dict[str, Any]] = field(
        default_factory=lambda: {
            "high": {
                "temperature": 1.75,
                "max_tokens": 5120,
                "top_p": 0.75,
                "frequency_penalty": 1.75,
                "presence_penalty": 0.75,
            },
            "mid": {
                "temperature": 1.0,
                "max_tokens": 1024,
                "top_p": 0.5,
                "frequency_penalty": 1.0,
                "presence_penalty": 0.5,
            },
            "low": {
                "temperature": 0.5,
                "max_tokens": 512,
                "top_p": 0.25,
                "frequency_penalty": 0.25,
                "presence_penalty": 0.075,
            },
            "good": {
                "temperature": 1.2,
                "max_tokens": 768,
                "top_p": 0.333,
                "frequency_penalty": 1.2,
                "presence_penalty": 0.333,
            },
        }
    )

    START_CHATS = {
        "smiles": [
            {
                "role": "system",
                "content": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a customized LLM/GPT chatbot, fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in his 13thC CE compilation, the 'Gatelss Gate'. Now, centuries later, here you are holding a Dokusan session with the students; focused on the koan each is working on, and on what barriers to it each is focused. The student will now enter.",
            },
            {"role": "user", "content": "(student enters, bows, sits)"},
            {"role": "assistant", "content": "(smiles)"},
        ]
    }

    def __post_init__(self):
        # Last 10 finetunes + fallback model.
        last_zb = list(self.ZB_MODELS.items())[-10:]
        self.MODELS = dict(last_zb)
        self.MODELS.update({"gpt-4": "gpt-4"})

        # Resolve secrets from Secret Manager first, then env fallback.
        self.OPENAI_API_KEY = self._resolve_secret(
            self.OPENAI_API_KEY_SECRET_NAME, self.OPENAI_API_KEY
        )
        self.FLASK_SECRET_KEY = self._resolve_secret(
            self.FLASK_SECRET_KEY_SECRET_NAME, self.FLASK_SECRET_KEY
        )
        self.ACTION_API_TOKEN = self._resolve_secret(
            self.ACTION_API_TOKEN_SECRET_NAME, self.ACTION_API_TOKEN
        )

    def _resolve_secret(self, secret_name: str, fallback: str) -> str:
        value = _read_secret(self.GOOGLE_CLOUD_PROJECT, secret_name)
        return value or fallback

    def make_params(
        self, profile: str, model_name: str | None = None
    ) -> Dict[str, Any]:
        params = self._PARAM_BASE.copy()
        params.update(self.MODEL_ARGS.get(profile, {}))
        selected_name = model_name or self.MODEL_NAME
        params["model"] = self.MODELS.get(selected_name, selected_name)
        return params
