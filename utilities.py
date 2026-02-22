
# utilities.py (refactored for Config.make_params)
from datetime import datetime
import os
import json
import random as rnd
import logging
from pathlib import Path
from typing import Optional

# Optional Google Cloud dependencies. The app can run locally without them, but
# features backed by GCS/Firestore will be disabled unless installed.
try:
    from google.cloud import storage, firestore
except ModuleNotFoundError:
    storage = None
    firestore = None
    logging.warning(
        "utilities: Google Cloud libs not installed; GCS/Firestore disabled. "
        "Install `google-cloud-storage` and `google-cloud-firestore`."
    )
from flask import request, jsonify
try:
    import openai
    from openai import OpenAI
except ModuleNotFoundError:
    openai = None
    OpenAI = None
    logging.warning(
        "utilities: OpenAI Python SDK not installed; chat features disabled. "
        "Install `openai` (see requirements.txt)."
    )
from config import Config
from models import MODEL_LOSSES

class SessionManager:
    def __init__(self, config: Config):
        self.config = config
        self.args = self._get_next_args()

    def _get_next_args(self, case_id=None, student=None) -> dict:
        # Update dynamic state
        self.config.CASE_ID = case_id
        self.config.STUDENT = student
        # Select a random model
        model_key = rnd.choice(list(self.config.MODELS.keys()))
        self.config.MODEL_NAME = model_key
        # Set training loss from MODEL_LOSSES
        self.config.TRAINING_LOSS = MODEL_LOSSES.get(model_key, 0.0)
        # Choose a random profile
        profile = rnd.choice(list(self.config.MODEL_ARGS.keys()))
        # Build parameters using the dataclass helper
        params = self.config.make_params(profile)
        return params

    def reset(self, case_id=None, student=None) -> dict:
        """Reset session parameters for a new conversation."""
        self.args = self._get_next_args(case_id, student)
        return self.args

class ModelAPIError(Exception):
    """Raised when an OpenAI API call fails."""
    pass

# Instantiate global clients and state
config = Config()
BOTLING = OpenAI(api_key=config.OPENAI_API_KEY) if OpenAI is not None else None

BUCKET = None
if storage is not None:
    try:
        BUCKET = storage.Client().bucket(config.BUCKET_NAME)
    except Exception as e:
        logging.warning(f"utilities: GCS bucket client unavailable: {e}")

DB = None
if firestore is not None:
    try:
        DB = firestore.Client()
    except Exception as e:
        logging.warning(f"utilities: Firestore client unavailable: {e}")

MEMORY_LOGBOOK = config.MEMORY_LOGBOOK
_LOCAL_LOGBOOK_PATH = Path(__file__).resolve().parent / 'config' / MEMORY_LOGBOOK

# Initialize session manager
session_mgr = SessionManager(config)

def reset_test(case_id=None, student=None):
    """Reset the session manager for a fresh conversation."""
    try:
        return session_mgr.reset(case_id, student)
    except Exception as e:
        err_msg = f"utilities.reset_test() failed: {e}"
        logging.error(err_msg)
        raise ModelAPIError(err_msg)

# ---------------- Session Tools ------------

def get_cid(student=None) -> str:
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if student:
        return f'zb-{student}-{timestamp}'
    return f'zb-local-{timestamp}' if config.LOCAL else f'zb-app-{timestamp}'

def save_messages_to_firestore(conversation_id: str, messages: list) -> None:
    try:
        if not DB:
            return
        DB.collection('conversations').document(conversation_id).set({'messages': messages})
    except Exception as e:
        logging.error(f"Error saving messages to Firestore: {e}")

def get_messages_from_firestore(conversation_id: str) -> list:
    try:
        if not DB:
            return config.START_CHATS['smiles'].copy()
        doc = DB.collection('conversations').document(conversation_id).get()
        if doc.exists:
            return doc.to_dict().get('messages', [])
    except Exception as e:
        logging.error(f"Error retrieving messages from Firestore: {e}")
    # Fallback to starter chat
    return config.START_CHATS['smiles'].copy()

