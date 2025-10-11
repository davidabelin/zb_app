
# utilities.py (refactored for Config.make_params)
from datetime import datetime
import os
import json
import random as rnd
import logging
from google.cloud import storage, firestore
from flask import request, jsonify
import openai
from openai import OpenAI
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
BOTLING = OpenAI(api_key=config.OPENAI_API_KEY)
BUCKET = storage.Client().bucket(config.BUCKET_NAME)
DB = firestore.Client()
MEMORY_LOGBOOK = config.MEMORY_LOGBOOK

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
        DB.collection('conversations').document(conversation_id).set({'messages': messages})
    except Exception as e:
        logging.error(f"Error saving messages to Firestore: {e}")

def get_messages_from_firestore(conversation_id: str) -> list:
    try:
        doc = DB.collection('conversations').document(conversation_id).get()
        if doc.exists:
            return doc.to_dict().get('messages', [])
    except Exception as e:
        logging.error(f"Error retrieving messages from Firestore: {e}")
    # Fallback to starter chat
    return config.START_CHATS['smiles'].copy()

def delete_messages_from_firestore(conversation_id: str) -> None:
    DB.collection('conversations').document(conversation_id).delete()

# ---------------- Interacting ------------

def get_model_stream(messages: list):
    """Streamed completion from the model."""
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
    except openai.OpenAIError as e:
        logging.error(f"OpenAI error: {e}")
        raise ModelAPIError(str(e))

def get_model_reply(messages: list) -> str:
    """Synchronous completion from the model."""
    try:
        completion = BOTLING.chat.completions.create(
            messages=messages,
            **session_mgr.args
        )
        return completion.choices[0].message.content
    except openai.OpenAIError as e:
        logging.error(f"OpenAI error: {e}")
        raise ModelAPIError(str(e))

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
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(to_jsonl(params, data), content_type='application/jsonl')

<<<<<<< HEAD
def  get_all_conversations_from_gcs():
=======
def get_all_conversations_from_gcs():
>>>>>>> 709cba9f08e1bbdf84b4739140ddd29f529acd28
    ''' Returns dictionary by id of all conversations in GCS bucket'''
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
    return [b.name for b in BUCKET.list_blobs(prefix='zbchats/') if b.name.endswith('.jsonl')]

def get_conversation_from_gcs(conversation_id: str) -> list:
    blob = BUCKET.blob(f'zbchats/{conversation_id}.jsonl')
    if blob.exists():
        return [json.loads(line) for line in blob.download_as_string().decode().splitlines()]
    return None

# -------- Memory Logbook ------------------
<<<<<<< HEAD
def load_memory_logbook():
    '''
    Load existing memory logbook from GCS bucket.
    '''
    blob = BUCKET.blob(config.MEMORY_LOGBOOK)
    if blob.exists():
        content = blob.download_as_string()
        lines = content.decode('utf-8').splitlines()
        memories = [json.loads(line) for line in lines]
        return memories
    else:
        if config.LOCAL: print(f"No memory logbook found in GCS Bucket.")
=======

def load_memory_logbook() -> list:
    # existing loader: download JSONL and return list of dicts
    try:
        payload = BUCKET.blob(MEMORY_LOGBOOK).download_as_text()
        return [json.loads(line) for line in payload.splitlines() if line.strip()]
    except Exception:
>>>>>>> 709cba9f08e1bbdf84b4739140ddd29f529acd28
        return []

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
    lines = [json.dumps(item) for item in logbook]
    payload = "\n".join(lines)
    blob = BUCKET.blob(MEMORY_LOGBOOK)
    blob.upload_from_string(payload, content_type='application/jsonl')

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
    #print(start_with_koan)
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
