from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit
import psycopg2
import time
from config import Config
from werkzeug.security import check_password_hash, generate_password_hash
import functools
from datetime import datetime, timedelta, timezone
import traceback
import os
import subprocess
from werkzeug.utils import secure_filename
from flask import send_file
import re
import psutil
import threading
import io
import requests
import pyotp

# === TELEGRAM NOTIFICATION ===
def send_telegram_notification(message):
    token = app.config.get('TELEGRAM_BOT_TOKEN')
    chat_id = app.config.get('TELEGRAM_CHAT_ID')
    if not token or not chat_id:
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'})
    except Exception as e:
        print(f"Telegram error: {e}")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'radius-dashboard-secret-2024'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config.from_object(Config)

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# === BACKUP & RESTORE CONFIGURATION ===
BACKUP_FOLDER = 'backups'
ALLOWED_BACKUP_EXTENSIONS = {'sql', 'backup'}

def ensure_backup_folder():
    if not os.path.exists(BACKUP_FOLDER):
        os.makedirs(BACKUP_FOLDER)

def allowed_backup_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_BACKUP_EXTENSIONS

def format_file_size(size_bytes):
    if size_bytes == 0:
        return '0 B'
    size_names = ['B', 'KB', 'MB', 'GB']
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

# === LOGIN REQUIRED DECORATOR ===
def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# === DATABASE CONNECTION ===
def get_db_connection():
    return psycopg2.connect(
        host=app.config['DB_HOST'],
        database=app.config['DB_NAME'],
        user=app.config['DB_USER'],
        password=app.config['DB_PASSWORD'],
        port=app.config['DB_PORT']
    )

def execute_query(query, params=None, fetch=True):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, params)
        if fetch:
            result = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in result]
        else:
            conn.commit()
            return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cur.close()
        conn.close()

# === HELPER FUNCTIONS ===
def get_request_data():
    try:
        if request.is_json:
            return request.get_json()
        else:
            data = request.form.to_dict()
            for key in data:
                if data[key] == '':
                    data[key] = None
            return data
    except Exception as e:
        print(f"DEBUG: Error getting request data: {str(e)}")
        return {}

def convert_data_limit_to_bytes(data_limit_str):
    """Convert data limit string (e.g., '10GB', '500MB') to bytes"""
    if not data_limit_str:
        return None
    try:
        data_limit_str = data_limit_str.strip().upper()
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGT]?B?)$', data_limit_str)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2)
        
        multipliers = {
            'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4,
            'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4
        }
        multiplier = multipliers.get(unit, 1)
        return int(value * multiplier)
    except Exception as e:
        print(f"DEBUG: Error converting data limit: {str(e)}")
        return None

# === NAS HEALTH MONITOR ===
def ping_nas(ip_address):
    """
    Fungsi untuk mem-ping sebuah IP address dan mengembalikan statusnya.
    Mengembalikan 'online', 'offline', atau 'error'.
    """
    try:
        # -c 1: kirim 1 packet (Linux/macOS)
        # -w 1: timeout 1 detik
        # Untuk Windows, ganti dengan '-n 1'
        output = subprocess.run(
            ["ping", "-c", "1", "-w", "1", ip_address],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2  # timeout untuk prosesnya sendiri
        )

        if output.returncode == 0:
            return "online"
        else:
            return "offline"

    except subprocess.TimeoutExpired:
        return "offline"
    except Exception as e:
        print(f"Error pinging {ip_address}: {e}")
        return "error"


# === BACKGROUND CLEANUP THREAD ===
def cleanup_stale_sessions():
    """Background task to clean up stale sessions"""
    while True:
        try:
            execute_query("""
                DELETE FROM radacct 
                WHERE acctstoptime IS NULL 
                AND acctstarttime < NOW() - INTERVAL '2 hours'
            """, fetch=False)
            
            execute_query("""
                DELETE FROM radacct
                WHERE radacctid NOT IN (
                    SELECT DISTINCT ON (username) radacctid
                    FROM radacct
                    WHERE acctstoptime IS NULL
                    ORDER BY username, acctstarttime DESC
                )
                AND acctstoptime IS NULL
            """, fetch=False)
            
            print(f"[{datetime.now()}] Cleaned up stale sessions")
        except Exception as e:
            print(f"[ERROR] Failed to clean sessions: {str(e)}")
        
        time.sleep(3600)  # Run every hour

