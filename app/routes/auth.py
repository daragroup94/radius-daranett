import os
import pyotp
import requests
from datetime import datetime
from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, session, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import limiter
from database import execute_query
from decorators import login_required
from helpers import get_request_data
from services.telegram import send_telegram_notification

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password'] or session.pop('_mfa_pending_password', '')
        mfa_code = request.form.get('mfa_code')
        
        user = execute_query("SELECT * FROM admin_users WHERE username = %s AND is_active = TRUE", (username,))
        
        if user and check_password_hash(user[0]['password_hash'], password):
            # Check MFA
            if user[0]['is_mfa_enabled']:
                if not mfa_code:
                    session['_mfa_pending_password'] = password
                    return render_template('login.html', mfa_required=True, username=username)
                
                totp = pyotp.TOTP(user[0]['mfa_secret'])
                if not totp.verify(mfa_code):
                    flash('Invalid MFA code', 'error')
                    session['_mfa_pending_password'] = password
                    return render_template('login.html', mfa_required=True, username=username)
            
            session['user_id'] = user[0]['id']
            session['username'] = user[0]['username']
            session['role'] = user[0]['role']
            
            execute_query("UPDATE admin_users SET last_login = NOW() WHERE id = %s", (user[0]['id'],), fetch=False)
            
            send_telegram_notification(f"🔔 <b>Admin Login</b>\nUser: {username}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard.dashboard_page')) # redirect to dashboard
        else:
            flash('Invalid username or password', 'error')
            # Brute force detection: track failed attempts per IP via Redis
            try:
                redis_client = current_app.config.get('SESSION_REDIS')
                if redis_client:
                    ip = request.remote_addr
                    key = f'failed_login:{ip}'
                    attempts = redis_client.incr(key)
                    redis_client.expire(key, 600)  # reset setelah 10 menit
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

@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'info')
    return redirect(url_for('auth.login'))

@auth_bp.route('/profile')
@login_required
def profile_page():
    return render_template('profile.html')

@auth_bp.route('/api/mfa/setup', methods=['POST'])
@login_required
def api_mfa_setup():
    try:
        secret = pyotp.random_base32()
        user = execute_query("SELECT username FROM admin_users WHERE id = %s", (session['user_id'],))[0]
        provisioning_url = pyotp.totp.TOTP(secret).provisioning_uri(
            name=user['username'], 
            issuer_name="RADIUS Manager"
        )
        session['temp_mfa_secret'] = secret
        
        return jsonify({
            'success': True, 
            'secret': secret,
            'provisioning_url': provisioning_url
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@auth_bp.route('/api/mfa/verify', methods=['POST'])
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

@auth_bp.route('/api/change-password', methods=['POST'])
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
