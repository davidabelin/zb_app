# utilities.py
from datetime import datetime
import os
import json
import random as rnd
import logging
from google.cloud import storage, firestore
from flask import request, jsonify  # For error responses in prompt_and_reply and get_model_reply
import openai
from openai import OpenAI
from config import Config
import json

class SessionManager:
    def __init__(self, config):
        self.config = config
        self.args = self._get_next_args()

    def _get_next_args(self, case_id=None, student=None) -> dict:
        # Essentially move your existing get_next_model() here:
        self.config.CASE_ID = case_id
        self.config.STUDENT = student
        # pick a new model name & loss
        model_key = rnd.choice(list(self.config.MODELS.keys()))
        self.config.MODEL_NAME = model_key
        self.config.TRAINING_LOSS = self.config.MODEL_LOSSES[model_key]
        # build the args dict
        clargs = self.config.PARAMS.copy()
        clargs["model"] = self.config.MODELS[model_key]
        profile = rnd.choice(list(self.config.MODEL_ARGS))
        clargs.update(self.config.MODEL_ARGS[profile])
        return clargs

    def reset(self, case_id=None, student=None) -> dict:
        """Call whenever you need a brand-new session_mgr.args set."""
        self.args = self._get_next_args(case_id, student)
        return self.args

# #### Instantiation of persistent variables:
# Global presets imported from config.py
config = Config()
# Client for model training
BOTLING = OpenAI(api_key=config.OPENAI_API_KEY)
# Cloud storage for archiving chats
BUCKET = storage.Client().bucket(config.BUCKET_NAME)
# Firestore client for in-chat storage
DB = firestore.Client()

# Botling evaluation tools
session_mgr = SessionManager(config)

# Reset botling model and test parameters, chosen rndly
# Must be called before EVERY chat when in Evaluation Mode
def get_next_model(config=config, case_id=None, student=None):
    '''
    config: object containing preset model params
    returns next model and parameters
    '''
    config.CASE_ID = case_id
    config.STUDENT = student
    config.KOAN = {
        'id': case_id,
        'title': 'None',
        'body': 'Empty'
    }
    # Reset changed params
    # Access the first item in the MODEL dict
    #config.MODEL_NAME = next(iter(config.MODELS.keys()))
    # OR rndly
    config.MODEL_NAME = rnd.choice(list(config.MODELS.keys()))
    config.TRAINING_LOSS = config.MODEL_LOSSES[config.MODEL_NAME]
    clargs = config.PARAMS.copy()
    clargs['model'] = config.MODELS[config.MODEL_NAME]
    test_params = rnd.choice(list(config.MODEL_ARGS.keys()))
    for arg in config.MODEL_ARGS[test_params]:
        clargs[arg] = config.MODEL_ARGS[test_params][arg]
    return clargs

# Load starting params for very first session after last app update:
#CLARGS=get_next_model()
def reset_test(case_id=None, student=None):
    session_mgr.reset(case_id, student)

# Load starting params thereafter with /reset_test:
def reset_test_og(config=config, case_id=None, student=None):
    '''
    Globally load new test model and parameters
    Called at start of every new dokusan session
    '''
    try:
        global CLARGS
        CLARGS = get_next_model(config, case_id, student)
    except Exception as e:
        logging.error(f"utilities.reset_test() failed: {e}")
    return

# ########## CHAT FUNCTIONS MOVED FROM MAIN.PY ###########
def get_cid(student=None):
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    if student:
        conversation_id = f'zb-{student}-{timestamp}'
    elif config.LOCAL:
        conversation_id = f'zb-local-{timestamp}'
    else:
        conversation_id = f'zb-app-{timestamp}'
    return conversation_id

def save_messages_to_firestore(conversation_id, messages):
    try:
        doc_ref = DB.collection('conversations').document(conversation_id)
        doc_ref.set({'messages': messages})
    except Exception as e:
        logging.error(f"Error saving messages to Firestore: {e}")

