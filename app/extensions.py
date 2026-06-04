from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_session import Session

socketio = SocketIO()
limiter = Limiter(get_remote_address, default_limits=[])
csrf = CSRFProtect()
sess = Session()
