# config.py
# Configuration of variables
from dataclasses import dataclass, field
from typing import Dict, Any
import os
from dotenv import load_dotenv
import logging
from models import ZB_MODELS

# Load environment variables
load_dotenv('config/.env')

@dataclass
class Config:
    # Environment & secrets
    OPENAI_API_KEY: str = field(default_factory=lambda: os.getenv('OPENAI_API_KEY', ''))
    FLASK_SECRET_KEY: str = field(default_factory=lambda: os.getenv('FLASK_SECRET_KEY', '1110111'))
    GOOGLE_API_KEY: str = field(default_factory=lambda: os.getenv('GOOGLE_API_KEY', ''))
    GOOGLE_APPLICATION_CREDENTIALS: str = field(default_factory=lambda: os.getenv('GOOGLE_APPLICATION_CREDENTIALS', ''))
    LOCAL: bool = field(default_factory=lambda: not os.getenv('GAE_ENV', '').startswith('standard'))
    BUCKET_NAME: str = 'zenbot_cloudstore'
    MEMORY_LOGBOOK: str = 'memory_logbook.json'
    MEMORY_ARCHIVES: str = 'memory_logbook_archives.json'

    # Dynamic state
    STUDENT: str = None
    CASE_ID: int = 0
    KOAN: Dict[str, Any] = field(default_factory=dict)
    MODEL_NAME: str = 'gpt-4o'
    TRAINING_LOSS: float = 0.0
    STREAMING: bool = False
    LOG_LEVEL: int = logging.WARNING

    # Model registries
    ZB_MODELS: Dict[str, str] = field(default_factory=lambda: ZB_MODELS)
    #XC_MODELS: Dict[str, str] = field(default_factory=lambda: XC_MODELS)
    # Active models: pick newest ZB + GPT-4 as fallback
    MODELS: Dict[str, str] = field(init=False)

    # Parameter defaults
    _PARAM_BASE: Dict[str, Any] = field(default_factory=lambda: {
        "temperature": 0,
        "max_tokens": 0,
        "top_p": 0,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "response_format": {"type": "text"}
    })

    MODEL_ARGS: Dict[str, Dict[str, Any]] = field(default_factory=lambda: {
        "high": {"temperature": 1.75, "max_tokens": 5120, "top_p": 0.75, "frequency_penalty": 1.75, "presence_penalty": 0.75},
        "mid":  {"temperature": 1.0,  "max_tokens": 1024, "top_p": 0.5,  "frequency_penalty": 1.0,  "presence_penalty": 0.5},
        "low":  {"temperature": 0.5,  "max_tokens": 512,  "top_p": 0.25, "frequency_penalty": 0.25, "presence_penalty": 0.075},
        "good": {"temperature": 1.2,  "max_tokens": 768,  "top_p": 0.333,"frequency_penalty": 1.2,  "presence_penalty": 0.333},
    })

    # Predefined starting chat flows
    START_CHATS = {
        "smiles": [
            {
                "role": "system",
                "content": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a customized LLM/GPT chatbot, fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in his 13thC CE compilation, the 'Gatelss Gate'. Now, centuries later, here you are holding a Dokusan session with the students; focused on the koan each is working on, and on what barriers to it each is focused. The student will now enter."
            },
            {
                "role": "user",
                "content": "(student enters, bows, sits)"
            },
            {
                "role": "assistant",
                "content": "(smiles)"
            },
        ]
    }

    def __post_init__(self):
        # Select the last 10 entries from ZB_MODELS + include gpt-4
        last_zb = list(self.ZB_MODELS.items())[-10:]
        self.MODELS = dict(last_zb)
        self.MODELS.update({"gpt-4": "gpt-4"})
        # Apply log level
        logging.basicConfig(level=self.LOG_LEVEL)

    def make_params(self, profile: str) -> Dict[str, Any]:
        """
        Build a merged parameter set: base + profile overrides + model selection
        """
        params = self._PARAM_BASE.copy()
        params.update(self.MODEL_ARGS.get(profile, {}))
        params["model"] = self.MODELS.get(self.MODEL_NAME, self.MODEL_NAME)
        return params