def get_messages_from_firestore(conversation_id):
    try:
        doc_ref = DB.collection('conversations').document(conversation_id)
        doc = doc_ref.get()
        # Retrieve messages if already chatting
        if doc.exists:
            return doc.to_dict().get('messages', [])
        # Else start a new conversation
        if config.LOCAL: print("Firestore: document not found. Starting a new conversation.")
    except Exception as e:
        #print(f"Error getting messages from Firestore: {e}")
        logging.error(f"Error retrieving messages from Firestore: {e}")
    return config.START_CHATS['smiles'].copy()

def delete_messages_from_firestore(conversation_id):
    DB.collection('conversations').document(conversation_id).delete()

def get_model_stream(messages):
    ''' NEW Streaming feature being implemented..
        Request completion from Client using pretrained model BOTLING
        Params:
            messages: (dict of strings) the full transcript up to this point in chat
        Returns:
            stream: returns the streaming response from the 'botling' (f.t. model)
    '''
    try:
        stream = BOTLING.chat.completions.create(
                                messages=messages,
                                stream=True,  # streaming enabled!
                                **session_mgr.args)
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except openai.APIConnectionError as e:
        logging.error(f"OpenAI API connection error: {e}")
        return jsonify({"error": str(e), "message": "Unable to connect to the AI model. Please check your network connection."}), 500
    except openai.APIError as e:
        logging.error(f"OpenAI API error: {e}")
        return jsonify({"error": str(e), "message": "An error occurred with the AI model. Please try again later."}), 500
    except Exception as e:
        logging.error(f"Unexpected error thrown in /get_model_reply(): {e}")
        return jsonify({"error": str(e), "message": "Unexpected error thrown in /get_model_reply()."}), 500

def prompt_and_stream(messages, prompt):
    ''' NEW Streaming feature being implemented..'''
    if not isinstance(prompt, str) or len(prompt) > 2048:  # string and length limit
        return jsonify({"error": "/prompt_and_reply(): Invalid input."}), 400
    messages.append({"role": "user", "content": prompt})
    #return messages # back to chat() or zb_api_chat()
    response_generator = get_model_reply(messages)
    # Accumulate the chunks and yield the complete reply
    full_reply = ""
    for chunk in response_generator:
        full_reply += chunk
        yield "data: " + json.dumps({"response": chunk}) + "\n\n" #SSE formatting
    messages.append({"role": "assistant", "content": full_reply})
    yield "data: " + json.dumps({"response": "[DONE]"}) + "\n\n"

def get_model_reply(messages):
    ''' 
        Request completion from Client using pretrained model BOTLING
        Params:
            messages (dict of strings):
                    the full transcript up to this point in chat,
                    with user's prompt appended
        Returns:
            reply (str): the chat completion string (expected in completion.choices[0].message.content)
                         or else the OpenAI error if there was a problem 
        '''
    try:
        #print(f"get_model_reply().messages[0].TYPE: {type(messages[0])}")
        #print(f"get_model_reply().messages[0]:")
        #print(messages[0])
        completion = BOTLING.chat.completions.create(
                                messages=messages,
                                **session_mgr.args)
        #print(f"get_model_reply().completion.choices[0].message.TYPE: {type(completion.choices[0].message)}")
        #print(f"get_model_reply().completion.choices[0].message:")
        #print(completion.choices[0].message)
        return completion.choices[0].message.content #to /prompt_and_reply()
    except openai.APIConnectionError as e:
        logging.error(f"OpenAI API connection error: {e}")
        return f"OpenAI API connection error caught in /get_model_reply():\n{e}"
    except openai.APIError as e:
        logging.error(f"OpenAI API error: {e}")
        return f"OpenAI API error caught in /get_model_reply():\n{e}"
    except Exception as e:
        logging.error(f"Unexpected error caught in /get_model_reply(): {e}")
        return f"Unexpected error caught in /get_model_reply():\n{e}"
    # All return strings back to /prompt_and_reply()

def prompt_and_reply(messages, prompt):
    ''' Messages (dict of str) + Prompt(str) = Completion (str) from the botling
        Returns: messages (dict of str) with completion appended
        Or an error...?
    '''
    #Not necessary really:
    #if not isinstance(prompt, str) or len(prompt) > 2048:  # string and length limit
    #    return jsonify({"error": "/prompt_and_reply(): Invalid input."}), 400
    messages.append({"role": "user", "content": prompt})
    assistant_reply = get_model_reply(messages) 
    messages.append({"role": "assistant", "content": assistant_reply})
    return messages # back to chat() or zb_api_chat()

