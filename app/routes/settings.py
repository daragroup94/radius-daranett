import os
import time
import psutil
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, jsonify, session, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from decorators import login_required
from database import execute_query
from helpers import get_request_data
from services.telegram import send_telegram_notification

settings_bp = Blueprint('settings', __name__)

DEFAULT_SETTINGS = {
    'isp_name': 'Daranett ISP',
    'app_title': 'Daranett RADIUS Manager',
    'support_hotline': '+62 812-3456-7890',
    'support_email': 'noc@daranett.net',
    'company_address': 'Indonesia',
    'timezone': 'Asia/Jakarta',
    'currency_symbol': 'Rp',
    'radius_interim_interval': '300',
    'radius_default_timeout': '86400',
    'stale_session_threshold': '10',
    'brute_force_max_attempts': '5',
    'brute_force_lock_minutes': '10',
    'session_idle_timeout': '60',
    'enable_mfa_enforcement': 'false',
    'default_ui_theme': 'cyber-slate',
    'default_ui_font': 'inter-outfit',
    'default_font_size': 'normal'
}

def get_all_settings():
    """Retrieve all settings from DB merged with default values."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        rows = execute_query("SELECT key, value FROM public.system_settings")
        for row in rows:
            settings[row['key']] = row['value']
    except Exception as e:
        print(f"[settings] Warning loading system_settings: {e}")
    return settings


@settings_bp.route('/settings')
@login_required
def settings_page():
    settings = get_all_settings()
    return render_template('settings/index.html', settings=settings)


@settings_bp.route('/settings/admins')
@login_required
def admins_page():
    return render_template('settings/admins.html')


@settings_bp.route('/api/settings', methods=['GET'])
@login_required
def api_get_settings():
    try:
        settings = get_all_settings()
        return jsonify({'success': True, 'settings': settings})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings', methods=['POST'])
@login_required
def api_save_settings():
    try:
        data = get_request_data()
        if not data:
            return jsonify({'success': False, 'error': 'No data provided'}), 400

        allowed_keys = set(DEFAULT_SETTINGS.keys())
        updated_keys = []

        for key, val in data.items():
            if key in allowed_keys:
                str_val = str(val).strip()
                execute_query("""
                    INSERT INTO public.system_settings (key, value, updated_at)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key) DO UPDATE 
                    SET value = EXCLUDED.value, updated_at = NOW()
                """, (key, str_val), fetch=False)
                updated_keys.append(key)

        return jsonify({
            'success': True,
            'message': f'Berhasil menyimpan {len(updated_keys)} pengaturan sistem.',
            'updated_keys': updated_keys
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/system-health', methods=['GET'])
@login_required
def api_system_health():
    """Get system resource stats and database telemetry."""
    try:
        cpu_pct = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
        uptime_seconds = int((datetime.now(timezone.utc) - boot_time).total_seconds())

        stale_count = 0
        active_count = 0
        try:
            active_res = execute_query("SELECT COUNT(*) as count FROM radacct WHERE acctstoptime IS NULL")
            if active_res:
                active_count = active_res[0]['count']

            stale_res = execute_query("""
                SELECT COUNT(*) as count FROM radacct 
                WHERE acctstoptime IS NULL 
                  AND (acctupdatetime < NOW() - INTERVAL '10 minutes' 
                       OR (acctupdatetime IS NULL AND acctstarttime < NOW() - INTERVAL '10 minutes'))
            """)
            if stale_res:
                stale_count = stale_res[0]['count']
        except Exception as e:
            print(f"[settings] Query error in health check: {e}")

        redis_status = 'Disconnected'
        try:
            redis_client = current_app.config.get('SESSION_REDIS')
            if redis_client and redis_client.ping():
                redis_status = 'Connected'
        except Exception:
            redis_status = 'Unavailable'

        return jsonify({
            'success': True,
            'cpu_percent': round(cpu_pct, 1),
            'cpu_cores': psutil.cpu_count(logical=True),
            'memory_percent': round(mem.percent, 1),
            'memory_used_mb': round(mem.used / (1024 * 1024), 1),
            'memory_total_mb': round(mem.total / (1024 * 1024), 1),
            'disk_percent': round(disk.percent, 1),
            'disk_used_gb': round(disk.used / (1024 * 1024 * 1024), 1),
            'disk_total_gb': round(disk.total / (1024 * 1024 * 1024), 1),
            'uptime_seconds': uptime_seconds,
            'active_sessions': active_count,
            'stale_sessions': stale_count,
            'redis_status': redis_status,
            'database_status': 'Connected'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/maintenance/cleanup-stale', methods=['POST'])
@login_required
def api_cleanup_stale_sessions():
    """Immediately terminates sessions that haven't sent accounting updates in >10 minutes."""
    try:
        stale_rows = execute_query("""
            SELECT radacctid, username FROM radacct
            WHERE acctstoptime IS NULL
              AND (acctupdatetime < NOW() - INTERVAL '10 minutes' 
                   OR (acctupdatetime IS NULL AND acctstarttime < NOW() - INTERVAL '10 minutes'))
        """)
        stale_count = len(stale_rows)

        if stale_count > 0:
            execute_query("""
                UPDATE radacct
                SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()),
                    acctterminatecause = 'Lost-Carrier'
                WHERE acctstoptime IS NULL
                  AND (acctupdatetime < NOW() - INTERVAL '10 minutes' 
                       OR (acctupdatetime IS NULL AND acctstarttime < NOW() - INTERVAL '10 minutes'))
            """, fetch=False)

            msg = f"🧹 <b>RADIUS Maintenance: Sesi Gantung Dibersihkan</b>\nJumlah Sesi: {stale_count}\nWaktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            try:
                send_telegram_notification(msg)
            except Exception:
                pass

        return jsonify({
            'success': True,
            'cleaned_count': stale_count,
            'message': f'Berhasil membersihkan {stale_count} sesi gantung (marked Lost-Carrier).'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/maintenance/duplicates', methods=['GET'])
@login_required
def api_get_duplicate_sessions():
    """Lists duplicate active sessions by username."""
    try:
        dupes = execute_query("""
            SELECT LOWER(username) as username, COUNT(*) as session_count,
                   STRING_AGG(COALESCE(callingstationid::text, '-'), ', ') as mac_addresses,
                   STRING_AGG(COALESCE(nasipaddress::text, '-'), ', ') as nas_ips,
                   STRING_AGG(radacctid::text, ', ') as acct_ids
            FROM radacct
            WHERE acctstoptime IS NULL
            GROUP BY LOWER(username)
            HAVING COUNT(*) > 1
            ORDER BY session_count DESC
            LIMIT 50
        """)
        return jsonify({'success': True, 'duplicates': dupes, 'total': len(dupes)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/maintenance/cleanup-duplicates', methods=['POST'])
@login_required
def api_cleanup_duplicate_sessions():
    """Terminates older duplicate sessions, keeping only the latest session per username."""
    try:
        dupes = execute_query("""
            SELECT LOWER(username) as username
            FROM radacct
            WHERE acctstoptime IS NULL
            GROUP BY LOWER(username)
            HAVING COUNT(*) > 1
        """)

        closed_total = 0
        for dupe in dupes:
            uname = dupe['username']
            sessions = execute_query("""
                SELECT radacctid FROM radacct
                WHERE LOWER(username) = %s AND acctstoptime IS NULL
                ORDER BY COALESCE(acctupdatetime, acctstarttime) DESC
            """, (uname,))
            if len(sessions) > 1:
                to_close = [s['radacctid'] for s in sessions[1:]]
                execute_query("""
                    UPDATE radacct
                    SET acctstoptime = NOW(),
                        acctterminatecause = 'Multiple-Logins-Terminated'
                    WHERE radacctid = ANY(%s)
                """, (to_close,), fetch=False)
                closed_total += len(to_close)

        return jsonify({
            'success': True,
            'closed_count': closed_total,
            'message': f'Berhasil menutup {closed_total} sesi duplikat lama.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/maintenance/clear-cache', methods=['POST'])
@login_required
def api_clear_system_cache():
    """Flushes Redis application cache."""
    try:
        redis_client = current_app.config.get('SESSION_REDIS')
        if redis_client:
            keys = redis_client.keys('failed_login:*')
            if keys:
                redis_client.delete(*keys)
        return jsonify({'success': True, 'message': 'Redis cache berhasil dibersihkan.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@settings_bp.route('/api/settings/maintenance/prune-logs', methods=['POST'])
@login_required
def api_prune_logs():
    """Prunes authentication logs and old signal telemetry to keep the database lean."""
    try:
        data = request.get_json() or {}
        auth_days = max(7, int(data.get('auth_days', 60)))
        signal_days = max(7, int(data.get('signal_days', 30)))
        acct_days = max(30, int(data.get('acct_days', 180)))

        # 1. Prune radpostauth
        execute_query("DELETE FROM public.radpostauth WHERE authdate < NOW() - make_interval(days => %s)", (auth_days,), fetch=False)
        # 2. Prune ont_signal_history
        execute_query("DELETE FROM public.ont_signal_history WHERE recorded_at < NOW() - make_interval(days => %s)", (signal_days,), fetch=False)
        # 3. Prune closed radacct (only sessions that have already stopped)
        execute_query("DELETE FROM public.radacct WHERE acctstoptime IS NOT NULL AND acctstoptime < NOW() - make_interval(days => %s)", (acct_days,), fetch=False)

        return jsonify({
            'success': True,
            'message': f'Log maintenance selesai: radpostauth (>{auth_days} hari), signal history (>{signal_days} hari), radacct tertutup (>{acct_days} hari) berhasil dibersihkan.'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==============================================================================
# ADMIN RBAC MANAGEMENT ENDPOINTS
# ==============================================================================

@settings_bp.route('/api/settings/admins', methods=['GET'])
@login_required
def api_list_admins():
    """List all admin accounts (omits sensitive hashes)."""
    try:
        users = execute_query("""
            SELECT id, username, full_name, role, is_active, is_mfa_enabled, 
                   last_login, created_at
            FROM public.admin_users
            ORDER BY id ASC
        """)
        current_user_id = session.get('user_id')
        current_role = session.get('role', 'operator')
        for u in users:
            u['is_current_user'] = (u['id'] == current_user_id)
            if u['last_login']:
                u['last_login_formatted'] = u['last_login'].strftime('%Y-%m-%d %H:%M:%S')
            else:
                u['last_login_formatted'] = 'Belum pernah login'
            if u['created_at']:
                u['created_at_formatted'] = u['created_at'].strftime('%Y-%m-%d %H:%M')
            else:
                u['created_at_formatted'] = '-'

        return jsonify({
            'success': True, 
            'admins': users, 
            'current_user_id': current_user_id,
            'current_role': current_role
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/admins', methods=['POST'])
@login_required
def api_add_admin():
    """Create a new administrator / NOC operator."""
    try:
        data = get_request_data()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        full_name = data.get('full_name', '').strip()
        role = data.get('role', 'operator').strip().lower()

        if not username or not password:
            return jsonify({'success': False, 'error': 'Username dan password wajib diisi'}), 400

        if len(password) < 6:
            return jsonify({'success': False, 'error': 'Password minimal 6 karakter'}), 400

        valid_roles = ['superadmin', 'admin', 'operator', 'technician', 'viewer']
        if role not in valid_roles:
            role = 'operator'

        existing = execute_query("SELECT id FROM public.admin_users WHERE LOWER(username) = LOWER(%s)", (username,))
        if existing:
            return jsonify({'success': False, 'error': f'Username "{username}" sudah digunakan'}), 400

        password_hash = generate_password_hash(password)
        execute_query("""
            INSERT INTO public.admin_users (username, password_hash, full_name, role, is_active, is_mfa_enabled, created_at)
            VALUES (%s, %s, %s, %s, TRUE, FALSE, NOW())
        """, (username, password_hash, full_name, role), fetch=False)

        try:
            send_telegram_notification(
                f"👤 <b>Admin Baru Dibuat</b>\nUsername: <code>{username}</code>\nRole: {role}\nDibuat oleh: {session.get('username')}"
            )
        except Exception:
            pass

        return jsonify({'success': True, 'message': f'Admin {username} berhasil ditambahkan.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/admins/<int:admin_id>', methods=['PUT'])
@login_required
def api_update_admin(admin_id):
    """Update admin details, role, status, or reset password."""
    try:
        data = get_request_data()
        user = execute_query("SELECT id, username, role FROM public.admin_users WHERE id = %s", (admin_id,))
        if not user:
            return jsonify({'success': False, 'error': 'Pengguna tidak ditemukan'}), 404

        full_name = data.get('full_name')
        role = data.get('role')
        is_active = data.get('is_active')
        new_password = data.get('password')
        reset_mfa = data.get('reset_mfa')

        if is_active is False and admin_id == session.get('user_id'):
            return jsonify({'success': False, 'error': 'Anda tidak dapat menonaktifkan akun Anda sendiri'}), 400

        updates = []
        params = []

        if full_name is not None:
            updates.append("full_name = %s")
            params.append(full_name.strip())

        if role is not None:
            valid_roles = ['superadmin', 'admin', 'operator', 'technician', 'viewer']
            if role in valid_roles:
                updates.append("role = %s")
                params.append(role)

        if is_active is not None:
            updates.append("is_active = %s")
            params.append(bool(is_active))

        if reset_mfa:
            updates.append("is_mfa_enabled = FALSE")
            updates.append("mfa_secret = NULL")

        if new_password and len(new_password.strip()) >= 6:
            updates.append("password_hash = %s")
            params.append(generate_password_hash(new_password.strip()))

        if not updates:
            return jsonify({'success': False, 'error': 'Tidak ada perubahan yang dikirim'}), 400

        params.append(admin_id)
        sql = f"UPDATE public.admin_users SET {', '.join(updates)} WHERE id = %s"
        execute_query(sql, tuple(params), fetch=False)

        return jsonify({'success': True, 'message': 'Data administrator berhasil diperbarui.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@settings_bp.route('/api/settings/admins/<int:admin_id>', methods=['DELETE'])
@login_required
def api_delete_admin(admin_id):
    """Delete an administrator."""
    try:
        if admin_id == session.get('user_id'):
            return jsonify({'success': False, 'error': 'Anda tidak dapat menghapus akun Anda sendiri'}), 400

        user = execute_query("SELECT id, username, role FROM public.admin_users WHERE id = %s", (admin_id,))
        if not user:
            return jsonify({'success': False, 'error': 'Pengguna tidak ditemukan'}), 404

        total_admins = execute_query("SELECT COUNT(*) as count FROM public.admin_users WHERE is_active = TRUE")[0]['count']
        if total_admins <= 1:
            return jsonify({'success': False, 'error': 'Tidak dapat menghapus admin terakhir yang aktif'}), 400

        execute_query("DELETE FROM public.admin_users WHERE id = %s", (admin_id,), fetch=False)

        return jsonify({'success': True, 'message': f"Akun {user[0]['username']} berhasil dihapus."})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
