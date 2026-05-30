from flask import Flask, render_template, request, flash, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit
import psycopg2
from psycopg2 import pool as psycopg2_pool
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
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError

# === TELEGRAM NOTIFICATION ===
def _send_telegram_worker(message):
    token, chat_id = '', ''
    env_path = '/app/.telegram.env'
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    token = line.strip().split('=', 1)[1]
                elif line.startswith('TELEGRAM_CHAT_ID='):
                    chat_id = line.strip().split('=', 1)[1]
    if not token or not chat_id:
        return
    try:
        import json as _json
        payload = _json.dumps({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'})
        subprocess.run(
            ['curl', '-s', '-m', '10', '-X', 'POST',
             f'https://api.telegram.org/bot{token}/sendMessage',
             '-H', 'Content-Type: application/json',
             '-d', payload],
            capture_output=True, timeout=12
        )
    except Exception as e:
        print(f"Telegram error: {e}")

def send_telegram_notification(message):
    import threading
    threading.Thread(target=_send_telegram_worker, args=(message,), daemon=True).start()

app = Flask(__name__)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config.from_object(Config)

# Load persisted Telegram settings (saved via UI)
_telegram_env = '/app/.telegram.env'
if os.path.exists(_telegram_env):
    with open(_telegram_env) as _f:
        for _line in _f:
            if '=' in _line:
                _k, _v = _line.strip().split('=', 1)
                app.config[_k] = _v

# Initialize SocketIO
_allowed_origins = os.getenv('ALLOWED_ORIGINS', '*')
socketio = SocketIO(app, cors_allowed_origins=_allowed_origins if _allowed_origins == '*' else _allowed_origins.split(','), async_mode='eventlet')

# Initialize Rate Limiter (Redis-backed, shared across gunicorn workers)
_redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri=_redis_url)

# Initialize CSRF Protection
# API routes (/api/*) are exempt — they're protected by @login_required + session cookie
app.config['WTF_CSRF_CHECK_DEFAULT'] = False
csrf = CSRFProtect(app)

@app.after_request
def add_no_cache(response):
    if 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response

@app.before_request
def csrf_protect():
    # Only enforce CSRF on state-changing form submissions (non-API)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        if not request.path.startswith('/api/'):
            csrf.protect()

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify({'success': False, 'error': 'CSRF token missing or invalid'}), 400

# === BACKUP & RESTORE CONFIGURATION ===
BACKUP_FOLDER = os.getenv('BACKUP_FOLDER', '/app/backups')
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
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# === DATABASE CONNECTION ===
# === DATABASE CONNECTION POOL ===
_db_pool = None

def get_db_pool():
    global _db_pool
    if _db_pool is None:
        _db_pool = psycopg2_pool.ThreadedConnectionPool(
            minconn=3, maxconn=10,
            host=app.config['DB_HOST'],
            database=app.config['DB_NAME'],
            user=app.config['DB_USER'],
            password=app.config['DB_PASSWORD'],
            port=app.config['DB_PORT']
        )

    return _db_pool

def get_db_connection():
    return get_db_pool().getconn()

def execute_query(query, params=None, fetch=True):
    pool = get_db_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        if fetch:
            result = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            cur.close()
            return [dict(zip(columns, row)) for row in result]
        else:
            conn.commit()
            cur.close()
            return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)

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
    Cek konektivitas NAS: ICMP ping dulu, jika gagal coba TCP ke port umum router.
    """
    import socket as _socket

    # 1. Coba ICMP ping
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip_address],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2
        )
        if result.returncode == 0:
            return "online"
    except Exception:
        pass

    # 2. Jika ping gagal (mungkin ICMP diblokir), coba TCP ke port umum router
    for port in [8728, 80, 443, 22, 23, 8291]:
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(1)
            if sock.connect_ex((ip_address, port)) == 0:
                sock.close()
                return "online"
            sock.close()
        except Exception:
            pass

    return "offline"


# Background cleanup thread dihapus - berbahaya karena menghapus session PPPoE aktif
# yang bisa online berhari-hari. Gunakan /api/sessions/fix-stale untuk mark stale secara manual.

# === AUTHENTICATION ROUTES ===
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
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
            # Brute force detection: track failed attempts per IP via Redis
            try:
                import redis as redis_lib
                r = redis_lib.from_url(_redis_url)
                ip = request.remote_addr
                key = f'failed_login:{ip}'
                attempts = r.incr(key)
                r.expire(key, 600)  # reset setelah 10 menit
                if attempts == 5:
                    send_telegram_notification(
                        f"⚠️ <b>Brute Force Detected</b>\n"
                        f"IP: <code>{ip}</code>\n"
                        f"Username dicoba: <code>{username}</code>\n"
                        f"Gagal: {attempts}x dalam 10 menit\n"
                        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
            except Exception as e:
                print(f"Brute force tracking error: {e}")
    
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
            'provisioning_url': provisioning_url
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

@app.route('/api/telegram/settings', methods=['GET'])
@login_required
def api_telegram_settings_get():
    return jsonify({
        'bot_token': app.config.get('TELEGRAM_BOT_TOKEN', ''),
        'chat_id': app.config.get('TELEGRAM_CHAT_ID', '')
    })

@app.route('/api/telegram/settings', methods=['POST'])
@login_required
def api_telegram_settings_save():
    try:
        data = get_request_data()
        bot_token = data.get('bot_token', '').strip()
        chat_id = data.get('chat_id', '').strip()

        # Update runtime config
        app.config['TELEGRAM_BOT_TOKEN'] = bot_token
        app.config['TELEGRAM_CHAT_ID'] = chat_id

        # Persist to .env file so it survives gunicorn reload
        env_path = '/app/.telegram.env'
        with open(env_path, 'w') as f:
            f.write(f"TELEGRAM_BOT_TOKEN={bot_token}\n")
            f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")

        return jsonify({'success': True, 'message': 'Telegram settings saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/telegram/test', methods=['POST'])
@login_required
def api_telegram_test():
    import json as _json

    env_path = '/app/.telegram.env'
    token, chat_id = '', ''
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    token = line.strip().split('=', 1)[1]
                elif line.startswith('TELEGRAM_CHAT_ID='):
                    chat_id = line.strip().split('=', 1)[1]

    if not token or not chat_id:
        return jsonify({'success': False, 'error': 'Bot Token atau Chat ID belum dikonfigurasi'}), 400

    try:
        msg = f"✅ <b>Test Notification</b>\nRADIUS Manager terhubung ke Telegram!\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        payload = _json.dumps({'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'})
        # Gunakan curl via subprocess untuk bypass eventlet monkey-patch sepenuhnya
        result = subprocess.run(
            ['curl', '-s', '-m', '10', '-X', 'POST',
             f'https://api.telegram.org/bot{token}/sendMessage',
             '-H', 'Content-Type: application/json',
             '-d', payload],
            capture_output=True, text=True, timeout=12
        )
        resp = _json.loads(result.stdout)
        if resp.get('ok'):
            return jsonify({'success': True, 'message': 'Pesan test berhasil dikirim ke Telegram!'})
        else:
            return jsonify({'success': False, 'error': f"Telegram: {resp.get('description', 'Unknown error')}"}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
@login_required
def api_stats():
    try:
        # PPPoE Stats (Users not in ipoe, vpn, or 8021x groups)
        pppoe_users = execute_query("""
            SELECT COUNT(*) as count FROM radcheck c 
            WHERE c.attribute = 'Cleartext-Password' 
            AND NOT EXISTS (
                SELECT 1 FROM radusergroup g 
                WHERE LOWER(g.username) = LOWER(c.username) 
                AND g.groupname IN ('ipoe', 'vpn', '8021x')
            )
        """)[0]['count']
        
        pppoe_sessions = execute_query("""
            SELECT COUNT(*) as count FROM radacct a 
            WHERE a.acctstoptime IS NULL 
            AND NOT EXISTS (
                SELECT 1 FROM radusergroup g 
                WHERE LOWER(g.username) = LOWER(a.username) 
                AND g.groupname IN ('ipoe', 'vpn', '8021x')
            ) 
            AND NOT EXISTS (
                SELECT 1 FROM hotspot_vouchers h 
                WHERE LOWER(h.username) = LOWER(a.username)
            )
        """)[0]['count']
        
        # IPoE Stats
        ipoe_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'ipoe'")[0]['count']
        ipoe_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'ipoe' WHERE a.acctstoptime IS NULL")[0]['count']

        # Hotspot Stats
        hotspot_users = execute_query("SELECT COUNT(*) as count FROM hotspot_vouchers WHERE deleted_at IS NULL")[0]['count']
        hotspot_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a WHERE a.acctstoptime IS NULL AND EXISTS (SELECT 1 FROM hotspot_vouchers h WHERE LOWER(h.username) = LOWER(a.username))")[0]['count']

        # VPN Stats
        vpn_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'vpn'")[0]['count']
        vpn_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'vpn' WHERE a.acctstoptime IS NULL")[0]['count']

        # 802.1X Stats
        x8021x_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = '8021x'")[0]['count']
        x8021x_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = '8021x' WHERE a.acctstoptime IS NULL")[0]['count']

        total_users = pppoe_users + ipoe_users + hotspot_users + vpn_users + x8021x_users
        active_sessions = pppoe_sessions + ipoe_sessions + hotspot_sessions + vpn_sessions + x8021x_sessions
        online_users = execute_query("SELECT COUNT(DISTINCT username) as count FROM radacct WHERE acctstoptime IS NULL")[0]['count']
        today_auth = execute_query("SELECT COUNT(*) as count FROM radpostauth WHERE DATE(authdate AT TIME ZONE 'Asia/Jakarta') = CURRENT_DATE")[0]['count']

        try:
            total_nas = execute_query("SELECT COUNT(*) as count FROM nas")[0]['count']
        except:
            total_nas = 0

        return jsonify({
            'pppoe_users': pppoe_users,
            'pppoe_sessions': pppoe_sessions,
            'ipoe_users': ipoe_users,
            'ipoe_sessions': ipoe_sessions,
            'hotspot_users': hotspot_users,
            'hotspot_sessions': hotspot_sessions,
            'vpn_users': vpn_users,
            'vpn_sessions': vpn_sessions,
            'x8021x_users': x8021x_users,
            'x8021x_sessions': x8021x_sessions,
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
@login_required
def api_nas():
    try:
        nas_list = execute_query("""
            SELECT n.*,
                   COUNT(r.acctsessionid) as session_count
            FROM nas n
            LEFT JOIN radacct r ON HOST(r.nasipaddress) = n.nasname AND r.acctstoptime IS NULL
            GROUP BY n.id
            ORDER BY n.id
        """)
        return jsonify(nas_list)
    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify([])
        return jsonify({'error': str(e)}), 500

@app.route('/api/nas/add', methods=['POST'])
@login_required
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
        
        send_telegram_notification(f"🖥️ <b>NAS Added</b>\nName: {data['nasname']}\nShort: {data['shortname']}\nType: {data['type']}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': 'NAS added successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nas/edit/<int:nas_id>', methods=['PUT'])
@login_required
def api_edit_nas(nas_id):
    try:
        data = get_request_data()
        execute_query("""
            UPDATE nas SET nasname=%s, shortname=%s, type=%s, ports=%s, secret=%s, description=%s
            WHERE id=%s
        """, (data['nasname'], data['shortname'], data['type'], int(data['ports']),
              data['secret'], data.get('description', ''), nas_id), fetch=False)
        return jsonify({'success': True, 'message': 'NAS updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/nas/delete/<int:nas_id>', methods=['DELETE'])
@login_required
def api_delete_nas(nas_id):
    try:
        nas_info = execute_query("SELECT nasname, shortname FROM nas WHERE id = %s", (nas_id,))
        execute_query("DELETE FROM nas WHERE id = %s", (nas_id,), fetch=False)
        if nas_info:
            send_telegram_notification(f"🗑️ <b>NAS Deleted</b>\nName: {nas_info[0]['nasname']}\nShort: {nas_info[0]['shortname']}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
@login_required
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
            AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'ipoe')
        """
        
        # Base query for count
        count_query = "SELECT COUNT(*) as total FROM radcheck c WHERE c.attribute = 'Cleartext-Password' AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'ipoe')"

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
@login_required
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
@login_required
def api_update_user(username):
    try:
        data = get_request_data()

        if data.get('ip_address') is not None:
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Framed-IP-Address'", (username,), fetch=False)
            if data['ip_address']:
                execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)",
                             (username, data['ip_address']), fetch=False)

        if data.get('rate_limit') is not None:
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Rate-Limit'", (username,), fetch=False)
            if data['rate_limit']:
                execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)",
                             (username, data['rate_limit']), fetch=False)

        if data.get('session_timeout') is not None:
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Session-Timeout'", (username,), fetch=False)
            if data['session_timeout']:
                execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Session-Timeout', ':=', %s)",
                             (username, data['session_timeout']), fetch=False)

        if data.get('idle_timeout') is not None:
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Idle-Timeout'", (username,), fetch=False)
            if data['idle_timeout']:
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

        # Kirim CoA-Request jika rate_limit berubah dan user sedang online
        coa_total = 0
        if data.get('rate_limit'):
            active_sessions = execute_query("""
                SELECT acctsessionid, nasipaddress FROM radacct
                WHERE username = %s AND acctstoptime IS NULL
            """, (username,))
            for s in active_sessions:
                nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
                secret = nas[0]['secret'] if nas else 'radiussecret'
                send_coa_update_async(s['nasipaddress'], s['acctsessionid'], secret, data['rate_limit'])
                coa_total += 1

        msg = 'User updated successfully'
        if coa_total > 0:
            msg += f' | CoA sent to {coa_total} active session(s)'
        elif data.get('rate_limit'):
            msg += ' | User not online, CoA not sent'

        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/throttle/<username>', methods=['POST'])