def start_cleanup_thread():
    cleanup_thread = threading.Thread(target=cleanup_stale_sessions, daemon=True)
    cleanup_thread.start()
    print("Background session cleanup thread started")

# === AUTHENTICATION ROUTES ===
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        mfa_code = request.form.get('mfa_code')
        
        user = execute_query("SELECT * FROM admin_users WHERE username = %s AND is_active = TRUE", (username,))
        
        if user and check_password_hash(user[0]['password_hash'], password):
            # Check MFA
            if user[0]['is_mfa_enabled']:
                if not mfa_code:
                    return render_template('login.html', mfa_required=True, username=username, password=password)
                
                totp = pyotp.TOTP(user[0]['mfa_secret'])
                if not totp.verify(mfa_code):
                    flash('Invalid MFA code', 'error')
                    return render_template('login.html', mfa_required=True, username=username, password=password)
            
            session['user_id'] = user[0]['id']
            session['username'] = user[0]['username']
            session['role'] = user[0]['role']
            
            execute_query("UPDATE admin_users SET last_login = NOW() WHERE id = %s", (user[0]['id'],), fetch=False)
            
            send_telegram_notification(f"🔔 <b>Admin Login</b>\nUser: {username}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

# === PROFILE ROUTES ===
@app.route('/profile')
@login_required
def profile_page():
    return render_template('profile.html')

@app.route('/api/mfa/setup', methods=['POST'])
@login_required
def api_mfa_setup():
    try:
        secret = pyotp.random_base32()
        user = execute_query("SELECT username FROM admin_users WHERE id = %s", (session['user_id'],))[0]
        provisioning_url = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user['username'], 
            issuer_name="RADIUS Manager"
        )
        # We store secret temporarily in session for verification
        session['temp_mfa_secret'] = secret
        
        return jsonify({
            'success': True, 
            'secret': secret,
            'qr_code_url': f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={provisioning_url}"
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/mfa/verify', methods=['POST'])
@login_required
def api_mfa_verify():
    try:
        data = get_request_data()
        code = data.get('code')
        secret = session.get('temp_mfa_secret')
        
        if not secret:
            return jsonify({'success': False, 'error': 'Session expired, please restart setup'})
            
        totp = pyotp.TOTP(secret)
        if totp.verify(code):
            execute_query("UPDATE admin_users SET mfa_secret = %s, is_mfa_enabled = TRUE WHERE id = %s", 
                         (secret, session['user_id']), fetch=False)
            session.pop('temp_mfa_secret', None)
            return jsonify({'success': True, 'message': 'MFA enabled successfully'})
        else:
            return jsonify({'success': False, 'error': 'Invalid verification code'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/change-password', methods=['POST'])
@login_required
def api_change_password():
    try:
        data = get_request_data()
        current_password = data['current_password']
        new_password = data['new_password']
        
        user = execute_query("SELECT * FROM admin_users WHERE id = %s", (session['user_id'],))
        
        if not user or not check_password_hash(user[0]['password_hash'], current_password):
            return jsonify({'success': False, 'error': 'Current password is incorrect'})
        
        new_password_hash = generate_password_hash(new_password)
        execute_query("UPDATE admin_users SET password_hash = %s WHERE id = %s", 
                     (new_password_hash, session['user_id']), fetch=False)
        
        return jsonify({'success': True, 'message': 'Password updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# === MAIN ROUTES ===
@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html')

@app.route('/nas')
@login_required
def nas_page():
    return render_template('nas.html')

@app.route('/users')
@login_required
def users_page():
    return render_template('users.html')

@app.route('/sessions')
@login_required
def sessions_page():
    return render_template('sessions.html')

@app.route('/logs')
@login_required
def logs_page():
    return render_template('logs.html')

@app.route('/backup')
@login_required
def backup_page():
    return render_template('backup/index.html')

# === API ROUTES ===
@app.route('/api/stats')
def api_stats():
    try:
        # Total users registered in the system
        total_users = execute_query("SELECT COUNT(*) as count FROM radcheck WHERE attribute = 'Cleartext-Password'")[0]['count']
        
        # Total active connections (Live Sessions)
        active_sessions = execute_query("SELECT COUNT(*) as count FROM radacct WHERE acctstoptime IS NULL")[0]['count']
        
        # Unique users currently online
        online_users = execute_query("SELECT COUNT(DISTINCT username) as count FROM radacct WHERE acctstoptime IS NULL")[0]['count']
        
        today_auth = execute_query("SELECT COUNT(*) as count FROM radpostauth WHERE DATE(authdate) = CURRENT_DATE")[0]['count']
        
        try:
            total_nas = execute_query("SELECT COUNT(*) as count FROM nas")[0]['count']
        except:
            total_nas = 0
        
        return jsonify({
            'total_users': total_users,
            'active_sessions': active_sessions,
            'online_users': online_users,
            'today_auth': today_auth,
            'total_nas': total_nas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === NAS API ROUTES ===
@app.route('/api/nas')
def api_nas():
    try:
        nas_list = execute_query("SELECT * FROM nas ORDER BY id")
        return jsonify(nas_list)
    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify([])
        return jsonify({'error': str(e)}), 500

@app.route('/api/nas/add', methods=['POST'])
def api_add_nas():
    try:
        data = get_request_data()
        
        try:
            execute_query("SELECT limit_proxy_state, require_ma FROM nas LIMIT 1")
            execute_query("""
                INSERT INTO nas (nasname, shortname, type, ports, secret, server, community, description, limit_proxy_state, require_ma)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'auto', 'auto')
            """, (data['nasname'], data['shortname'], data['type'], data['ports'], data['secret'],
                  data.get('server', ''), data.get('community', ''), data.get('description', '')), fetch=False)
        except:
            execute_query("""
                INSERT INTO nas (nasname, shortname, type, ports, secret, server, community, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (data['nasname'], data['shortname'], data['type'], data['ports'], data['secret'],
                  data.get('server', ''), data.get('community', ''), data.get('description', '')), fetch=False)
        
        return jsonify({'success': True, 'message': 'NAS added successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nas/delete/<int:nas_id>', methods=['DELETE'])
def api_delete_nas(nas_id):
    try:
        execute_query("DELETE FROM nas WHERE id = %s", (nas_id,), fetch=False)
        return jsonify({'success': True, 'message': 'NAS deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# === NAS HEALTH MONITOR API ROUTE ===
@app.route('/api/nas/status', methods=['GET'])
@login_required  # Menambahkan dekorator login_required untuk keamanan
def get_nas_status():
    """
    API endpoint untuk mendapatkan status (online/offline) semua NAS.
    Memerlukan autentikasi.
    """
    try:
        # Ambil data NAS dari database menggunakan fungsi yang sudah ada
        nas_list = execute_query("SELECT * FROM nas ORDER BY id")
        
        nas_status_list = []
        for nas in nas_list:
            status = ping_nas(nas['nasname'])
            nas_status_list.append({
                'id': nas['id'],
                'nasname': nas['nasname'],
                'shortname': nas['shortname'],
                'status': status
            })
        
        return jsonify({'success': True, 'nas_status': nas_status_list})

    except Exception as e:
        # Jika tabel 'nas' tidak ada, kembalikan array kosong
        if 'relation "nas" does not exist' in str(e):
            return jsonify({'success': True, 'nas_status': []})
        return jsonify({'success': False, 'error': str(e)}), 500


# === USER API ROUTES ===
@app.route('/api/users')
def api_users():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit
        
        # Base query for data
        data_query = """
            SELECT c.id, c.username, c.attribute, c.op, c.value as password,
                   (SELECT value FROM radreply r1 WHERE r1.username = c.username AND r1.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r2 WHERE r2.username = c.username AND r2.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r3 WHERE r3.username = c.username AND r3.attribute = 'Session-Timeout' LIMIT 1) as session_timeout,
                   (SELECT value FROM radreply r4 WHERE r4.username = c.username AND r4.attribute = 'Idle-Timeout' LIMIT 1) as idle_timeout,
                   (SELECT value FROM radreply r5 WHERE r5.username = c.username AND r5.attribute = 'Mikrotik-Total-Limit' LIMIT 1) as data_limit,
                   (SELECT COUNT(*) FROM radacct a WHERE a.username = c.username AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c
            WHERE c.attribute = 'Cleartext-Password'
        """
        
        # Base query for count
        count_query = "SELECT COUNT(*) as total FROM radcheck c WHERE c.attribute = 'Cleartext-Password'"
        
        params = []
        if search:
            data_query += " AND c.username ILIKE %s"
            count_query += " AND c.username ILIKE %s"
            params.append(f'%{search}%')
            
        data_query += " ORDER BY c.username LIMIT %s OFFSET %s"
        data_params = params + [limit, offset]
        
        users = execute_query(data_query, tuple(data_params))
        total_count = execute_query(count_query, tuple(params))[0]['total']
        
        return jsonify({
            'users': users,
            'total': total_count,
            'page': page,
            'limit': limit,
            'total_pages': (total_count + limit - 1) // limit
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/users/add', methods=['POST'])
def api_add_user():
    try:
        data = get_request_data()
        
        # Insert password
        execute_query("""
            INSERT INTO radcheck (username, attribute, op, value)
            VALUES (%s, 'Cleartext-Password', ':=', %s)
        """, (data['username'], data['password']), fetch=False)
        
        # PPP attributes
        ppp_attributes = [
            ('Framed-Protocol', ':=', 'PPP'),
            ('Service-Type', ':=', 'Framed-User'),
            ('Framed-Compression', ':=', 'Van-Jacobson-TCP-IP')
        ]
        
        for attr, op, value in ppp_attributes:
            execute_query("""
                INSERT INTO radreply (username, attribute, op, value)
                VALUES (%s, %s, %s, %s)
            """, (data['username'], attr, op, value), fetch=False)
        
        # Optional attributes
        if data.get('ip_address'):
            execute_query("""
                INSERT INTO radreply (username, attribute, op, value)
                VALUES (%s, 'Framed-IP-Address', ':=', %s)
            """, (data['username'], data['ip_address']), fetch=False)
        
        if data.get('rate_limit'):
            execute_query("""
                INSERT INTO radreply (username, attribute, op, value)
                VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)
            """, (data['username'], data['rate_limit']), fetch=False)
        
        if data.get('session_timeout'):
            execute_query("""
                INSERT INTO radreply (username, attribute, op, value)
                VALUES (%s, 'Session-Timeout', ':=', %s)
            """, (data['username'], data['session_timeout']), fetch=False)
        
        if data.get('idle_timeout'):
            execute_query("""
                INSERT INTO radreply (username, attribute, op, value)
                VALUES (%s, 'Idle-Timeout', ':=', %s)
            """, (data['username'], data['idle_timeout']), fetch=False)
        
        if data.get('data_limit'):
            data_limit_bytes = convert_data_limit_to_bytes(data['data_limit'])
            if data_limit_bytes:
                execute_query("""
                    INSERT INTO radreply (username, attribute, op, value)
                    VALUES (%s, 'Mikrotik-Total-Limit', ':=', %s)
                """, (data['username'], str(data_limit_bytes)), fetch=False)
        
        send_telegram_notification(f"👤 <b>New User Created</b>\nUsername: {data['username']}\nIP: {data.get('ip_address', 'Dynamic')}\nRate: {data.get('rate_limit', 'Not set')}")
        return jsonify({'success': True, 'message': 'User added successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/update/<username>', methods=['PUT'])
def api_update_user(username):
    try:
        data = get_request_data()
        
        if data.get('ip_address'):
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Framed-IP-Address'", (username,), fetch=False)
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)", 
                         (username, data['ip_address']), fetch=False)
        
        if data.get('rate_limit'):
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Rate-Limit'", (username,), fetch=False)
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)", 
                         (username, data['rate_limit']), fetch=False)
        
        if data.get('session_timeout'):
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Session-Timeout'", (username,), fetch=False)
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Session-Timeout', ':=', %s)", 
                         (username, data['session_timeout']), fetch=False)
        
        if data.get('idle_timeout'):
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Idle-Timeout'", (username,), fetch=False)
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Idle-Timeout', ':=', %s)", 
                         (username, data['idle_timeout']), fetch=False)
        
        if data.get('data_limit'):
            data_limit_bytes = convert_data_limit_to_bytes(data['data_limit'])
            if data_limit_bytes:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Total-Limit'", (username,), fetch=False)
                execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Total-Limit', ':=', %s)", 
                             (username, str(data_limit_bytes)), fetch=False)
        
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'", 
                         (data['password'], username), fetch=False)
        
        return jsonify({'success': True, 'message': 'User updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/delete/<int:user_id>', methods=['DELETE'])
def api_delete_user(user_id):
    try:
        user = execute_query("SELECT username FROM radcheck WHERE id = %s", (user_id,))
        if user:
            username = user[0]['username']
            execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
            execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
            send_telegram_notification(f"🗑️ <b>User Deleted</b>\nUsername: {username}")
            return jsonify({'success': True, 'message': 'User deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# === SESSION API ROUTES ===
@app.route('/api/sessions')
def api_sessions():
    try:
        sessions = execute_query("""
            SELECT DISTINCT ON (username, acctsessionid)
                   radacctid, acctsessionid, acctuniqueid, username,
                   nasipaddress, nasportid, nasporttype, acctstarttime,
                   acctsessiontime, acctinputoctets, acctoutputoctets,
                   calledstationid, callingstationid, framedipaddress,
                   framedprotocol, servicetype, connectinfo_start,
                   CASE WHEN acctstoptime IS NULL THEN 'active' ELSE 'inactive' END as status
            FROM radacct
            WHERE acctstoptime IS NULL
            ORDER BY username, acctsessionid, acctstarttime DESC
        """)
        return jsonify(sessions)
    except Exception as e:
        print(f"Error in api_sessions: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/sessions/cleanup', methods=['POST'])
@login_required
def api_cleanup_sessions():
    try:
        execute_query("""
            DELETE FROM radacct 
            WHERE acctstoptime IS NULL 
            AND acctstarttime < NOW() - INTERVAL '2 hours'
        """, fetch=False)
        
        execute_query("""
            DELETE FROM radacct
            WHERE radacctid NOT IN (
                SELECT DISTINCT ON (username) radacctid
                FROM radacct
                WHERE acctstoptime IS NULL
                ORDER BY username, acctstarttime DESC
            )
            AND acctstoptime IS NULL
        """, fetch=False)
        
        return jsonify({'success': True, 'message': 'Stale sessions cleaned up successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/disconnect/<session_id>', methods=['POST'])
@login_required
def api_disconnect_session(session_id):
    try:
        execute_query("""
            UPDATE radacct 
            SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
            WHERE acctsessionid = %s
        """, (session_id,), fetch=False)
        
        return jsonify({'success': True, 'message': 'Session disconnected successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/disconnect-user/<username>', methods=['POST'])
@login_required
def api_disconnect_user_sessions(username):
    try:
        sessions = execute_query("""
            SELECT acctsessionid FROM radacct 
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))
        
        for session in sessions:
            execute_query("""
                UPDATE radacct 
                SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                WHERE acctsessionid = %s
            """, (session['acctsessionid'],), fetch=False)
        
        return jsonify({'success': True, 'message': f'Disconnected {len(sessions)} sessions for {username}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logs')
def api_logs():
    try:
        logs = execute_query("""
            SELECT id, username, reply, authdate, pass, 
                   calledstationid, callingstationid
            FROM radpostauth
            ORDER BY authdate DESC
            LIMIT 50
        """)
        return jsonify(logs)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === DASHBOARD API ROUTES - FIXED ===

@app.route('/api/auth-activity')
def api_auth_activity():
    """Get authentication activity for the last 30 minutes - FIXED COUNTING"""
    try:
        activity = execute_query("""
            WITH minutes AS (
                SELECT generate_series(
                    DATE_TRUNC('minute', NOW() - INTERVAL '30 minutes'),
                    DATE_TRUNC('minute', NOW()),
                    INTERVAL '1 minute'
                ) AS minute
            )
            SELECT 
                TO_CHAR(m.minute, 'HH24:MI') as time_label,
                COUNT(p.id) as attempts
            FROM minutes m
            LEFT JOIN radpostauth p ON DATE_TRUNC('minute', p.authdate) = m.minute
            GROUP BY m.minute, time_label
            ORDER BY m.minute
        """)
        
        labels = [item['time_label'] for item in activity]
        data = [item['attempts'] for item in activity]
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        print(f"Error in auth activity: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'labels': [], 'data': []})

@app.route('/api/sessions-by-nas')
def api_sessions_by_nas():
    """Get session distribution by NAS device - FIXED with HOST() function"""
    try:
        # Gunakan fungsi HOST() untuk menghilangkan /32 dari alamat IP INET
        # sehingga bisa dibandingkan dengan nasname yang bertipe TEXT
        sessions = execute_query("""
            SELECT COALESCE(n.shortname, n.nasname, 'Unknown') as nasname,
                   COUNT(r.acctsessionid) as session_count
            FROM radacct r
            LEFT JOIN nas n ON HOST(r.nasipaddress) = n.nasname
            WHERE r.acctstoptime IS NULL
            GROUP BY COALESCE(n.shortname, n.nasname, 'Unknown')
            ORDER BY session_count DESC
        """)
        
        labels = []
        data = []
        for item in sessions:
            labels.append(item['nasname'])
            data.append(item['session_count'])
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        print(f"Error in sessions by NAS: {str(e)}")
        # Jika terjadi error, tetap kembalikan struktur yang benar agar frontend tidak error
        return jsonify({'labels': ['Error'], 'data': [0]})

@app.route('/api/recent-activity')
def api_recent_activity():
    """Get recent authentication activity - FIXED TIMEZONE ISSUE"""
    try:
        activity = execute_query("""
            SELECT username, reply, authdate, 
                   calledstationid, callingstationid
            FROM radpostauth
            WHERE authdate >= NOW() - INTERVAL '30 minutes'
            ORDER BY authdate DESC
            LIMIT 10
        """)
        
        formatted_activity = []
        # Get current time with timezone awareness
        now = datetime.now(timezone.utc)
        
        for item in activity:
            is_success = item['reply'] == 'Access-Accept'
            icon = 'check-circle' if is_success else 'x-circle'
            color = 'green' if is_success else 'red'
            text = f"User {item['username']} {'login successful' if is_success else 'login failed'}"
            
            # Make authdate timezone-aware if it isn't already
            authdate = item['authdate']
            if authdate.tzinfo is None:
                authdate = authdate.replace(tzinfo=timezone.utc)
            
            # Calculate time difference
            time_diff = now - authdate
            
            if time_diff.total_seconds() < 60:
                time_text = 'Just now'
            else:
                minutes = int(time_diff.total_seconds() / 60)
                time_text = f"{minutes} min ago"
            
            formatted_activity.append({
                'type': color,
                'icon': icon,
                'text': text,
                'time': time_text,
                'color': color,
                'username': item['username'],
                'reply': item['reply'],
                'calledstationid': item.get('calledstationid', ''),
                'callingstationid': item.get('callingstationid', '')
            })
        
        return jsonify(formatted_activity)
    except Exception as e:
        print(f"Error in recent activity: {str(e)}")
        return jsonify([])

@app.route('/api/system-metrics')
def api_system_metrics():
    """Get system performance metrics"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        try:
            db_connections = execute_query("""
                SELECT count(*) as connections 
                FROM pg_stat_activity 
                WHERE state = 'active'
            """)[0]['connections']
        except:
            db_connections = 0
        
        try:
            disk_usage = psutil.disk_usage(BACKUP_FOLDER)
            disk_percent = (disk_usage.used / disk_usage.total) * 100
        except:
            disk_usage = type('obj', (object,), {'used': 0, 'total': 1})()
            disk_percent = 0
        
        return jsonify({
            'cpu_percent': round(cpu_percent, 1),
            'memory_percent': round(memory.percent, 1),
            'memory_used': f"{round(memory.used / (1024**3), 1)}GB",
            'memory_total': f"{round(memory.total / (1024**3), 1)}GB",
            'db_connections': db_connections,
            'db_max_connections': 100,
            'disk_percent': round(disk_percent, 1),
            'disk_used': f"{round(disk_usage.used / (1024**3), 1)}GB",
            'disk_total': f"{round(disk_usage.total / (1024**3), 1)}GB"
        })
    except Exception as e:
        print(f"Error in system metrics: {str(e)}")
        return jsonify({
            'cpu_percent': 0, 'memory_percent': 0,
            'memory_used': '0GB', 'memory_total': '0GB',
            'db_connections': 0, 'db_max_connections': 100,
            'disk_percent': 0, 'disk_used': '0GB', 'disk_total': '0GB'
        })

# === BACKUP API ROUTES (shortened for space) ===
# ... (include all backup routes from original code)

@app.route('/health')
def health():
    return 'OK'

# === TAMBAHKAN KODE INI KE app.py ANDA ===

# Tambahkan import ini di bagian atas file, bersama import lainnya
import io

# === BACKUP API ROUTES (LENGKAP) ===

@app.route('/api/backup/list')
def api_list_backups():
    """List all available backup files"""
    try:
        # Fungsi ini sudah ada di file Anda, pastikan sudah didefinisikan
        ensure_backup_folder() 
        
        backups = []
        
        if os.path.exists(BACKUP_FOLDER):
            for filename in os.listdir(BACKUP_FOLDER):
                if allowed_backup_file(filename):
                    file_path = os.path.join(BACKUP_FOLDER, filename)
                    if os.path.isfile(file_path):
                        # Dapatkan informasi file
                        stats = os.stat(file_path)
                        size_bytes = stats.st_size
                        created_at = datetime.fromtimestamp(stats.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
                        
                        backups.append({
                            'filename': filename,
                            'created_at': created_at,
                            'size': size_bytes,
                            'size_human': format_file_size(size_bytes)
                        })
        
        # Urutkan dari yang terbaru
        backups.sort(key=lambda x: x['created_at'], reverse=True)
        
        return jsonify({'success': True, 'backups': backups})
    except Exception as e:
        print(f"Error listing backups: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/create', methods=['POST'])
def api_create_backup():
    """Create a new database backup"""
    try:
        ensure_backup_folder()
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"radius_backup_{timestamp}.sql"
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        # Get database connection info from config
        db_host = app.config['DB_HOST']
        db_name = app.config['DB_NAME']
        db_user = app.config['DB_USER']
        db_password = app.config['DB_PASSWORD']
        db_port = app.config['DB_PORT']
        
        # Create pg_dump command
        cmd = [
            'pg_dump',
            f'--host={db_host}',
            f'--port={db_port}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            '--no-password',
            '--clean',
            '--if-exists',
            '--inserts',
            '--column-inserts',
            '--attribute-inserts',
            '--disable-dollar-quoting',
            '--disable-triggers',
            '--no-tablespaces',
            '--no-unlogged',
            '--format=custom',
            '--file=' + filepath
        ]
        
        # Set PGPASSWORD environment variable for pg_dump
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        # Execute pg_dump command
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        if result.returncode == 0:
            return jsonify({
                'success': True, 
                'message': 'Backup created successfully',
                'filename': filename
            })
        else:
            return jsonify({
                'success': False, 
                'error': f"pg_dump failed: {result.stderr}"
            }), 500
            
    except Exception as e:
        print(f"Error creating backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/download/<filename>')
def api_download_backup(filename):
    """Download a specific backup file"""
    try:
        # Validate filename
        if not allowed_backup_file(filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        return send_file(
            filepath, 
            as_attachment=True, 
            download_name=filename
        )
    except Exception as e:
        print(f"Error downloading backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/delete/<filename>', methods=['DELETE'])
def api_delete_backup(filename):
    """Delete a specific backup file"""
    try:
        # Validate filename
        if not allowed_backup_file(filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        os.remove(filepath)
        
        return jsonify({
            'success': True, 
            'message': 'Backup deleted successfully'
        })
    except Exception as e:
        print(f"Error deleting backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/upload', methods=['POST'])
def api_upload_backup():
    """Upload a backup file"""
    try:
        ensure_backup_folder()
        
        if 'backup_file' not in request.files:
            return jsonify({'success': False, 'error': 'No file part'}), 400
        
        file = request.files['backup_file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No selected file'}), 400
        
        if file and allowed_backup_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(BACKUP_FOLDER, filename)
            
            # Save the file
            file.save(filepath)
            
            return jsonify({
                'success': True, 
                'message': 'Backup uploaded successfully',
                'filename': filename
            })
        else:
            return jsonify({'success': False, 'error': 'File type not allowed'}), 400
            
    except Exception as e:
        print(f"Error uploading backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/restore', methods=['POST'])
def api_restore_backup():
    """Restore database from a backup file"""
    try:
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        # Validate filename
        if not allowed_backup_file(filename):
            return jsonify({'success': False, 'error': 'Invalid file type'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        # Get database connection info from config
        db_host = app.config['DB_HOST']
        db_name = app.config['DB_NAME']
        db_user = app.config['DB_USER']
        db_password = app.config['DB_PASSWORD']
        db_port = app.config['DB_PORT']
        
        # Create psql command to restore from backup
        cmd = [
            'psql',
            f'--host={db_host}',
            f'--port={db_port}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            '--no-password',
            '--file=' + filepath
        ]
        
        # Set PGPASSWORD environment variable for psql
        env = os.environ.copy()
        env['PGPASSWORD'] = db_password
        
        # Execute psql command
        result = subprocess.run(cmd, capture_output=True, text=True, env=env)
        
        if result.returncode == 0:
            return jsonify({
                'success': True, 
                'message': 'Database restored successfully'
            })
        else:
            return jsonify({
                'success': False, 
                'error': f"psql failed: {result.stderr}"
            }), 500
            
    except Exception as e:
        print(f"Error restoring backup: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# === AKHIRI KODE TAMBAHAN ===

# === SOCKET IO EVENTS ===
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('status', {'data': 'Connected'})

def background_metrics():
    """Emit system metrics every 5 seconds"""
    while True:
        try:
            metrics = api_system_metrics_data()
            socketio.emit('system_metrics', metrics)
        except Exception as e:
            print(f"Error in background metrics: {e}")
        socketio.sleep(5)

def api_system_metrics_data():
    """Helper to get system metrics without request context"""
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    return {
        'cpu_percent': round(cpu_percent, 1),
        'memory_percent': round(memory.percent, 1),
        'db_connections': 0, # Simplified for now
        'db_max_connections': 100,
        'disk_percent': 0
    }

# Start background thread always
start_cleanup_thread()
threading.Thread(target=background_metrics, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
