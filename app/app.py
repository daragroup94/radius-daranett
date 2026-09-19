import os

# Non-blocking async eventlet & psycopg2 patching
try:
    import eventlet
    eventlet.monkey_patch()
    from psycogreen.eventlet import patch_psycopg
    patch_psycopg()
except ImportError:
    pass

from flask import Flask, jsonify, request
from flask_wtf.csrf import CSRFError
from config import Config
from extensions import socketio, limiter, csrf, sess

# Create Flask Application
app = Flask(__name__)
app.config.from_object(Config)

# Enable ProxyFix to resolve real client IP behind reverse proxy (Traefik)
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Load persisted Telegram settings from local .env if available
_telegram_env = os.path.join(os.path.dirname(__file__), '.telegram.env')
if os.path.exists(_telegram_env):
    with open(_telegram_env) as _f:
        for _line in _f:
            if '=' in _line:
                _k, _v = _line.strip().split('=', 1)
                app.config[_k] = _v

# Initialize Extensions
sess.init_app(app)  # Server-side sessions (Redis cache backed)
_allowed_origins = os.getenv('ALLOWED_ORIGINS', '*')
socketio.init_app(
    app, 
    cors_allowed_origins=_allowed_origins if _allowed_origins == '*' else _allowed_origins.split(','),
    async_mode='eventlet'
)
app.config['RATELIMIT_STORAGE_URI'] = app.config.get('REDIS_URL', 'redis://localhost:6379/0')
limiter.init_app(app)
app.config['WTF_CSRF_CHECK_DEFAULT'] = False
csrf.init_app(app)

# Initialize Database Schema Tables
from database import init_db_schema
init_db_schema()

# Register Blueprints
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.nas import nas_bp
from routes.sessions import sessions_bp
from routes.logs import logs_bp
from routes.diagnostics import diagnostics_bp
from routes.backup import backup_bp
from routes.integrations import integrations_bp
from routes.users import users_bp
from routes.network_map import network_map_bp
from routes.settings import settings_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(nas_bp)
app.register_blueprint(sessions_bp)
app.register_blueprint(logs_bp)
app.register_blueprint(diagnostics_bp)
app.register_blueprint(backup_bp)
app.register_blueprint(integrations_bp)
app.register_blueprint(users_bp)
app.register_blueprint(network_map_bp)
app.register_blueprint(settings_bp)



# Socket.IO connection events for logging
@socketio.on('connect')
def handle_connect():
    print('[socketio] Client connected successfully')

@socketio.on('disconnect')
def handle_disconnect():
    print('[socketio] Client disconnected')

# Global Filters & CSRF Handlers
@app.after_request
def add_no_cache(response):
    if response.content_type and ('text/html' in response.content_type or 'application/json' in response.content_type):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

@app.before_request
def csrf_protect():
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        # Exempt public stateless APIs
        if request.path.startswith('/api/ipoe/get-name') or request.path.startswith('/api/ipoe/sync-static'):
            return
        csrf.protect()

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify({'success': False, 'error': 'CSRF token missing or invalid'}), 400

@app.route('/health')
def health():
    return 'OK'

@app.route('/sw.js')
def service_worker():
    response = app.send_static_file('sw.js')
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['Service-Worker-Allowed'] = '/'
    return response

# Start SocketIO Background Workers
from services.session_tasks import (
    background_metrics,
    background_stale_sessions,
    background_expired_disconnect,
    background_auth_log_tail,
    background_map_status_sync
)
from services.nas_monitor import background_nas_monitor
from services.telegram import background_telegram_bot
from services.optical_health import background_optical_health_scan

# Safe startup delay inside workers is handled internally inside service monitors.
# Run them as background tasks in eventlet context:
socketio.start_background_task(background_metrics)
socketio.start_background_task(background_stale_sessions)
socketio.start_background_task(background_expired_disconnect)
socketio.start_background_task(background_auth_log_tail)
socketio.start_background_task(background_nas_monitor)
socketio.start_background_task(background_telegram_bot)
socketio.start_background_task(background_optical_health_scan)
socketio.start_background_task(background_map_status_sync)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=Config.DEBUG)
