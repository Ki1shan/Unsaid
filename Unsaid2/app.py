import eventlet
eventlet.monkey_patch() # Fix 5: Critical for Flask-SocketIO stability

import time # Fix 4: For rate limiting
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import secrets

app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- MEMORY STORAGE ---
queue_users = []
queue_listeners = []
active_connections = {}
last_message_time = {} # Fix 4: Rate limiting storage

@app.route('/')
def index():
    return render_template('index.html')

# --- HELPER FUNCTIONS ---

def remove_from_queue(sid):
    # Fix 2: Separate queue removal from chat termination
    if sid in queue_users:
        queue_users.remove(sid)
    if sid in queue_listeners:
        queue_listeners.remove(sid)

def end_active_chat(sid):
    # Fix 2: Only kills the active chat, not the user session itself
    if sid in active_connections:
        data = active_connections[sid]
        partner_id = data.get('partner')
        
        # Notify partner
        if partner_id:
            emit('chat_ended', {'reason': 'Partner disconnected'}, room=partner_id)
            if partner_id in active_connections:
                del active_connections[partner_id]
        
        # Remove self
        del active_connections[sid]

# --- SOCKET EVENTS ---

@socketio.on('connect')
def handle_connect():
    print(f"New connection: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    remove_from_queue(sid)
    end_active_chat(sid)
    # Clean up rate limit data
    if sid in last_message_time:
        del last_message_time[sid]

@socketio.on('login_listener')
def handle_login(data):
    print(f"Listener logged in: {data.get('name')}")
    emit('login_success', {'role': 'listener'})

@socketio.on('join_queue')
def handle_join_queue(data):
    sid = request.sid
    role = data.get('role')
    
    print(f"Join Queue Request: {sid} as {role}")

    partner_sid = None

    # PAIRING LOGIC
    if role == 'user':
        if queue_listeners:
            partner_sid = queue_listeners.pop(0)
        else:
            if sid not in queue_users:
                queue_users.append(sid)
                emit('waiting_in_queue')

    elif role == 'listener':
        if queue_users:
            partner_sid = queue_users.pop(0)
        else:
            if sid not in queue_listeners:
                queue_listeners.append(sid)
                emit('waiting_in_queue')

    # IF MATCHED
    if partner_sid:
        room_id = secrets.token_hex(8)
        
        join_room(room_id, sid=sid)
        join_room(room_id, sid=partner_sid)
        
        active_connections[sid] = {'partner': partner_sid, 'room': room_id}
        active_connections[partner_sid] = {'partner': sid, 'room': room_id}
        
        # Fix 1: Explicit Role Assignment
        # If I joined as 'role', that is MY role. My partner is the opposite.
        my_role = role
        partner_role = 'listener' if role == 'user' else 'user'

        emit('match_found', {'role': my_role, 'room': room_id}, room=sid)
        emit('match_found', {'role': partner_role, 'room': room_id}, room=partner_sid)

@socketio.on('send_message')
def handle_message(data):
    sid = request.sid
    
    # Fix 4: Rate Limiting / Flood Protection
    current_time = time.time()
    if current_time - last_message_time.get(sid, 0) < 0.5: # 0.5 second cooldown
        return
    last_message_time[sid] = current_time

    if sid in active_connections:
        partner_id = active_connections[sid]['partner']
        # Send explicitly to partner socket to prevent echoing back to sender if using room broadcast incorrectly
        emit('receive_message', {'text': data['text']}, room=partner_id)

@socketio.on('typing')
def handle_typing():
    sid = request.sid
    if sid in active_connections:
        partner_id = active_connections[sid]['partner']
        emit('partner_typing', room=partner_id)

@socketio.on('stop_typing')
def handle_stop_typing():
    sid = request.sid
    if sid in active_connections:
        partner_id = active_connections[sid]['partner']
        emit('partner_stop_typing', room=partner_id)

@socketio.on('leave_queue')
def handle_leave_queue():
    # Fix 2: Only remove from queue, don't kill active chats if any
    remove_from_queue(request.sid)

@socketio.on('end_chat')
def handle_end_chat():
    end_active_chat(request.sid)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)