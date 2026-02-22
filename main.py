# Web App: Zenbot Dokusan  --  https://zenbot-434517.uw.r.appspot.com/
# API schemas live in `../zenbot_knowledge/action_schemas.yaml`

import os
import logging
import json
import csv
from pathlib import Path
from flask import (Flask, jsonify, render_template, send_from_directory,
                   make_response, abort, request, Response, redirect, url_for)
from werkzeug.exceptions import BadRequest
import utilities as utipy
from utilities import (get_cid, save_messages_to_firestore, get_messages_from_firestore, get_request_data,
                       delete_messages_from_firestore, save_chat_to_bucket, reset_test,
                       prompt_and_reply, prompt_and_stream, save_chat_to_file,
                       get_conversation_from_gcs, list_conversation_files_in_gcs, save_logbook,
                       create_koan_conversation, load_memory_logbook, update_logbook, download_all,
                       ModelAPIError, save_logbook as replace_logbook)  # Import utility functions

# Log settings -- set in utilities.py
logging.basicConfig(level=utipy.config.LOG_LEVEL)   #, format='%(asctime)s - %(levelname)s - %(message)s')

# Start flask server
app = Flask(__name__, static_url_path='/static')
# Set Flask's secret key from environment variable
app.secret_key = utipy.config.FLASK_SECRET_KEY

# ########## FLASK APP STATIC ROUTES ###########
@app.route('/')
def home():
    try:
        return render_template('index.html')
    except:
        if utipy.config.LOCAL: print("Trying 'templates' directory.")
        return render_template('templates/index.html')
@app.route('/privacy')
def privacy():
    return render_template('privacy.html')
@app.route('/chatter')
def chatter():
    if utipy.config.STREAMING:
        return render_template('chatter_stream.html')
    else:
        return render_template('chatter.html')

# ########## FLASK ERROR HANDLING ###########
@app.errorhandler(ModelAPIError)
def handle_model_error(err):
    logging.error(f"ModelAPIError handler caught: {err}")
    return jsonify({
        "error": "model_error",
        "message": str(err)
    }), 502

# ########## CHAT ROUTES ###########
@app.route('/chat', methods=['POST', 'GET'])
def chat():
    try:
        logging.info(f"{request.method} request received by /chat with data: {request.data}")
        # Handle input consistently
        data = get_request_data()
        student = data.get('student', '')
        user_input = data.get('message', '')
        if not user_input or user_input=='':
            return jsonify({"error": "No user input detected."}), 400
        # Check for existing conversation via cookies by default
        conversation_id = data.get('conversation_id', 
                                   request.cookies.get('conversation_id', ''))
        # Assuming didn't throw an error getting it...
        if conversation_id and conversation_id!='':
            # Continue ongoing conversation:
            messages = get_messages_from_firestore(conversation_id)
            if utipy.config.LOCAL: print(f"Continuing conversation: {conversation_id}...")
        else:
            # Start a new conversation
            reset_test()
            if student and student!='':
                utipy.config.STUDENT = student
            else:
                utipy.config.STUDENT = 'webmonkE'
            conversation_id = get_cid(utipy.config.STUDENT)
            # Provide initial context
            messages = utipy.config.START_CHATS['smiles'].copy()
            if utipy.config.LOCAL: print(f"Starting new conversation: {conversation_id}.")
        if not utipy.config.STREAMING:
            # The usual routine: without streaming
            messages = prompt_and_reply(messages, user_input)
            response_message = messages[-1]['content']
            response = make_response(jsonify({"response": response_message,
                                              "conversation_id": conversation_id}), 200)
        else:
            # Now streaming!
            def stream():
                for event in prompt_and_stream(messages, user_input):
                    yield event
                    if "[DONE]" in event:
                        break # Exit loop
            # Prepare response as an SSE stream
            response = Response(stream(), mimetype='text/event-stream')
        save_messages_to_firestore(conversation_id, messages)
        # Set conversation ID in cookies if not already set
        if not request.cookies.get('conversation_id'):
            response.set_cookie('conversation_id', conversation_id)
        return response
    except BadRequest as e:
        logging.error(f"/chat route request error: {e}")
        if isinstance(e.original_exception, json.JSONDecodeError):
            return jsonify({"error": "JSONDecodeError", "message": "/chat: Did not find valid JSON data in request body."}), 400
        else:
            return jsonify({"error": str(e), "message": f"Bad Request error encountered in /chat route: {e}."}), 400
    except Exception as e:
        logging.error(f"/chat() route unexpected error: {e}")
        return jsonify({"error": str(e), "message": "Exception thrown in /chat() route."}), 500