def delete_messages_from_firestore(conversation_id: str) -> None:
    if not DB:
        return
    DB.collection('conversations').document(conversation_id).delete()

# ---------------- Interacting ------------

def get_model_stream(messages: list):
    """Streamed completion from the model."""
    if BOTLING is None or openai is None:
        raise ModelAPIError(
            "OpenAI client unavailable. Install `openai` and set `OPENAI_API_KEY`."
        )
    try:
        stream = BOTLING.chat.completions.create(
            messages=messages,
            stream=True,
            **session_mgr.args
        )
        for chunk in stream:
            text = chunk.choices[0].delta.content
            if text:
                yield text
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        raise ModelAPIError(str(e))

def get_model_reply(messages: list) -> str:
    """Synchronous completion from the model."""
    if BOTLING is None or openai is None:
        raise ModelAPIError(
            "OpenAI client unavailable. Install `openai` and set `OPENAI_API_KEY`."
        )
    try:
        completion = BOTLING.chat.completions.create(
            messages=messages,
            **session_mgr.args
        )
        return completion.choices[0].message.content
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        raise ModelAPIError(str(e))

### TO DO FINISH AS NEEDED

def _load_mmnk_cases() -> list[dict]:
    with open('static/mmnk.json', 'r', encoding='utf-8') as f:
        payload = json.load(f)
    cases = payload.get('cases', [])
    return cases if isinstance(cases, list) else []

def get_mmnk_case(case_id: str) -> Optional[dict]:
    """Retrieve the koan case object for a given case ID from mmnk.json."""
    try:
        cases = _load_mmnk_cases()
        return next((k for k in cases if str(k.get('id')) == str(case_id)), None)
    except Exception as e:
        logging.error(f"Error retrieving koan case for case ID {case_id}: {e}")
        return None

def get_random_koan_case_id() -> str:
    """Retrieve a random koan case ID from mmnk.json (fallback: 1..48)."""
    try:
        cases = _load_mmnk_cases()
        if cases:
            koan = rnd.choice(cases)
            return str(koan.get('id'))
    except Exception as e:
        logging.error(f"Error retrieving random koan case ID: {e}")

    return str(rnd.choice(range(1, 49)))


def get_mmnk_text(case_id: str) -> str:
    """Retrieve the koan text for a given case ID from the mmnk.json dataset."""
    koan = get_mmnk_case(case_id)
    if not koan:
        return ""
    body = koan.get('body')
    return body if isinstance(body, str) else ""


# ---------------- Prompting ----------------

def prompt_and_stream(messages: list, prompt: str):
    if not isinstance(prompt, str) or len(prompt) > 2048:
        return jsonify({"error": "Invalid input."}), 400
    messages.append({"role": "user", "content": prompt})
    full_reply = ""
    for chunk in get_model_stream(messages):
        full_reply += chunk
        yield "data: " + json.dumps({"response": chunk}) + "\n\n"
    messages.append({"role": "assistant", "content": full_reply})
    yield "data: " + json.dumps({"response": "[DONE]"}) + "\n\n"

def prompt_and_reply(messages: list, prompt: str) -> list:
    messages.append({"role": "user", "content": prompt})
    reply = get_model_reply(messages)
    messages.append({"role": "assistant", "content": reply})
    return messages

# ---------------- LOCAL Files ----------------
def save_chat_to_file(data: list, params: dict, file_path: str) -> None:
    content = to_jsonl(params, data)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

