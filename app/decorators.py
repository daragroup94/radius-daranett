import functools
from flask import session, request, redirect, url_for, jsonify

def login_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'success': False, 'error': 'Session expired'}), 401
            return redirect(url_for('auth.login')) # Note: auth.login because login route will be under 'auth' blueprint
        return f(*args, **kwargs)
    return decorated_function
