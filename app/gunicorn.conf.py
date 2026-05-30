# Gunicorn configuration file
import os

# Server Configuration
bind = "0.0.0.0:5000"
backlog = 2048

# Worker Configuration
workers = 1 # SocketIO needs 1 worker for eventlet/gevent
worker_class = "eventlet"
worker_connections = 1000
timeout = 120
graceful_timeout = 30
keepalive = 2

# Process Naming
proc_name = 'radius-dashboard'

# Server Mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv('GUNICORN_LOGLEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Request Handling
max_requests = 1000
max_requests_jitter = 50
preload_app = False  # harus False agar socketio background tasks jalan di worker, bukan master

# SSL/TLS Configuration (if needed)
# keyfile = "/path/to/keyfile"
# certfile = "/path/to/certfile"
# ssl_version = "TLSv1_2"

# Server hooks
def post_fork(server, worker):
    """Called after a worker has been spawned."""
    pass

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    pass

def pre_exec(server):
    """Called before the master process is replaced by a new master."""
    pass

def when_ready(server):
    """Called just after the server is started."""
    print("Gunicorn server is ready. Spawning workers")

def on_exit(server):
    """Called just before a worker exits."""
    pass