@app.route('/chat_case/<case_id>', methods=['GET', 'POST'])
def chat_case(case_id):
    """Create a new conversation with the user’s selected Koan as context.
        To be called only from /ggcase route 
    """
    try:
        if not case_id:
            return jsonify({"error": "No case_id provided."}), 400
        student = request.args.get('student', utipy.config.STUDENT) or 'unk'
        reset_test(case_id=case_id, student=student)
        conversation_id = create_koan_conversation()
        if not conversation_id:
            return jsonify({"error": "Failed to create conversation."}), 500
        return jsonify({'conversation_id': conversation_id, 'case_id': str(case_id), 'status': 'success'}), 200
    except Exception as e:
        logging.error(f"Exception in /chat_case/<case_id>: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/save_chat', methods=['POST', 'GET']) # <--- Added 'GET' back, and see below...
def save_chat():
    logging.info("save_chat(): Entered the /save_chat route.")
    try:
        # Check for existing conversation via cookies by default
        # this returns error!!! data = get_request_data()
        conversation_id = request.cookies.get('conversation_id')
        if conversation_id:
            filename = f"{conversation_id}.jsonl"
            messages = get_messages_from_firestore(conversation_id)
            if messages:
                # Prepare to archive:
                params = utipy.session_mgr.args.copy()
                params['model'] = utipy.config.MODEL_NAME
                params.update({'conversation_id': conversation_id})
                case_id = request.cookies.get('case_id')
                if case_id: params.update({'case_id': case_id})
                if utipy.config.STUDENT:
                    params.update({'student': utipy.config.STUDENT})
                    utipy.config.STUDENT = 'unk'
                params.update({'loss': utipy.config.TRAINING_LOSS})
                # Archive chat to apprpriate storage
                if utipy.config.LOCAL:
                    filepath = os.path.join('config', 'zbchats', filename)
                    save_chat_to_file(messages, params, filepath)
                    print(f"Chat saved locally as {filepath}")
                else: # save to GCS BUCKET
                    blob_name = f'zbchats/{filename}'
                    save_chat_to_bucket(messages, params, blob_name)
                    logging.info(f"Chat saved to bucket as {blob_name}")
                delete_messages_from_firestore(conversation_id)
                reset_test()
                response = jsonify({'status': 'success'})   #, 'conversation_id': conversation_id
                response.set_cookie('conversation_id', '', expires=0)
                if case_id: response.set_cookie('case_id', '', expires=0)
                return response, 200
        logging.error("Error 404 in save_chat() route: 'No conversation to save.'")
        return jsonify({'error': 'No conversation to save.'}), 404
    except Exception as e:
        logging.error(f" Exception in save_chat(): {e}")
        return jsonify({'error': str(e)}), 500
# ########## END OF OUTGOING ROUTES##########

# ########## INCOMING API ROUTES ############
# Note: most endpoints are GET-only for simplicity unless otherwise specified.

@app.route('/zb_api/chat', methods=['GET']) # all GET for simplicity
def zb_api_chat():
    conversation_id = None
    try:
        data = get_request_data()
        api_prompt = data.get('message')
        if not api_prompt:
            return jsonify({
                "status": "failure",
                "error": "Missing required 'message' parameter.",
                "conversation_id": None,
            }), 400

        conversation_id = data.get('conversation_id', request.cookies.get('conversation_id', ''))
        if conversation_id and conversation_id != '':
            # Continue ongoing conversation:
            messages = get_messages_from_firestore(conversation_id)
        else: # Initialize a new conversation with 'generic' preset context
            reset_test()
            utipy.config.STUDENT = data.get('student', 'api')
            conversation_id = get_cid(student=utipy.config.STUDENT)
            messages = utipy.config.START_CHATS['smiles'].copy()
        messages = prompt_and_reply(messages, api_prompt)
        save_messages_to_firestore(conversation_id, messages)
        return jsonify({
            "status": "success",
            "conversation_id": conversation_id,
            "response": messages[-1]['content'],
        }), 200
    except ModelAPIError:
        raise
    except BadRequest as e:
        logging.exception(f"/zb_api/chat BadRequest error: {e}")  # Log the full traceback
        return jsonify({
            "status": "failure",
            "error": str(e),
            "conversation_id": conversation_id,
        }), 400
    except Exception as e:
        logging.exception(f"/zb_api/chat error: {e}")  # Log the full traceback
        return jsonify({
            "status": "failure",
            "error": str(e),
            "conversation_id": conversation_id,
        }), 500 # Generic error

@app.route('/zb_api/chat_case/<case_id>', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_chat_case(case_id):
    conversation_id = None
    try:
        data = get_request_data() # Handles both GET and POST
        student = data.get('student', 'api-case')
        case_id = case_id or data.get('case_id')
        reset_test(case_id=case_id, student=student) # Generate new random set of test botling and params
        conversation_id = create_koan_conversation()
        if conversation_id is None:  # Handle potential errors in create_koan_conversation
            return jsonify({
                "status": "failure",
                "error": f"No conversation_id returned by create_koan_conversation() for case #{case_id}.",
                "conversation_id": None,
                "case_id": str(case_id) if case_id is not None else None,
            }), 500
        return jsonify({
            "status": "success",
            "conversation_id": conversation_id,
            "case_id": str(case_id),
        }), 200
    except ModelAPIError:
        raise
    except Exception as e:  # Catch any other exceptions
        logging.exception(f"Exception in /zb_api/chat_case/{case_id}: {e}")  # Log traceback
        return jsonify({
            "status": "failure",
            "error": str(e),
            "conversation_id": conversation_id,
            "case_id": str(case_id) if case_id is not None else None,
        }), 500

@app.route('/zb_api/save_chat', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_save_chat():
    try:
        data = get_request_data()
        student = data.get('student', utipy.config.STUDENT)
        case_id = data.get('case_id', request.cookies.get('case_id', ''))
        conversation_id = data.get('conversation_id')
        if conversation_id and str(conversation_id).strip():
            # Prepare to archive:
            params = utipy.session_mgr.args.copy()
            params.update({'conversation_id': conversation_id})
            params['model'] = utipy.config.MODEL_NAME
            params.update({'loss': utipy.config.TRAINING_LOSS})
            params.update({"student": student})
            if case_id and case_id!='':                   
                params.update({'case_id': case_id})
            messages = get_messages_from_firestore(conversation_id)
            if not messages or len(messages) <= 0:
                return jsonify({
                    "status": "failure",
                    "error": "No messages found for conversation_id.",
                    "conversation_id": conversation_id,
                }), 404
            # Save to GCS
            filename = f"{conversation_id}.jsonl"
            blob_name = f'zbchats/{filename}'
            save_chat_to_bucket(messages, params, blob_name)
            logging.info(f"Chat saved by API caller to GCS bucket as {blob_name}")
            # Delete from Firestore
            delete_messages_from_firestore(conversation_id)
            # And reset with new test model and params and utipy.config
            reset_test()
            return jsonify({'status': 'success', "error" : "None"}), 200
        # Missing or blank conversation_id
        logging.error("Error in zb_api_save_chat: 'No conversation_id provided.'")
        return jsonify({
            "status": "failure",
            "error": "No conversation_id provided in zb_api_save_chat().",
        }), 400
    # Log any exception and return it to caller
    except ModelAPIError:
        raise
    except Exception as e:
        logging.exception(f"Exception in zb_api_save_chat: {e}") # Log traceback
        return jsonify({"status": "failure",
                        "error": "Exception caught in zb_api_save_chat: " + str(e)}), 500  # Generic error response

@app.route('/zb_api/conversations/list', methods=['GET']) # all GET for simplicity
def zb_api_conversations_list():
    # No request data needed for this endpoint.
    conversation_files = list_conversation_files_in_gcs()
    conversation_ids = [file_name.split('/')[-1].replace('.jsonl', '') for file_name in conversation_files]
    return jsonify({'status': 'success', 'conversation_ids': conversation_ids}), 200

@app.route('/zb_api/conversations/<conversation_id>', methods=['GET']) # all GET for simplicity
def zb_api_conversation(conversation_id):
    # No request data needed for this endpoint (conversation_id is a path parameter).
    if not conversation_id:
        return jsonify({'conversation_id': None, 'messages': None, 'status': 'Missing conversation_id.'}), 404
    messages = get_conversation_from_gcs(conversation_id)
    if not messages:
        return jsonify({'conversation_id': conversation_id, 'messages': None, 'status': 'Conversation not found.'}), 404
    return jsonify({'conversation_id': conversation_id, 'messages': messages, 'status': 'success'}), 200

# —— Memory Logbook APIs —— #
@app.route('/zb_api/load_memory_logbook', methods=['GET'])
def zb_api_load_memory_logbook():
    memories = load_memory_logbook()
    return jsonify({
        'memories': memories,
        'status': 'success' if memories else 'empty',
        'count': len(memories),
    }), 200

@app.route('/zb_api/update_memory_logbook', methods=['GET', 'POST']) # all GET and POST
def zb_api_update_memory_logbook():
    """
    GET:  build a new entry from query params, append it, and return updated list.
    POST: accept JSON body with either:
          - { "entry": { … } }       → append single entry
          - { "full_logbook": [ … ] } → replace entire logbook
    """
    # - GET branch (for simplicity) -
    if request.method == 'GET':
        raw = request.args.to_dict(flat=False)
        if not raw:
            return jsonify({"error": "No query parameters provided to form a memory entry"}), 400
        entry = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in raw.items()}

        # Allow common list fields to be passed as comma-separated strings in GET requests.
        list_fields = {'koans_used', 'key_insights', 'lessons_learned', 'user_instructions'}
        json_fields = {'session_evaluations'}
        for key in list_fields:
            val = entry.get(key)
            if isinstance(val, str):
                entry[key] = [part.strip() for part in val.split(',') if part.strip()]

        for key in json_fields:
            val = entry.get(key)
            if isinstance(val, str) and val.strip().startswith(('[', '{')):
                try:
                    entry[key] = json.loads(val)
                except Exception:
                    pass

        try:
            updated = update_logbook(entry)
            return jsonify({'memories': updated, 'status': 'appended via GET'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # — POST branch —
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid or missing JSON body"}), 400

    # Case A: append one new entry
    if 'entry' in data:
        entry = data['entry']
        if not isinstance(entry, dict):
            return jsonify({"error": "'entry' must be an object"}), 400
        try:
            updated = update_logbook(entry)
            return jsonify({'memories': updated, 'status': 'entry appended via POST'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Case B: replace entire logbook
    if 'full_logbook' in data:
        full = data['full_logbook']
        if not isinstance(full, list):
            return jsonify({"error": "'full_logbook' must be an array"}), 400
        try:
            replace_logbook(full)
            return jsonify({'memories': full, 'status': 'logbook replaced via POST'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # Legacy: treat a direct MemoryEntry body as an append.
    if isinstance(data, dict):
        try:
            updated = update_logbook(data)
            return jsonify({'memories': updated, 'status': 'entry appended via POST (legacy body)'}), 200
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    return jsonify({
        "error": "JSON body must contain either 'entry' or 'full_logbook'"}), 400


@app.route('/appendMemoryLogbookEntry', methods=['POST'])
def append_memory_logbook_entry_legacy():
    """
    Legacy endpoint for older GPT Action specs.
    Accepts a direct MemoryEntry JSON body and appends it to the logbook.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid or missing JSON body"}), 400
    try:
        updated = update_logbook(data)
        return jsonify({'memories': updated, 'status': 'entry appended (legacy endpoint)'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

#### TO DO IMPLEMENT/ FINISH AS NEEDED: 
@app.route('/zb_api/get_random_koan', methods=['GET'])
def zb_api_get_random_koan():
    case_id = utipy.get_random_koan_case_id()
    if not case_id:
        return jsonify({'case_id': None, 'case_text': "", 'status': 'Random number not generated.'}), 404

    koan = utipy.get_mmnk_case(case_id)
    if not koan:
        return jsonify({'case_id': case_id, 'case_text': "", 'koan': None, 'status': "koan not found"}), 404

    case_text = koan.get('body', '')
    return jsonify({
        'case_id': str(koan.get('id', case_id)),
        'case_text': case_text if isinstance(case_text, str) else '',
        'koan': koan,
        'status': "success",
    }), 200


# # ########## END OF API ROUTES #############

# ########## Display source texts ############
# Mumonkan/Gateless-Gate source commentary
@app.route('/gg')
def gg():
    return render_template('gg.html')
@app.route('/gg/<id>')
def ggcase(id):
    return render_template('ggcase.html', caseId=str(id))    #, caseTitle=f"Case {id}"
# Blue Cliff Record source commentaries
@app.route('/bcr')
def display_pdf():
    return render_template('bcr.html')
@app.route('/pdf/<filename>') #static\bcr_commentaries.pdf
def serve_pdf(filename):
    pdf_directory = os.path.join(os.getcwd(), "static")
    return send_from_directory(pdf_directory, filename)
# ###### End of displaying source texts ########

# ########## DATA ADMINISTRATION #############


# ---- Local Review UI (local server only) ----

def _require_local_admin() -> None:
    if not utipy.config.LOCAL:
        abort(404)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _trainset04_review_csv_path() -> Path:
    return _repo_root() / 'training' / 'trainset04' / 'review.csv'


def _load_review_rows() -> tuple[list[dict[str, str]], list[str]]:
    review_path = _trainset04_review_csv_path()
    if not review_path.exists():
        abort(500, description=f"Missing review.csv at {review_path}")
    with review_path.open('r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []
    return rows, headers


def _write_review_rows(headers: list[str], rows: list[dict[str, str]]) -> None:
    review_path = _trainset04_review_csv_path()
    with review_path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _update_review_keep(decisions: dict[str, str]) -> None:
    rows, headers = _load_review_rows()
    if 'id' not in headers or 'keep' not in headers:
        abort(500, description="review.csv must include 'id' and 'keep' columns")

    changed = 0
    for r in rows:
        rid = (r.get('id') or '').strip()
        if rid in decisions:
            r['keep'] = decisions[rid]
            changed += 1

    _write_review_rows(headers, rows)
    logging.info(f"/admin/review wrote {changed} decisions")


def _session_path_from_review_row(row: dict[str, str]) -> Path:
    rel = (row.get('source_path') or '').strip()
    if not rel:
        abort(500, description='review.csv row missing source_path')

    root = _repo_root().resolve()
    sessions_root = (root / 'collected_sessions').resolve()
    candidate = (root / rel).resolve()

    if sessions_root != candidate and sessions_root not in candidate.parents:
        abort(400, description='Invalid source_path (not under collected_sessions)')
    if not candidate.exists():
        abort(404, description=f"Missing session file: {candidate}")
    return candidate


def _is_message_obj(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    role = obj.get('role')
    content = obj.get('content')
    return role in {'system', 'user', 'assistant'} and isinstance(content, str)


def _parse_session_jsonl(path: Path) -> tuple[dict, list[dict[str, str]]]:
    metadata: dict = {}
    messages: list[dict[str, str]] = []

    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue

        if _is_message_obj(obj):
            messages.append({'role': obj['role'], 'content': obj['content']})
        elif not metadata and isinstance(obj, dict):
            metadata = obj

    return metadata, messages


@app.route('/admin/review', methods=['GET', 'POST'])
def admin_review():
    _require_local_admin()

    rows, _headers = _load_review_rows()

    if request.method == 'POST':
        decisions: dict[str, str] = {}
        for k in request.form.keys():
            if k.startswith('use_'):
                decisions[k[len('use_'):]] = '1'
            elif k.startswith('dont_'):
                decisions[k[len('dont_'):]] = '0'

        if decisions:
            _update_review_keep(decisions)

        return redirect(url_for('admin_review'))

    pending = [r for r in rows if not (r.get('keep') or '').strip()]

    try:
        offset = max(0, int(request.args.get('offset', 0)))
    except Exception:
        offset = 0
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    limit = min(max(10, limit), 200)

    page = pending[offset: offset + limit]

    return render_template(
        'admin_review.html',
        rows=page,
        pending_total=len(pending),
        offset=offset,
        limit=limit,
    )


@app.route('/admin/review/view/<record_id>')
def admin_review_view(record_id):
    _require_local_admin()

    rows, _headers = _load_review_rows()
    row = next((r for r in rows if (r.get('id') or '') == record_id), None)
    if not row:
        abort(404)

    session_path = _session_path_from_review_row(row)
    metadata, messages = _parse_session_jsonl(session_path)

    return render_template(
        'admin_review_view.html',
        row=row,
        session_path=str(session_path),
        metadata=metadata,
        messages=messages,
    )

# ---- End Local Review UI ----

@app.route('/admin/conversations')
#@login_required
def admin_conversations():
    conversation_files = list_conversation_files_in_gcs()
    conversation_ids = [file_name.split('/')[-1].replace('.jsonl', '') for file_name in conversation_files]
    return render_template('admin_conversations.html', conversation_ids=conversation_ids)

@app.route('/admin/conversations/<conversation_id>')
#@login_required
def admin_conversation_detail(conversation_id):
    if not conversation_id:
        logging.error(" admin_conversation_detail(): No conversation_id provided." )
        abort(400)
    messages = get_conversation_from_gcs(conversation_id)
    if not messages:
        logging.error(" admin_conversation_detail(): No messages found at Firestore with the provided conversation_id." )
        abort(404)
    return render_template('admin_conversation_detail.html', conversation_id=conversation_id, messages=messages)

@app.route('/download_chats', methods=['GET', 'POST'])
#@login_required
def download_chats():
    if download_all():
        # TO DO FIX:
        #if not {{current_user}}.is_authenticated:
            #return app.login_manager.unauthorized()
        print("All chats stored in GCS Bucket have been downloaded to local files.")
        return jsonify({"status": "success"}), 200
    else:
        print("Failed to download chats from GCS Bucket.")
        return jsonify({"status": "failure)"}), 500
# ########## END OF (web/local user) DATA ADMIN #############

# ### ##########      MAIN      ############## ### #
if __name__ == '__main__':
    # Start app locally or on GAE after utipy.config.LOCAL determined first thing above ^
    if utipy.config.LOCAL:
        # Local host-ed app:
        #print(" __main__: utipy.config.LOCAL=True. Running server with 'app.run(debug=True)' right here in __main__... .. .")
        app.run(debug=True)
        print(" __main__: Ran local server. Done.")
    else:
        # Cloud hosted web app
        app.run(host='0.0.0.0', port=8080, debug=True)
        logging.info(" __main__: Running Flask server on GAE completed.")