def save_chat_to_file(data, params, file_path):
    jsonl_content = to_jsonl(params, data)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(jsonl_content)

def save_chat_to_file_og(data, params, file_path):
    # Sent from /save_chat()
    # Convert data to JSONL format
    lines = [json.dumps(params)] + [json.dumps(item) for item in data]
    jsonl_content = '\n'.join(lines)
    # Save locally
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(jsonl_content)

def get_chat_from_file(file_path):
    data = []
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            data.append(json.loads(line.strip()))
    return data

def save_chat_to_bucket(data, params, blob_name):
    jsonl_content = to_jsonl(params, data)
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(jsonl_content, content_type="application/jsonl")

def save_chat_to_bucket_og(data, params, blob_name):
    # Convert data to JSONL format
    lines = [json.dumps(params)] + [json.dumps(item) for item in data]
    jsonl_content = '\n'.join(lines)
    # Save to Google Cloud Storage
    blob = BUCKET.blob(blob_name)
    blob.upload_from_string(jsonl_content, content_type='application/jsonl')

def list_conversation_files_in_gcs():
    blobs = BUCKET.list_blobs(prefix='zbchats/')
    conversation_files = [blob.name for blob in blobs if blob.name.endswith('.jsonl')]
    return conversation_files

def get_conversation_from_gcs(conversation_id):
    filename = f'zbchats/{conversation_id}.jsonl'
    blob = BUCKET.blob(filename)
    if blob.exists():
        content = blob.download_as_string()
        lines = content.decode('utf-8').splitlines()
        messages = [json.loads(line) for line in lines] #[1:]
        return messages
    else:
        if config.LOCAL: print(f"No conversation found with ID {conversation_id}.")
        return None

def get_all_conversations_from_gcs():
    ''' Returns dictionary by id of all conversations in GCS bucket'''
    blobs = BUCKET.list_blobs(prefix='zbchats/')
    conversations = {}
    for blob in blobs:
        if blob.name.endswith('.jsonl'):
            conversation_id = blob.name.split('/')[-1].replace('.jsonl', '')
            content = blob.download_as_string()
            lines = content.decode('utf-8').splitlines()
            # First line is params
            # Keep it in; BOTLING_params = json.loads(lines[0])
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

# ########## ADDITIONAL FUNCTIONALITY #######
#

def to_jsonl(params: dict, messages: list) -> str:
    """
    Convert a params dict and a list of message‐dicts into a JSONL string.
    First line is the params JSON, each subsequent line is one message.
    """
    lines = [json.dumps(params)] + [json.dumps(item) for item in messages]
    return "\n".join(lines)


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
        return []
    
def update_logbook():
    '''
    Load existing memory logbook, append a new entry to the end, and save back to GCS bucket.
    '''
    logbook = load_memory_logbook()
    if not logbook or len(logbook)==0:
        logbook = []
        if config.LOCAL: print(f"No memory logbook found in GCS Bucket.")
    return logbook

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

def get_request_data():  # Helper function to handle both GET and POST data
    logging.info(f"get_request_data(): request.method = {request.method}")
    if request.method == 'POST':
        data = request.get_json()
        if not data:  # Handle empty or malformed JSON in POST
            logging.warning(f"get_request_data(): POST request data is empty")
            data = {} # <--- Add this line.
        logging.info(f"get_request_data(): POST data = {data}") # <--- Modified to capture data
    else: # GET
        data = request.args # ImmutableMultiDict; convert to regular dict:
        data = data.to_dict()
        logging.info(f"get_request_data(): GET data = {data}")
    return data

def process_chat(conversation_id, user_input):
    ''' Not in use but should be '''
    messages = get_messages_from_firestore(conversation_id)
    messages = prompt_and_reply(messages, user_input)
    save_messages_to_firestore(conversation_id, messages)
    return messages