@login_required
def api_throttle_user(username):
    """Throttle user ke bandwidth rendah via CoA tanpa disconnect"""
    try:
        data = get_request_data()
        rate_limit = data.get('rate_limit', '512k/512k')  # default throttle 512kbps

        active_sessions = execute_query("""
            SELECT acctsessionid, nasipaddress FROM radacct
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))

        if not active_sessions:
            return jsonify({'success': False, 'error': 'User not online'}), 404

        coa_sent = 0
        for s in active_sessions:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_update(s['nasipaddress'], s['acctsessionid'], secret, rate_limit):
                coa_sent += 1

        return jsonify({
            'success': True,
            'message': f'Throttled to {rate_limit} | {coa_sent}/{len(active_sessions)} sessions updated'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/unthrottle/<username>', methods=['POST'])
@login_required
def api_unthrottle_user(username):
    """Restore bandwidth user ke rate_limit asli via CoA"""
    try:
        rate_limit = execute_query("""
            SELECT value FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Rate-Limit'
        """, (username,))

        if not rate_limit:
            return jsonify({'success': False, 'error': 'No rate limit configured for this user'}), 404

        original_rate = rate_limit[0]['value']
        active_sessions = execute_query("""
            SELECT acctsessionid, nasipaddress FROM radacct
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))

        if not active_sessions:
            return jsonify({'success': False, 'error': 'User not online'}), 404

        coa_sent = 0
        for s in active_sessions:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_update(s['nasipaddress'], s['acctsessionid'], secret, original_rate):
                coa_sent += 1

        return jsonify({
            'success': True,
            'message': f'Restored to {original_rate} | {coa_sent}/{len(active_sessions)} sessions updated'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/users/delete/<int:user_id>', methods=['DELETE'])
@login_required
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

@app.route('/api/users/delete/by-username/<username>', methods=['DELETE'])
@login_required
def api_delete_user_by_username(username):
    try:
        user = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if user:
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
@login_required
def api_sessions():
    try:
        sessions = execute_query("""
            SELECT DISTINCT ON (username, acctsessionid)
                   radacctid, acctsessionid, acctuniqueid, username,
                   nasipaddress, nasportid, nasporttype, acctstarttime,
                   acctupdatetime, acctsessiontime, acctinputoctets, acctoutputoctets,
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
        # Hanya hapus session yang benar-benar stale:
        # acctstoptime IS NULL DAN tidak ada interim-update dalam 30 menit terakhir
        # (bukan berdasarkan acctstarttime - user PPPoE bisa online berhari-hari)
        deleted = execute_query("""
            DELETE FROM radacct 
            WHERE acctstoptime IS NULL 
            AND acctupdatetime < NOW() - INTERVAL '30 minutes'
            RETURNING radacctid
        """)
        
        count = len(deleted) if deleted else 0
        return jsonify({'success': True, 'message': f'{count} stale sessions cleaned up (no update for 30+ minutes)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/fix-stale', methods=['POST'])
@login_required
def api_fix_stale_sessions():
    """Mark session stale (set acctstoptime) jika tidak ada interim-update dalam 15 menit.
    Lebih aman dari delete - data tetap ada untuk audit."""
    try:
        threshold = int(request.json.get('minutes', 15)) if request.is_json else 15
        threshold = max(5, min(threshold, 1440))  # clamp 5 menit - 24 jam
        updated = execute_query("""
            UPDATE radacct 
            SET acctstoptime = acctupdatetime, acctterminatecause = 'Lost-Carrier'
            WHERE acctstoptime IS NULL 
            AND acctupdatetime < NOW() - (INTERVAL '1 minute' * %s)
            RETURNING radacctid, username
        """, (threshold,))
        
        count = len(updated) if updated else 0
        return jsonify({'success': True, 'message': f'{count} stale sessions marked as disconnected', 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def _send_coa_packet(nas_ip, secret, packet_type, attributes):
    """Kirim CoA/Disconnect packet via radclient di freeradius container"""
    try:
        # Gunakan newline-separated attributes
        attr_lines = '\n'.join(f'{k} = "{v}"' for k, v in attributes.items())
        # Jalankan docker exec secara langsung tanpa sh -c / pipeline untuk menghindari RCE
        cmd = [
            'docker', 'exec', '-i', 'freeradius-server',
            'radclient', '-t', '3', '-r', '1', f'{nas_ip}:3799', packet_type, secret
        ]
        result = subprocess.run(
            cmd, input=f"{attr_lines}\n", capture_output=True, text=True, timeout=6
        )
        return result.stdout
    except Exception as e:
        print(f"CoA error ({nas_ip}): {e}")
        return ''

def send_coa_disconnect(nas_ip, session_id, nas_secret, framed_ip=None):
    if framed_ip:
        out = _send_coa_packet(nas_ip, nas_secret, 'disconnect', {
            'Framed-IP-Address': framed_ip, 'NAS-IP-Address': nas_ip
        })
        if 'Disconnect-ACK' in out:
            return True
    # Fallback: Acct-Session-Id (PPPoE)
    out = _send_coa_packet(nas_ip, nas_secret, 'disconnect', {
        'Acct-Session-Id': session_id, 'NAS-IP-Address': nas_ip
    })
    return 'Disconnect-ACK' in out

def send_coa_update(nas_ip, session_id, nas_secret, rate_limit):
    out = _send_coa_packet(nas_ip, nas_secret, 'coa', {
        'Acct-Session-Id': session_id, 'NAS-IP-Address': nas_ip,
        'Mikrotik-Rate-Limit': rate_limit
    })
    return 'CoA-ACK' in out

def send_coa_update_async(nas_ip, session_id, nas_secret, rate_limit):
    """Kirim CoA di background thread agar tidak block response"""
    t = threading.Thread(target=send_coa_update, args=(nas_ip, session_id, nas_secret, rate_limit), daemon=True)
    t.start()

@app.route('/api/sessions/disconnect/<session_id>', methods=['POST'])
@login_required
def api_disconnect_session(session_id):
    try:
        session_data = execute_query("""
            SELECT acctsessionid, nasipaddress, framedipaddress FROM radacct 
            WHERE acctsessionid = %s AND acctstoptime IS NULL
        """, (session_id,))

        if not session_data:
            return jsonify({'success': False, 'error': 'Session not found'}), 404

        nas_ip = session_data[0]['nasipaddress']
        nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (nas_ip,))
        secret = nas[0]['secret'] if nas else 'radiussecret'

        # Kirim CoA Disconnect ke MikroTik
        coa_sent = send_coa_disconnect(nas_ip, session_id, secret, framed_ip=session_data[0].get('framedipaddress'))

        # Update database
        execute_query("""
            UPDATE radacct 
            SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
            WHERE acctsessionid = %s
        """, (session_id,), fetch=False)

        msg = 'Session disconnected (CoA sent to NAS)' if coa_sent else 'Session marked disconnected (CoA failed, user may still be online)'
        send_telegram_notification(f"🔌 <b>Session Disconnected</b>\nUsername: {session_data[0].get('username', '-')}\nNAS: {nas_ip}\nBy: {session.get('username','admin')}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': msg, 'coa_sent': coa_sent})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/disconnect-user/<username>', methods=['POST'])
@login_required
def api_disconnect_user_sessions(username):
    try:
        sessions = execute_query("""
            SELECT acctsessionid, nasipaddress FROM radacct 
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))

        coa_success = 0
        for s in sessions:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret):
                coa_success += 1
            execute_query("""
                UPDATE radacct 
                SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                WHERE acctsessionid = %s
            """, (s['acctsessionid'],), fetch=False)

        return jsonify({
            'success': True,
            'message': f'Disconnected {len(sessions)} sessions for {username} ({coa_success} CoA sent)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/duplicates')
@login_required
def api_sessions_duplicates():
    try:
        dupes = execute_query("""
            SELECT username, COUNT(*) as session_count,
                   array_agg(acctsessionid ORDER BY acctstarttime DESC) as session_ids,
                   array_agg(nasipaddress ORDER BY acctstarttime DESC) as nas_ips,
                   array_agg(framedipaddress ORDER BY acctstarttime DESC) as framed_ips,
                   array_agg(acctstarttime ORDER BY acctstarttime DESC) as start_times
            FROM radacct
            WHERE acctstoptime IS NULL
            GROUP BY username
            HAVING COUNT(*) > 1
            ORDER BY session_count DESC, username
        """)
        return jsonify(dupes or [])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/sessions/disconnect-expired', methods=['POST'])
@login_required
def api_disconnect_expired():
    """Disconnect semua sesi aktif milik user yang sudah expired (trigger manual)."""
    try:
        expired_sessions = execute_query("""
            SELECT a.acctsessionid, a.nasipaddress, a.username
            FROM radacct a
            JOIN radcheck rc ON LOWER(rc.username) = LOWER(a.username)
                AND rc.attribute = 'Expiration'
            WHERE a.acctstoptime IS NULL
              AND TO_TIMESTAMP(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
        """)
        if not expired_sessions:
            return jsonify({'success': True, 'message': 'Tidak ada sesi expired yang aktif', 'count': 0})

        coa_ok = 0
        for s in expired_sessions:
            nas_ip = str(s['nasipaddress'])
            nas_row = execute_query("SELECT secret FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
            secret = nas_row[0]['secret'] if nas_row else 'testing123'
            if send_coa_disconnect(nas_ip, s['acctsessionid'], secret):
                coa_ok += 1
            execute_query("""
                UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Session-Timeout'
                WHERE acctsessionid = %s AND acctstoptime IS NULL
            """, (s['acctsessionid'],), fetch=False)

        return jsonify({
            'success': True,
            'message': f'{len(expired_sessions)} sesi expired di-disconnect ({coa_ok} CoA sent)',
            'count': len(expired_sessions)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/sessions/duplicates/disconnect', methods=['POST'])
@login_required
def api_disconnect_duplicates():
    """Disconnect semua session duplikat, sisakan yang terbaru per username."""
    try:
        dupes = execute_query("""
            WITH ranked AS (
                SELECT acctsessionid, nasipaddress, username,
                       ROW_NUMBER() OVER (PARTITION BY username ORDER BY acctstarttime DESC) AS rn
                FROM radacct WHERE acctstoptime IS NULL
            )
            SELECT acctsessionid, nasipaddress, username FROM ranked WHERE rn > 1
        """)
        if not dupes:
            return jsonify({'success': True, 'message': 'Tidak ada session duplikat', 'count': 0})

        coa_success = 0
        for s in dupes:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret):
                coa_success += 1
            execute_query("""
                UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                WHERE acctsessionid = %s
            """, (s['acctsessionid'],), fetch=False)

        return jsonify({
            'success': True,
            'message': f'{len(dupes)} session duplikat di-disconnect ({coa_success} CoA sent)',
            'count': len(dupes)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/logs')
@login_required
def api_logs():
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        search = request.args.get('search', '')
        reply_filter = request.args.get('reply', '')
        offset = (page - 1) * limit

        where_clauses = []
        params = []

        if search:
            where_clauses.append("username ILIKE %s")
            params.append(f'%{search}%')
        if reply_filter:
            where_clauses.append("reply = %s")
            params.append(reply_filter)

        where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

        logs = execute_query(f"""
            SELECT id, username, reply,
                   TO_CHAR(authdate AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as authdate,
                   calledstationid, callingstationid
            FROM radpostauth
            {where_sql}
            ORDER BY authdate DESC
            LIMIT %s OFFSET %s
        """, tuple(params + [limit, offset]))

        total = execute_query(f"SELECT COUNT(*) as total FROM radpostauth {where_sql}", tuple(params))[0]['total']

        return jsonify({
            'logs': logs,
            'total': total,
            'page': page,
            'total_pages': (total + limit - 1) // limit
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# === DASHBOARD API ROUTES - FIXED ===

@app.route('/api/auth-activity')
@login_required
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
@login_required
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
@login_required
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
@login_required
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

@app.route('/api/users/<username>/device')
@login_required
def api_user_device(username):
    """Query GenieACS NBI untuk info device berdasarkan pppoeUsername"""
    try:
        GENIEACS_NBI = os.getenv('GENIEACS_NBI_URL', 'http://10.204.0.22:7557')
        query = f'{{"VirtualParameters.pppoeUsername._value":"{username}"}}'
        projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeIP,VirtualParameters.getdeviceuptime,VirtualParameters.getpppuptime,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber'
        resp = requests.get(
            f'{GENIEACS_NBI}/devices',
            params={'query': query, 'projection': projection},
            timeout=5
        )
        devices = resp.json()
        if not devices:
            return jsonify({'found': False, 'message': 'Device not found in GenieACS'})

        d = devices[0]
        vp = d.get('VirtualParameters', {})
        di = d.get('DeviceInfo', {})
        dev_id = d.get('_deviceId', {})

        def val(obj):
            return obj.get('_value', 'N/A') if isinstance(obj, dict) else 'N/A'

        rx = val(vp.get('RXPower', {}))
        try:
            rx_float = float(rx)
            if rx_float >= -15:
                rx_status = 'good'
            elif rx_float >= -25:
                rx_status = 'warning'
            else:
                rx_status = 'bad'
        except (ValueError, TypeError):
            rx_status = 'unknown'

        return jsonify({
            'found': True,
            'serial': val(vp.get('getSerialNumber', {})) or dev_id.get('_SerialNumber', 'N/A'),
            'manufacturer': dev_id.get('_Manufacturer', 'N/A'),
            'model': dev_id.get('_ProductClass', 'N/A'),
            'firmware': val(di.get('SoftwareVersion', {})),
            'rx_power': rx,
            'rx_status': rx_status,
            'pppoe_ip': val(vp.get('pppoeIP', {})),
            'device_uptime': val(vp.get('getdeviceuptime', {})),
            'ppp_uptime': val(vp.get('getpppuptime', {})),
            'temperature': val(vp.get('gettemp', {})),
            'pon_mode': val(vp.get('getponmode', {})),
            'last_inform': d.get('_lastInform', 'N/A'),
        })
    except requests.exceptions.ConnectionError:
        return jsonify({'found': False, 'message': 'GenieACS tidak dapat dihubungi'}), 503
    except Exception as e:
        return jsonify({'found': False, 'message': str(e)}), 500


@app.route('/health')
def health():
    return 'OK'

# === TAMBAHKAN KODE INI KE app.py ANDA ===

# Tambahkan import ini di bagian atas file, bersama import lainnya
import io

# === BACKUP API ROUTES (LENGKAP) ===

@app.route('/api/backup/list')
@login_required
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
@login_required
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
        
        # Create pg_dump command - hanya tabel user dan NAS, exclude session/log
        cmd = [
            'pg_dump',
            f'--host={db_host}',
            f'--port={db_port}',
            f'--username={db_user}',
            f'--dbname={db_name}',
            '--no-password',
            '--clean',
            '--if-exists',
            '--format=plain',
            '--table=radcheck',
            '--table=radreply',
            '--table=nas',
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
@login_required
def api_download_backup(filename):
    """Download a specific backup file"""
    try:
        # Validate filename
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
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
@login_required
def api_delete_backup(filename):
    """Delete a specific backup file"""
    try:
        # Validate filename
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
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
@login_required
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
@login_required
def api_restore_backup():
    """Restore database from a backup file"""
    try:
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'Filename is required'}), 400
        
        # Validate filename
        if not allowed_backup_file(filename) or secure_filename(filename) != filename:
            return jsonify({'success': False, 'error': 'Invalid file name or path'}), 400
        
        filepath = os.path.join(BACKUP_FOLDER, filename)
        
        if not os.path.exists(filepath) or not os.path.isfile(filepath):
            return jsonify({'success': False, 'error': 'File not found'}), 404
        
        # Get database connection info from config
        db_host = app.config['DB_HOST']
        db_name = app.config['DB_NAME']
        db_user = app.config['DB_USER']
        db_password = app.config['DB_PASSWORD']
        db_port = app.config['DB_PORT']
        
        # psql untuk restore format plain SQL
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

# === INTEGRATIONS API ===
@app.route('/integrations')
@login_required
def integrations_page():
    return render_template('integrations.html')

@app.route('/api/integrations')
@login_required
def api_integrations():
    try:
        rows = execute_query("SELECT id, name, type, host, port, username, description, is_active, created_at FROM integrations ORDER BY id")
        return jsonify(rows or [])
    except Exception as e:
        if 'does not exist' in str(e):
            return jsonify([])
        return jsonify({'error': str(e)}), 500

@app.route('/api/integrations/add', methods=['POST'])
@login_required
def api_add_integration():
    data = request.get_json()
    try:
        execute_query("""
            INSERT INTO integrations (name, type, host, port, username, password, description, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (data['name'], data.get('type','mikrotik'), data['host'], int(data.get('port',8728)),
              data['username'], data['password'], data.get('description',''), data.get('is_active', True)), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/integrations/edit/<int:iid>', methods=['PUT'])
@login_required
def api_edit_integration(iid):
    data = request.get_json()
    try:
        if data.get('password'):
            execute_query("""
                UPDATE integrations SET name=%s, type=%s, host=%s, port=%s, username=%s, password=%s, description=%s, is_active=%s
                WHERE id=%s
            """, (data['name'], data.get('type','mikrotik'), data['host'], int(data.get('port',8728)),
                  data['username'], data['password'], data.get('description',''), data.get('is_active', True), iid), fetch=False)
        else:
            execute_query("""
                UPDATE integrations SET name=%s, type=%s, host=%s, port=%s, username=%s, description=%s, is_active=%s
                WHERE id=%s
            """, (data['name'], data.get('type','mikrotik'), data['host'], int(data.get('port',8728)),
                  data['username'], data.get('description',''), data.get('is_active', True), iid), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/integrations/delete/<int:iid>', methods=['DELETE'])
@login_required
def api_delete_integration(iid):
    try:
        execute_query("DELETE FROM integrations WHERE id=%s", (iid,), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/integrations/test/<int:iid>', methods=['POST'])
@login_required
def api_test_integration(iid):
    try:
        rows = execute_query("SELECT * FROM integrations WHERE id=%s", (iid,))
        if not rows:
            return jsonify({'success': False, 'error': 'Integration not found'}), 404
        ig = rows[0]
        if ig['type'] == 'mikrotik':
            import librouteros
            api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
            identity = list(api('/system/identity/print'))
            api.close()
            return jsonify({'success': True, 'message': f"Connected! Identity: {identity[0].get('name','?')}"})
        return jsonify({'success': False, 'error': 'Unsupported type'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# === HOTSPOT USERS ===








@app.route('/api/mikrotik/active')
@login_required
def api_mikrotik_active():
    """Query /ppp active + /interface/print dari satu integrasi Mikrotik (read-only)"""
    import librouteros
    router_id = request.args.get('router_id', type=int)
    try:
        if router_id:
            integrations = execute_query("SELECT * FROM integrations WHERE id=%s AND type='mikrotik' AND is_active=true", (router_id,)) or []
        else:
            integrations = execute_query("SELECT * FROM integrations WHERE type='mikrotik' AND is_active=true ORDER BY id LIMIT 1") or []
    except Exception:
        integrations = []

    results = []
    for ig in integrations:
        try:
            api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
            active = list(api('/ppp/active/print'))
            ifaces = list(api('/interface/print'))
            api.close()

            iface_map = {}
            for iface in ifaces:
                if iface.get('type') != 'pppoe-in':
                    continue
                iname = iface.get('name', '').strip('<>')
                uname = iname[6:] if iname.startswith('pppoe-') else iname
                iface_map[uname] = {
                    'rx_byte': int(iface.get('rx-byte', 0)),
                    'tx_byte': int(iface.get('tx-byte', 0)),
                }

            for s in active:
                uname = s.get('name', '')
                idata = iface_map.get(uname, {})
                results.append({
                    'router': ig['name'],
                    'router_host': ig['host'],
                    'router_id': ig['id'],
                    'name': uname,
                    'address': s.get('address', ''),
                    'uptime': s.get('uptime', ''),
                    'rx_byte': idata.get('rx_byte', 0),
                    'tx_byte': idata.get('tx_byte', 0),
                    'caller_id': s.get('caller-id', ''),
                    'service': s.get('service', ''),
                    'session_id': s.get('.id', ''),
                })
        except Exception as e:
            results.append({'router': ig['name'], 'router_host': ig['host'], 'error': str(e)})
        except Exception as e:
            results.append({'router': ig['name'], 'router_host': ig['host'], 'error': str(e)})

    return jsonify({'success': True, 'data': results, 'total': len([r for r in results if 'error' not in r])})

@app.route('/api/ipoe/dhcp-leases')
@login_required
def api_ipoe_dhcp_leases():
    """Ambil DHCP lease dari semua MikroTik, gabungkan dengan data RADIUS"""
    import librouteros
    integrations = execute_query("SELECT * FROM integrations WHERE type='mikrotik' AND is_active=true ORDER BY id") or []
    leases = {}
    errors = []
    for ig in integrations:
        try:
            api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
            raw = list(api('/ip/dhcp-server/lease/print'))
            api.close()
            for l in raw:
                mac = l.get('mac-address', '').lower()
                if not mac:
                    continue
                flags = ''
                if l.get('radius'): flags += 'R'
                if l.get('dynamic'): flags += 'D'
                if l.get('blocked'): flags += 'B'
                leases[mac] = {
                    'router': ig['name'],
                    'router_id': ig['id'],
                    'address': l.get('active-address') or l.get('address', ''),
                    'mac_address': mac,
                    'client_id': l.get('active-client-id') or l.get('client-id', ''),
                    'host_name': l.get('host-name', ''),
                    'server': l.get('active-server') or l.get('server', ''),
                    'status': l.get('status', ''),
                    'expires_after': l.get('expires-after', ''),
                    'last_seen': l.get('last-seen', ''),
                    'rate_limit': l.get('rate-limit', ''),
                    'flags': flags,
                }
        except Exception as e:
            errors.append({'router': ig['name'], 'error': str(e)})

    # Gabungkan dengan data RADIUS (radreply)
    if leases:
        macs = list(leases.keys())
        placeholders = ','.join(['%s'] * len(macs))
        radius_data = execute_query(
            f"SELECT username, attribute, value FROM radreply WHERE username IN ({placeholders}) AND attribute IN ('Framed-IP-Address','Mikrotik-Rate-Limit','Framed-Pool')",
            tuple(macs)
        ) or []
        for row in radius_data:
            mac = row['username']
            if mac in leases:
                if row['attribute'] == 'Framed-IP-Address':
                    leases[mac]['radius_ip'] = row['value']
                elif row['attribute'] == 'Mikrotik-Rate-Limit':
                    leases[mac]['radius_rate'] = row['value']
                elif row['attribute'] == 'Framed-Pool':
                    leases[mac]['radius_pool'] = row['value']

    return jsonify({'success': True, 'leases': list(leases.values()), 'errors': errors})

@app.route('/api/ipoe/stats')
@login_required
def api_ipoe_stats():
    try:
        total = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'ipoe'")[0]['count']
        online = execute_query("""
            SELECT COUNT(DISTINCT a.username) as count 
            FROM radacct a 
            INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'ipoe'
            WHERE a.acctstoptime IS NULL
        """)[0]['count']
        return jsonify({'total': total, 'online': online})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================
# IPoE ROUTES (MAC-based RADIUS auth)
# ============================================================

@app.route('/ipoe-users')
@login_required
def ipoe_users_page():
    return render_template('ipoe-users.html')

@app.route('/api/ipoe/users')
@login_required
def api_ipoe_users():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit

        base_where = "c.attribute = 'Cleartext-Password' AND EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'ipoe')"
        params = []
        if search:
            base_where += " AND c.username ILIKE %s"
            params.append(f'%{search}%')

        data_query = f"""
            SELECT c.id, c.username, c.username as mac_address, c.value as password,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-Pool' LIMIT 1) as framed_pool,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Description' LIMIT 1) as name,
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(c.username) AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c WHERE {base_where}
            ORDER BY c.id DESC LIMIT %s OFFSET %s
        """
        count_query = f"SELECT COUNT(*) as total FROM radcheck c WHERE {base_where}"

        users = execute_query(data_query, tuple(params + [limit, offset]))
        total = execute_query(count_query, tuple(params))[0]['total']
        return jsonify({'users': users, 'total': total, 'page': page, 'limit': limit,
                        'total_pages': (total + limit - 1) // limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/ipoe/users/add', methods=['POST'])
@login_required
def api_ipoe_add_user():
    try:
        data = get_request_data()
        username = (data.get('mac_address') or data.get('username', '')).strip().lower()
        password = data.get('password', username).strip() or username
        rate_limit = data.get('rate_limit', '').strip()
        framed_pool = (data.get('ip_pool') or data.get('framed_pool', '')).strip()
        ip_address = data.get('ip_address', '').strip()
        name = data.get('name', '').strip()

        if not username:
            return jsonify({'success': False, 'error': 'Username (MAC) wajib diisi'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)",
                      (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'ipoe', 1) ON CONFLICT DO NOTHING",
                      (username,), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)",
                          (username, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)",
                          (username, framed_pool), fetch=False)
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)",
                          (username, ip_address), fetch=False)
        if name:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Description', ':=', %s)",
                          (username, name), fetch=False)

        return jsonify({'success': True, 'message': 'IPoE user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ipoe/users/update/<username>', methods=['PUT'])
@login_required
def api_ipoe_update_user(username):
    try:
        data = get_request_data()
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'",
                          (data['password'], username), fetch=False)
        for attr, key in [('Mikrotik-Rate-Limit', 'rate_limit'), ('Framed-Pool', 'framed_pool'), ('Framed-IP-Address', 'ip_address'), ('Description', 'name')]:
            if key in data:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = %s", (username, attr), fetch=False)
                if data[key]:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, ':=', %s)",
                                  (username, attr, data[key]), fetch=False)
        return jsonify({'success': True, 'message': 'IPoE user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ipoe/users/delete/<username>', methods=['DELETE'])
@login_required
def api_ipoe_delete_user(username):
    try:
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'ipoe'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'IPoE user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/ipoe/get-name')
@app.route('/api/ipoe/get-name/<mac>')
def api_ipoe_get_name(mac=None):
    """Endpoint publik untuk diakses MikroTik Lease Script.
    Menerima parameter 'mac' via path atau query dan membalas dengan format: name;rate_limit."""
    try:
        if mac is None:
            mac = request.args.get('mac', '').strip().lower()
        else:
            mac = mac.strip().lower()

        if not mac:
            return "Name-Not-Found; ", 400
        
        # Bersihkan MAC address dari separator (hanya sisakan alfanumerik)
        clean_mac = "".join(c for c in mac if c.isalnum())
        
        # Query ke database mencari nama (Description) dan rate limit (Mikrotik-Rate-Limit)
        rows = execute_query("""
            SELECT attribute, value FROM radreply 
            WHERE regexp_replace(LOWER(username), '[^a-z0-9]', '', 'g') = %s 
            AND attribute IN ('Description', 'Mikrotik-Rate-Limit')
        """, (clean_mac,))
        
        name = ""
        rate_limit = ""
        for r in rows:
            if r['attribute'] == 'Description':
                name = r['value'].strip()
            elif r['attribute'] == 'Mikrotik-Rate-Limit':
                rate_limit = r['value'].strip()
                
        # Format name
        if name:
            name = name.replace(" ", "-")
            safe_name = "".join(c for c in name if c.isalnum() or c == '-')
        else:
            safe_name = f"IPoE-{clean_mac}"
            
        return f"{safe_name};{rate_limit}"
    except Exception as e:
        return f"Error-{str(e)}; ", 500

# ============================================================
# VPN ROUTES
# ============================================================

@app.route('/vpn-users')
@login_required
def vpn_users_page():
    return render_template('vpn-users.html')

@app.route('/api/vpn/users')
@login_required
def api_vpn_users():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit

        base_where = "c.attribute = 'Cleartext-Password' AND EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'vpn')"
        params = []
        if search:
            base_where += " AND c.username ILIKE %s"
            params.append(f'%{search}%')

        data_query = f"""
            SELECT c.id, c.username, c.value as password,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-Pool' LIMIT 1) as framed_pool,
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(c.username) AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c WHERE {base_where}
            ORDER BY c.id DESC LIMIT %s OFFSET %s
        """
        count_query = f"SELECT COUNT(*) as total FROM radcheck c WHERE {base_where}"

        users = execute_query(data_query, tuple(params + [limit, offset]))
        total = execute_query(count_query, tuple(params))[0]['total']
        return jsonify({'users': users, 'total': total, 'page': page, 'limit': limit,
                        'total_pages': (total + limit - 1) // limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vpn/users/add', methods=['POST'])
@login_required
def api_vpn_add_user():
    try:
        data = get_request_data()
        username = data.get('username', '').strip().lower()
        password = data.get('password', '').strip()
        rate_limit = data.get('rate_limit', '').strip()
        framed_pool = data.get('framed_pool', '').strip()
        ip_address = data.get('ip_address', '').strip()

        if not username or not password:
            return jsonify({'success': False, 'error': 'Username dan Password wajib diisi'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)",
                      (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'vpn', 1) ON CONFLICT DO NOTHING",
                      (username,), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)",
                          (username, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)",
                          (username, framed_pool), fetch=False)
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)",
                          (username, ip_address), fetch=False)

        return jsonify({'success': True, 'message': 'VPN user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/vpn/users/update/<username>', methods=['PUT'])
@login_required
def api_vpn_update_user(username):
    try:
        data = get_request_data()
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'",
                          (data['password'], username), fetch=False)
        for attr, key in [('Mikrotik-Rate-Limit', 'rate_limit'), ('Framed-Pool', 'framed_pool'), ('Framed-IP-Address', 'ip_address')]:
            if key in data:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = %s", (username, attr), fetch=False)
                if data[key]:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, ':=', %s)",
                                  (username, attr, data[key]), fetch=False)
        return jsonify({'success': True, 'message': 'VPN user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/vpn/users/delete/<username>', methods=['DELETE'])
@login_required
def api_vpn_delete_user(username):
    try:
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'vpn'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'VPN user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# 802.1X ROUTES
# ============================================================

@app.route('/8021x-users')
@login_required
def x8021x_users_page():
    return render_template('8021x-users.html')

@app.route('/api/8021x/users')
@login_required
def api_x8021x_users():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit

        base_where = "c.attribute = 'Cleartext-Password' AND EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = '8021x')"
        params = []
        if search:
            base_where += " AND c.username ILIKE %s"
            params.append(f'%{search}%')

        data_query = f"""
            SELECT c.id, c.username, c.value as password,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-Pool' LIMIT 1) as framed_pool,
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(c.username) AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c WHERE {base_where}
            ORDER BY c.id DESC LIMIT %s OFFSET %s
        """
        count_query = f"SELECT COUNT(*) as total FROM radcheck c WHERE {base_where}"

        users = execute_query(data_query, tuple(params + [limit, offset]))
        total = execute_query(count_query, tuple(params))[0]['total']
        return jsonify({'users': users, 'total': total, 'page': page, 'limit': limit,
                        'total_pages': (total + limit - 1) // limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/8021x/users/add', methods=['POST'])
@login_required
def api_x8021x_add_user():
    try:
        data = get_request_data()
        username = data.get('username', '').strip().lower()
        password = data.get('password', '').strip()
        rate_limit = data.get('rate_limit', '').strip()
        framed_pool = data.get('framed_pool', '').strip()
        ip_address = data.get('ip_address', '').strip()

        if not username or not password:
            return jsonify({'success': False, 'error': 'Username dan Password wajib diisi'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)",
                      (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, '8021x', 1) ON CONFLICT DO NOTHING",
                      (username,), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)",
                          (username, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)",
                          (username, framed_pool), fetch=False)
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)",
                          (username, ip_address), fetch=False)

        return jsonify({'success': True, 'message': '802.1X user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/8021x/users/update/<username>', methods=['PUT'])
@login_required
def api_x8021x_update_user(username):
    try:
        data = get_request_data()
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'",
                          (data['password'], username), fetch=False)
        for attr, key in [('Mikrotik-Rate-Limit', 'rate_limit'), ('Framed-Pool', 'framed_pool'), ('Framed-IP-Address', 'ip_address')]:
            if key in data:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = %s", (username, attr), fetch=False)
                if data[key]:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, ':=', %s)",
                                  (username, attr, data[key]), fetch=False)
        return jsonify({'success': True, 'message': '802.1X user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/8021x/users/delete/<username>', methods=['DELETE'])
@login_required
def api_x8021x_delete_user(username):
    try:
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = '8021x'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'802.1X user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# === AKHIRI KODE TAMBAHAN ===

# === SOCKET IO EVENTS ===
@socketio.on('connect')
def handle_connect():
    print('Client connected')
    emit('status', {'data': 'Connected'})

def background_metrics():
    """Emit system metrics every 5 seconds via socketio background task"""
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
        'db_connections': 0,
        'db_max_connections': 100,
        'disk_percent': 0
    }

# Start background task using socketio (safe with eventlet)
socketio.start_background_task(background_metrics)

def background_stale_sessions():
    """Mark stale sessions setelah 24 jam tidak ada interim-update.
    Interval panjang agar tidak mengganggu penghitungan sqlcounter untuk voucher aktif."""
    socketio.sleep(60)
    while True:
        try:
            execute_query("""
                UPDATE radacct
                SET acctstoptime = acctupdatetime, acctterminatecause = 'Lost-Carrier'
                WHERE acctstoptime IS NULL
                AND acctupdatetime < NOW() - INTERVAL '24 hours'
            """, fetch=False)
        except Exception as e:
            print(f"[stale-session-cleaner] Error: {e}")
        socketio.sleep(300)

socketio.start_background_task(background_stale_sessions)


def background_expired_disconnect():
    """Setiap 60 detik: disconnect sesi aktif milik user yang Expiration-nya sudah lewat, lalu hapus akun."""
    socketio.sleep(30)
    while True:
        try:
            expired_sessions = execute_query("""
                SELECT a.acctsessionid, a.nasipaddress, a.username, a.framedipaddress
                FROM radacct a
                JOIN radcheck rc ON LOWER(rc.username) = LOWER(a.username) AND rc.attribute = 'Expiration'
                WHERE a.acctstoptime IS NULL
                  AND to_timestamp(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
            """)
            for s in (expired_sessions or []):
                nas_ip = str(s['nasipaddress'])
                nas_row = execute_query("SELECT secret FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
                secret = nas_row[0]['secret'] if nas_row else 'testing123'
                ok = send_coa_disconnect(nas_ip, s['acctsessionid'], secret, framed_ip=s.get('framedipaddress'))
                execute_query("UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Session-Timeout' WHERE acctsessionid = %s AND acctstoptime IS NULL",
                              (s['acctsessionid'],), fetch=False)
                print(f"[expired-disconnect] {s['username']} @ {nas_ip} | CoA={'OK' if ok else 'FAIL'}")

            execute_query("""
                DELETE FROM radcheck WHERE username IN (
                    SELECT rc.username FROM radcheck rc
                    WHERE rc.attribute = 'Expiration'
                      AND to_timestamp(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
                      AND NOT EXISTS (SELECT 1 FROM radacct a WHERE a.username = rc.username AND a.acctstoptime IS NULL)
                )
            """, fetch=False)
            execute_query("""
                DELETE FROM radreply WHERE username NOT IN (
                    SELECT username FROM radcheck WHERE attribute = 'Cleartext-Password'
                )
            """, fetch=False)
            execute_query("""
                DELETE FROM radusergroup WHERE username NOT IN (
                    SELECT username FROM radcheck WHERE attribute = 'Cleartext-Password'
                )
            """, fetch=False)
        except Exception as e:
            print(f"[expired-disconnect] Error: {e}")
        socketio.sleep(60)

socketio.start_background_task(background_expired_disconnect)


_nas_last_status = {}

def background_nas_monitor():
    """Memantau status ping ke setiap IP NAS setiap 1 menit dan mengirim notifikasi Telegram jika berubah."""
    socketio.sleep(15) # Tunggu 15 detik saat startup agar koneksi stabil
    while True:
        try:
            nas_list = execute_query("SELECT nasname, shortname FROM nas")
            for nas in (nas_list or []):
                nas_ip = nas['nasname']
                nas_name = nas['shortname'] or nas_ip
                
                # Gunakan fungsi ping_nas yang lebih tangguh (ping + port check)
                is_online = (ping_nas(nas_ip) == "online")
                
                prev_status = _nas_last_status.get(nas_ip)
                if prev_status is not None:
                    # Jika status berubah dari ONLINE ke OFFLINE
                    if prev_status and not is_online:
                        time_str = datetime.now().strftime('%H:%M')
                        msg = f"⚠️ <b>[ALERT]</b> NAS \"<b>{nas_name}</b>\" terdeteksi <b>OFFLINE</b> pada <b>{time_str} WIB</b>. Mohon segera periksa!"
                        send_telegram_notification(msg)
                        _nas_last_status[nas_ip] = False
                    # Jika status berubah dari OFFLINE ke ONLINE
                    elif not prev_status and is_online:
                        msg = f"✅ <b>[RESOLVED]</b> NAS \"<b>{nas_name}</b>\" kembali <b>ONLINE</b>."
                        send_telegram_notification(msg)
                        _nas_last_status[nas_ip] = True
                else:
                    # Inisialisasi awal
                    _nas_last_status[nas_ip] = is_online
        except Exception as e:
            print(f"[nas-monitor] Error: {e}")
            
        socketio.sleep(60)

socketio.start_background_task(background_nas_monitor)




# === HOTSPOT API ROUTES ===
import secrets as _secrets
import string as _string

def _gen_voucher_code(length=10):
    chars = _string.ascii_uppercase + _string.digits
    return ''.join(_secrets.choice(chars) for _ in range(length))

HOTSPOT_PACKAGES = {
    '3h':  {'name': 'Quick 3 Jam',  'seconds': 10800},
    '1d':  {'name': 'Daily 24 Jam', 'seconds': 86400},
    '7d':  {'name': 'Weekly 7 Hari', 'seconds': 604800},
    '30d': {'name': 'Monthly Unlimited', 'seconds': 2592000},
}

@app.route('/hotspot-users')
@login_required
def hotspot_users_page():
    return render_template('hotspot-users.html')

@app.route('/api/hotspot/vouchers')
@login_required
def api_hotspot_vouchers():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit
        status = request.args.get('status', '')  # active, expired, unused

        where = "deleted_at IS NULL"
        params = []

        if status == 'unused':
            where += " AND first_login IS NULL"
        elif status == 'active':
            where += " AND first_login IS NOT NULL AND expires_at > NOW()"
        elif status == 'expired':
            where += " AND expires_at IS NOT NULL AND expires_at <= NOW()"

        if search:
            where += " AND (voucher_code ILIKE %s OR username ILIKE %s)"
            params += [f'%{search}%', f'%{search}%']

        rows = execute_query(f"""
            SELECT id, voucher_code, username, password, package_name, rate_limit,
                   first_login, expires_at, locked_mac, created_at,
                   CASE
                       WHEN first_login IS NULL THEN 'unused'
                       WHEN expires_at > NOW() THEN 'active'
                       ELSE 'expired'
                   END AS status,
                   CASE
                       WHEN expires_at IS NOT NULL AND expires_at > NOW()
                       THEN EXTRACT(EPOCH FROM (expires_at - NOW()))::INTEGER
                       ELSE 0
                   END AS remaining_seconds
            FROM hotspot_vouchers
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
        """, tuple(params + [limit, offset]))

        total = execute_query(f"SELECT COUNT(*) as total FROM hotspot_vouchers WHERE {where}", tuple(params))[0]['total']

        return jsonify({'vouchers': rows, 'total': total, 'page': page,
                        'limit': limit, 'total_pages': (total + limit - 1) // limit})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/hotspot/vouchers/generate', methods=['POST'])
@login_required
def api_hotspot_generate():
    try:
        data = get_request_data()
        pkg_key = data.get('package', '1d')
        rate_limit = data.get('rate_limit', '').strip()
        count = min(int(data.get('count', 1)), 100)

        pkg = HOTSPOT_PACKAGES.get(pkg_key)
        if not pkg:
            return jsonify({'success': False, 'error': 'Paket tidak valid'}), 400

        generated = []
        for _ in range(count):
            code = _gen_voucher_code()
            username = f"hs-{code.lower()}"
            password = _gen_voucher_code(8)
            execute_query("""
                INSERT INTO hotspot_vouchers (voucher_code, username, password, duration_seconds, package_name, rate_limit)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (code, username, password, pkg['seconds'], pkg['name'], rate_limit), fetch=False)
            generated.append({'voucher_code': code, 'username': username, 'password': password,
                               'package': pkg['name']})

        return jsonify({'success': True, 'vouchers': generated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/hotspot/vouchers/delete/<int:vid>', methods=['DELETE'])
@login_required
def api_hotspot_delete(vid):
    try:
        execute_query("UPDATE hotspot_vouchers SET deleted_at = NOW() WHERE id = %s", (vid,), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/hotspot/vouchers/delete-expired', methods=['DELETE'])
@login_required
def api_hotspot_delete_expired():
    try:
        result = execute_query("""
            UPDATE hotspot_vouchers SET deleted_at = NOW()
            WHERE expires_at IS NOT NULL AND expires_at <= NOW() AND deleted_at IS NULL
            RETURNING id
        """)
        return jsonify({'success': True, 'deleted': len(result) if result else 0})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/hotspot/packages')
@login_required
def api_hotspot_packages():
    return jsonify(HOTSPOT_PACKAGES)



if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
