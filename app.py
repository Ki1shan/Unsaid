import sqlite3
import uuid
from flask import Flask, request
# Added 'rooms' to imports
from flask_socketio import SocketIO, emit, join_room, leave_room, rooms
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
# CORS allow all origins so Netlify frontend can talk to Railway backend
socketio = SocketIO(app, cors_allowed_origins="*")

DB_NAME = "unnsaid.db"

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Create table for Listeners
    c.execute('''
        CREATE TABLE IF NOT EXISTS listeners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            name TEXT NOT NULL
        )
    ''')
    
    # --- MANUAL LISTENER LIST ---
    # Add your real students here!
    # Format: ("email", "password", "Name")
    students_to_add = [
        ("admin@unnsaid.com", "admin123", "Admin Listener"),
        ("sarah@university.edu", "psych2024", "Sarah (Psych Student)"),
        ("mike@university.edu", "securePass1", "Mike (Trained Peer)"),
        # Add more lines here for more students
    ]

    for email, password, name in students_to_add:
        # Check if user exists
        c.execute('SELECT * FROM listeners WHERE email = ?', (email,))
        if c.fetchone() is None:
            hashed_pw = generate_password_hash(password)
            c.execute('INSERT INTO listeners (email, password, name) VALUES (?, ?, ?)', 
                      (email, hashed_pw, name))
            print(f">>> ADDED LISTENER: {name} ({email})")
        
    conn.commit()
    conn.close()

# Initialize DB on startup
init_db()

# --- MEMORY STORES (RAM) ---
waiting_listeners = []  # List of socket IDs
waiting_users = []      # List of socket IDs
active_listeners = {}   # Map: socket_id -> database_id (to track who is logged in)

@app.route('/')
def index():
    return "Unnsaid Backend is Running & Database is Active!"

# --- SOCKET EVENTS ---

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f"Client disconnected: {sid}")
    
    # 1. Notify partners (Fix for Issue #1)
    # active_rooms = rooms(sid) returns all rooms this specific client is in
    for room in rooms(sid):
        if room != sid:
            # Notify the other person in the room that their partner left
            emit('partner_disconnected', room=room)
            leave_room(room, sid=sid)
    
    # 2. Cleanup Queues
    if sid in waiting_listeners: waiting_listeners.remove(sid)
    if sid in waiting_users: waiting_users.remove(sid)
    
    # 3. Cleanup Active Login Session
    if sid in active_listeners:
        del active_listeners[sid]

# 1. LISTENER LOGIN
@socketio.on('listener_login')
def handle_login(data):
    email = data.get('email')
    password = data.get('password')
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT id, password, name FROM listeners WHERE email = ?', (email,))
    user = c.fetchone()
    conn.close()

    if user and check_password_hash(user[1], password):
        # Success
        active_listeners[request.sid] = user[0] # Mark this socket as logged in
        emit('login_success', {'name': user[2]})
        print(f"Listener logged in: {user[2]}")
    else:
        # Fail
        emit('login_error', {'message': "Invalid email or password"})

# 2. QUEUE LOGIC
@socketio.on('join_queue')
def handle_join_queue(data):
    sid = request.sid
    user_type = data.get('user_type')
    
    # SECURITY: If trying to join as listener, MUST be logged in
    if user_type == 'listener':
        if sid not in active_listeners:
            emit('login_error', {'message': "Authentication required"})
            return
            
        # Check if user waiting
        if waiting_users:
            partner_sid = waiting_users.pop(0)
            match_users(sid, partner_sid)
        else:
            waiting_listeners.append(sid)
            
    elif user_type == 'normal':
        # Check if listener waiting
        if waiting_listeners:
            partner_sid = waiting_listeners.pop(0)
            match_users(partner_sid, sid)
        else:
            waiting_users.append(sid)

def match_users(listener_sid, user_sid):
    room_id = str(uuid.uuid4())
    
    join_room(room_id, sid=listener_sid)
    join_room(room_id, sid=user_sid)
    
    # Tell Frontend to switch to chat view
    # Authoritative Role Assignment
    emit('paired', {'room_id': room_id, 'role': 'listener'}, room=listener_sid)
    emit('paired', {'room_id': room_id, 'role': 'normal'}, room=user_sid)
    
    print(f"Matched Room {room_id}")

# 3. CHAT RELAY
@socketio.on('send_message')
def handle_message(data):
    room_id = data.get('room')
    text = data.get('text')
    # Relay to everyone in room EXCEPT sender
    emit('receive_message', {'text': text}, room=room_id, include_self=False)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)