def get_chat_from_local_file(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            data.append(json.loads(line.strip()))
    return data

# ---------------- GCS BUCKET Files ----------------

def to_jsonl(params: dict, messages: list) -> str:
    lines = [json.dumps(params)] + [json.dumps(m) for m in messages]
    return "\n".join(lines)

def save_chat_to_bucket(data: list, params: dict, blob_name: str) -> None:
    if not BUCKET:
        raise RuntimeError("GCS bucket client unavailable; cannot save chat to bucket.")
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(to_jsonl(params, data), content_type='application/jsonl')

def get_all_conversations_from_gcs():
    ''' Returns dictionary by id of all conversations in GCS bucket'''
    if not BUCKET:
        return {}
    blobs = BUCKET.list_blobs(prefix='zbchats/')
    conversations = {}
    for blob in blobs:
        if blob.name.endswith('.jsonl'):
            conversation_id = blob.name.split('/')[-1].replace('.jsonl', '')
            content = blob.download_as_string()
            lines = content.decode('utf-8').splitlines()
            # First line is params
            messages = [json.loads(line) for line in lines] #[1:]]
            conversations[conversation_id] = messages
    return conversations

def download_all():
    '''    Retrieve all chats from GCS and save locally.
           To be called by button-click from /admin/conversations
           TO DO error handling
    '''
    try:
        if config.LOCAL:
            full_bucket = get_all_conversations_from_gcs()
            for (c_id, c_text) in full_bucket.items():
                filename = c_id.replace('\"', '') + ".jsonl"
                filepath = os.path.join('config', 'zbchats', filename)
                print("c_id: ", c_id.replace('\"', ''), "\tfilepath: ", filepath)
                lines = [json.dumps(item) for item in c_text]
                #print(f"lines[0]: {lines[0]}")
                jsonl_content = '\n'.join(lines)
                # Save in relative local app directory for now
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(jsonl_content)
            del full_bucket
            return True
        else:
            print("Unable to download: this is not a local environment.")
            return False
    except Exception as e:
        logging.error(f"Unknown exception in download_all(): {e}")
        return False

def list_conversation_files_in_gcs() -> list:
    if not BUCKET:
        return []
    return [b.name for b in BUCKET.list_blobs(prefix='zbchats/') if b.name.endswith('.jsonl')]

def get_conversation_from_gcs(conversation_id: str) -> list:
    if not BUCKET:
        return None
    blob = BUCKET.blob(f'zbchats/{conversation_id}.jsonl')
    if blob.exists():
        return [json.loads(line) for line in blob.download_as_string().decode().splitlines()]
    return None

# -------- Memory Logbook ------------------
def _parse_logbook_payload(payload: str) -> list:
    text = (payload or "").strip()
    if not text:
        return []

    if text.startswith('['):
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    memories: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            memories.append(obj)
    return memories

def _logbook_blob_candidates() -> list[str]:
    candidates: list[str] = [MEMORY_LOGBOOK]
    if MEMORY_LOGBOOK.endswith('.json'):
        candidates.append(MEMORY_LOGBOOK[:-5] + '.jsonl')
    elif not MEMORY_LOGBOOK.endswith('.jsonl'):
        candidates.append(MEMORY_LOGBOOK + '.jsonl')
    # Preserve order, remove duplicates.
    seen = set()
    unique: list[str] = []
    for name in candidates:
        if name and name not in seen:
            unique.append(name)
            seen.add(name)
    return unique

def load_memory_logbook() -> list:
    """
    Load existing memory logbook from the GCS bucket.

    Stored format is JSONL (one JSON object per line), but JSON arrays are also accepted.
    """
    payload = None

    if BUCKET:
        for blob_name in _logbook_blob_candidates():
            try:
                blob = BUCKET.blob(blob_name)
                if not blob.exists():
                    continue
                payload = blob.download_as_text()
                break
            except Exception as e:
                logging.warning(f"Error loading memory logbook blob '{blob_name}': {e}")

    if payload is None:
        try:
            if _LOCAL_LOGBOOK_PATH.exists():
                payload = _LOCAL_LOGBOOK_PATH.read_text(encoding='utf-8')
        except Exception as e:
            logging.warning(f"Error loading local memory logbook at {_LOCAL_LOGBOOK_PATH}: {e}")

    return _parse_logbook_payload(payload or "")

def update_logbook(new_entry: dict) -> list:
    """
    Append one entry to the logbook and save.
    """
    logbook = load_memory_logbook()
    logbook.append(new_entry)
    save_logbook(logbook)
    return logbook

def save_logbook(logbook: list):
    """
    Overwrite the entire memory logbook with the provided list.
    """
    lines = [json.dumps(item, ensure_ascii=False, default=str) for item in logbook]
    payload = "\n".join(lines)

    if BUCKET:
        blob = BUCKET.blob(MEMORY_LOGBOOK)
        blob.upload_from_string(payload, content_type='application/jsonl')
    else:
        _LOCAL_LOGBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LOCAL_LOGBOOK_PATH.write_text(payload, encoding='utf-8')

# --------- Koan Work --------------

def koan_startup(koan=config.KOAN):
    start_with_koan = [
        {
            "role": "system",
            "content": "You are Mumonbot, the faithful emulation of a renowned Zen Master! You are a customized LLM/GPT chatbot, fine-tuned on Zen Master Mumon Ekai's classic commentaries on the canonical Chinese koans collected in his 13thC CE compilation, the 'Gatelss Gate'. Now, centuries later, here you are holding a Dokusan session with the students; focused on the koan each is working on, and on what barriers to it each is focused. The student will now enter."
        },
        {
            "role": "user",
            "content": str(f"(student enters, bows, sits) Teacher Mumonbot, I am working on the case of {koan['title']}.")
        },
        {
            "role": "system",
            "content": str(f"Recall the exact wording of case #{koan['id']} from the original text:\n{koan['body']}.")
        },
        {
            "role": "assistant",
            "content": "(smiles)"
        }
    ]
    return start_with_koan

def create_koan_conversation(config=config):
    ''' Called from routes /chat_case/<case_id> and /zb_api/chat_case/<case_id>
        Sets up the first exchange in a chat on a pre-specified koan.
            case_id (string of digits): the user-selected koan id
            student_name (string): the bot-evaluator playing the Student role
        returns:
            conversation_id: unique identifier created at start of chat
            or None if error
    '''
    try:
        with open('static/mmnk.json', 'r', encoding='utf-8') as f:
            all_koans = json.load(f)
        config.KOAN = next((k for k in all_koans['cases'] if str(k['id']) == str(config.CASE_ID)), None)
        #print(f"Koan #{config.KOAN['id']}:\t{config.KOAN['title']}")
        if not config.KOAN:
            logging.error(f"Koan not found or unspecified in create_koan_conversation: case_id={config.CASE_ID})")
            return None
        # Initial start-up context, including the assistant reciting the koan:
        messages = koan_startup(config.KOAN) #config.START_CHATS["case_id"]
        #print(f"START CHAT (messages):\n{messages}")
        if config.STUDENT: # != 'unk':
            conversation_id = get_cid(f"{config.STUDENT}-mmnk{config.CASE_ID}")
        else:
            conversation_id = get_cid(f"mmnk{config.CASE_ID}")
        # The user by referencing this conversation_id will
        # enter the conversation following the initial start-up context
        # normally used to retrieve history of ongoing chat from firestore
        save_messages_to_firestore(conversation_id, messages)
        return conversation_id
    except Exception as e:
        logging.error(f"Error in create_koan_conversation: {e}")
        return None

# -------- Assorted Helpers --------

def get_request_data() -> dict:
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
    else:
        data = request.args.to_dict()
    return data

def process_chat(conversation_id, user_input):
    ''' Not in use but should be '''
    messages = get_messages_from_firestore(conversation_id)
    messages = prompt_and_reply(messages, user_input)
    save_messages_to_firestore(conversation_id, messages)
    return messages
