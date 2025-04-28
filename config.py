# config.py
# Configuration of variables
import os
from dotenv import load_dotenv
import logging

# Load environment variables from config/.env
load_dotenv('config/.env')

class Config:
    '''
        container for shared variables
        set in utilities.py and used in main.py
        reset by utilities.reset_test()
    '''
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    FLASK_SECRET_KEY = os.getenv('FLASK_SECRET_KEY', '1110111')
    GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
    GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    # Determine if running locally
    LOCAL = not os.getenv('GAE_ENV', '').startswith('standard')
    BUCKET_NAME = 'zenbot_cloudstore'
    MEMORY_LOGBOOK = 'memory_logbook.json'
    MEMORY_ARCIVES = 'memory_logbook_archives.json'
    STUDENT = None
    CASE_ID = 0
    KOAN = {}
    MODEL_NAME = 'gpt-4o'
    PARAMS = {
        "model": MODEL_NAME,
        "temperature": 0,
        "max_tokens": 0,
        "top_p": 0,
        "frequency_penalty": 0,
        "presence_penalty": 0,
        "response_format": {"type": "text"}
    }   # ... No function calls for Zenbot?!
    TRAINING_LOSS = 0.0 # by assumption
    STREAMING = False # It's still being implmented
    LOG_LEVEL = logging.WARNING # Set to CRITICAL for least,
                                # INFO for most verbose setting
    # Model parameters (for OpenAI API client completion calls)
    MODEL_ARGS = {
        "high": {
            "temperature": 1.75,
            "max_tokens": 5120,
            "top_p": 0.75,
            "frequency_penalty": 1.75,
            "presence_penalty": 0.75,
            "response_format": {"type": "text"},
        },
        "low": {
            "temperature": 0.5,
            "max_tokens": 512,
            "top_p": 0.25,
            "frequency_penalty": 0.25,
            "presence_penalty": 0.075,
            "response_format": {"type": "text"},
        },
        "mid": {
            "temperature": 1.0,
            "max_tokens": 1024,
            "top_p": 0.5,
            "frequency_penalty": 1.0,
            "presence_penalty": 0.5,
            "response_format": {"type": "text"},
        },
        "good": {
            "temperature": 1.2,
            "max_tokens": 768,
            "top_p": 0.333,
            "frequency_penalty": 1.2,
            "presence_penalty": 0.333,
            "response_format": {"type": "text"},
        }
    }
    ZB_MODELS =  {
        "mmnk_ble824": "ft:gpt-4o-mini-2024-07-18:chatbot-tuners:mmnk-ble824:A7q3grXA",
        'mmnk_ble5053_cntxt': 'ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble5053:ATHGiQEV',
        'mmnk_ble8053_cntxt': 'ft:gpt-4o-2024-08-06:chatbot-tuners:zenbot-context-ble8053:AV7mu6NG',
        'mmnk-bows-ble513': 'ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble513:AVXPlofK',
        'mmnk-bows-ble5053': 'ft:gpt-4o-2024-08-06:chatbot-tuners:mmnk-bows-ble5053:AVXUbfHM',
        "zbset01-bs3-lr15-ne2": "ft:gpt-4o-2024-08-06:dca:set01-bs3-lr15-ne2:AyP4ENFm",
        "zbset01-bs3-lr075-ne2": "ft:gpt-4o-2024-08-06:dca:zbset01-bs3-lr075-ne2:AyMDelJ3",
        "zbset01-bs5-lr1-ne3": "ft:gpt-4o-2024-08-06:dca:set01-bs5-lr1-ne3-av7ncsif-s20:B3ntgBgb",
        "base-bs8lr05e2": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2:B9PxHhZT",
        "base-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr08e3:B9NVrQ17",
        "base-bs6lr01e2": "ft:gpt-4o-2024-08-06:dca:base-bs6lr01e2:B9KhhY0V",
        "base-bs8lr08e3-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1fEeyM",
        "base-bs8lr05e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:base-bs8lr05e2-cntxt-bs8lr08e3:BD1eYc2n",
        "base-bs6lr01e2-cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD1p2sLf",
        "cntxt-bs8lr08e3": "ft:gpt-4o-2024-08-06:dca:cntxt-bs8lr08e3:BD220aUe",
        "bs8lr08e4-set00-02": "ft:gpt-4o-2024-08-06:dca:bs8lr08e4-set00-02:BIzpjOBE",
        "set00-02mix-bs10lr05e2": "ft:gpt-4o-2024-08-06:dca:bd1eyc2n-set00-02mix-bs10lr05e2:BJ02Ragt",
        "set00-02mix-bs6lr06e3": "ft:gpt-4o-2024-08-06:dca:b9khhy0v-set00-02mix-bs6lr06e3:BJ09MKSB",
        }
    XC_MODELS =  {
        "xcset01-bs3-lr3-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr3-ne3:Ay3Xz411",
        "xcset01-bs3-lr033-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr033-ne3:AxzCKK12",
        "xcset01-bs4-lr04-ne4": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs4-lr04-ne4:Ay3MdXVg",
        "xcset01-bs4-lr04-ne4-s54": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs4-lr04-ne4:Ay3McO8O:ckpt-step-54",
        "xcset01-bs2-lr04-ne4": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs2-lr04-ne4:Ay3TpHKf",
        "xcset01-bs2-lr04-ne4-s108": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs2-lr04-ne4:Ay3TpHKf:ckpt-step-108",
        "xcset01-bs3-lr1-ne3": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr1-ne3:Ay3OQylM",
        "xcset01-bs3-lr1-ne3-s72": "ft:gpt-4o-2024-08-06:chatbot-tuners:set01-bs3-lr1-ne3:Ay3OQS40:ckpt-step-72"
        }
    
    # Models actively being chosen for evaluation:
    MODELS = dict(item for item in list(ZB_MODELS.items())[-10:])  #.copy()
    MODELS.update({"gpt-4": "gpt-4"})
    
	# Training loss
    MODEL_LOSSES = {
        "gpt-4": 0.0,
        "xcset01-bs4-lr04-ne4": 0.3,
        "xcset01-bs4-lr04-ne4-s54": 0.5,
        "xcset01-bs2-lr04-ne4": 0.27,
        "xcset01-bs2-lr04-ne4-s108": 0.33,
        "xcset01-bs3-lr3-ne3": 1.86,
        "xcset01-bs3-lr1-ne3": 1.99,
        "xcset01-bs3-lr1-ne3-s72": 0.29,
        "xcset01-bs3-lr033-ne3": 2.09,
        "zbset01-bs5-lr1-ne3": 1.77,
        "zbset01-bs3-lr15-ne2": 0.296,
        "zbset01-bs3-lr075-ne2": 0.38,
        "mmnk_ble824": 1.32,
        'mmnk_ble5053_cntxt': 0.515,
        'mmnk_ble8053_cntxt': 0.747,
        'mmnk-bows-ble513': 0.334,
        'mmnk-bows-ble5053': 0.49,
        "base-bs8lr08e3": 1.6,
        "base-bs8lr05e2": 3.03,
        "base-bs6lr01e2": 3.01,
        "base-bs8lr08e3-cntxt-bs8lr08e3": 0.5968,
        "base-bs8lr05e2-cntxt-bs8lr08e3": 0.6002,
        "base-bs6lr01e2-cntxt-bs8lr08e3": 0.5997,
        "cntxt-bs8lr08e3": 0.5759,
		"bs8lr08e4-set00-02": 0.555,
		"set00-02mix-bs10lr05e2": 0.3922,
		"set00-02mix-bs6lr06e3": 0.5753
        }
    
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
        ],
        "random": [
            {
                "role": "system",
                "content": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a customized LLM/GPT chatbot, fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in his 13thC CE compilation, the 'Gatelss Gate'. Now, centuries later, here you are holding a Dokusan session with the students; focused on the koan each is working on, and on what barriers to it each is focused. The student will now enter."
            },
            {
                "role": "user",
                "content": "(student enters) I have selected the case of 'Proceed from the Top of the Pole' to focus on in this session, Teacher Mumonbot."
            },
            {
                "role": "system",
                "content": "Joshu said, 'Placeholding text carries no intrinsic meaning.'\n"
                            "'None whatsoever,' agreed the voice in the Assistant's head.\n\n"
                            "When Mumonbot indicates with the sign, the student may proceed with their question or comment."
            },
            {
                "role": "assistant",
                "content": "(nods)"
            },
        ]
    }