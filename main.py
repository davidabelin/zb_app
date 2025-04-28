# C:\Users\David\Documents\Local_Python\zenbot\zb_app\main.py
# Web App: 'Zenbot Dokusan' v7.0 https://zenbot-434517.uw.r.appspot.com/
# -- API SCHEMAS v3.0.x

import os
import logging
import json
from flask import (Flask, jsonify, render_template, send_from_directory,
                   make_response, abort, request, Response)
from werkzeug.exceptions import BadRequest
import utilities as utipy
from utilities import (get_cid, save_messages_to_firestore, get_messages_from_firestore, get_request_data,
                       delete_messages_from_firestore, save_chat_to_bucket, reset_test,
                       prompt_and_reply, save_chat_to_file, list_conversation_files_in_gcs,
                       get_conversation_from_gcs, download_all,
                       create_koan_conversation, load_memory_logbook, update_logbook)  # Import utility functions

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
        TO be called only from /ggcase route 
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
                params = utipy.CLARGS.copy()
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
# ########## END OF OUTGOING #############

# ########## INCOMING API ROUTES #############
# TO DO provide POST methods, too
@app.route('/zb_api/chat', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_chat():
    try:
        data = get_request_data() # Handles both GET and POST...
        api_prompt = data.get('message')
        if not api_prompt:
            abort(400, description="Missing 'message' parameter") # More informative error
        conversation_id = data.get('conversation_id', request.cookies.get('conversation_id', ''))
        if conversation_id and conversation_id!='':
            # Continue ongoing conversation:
            messages = get_messages_from_firestore(conversation_id)
        else: # Initialize a new conversation with 'generic' preset context
            reset_test()
            utipy.config.STUDENT = data.get('student', 'api')
            conversation_id = get_cid(student=utipy.config.STUDENT)
            messages = utipy.config.START_CHATS['smiles'].copy()
        messages = prompt_and_reply(messages, api_prompt)
        if "error caught in" in messages[-1]['content']:
            raise Exception(messages[-1]['content'])
        save_messages_to_firestore(conversation_id, messages)
        return jsonify({"conversation_id": conversation_id, "response": messages[-1]['content']}), 200
    except BadRequest as e:
        logging.exception(f"/zb_api/chat BadRequest error: {e}")  # Log the full traceback
        return jsonify({"conversation_id": conversation_id, "response": str(e)}), 400
    except Exception as e:
        logging.exception(f"/zb_api/chat error: {e}")  # Log the full traceback
        return jsonify({"conversation_id": conversation_id, "response": str(e)}), 500 # Generic error

@app.route('/zb_api/chat_case/<case_id>', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_chat_case(case_id):
    try:
        data = get_request_data() # Handles both GET and POST
        student = data.get('student', 'api-case')
        case_id = case_id or data.get('case_id')
        reset_test(case_id=case_id, student=student) # Generate new random set of test botling and params
        conversation_id = create_koan_conversation()
        if conversation_id is None:  # Handle potential errors in create_koan_conversation
            abort(500, description=f"No conversation_id returned by create_koan_conversation() for case #{case_id}.")
        return jsonify({'error': 'success', 'conversation_id': conversation_id}), 200
    except Exception as e:  # Catch any other exceptions
        logging.exception(f"Exception in /zb_api/chat_case/{case_id}: {e}")  # Log traceback
        return jsonify({"error": str(e), 'conversation_id': conversation_id}), 500

@app.route('/zb_api/save_chat', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_save_chat():
    try:
        data = get_request_data()
        student = data.get('student', utipy.config.STUDENT)
        case_id = data.get('case_id', request.cookies.get('case_id', ''))
        conversation_id = data.get('conversation_id')
        if conversation_id:
            # Prepare to archive:
            params = utipy.CLARGS.copy()
            params.update({'conversation_id': conversation_id})
            params['model'] = utipy.config.MODEL_NAME
            params.update({'loss': utipy.config.TRAINING_LOSS})
            params.update({"student": student})
            if case_id and case_id!='':                   
                params.update({'case_id': case_id})
            messages = get_messages_from_firestore(conversation_id)
            if not messages or len(messages) <= 0:
                # Handle missing messages the easy way...
                messages = utipy.config.START_CHATS['random'].copy()
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
        return jsonify({"status": "failure",
                        "error": "No conversation_id provided in zb_api_save_chat()"}), 404
    # Log any exception and return it to caller
    except Exception as e:
        logging.exception(f"Exception in zb_api_save_chat: {e}") # Log traceback
        return jsonify({"status": "failure",
                        "error": "Exception caught in zb_api_save_chat: " + str(e)}), 500  # Generic error response

@app.route('/zb_api/conversations/list', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_conversations_list():
    # No request data needed for this endpoint.
    conversation_files = list_conversation_files_in_gcs()
    conversation_ids = [file_name.split('/')[-1].replace('.jsonl', '') for file_name in conversation_files]
    return jsonify({'status': 'success', 'conversation_ids': conversation_ids}), 200

@app.route('/zb_api/load_memory_logbook', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_load_memory_logbook():
    memories = load_memory_logbook()
    if not memories or len(memories)==0:
        return jsonify({'memories': None, 'status': 'Memory logbook not found.'}), 404
    return jsonify({'memories': memories, 'status': 'success'}), 200

@app.route('/zb_api/update_memory_logbook', methods=['GET']) # all GET for simplicity  , 'POST'
def zb_api_update_memory_logbook():
    memories = load_memory_logbook()
    if not memories or len(memories)==0:
        return jsonify({'memories': None, 'status': 'Memory logbook not found.'}), 404
    update_logbook(memories)
    
    return jsonify({'memories': memories, 'status': 'success'}), 200

# # ########## END OF API ROUTES #############

# ########## Display source texts #############
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
@app.route('/admin/conversations')
#@login_required
def admin_conversations():
    conversation_files = list_conversation_files_in_gcs()
    conversation_ids = [file_name.split('/')[-1].replace('.jsonl', '') for file_name in conversation_files]
    return render_template('admin_conversations.html', conversation_ids=conversation_ids)

@app.route('/admin/conversations/<conversation_id>}')
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