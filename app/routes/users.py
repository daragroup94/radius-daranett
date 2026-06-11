import os
import re
import librouteros
import subprocess
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, current_app
from decorators import login_required
from database import execute_query
from helpers import get_request_data, convert_data_limit_to_bytes
from services.telegram import send_telegram_notification
from services.mikrotik_coa import send_coa_update, send_coa_update_async
from extensions import limiter
from services.genieacs import get_genieacs_device_info

users_bp = Blueprint('users', __name__)

def _sync_ikev2_pool(username, ip_address):
    """Sync static IP ke SQLite pool di ikev2-vpn container."""
    def _sqlite(sql):
        subprocess.run(
            ['docker', 'exec', 'ikev2-vpn', 'sqlite3', '/etc/ipsec.d/pool.db', sql],
            timeout=5, capture_output=True
        )
    # Escape single-quotes to prevent SQL injection in sqlite3 shell args
    u = username.replace("'", "''")
    try:
        if ip_address:
            ip = ip_address.replace("'", "''")
            # 1. Insert identity first (address FK depends on it)
            _sqlite(f"INSERT OR IGNORE INTO identities (type, data) VALUES (2, '{u}');")
            # 2. Insert/replace address linked to the identity
            _sqlite(
                f"INSERT OR REPLACE INTO addresses (pool, address, identity, acquired, released) "
                f"SELECT p.id, '{ip}', (SELECT id FROM identities WHERE data='{u}' LIMIT 1), 0, 1 "
                f"FROM pools p WHERE p.name='ikev2-pool';"
            )
        else:
            _sqlite(f"DELETE FROM addresses WHERE identity=(SELECT id FROM identities WHERE data='{u}' LIMIT 1);")
    except Exception:
        pass  # Non-critical

# Tab-based main page
@users_bp.route('/users')
@login_required
def users_page():
    tab = request.args.get('tab', 'pppoe')
    return render_template('users.html', active_tab=tab)

# GenieACS device info
@users_bp.route('/api/users/<username>/device')
@login_required
def api_user_device(username):
    res = get_genieacs_device_info(username)
    if not res.get('found', False):
        return jsonify(res)
    # Re-structure to match original response keys expected by users.html expand row
    return jsonify({
        'found': True,
        'serial': res.get('serial', 'N/A'),
        'manufacturer': res.get('manufacturer', 'N/A'),
        'model': res.get('model', 'N/A'),
        'firmware': res.get('firmware', 'N/A'),
        'rx_power': res.get('rx_power', 'N/A'),
        'rx_status': res.get('rx_status', 'N/A'),
        'pppoe_ip': res.get('pppoe_ip', 'N/A'),
        'device_uptime': res.get('device_uptime', 'N/A'),
        'ppp_uptime': res.get('ppp_uptime', 'N/A'),
        'temperature': res.get('temperature', 'N/A'),
        'pon_mode': res.get('pon_mode', 'N/A'),
        'last_inform': res.get('last_inform', 'N/A'),
    })

# Backward compatibility redirects
@users_bp.route('/vpn-users')
@login_required
def redirect_vpn():
    return redirect(url_for('users.users_page', tab='vpn'))

@users_bp.route('/hotspot-users')
@login_required
def redirect_hotspot():
    return redirect(url_for('users.users_page', tab='hotspot'))

@users_bp.route('/ipoe-users')
@login_required
def redirect_ipoe():
    return redirect(url_for('users.users_page', tab='ipoe'))

@users_bp.route('/8021x-users')
@login_required
def redirect_8021x():
    return redirect(url_for('users.users_page', tab='8021x'))

@users_bp.route('/wireless-users')
@login_required
def redirect_wireless():
    return redirect(url_for('users.users_page', tab='wireless'))


# ============================================================
# PPPOE USER APIS
# ============================================================
@users_bp.route('/api/users')
@login_required
def api_users():
    try:

        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit
        
        data_query = """
            SELECT c.id, c.username, c.attribute, c.op, c.value as password,
                   (SELECT value FROM radreply r1 WHERE r1.username = c.username AND r1.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r2 WHERE r2.username = c.username AND r2.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r3 WHERE r3.username = c.username AND r3.attribute = 'Session-Timeout' LIMIT 1) as session_timeout,
                   (SELECT value FROM radreply r4 WHERE r4.username = c.username AND r4.attribute = 'Idle-Timeout' LIMIT 1) as idle_timeout,
                   (SELECT value FROM radreply r5 WHERE r5.username = c.username AND r5.attribute = 'Mikrotik-Total-Limit' LIMIT 1) as data_limit,
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(c.username) AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c
            WHERE c.attribute = 'Cleartext-Password'
            AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname IN ('ipoe', 'vpn', '8021x', 'hotspot', 'wireless'))
        """
        
        count_query = "SELECT COUNT(*) as total FROM radcheck c WHERE c.attribute = 'Cleartext-Password' AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname IN ('ipoe', 'vpn', '8021x', 'hotspot', 'wireless'))"

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

@users_bp.route('/api/users/get/<username>')
@login_required
def api_get_user(username):
    try:
        users = execute_query("""
            SELECT c.id, c.username, c.attribute, c.op, c.value as password,
                   (SELECT value FROM radreply r1 WHERE r1.username = c.username AND r1.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                   (SELECT value FROM radreply r2 WHERE r2.username = c.username AND r2.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                   (SELECT value FROM radreply r3 WHERE r3.username = c.username AND r3.attribute = 'Session-Timeout' LIMIT 1) as session_timeout,
                   (SELECT value FROM radreply r4 WHERE r4.username = c.username AND r4.attribute = 'Idle-Timeout' LIMIT 1) as idle_timeout,
                   (SELECT value FROM radreply r5 WHERE r5.username = c.username AND r5.attribute = 'Mikrotik-Total-Limit' LIMIT 1) as data_limit
            FROM radcheck c
            WHERE LOWER(c.username) = LOWER(%s) AND c.attribute = 'Cleartext-Password'
            LIMIT 1
        """, (username,))
        if not users:
            return jsonify({'error': 'User not found'}), 404
        return jsonify(users[0])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@users_bp.route('/api/users/add', methods=['POST'])
@login_required
def api_add_user():
    try:
        data = get_request_data()
        
        execute_query("""
            INSERT INTO radcheck (username, attribute, op, value)
            VALUES (%s, 'Cleartext-Password', ':=', %s)
        """, (data['username'], data['password']), fetch=False)
        
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

@users_bp.route('/api/users/update/<username>', methods=['PUT'])
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

        if data.get('data_limit') is not None:
            execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Total-Limit'", (username,), fetch=False)
            if data['data_limit']:
                data_limit_bytes = convert_data_limit_to_bytes(data['data_limit'])
                if data_limit_bytes:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Total-Limit', ':=', %s)",
                                 (username, str(data_limit_bytes)), fetch=False)

        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'",
                         (data['password'], username), fetch=False)

        coa_total = 0
        if data.get('rate_limit'):
            active_sessions = execute_query("""
                SELECT acctsessionid, nasipaddress FROM radacct
                WHERE LOWER(username) = LOWER(%s) AND acctstoptime IS NULL
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

@users_bp.route('/api/users/throttle/<username>', methods=['POST'])
@login_required
def api_throttle_user(username):
    try:
        data = get_request_data()
        rate_limit = data.get('rate_limit', '512k/512k')

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

@users_bp.route('/api/users/unthrottle/<username>', methods=['POST'])
@login_required
def api_unthrottle_user(username):
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

@users_bp.route('/api/users/delete/<int:user_id>', methods=['DELETE'])
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

@users_bp.route('/api/users/delete/by-username/<username>', methods=['DELETE'])
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


# ============================================================
# IPOE USER APIS
# ============================================================
@users_bp.route('/api/ipoe/users')
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
                   (SELECT COUNT(*) FROM radacct a WHERE (LOWER(a.username) = LOWER(c.username) OR REGEXP_REPLACE(LOWER(a.username), '[^a-z0-9]', '', 'g') = REGEXP_REPLACE(LOWER(c.username), '[^a-z0-9]', '', 'g')) AND a.acctstoptime IS NULL) as active_sessions,
                   (SELECT HOST(framedipaddress) FROM radacct a WHERE (LOWER(a.username) = LOWER(c.username) OR REGEXP_REPLACE(LOWER(a.username), '[^a-z0-9]', '', 'g') = REGEXP_REPLACE(LOWER(c.username), '[^a-z0-9]', '', 'g')) AND a.acctstoptime IS NULL ORDER BY acctstarttime DESC LIMIT 1) as active_ip
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

@users_bp.route('/api/ipoe/users/add', methods=['POST'])
@login_required
def api_ipoe_add_user():
    try:
        data = get_request_data()
        mac = data.get('mac_address', '').strip().lower()
        
        if not mac:
            return jsonify({'success': False, 'error': 'MAC Address wajib diisi'}), 400

        # Normalisasi MAC
        mac = mac.replace(':', '-').replace('_', '-')
        
        password = data.get('password', '').strip()
        if not password:
            password = mac # Default password to MAC address
            
        ip_address = data.get('ip_address', '').strip()
        rate_limit = data.get('rate_limit', '').strip()
        framed_pool = (data.get('framed_pool') or data.get('ip_pool') or '').strip()
        name = data.get('name', '').strip()

        # Cek duplikat
        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (mac,))
        if existing:
            return jsonify({'success': False, 'error': 'MAC Address sudah terdaftar'}), 400

        # Insert check & user group
        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)", (mac, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'ipoe', 1) ON CONFLICT DO NOTHING", (mac,), fetch=False)
        
        # Insert replies
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)", (mac, ip_address), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)", (mac, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)", (mac, framed_pool), fetch=False)
        if name:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Description', ':=', %s)", (mac, name), fetch=False)

        return jsonify({'success': True, 'message': 'User IPoE berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/ipoe/users/update/<username>', methods=['PUT'])
@login_required
def api_ipoe_update_user(username):
    try:
        data = get_request_data()
        
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'", (data['password'], username), fetch=False)
            
        # Support both ip_pool and framed_pool
        if 'ip_pool' in data and 'framed_pool' not in data:
            data['framed_pool'] = data['ip_pool']

        for attr, key in [('Framed-IP-Address', 'ip_address'), ('Mikrotik-Rate-Limit', 'rate_limit'), ('Framed-Pool', 'framed_pool'), ('Description', 'name')]:
            if key in data:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = %s", (username, attr), fetch=False)
                if data[key]:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, ':=', %s)", (username, attr, data[key]), fetch=False)
                    
        return jsonify({'success': True, 'message': 'User IPoE berhasil diperbarui'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/ipoe/users/delete/<username>', methods=['DELETE'])
@login_required
def api_ipoe_delete_user(username):
    try:
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'ipoe'", (username,), fetch=False)
        # Hapus radcheck/radreply hanya jika user tidak terdaftar di group lain
        other_groups = execute_query(
            "SELECT 1 FROM radusergroup WHERE username = %s LIMIT 1", (username,)
        )
        if not other_groups:
            execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
            execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'User IPoE {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/ipoe/get-name')
@users_bp.route('/api/ipoe/get-name/<mac>')
@limiter.limit("60 per minute")
def api_ipoe_get_name(mac=None):
    """API endpoint public for MikroTik Lease Script."""
    try:
        if mac is None:
            mac = request.args.get('mac', '').strip().lower()
        else:
            mac = mac.strip().lower()

        if not mac:
            return "Name-Not-Found; ", 400
        
        clean_mac = "".join(c for c in mac if c.isalnum())
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
                
        if name:
            name = name.replace(" ", "-")
            safe_name = "".join(c for c in name if c.isalnum() or c == '-')
        else:
            safe_name = f"IPoE-{clean_mac}"
            
        return f"{safe_name};{rate_limit}"
    except Exception as e:
        return f"Error-{str(e)}; ", 500

@users_bp.route('/api/ipoe/sync-static')
def api_ipoe_sync_static():
    try:
        token = request.args.get('token')
        expected_token = os.environ.get('SECRET_KEY') or current_app.config.get('SECRET_KEY') or "05052020fAk@#"
        if not token or token != expected_token:
            return "Unauthorized", 401
            
        rows = execute_query("""
            SELECT 
                c.username,
                (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Framed-IP-Address' LIMIT 1) as ip_address,
                (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Mikrotik-Rate-Limit' LIMIT 1) as rate_limit,
                (SELECT value FROM radreply r WHERE r.username = c.username AND r.attribute = 'Description' LIMIT 1) as name
            FROM radcheck c
            WHERE c.attribute = 'Cleartext-Password'
              AND EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'ipoe')
        """)
        
        lines = []
        for r in rows:
            ip = r['ip_address']
            if not ip:
                continue
            
            mac = r['username'] or ""
            rate = r['rate_limit'] or ""
            name = r['name'] or f"Static-{mac}"
            name = name.replace(" ", "-")
            safe_name = "".join(c for c in name if c.isalnum() or c in ('-', '_'))
            
            lines.append(f"{ip.strip()};{mac.strip()};{rate.strip()};{safe_name}")
            
        return "\n".join(lines), 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return f"Error: {str(e)}", 500

@users_bp.route('/api/ipoe/dhcp-leases')
@login_required
def api_ipoe_dhcp_leases():
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

    if leases:
        clean_macs = [mac.replace(':', '').replace('-', '').replace('_', '') for mac in leases.keys()]
        placeholders = ','.join(['%s'] * len(clean_macs))
        radius_data = execute_query(
            f"""
            SELECT username, attribute, value, REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g') as clean_username 
            FROM radreply 
            WHERE REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g') IN ({placeholders}) 
              AND attribute IN ('Framed-IP-Address','Mikrotik-Rate-Limit','Framed-Pool')
            """,
            tuple(clean_macs)
        ) or []
        
        clean_to_original = {mac.replace(':', '').replace('-', '').replace('_', ''): mac for mac in leases.keys()}
        for row in radius_data:
            clean_user = row['clean_username']
            orig_mac = clean_to_original.get(clean_user)
            if orig_mac:
                if row['attribute'] == 'Framed-IP-Address':
                    leases[orig_mac]['radius_ip'] = row['value']
                elif row['attribute'] == 'Mikrotik-Rate-Limit':
                    leases[orig_mac]['radius_rate'] = row['value']
                elif row['attribute'] == 'Framed-Pool':
                    leases[orig_mac]['radius_pool'] = row['value']

    return jsonify({'success': True, 'leases': list(leases.values()), 'errors': errors})

@users_bp.route('/api/ipoe/stats')
@login_required
def api_ipoe_stats():
    try:
        total = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'ipoe'")[0]['count']
        online = execute_query("""
            SELECT COUNT(DISTINCT a.username) as count 
            FROM radacct a 
            INNER JOIN radusergroup g ON (LOWER(g.username) = LOWER(a.username) OR REGEXP_REPLACE(LOWER(g.username), '[^a-z0-9]', '', 'g') = REGEXP_REPLACE(LOWER(a.username), '[^a-z0-9]', '', 'g')) AND g.groupname = 'ipoe'
            WHERE a.acctstoptime IS NULL
        """)[0]['count']
        return jsonify({'total': total, 'online': online})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# HOTSPOT USER APIS (VOUCHERS)
# ============================================================
@users_bp.route('/api/hotspot/vouchers')
@login_required
def api_hotspot_vouchers():
    try:
        search = request.args.get('search', '')
        status = request.args.get('status', '')  # active | unused | expired
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit

        where_clauses = ["deleted_at IS NULL"]
        params = []

        if search:
            where_clauses.append("(voucher_code ILIKE %s OR username ILIKE %s OR package_name ILIKE %s)")
            params.extend([f'%{search}%', f'%{search}%', f'%{search}%'])

        # Filter berdasarkan status voucher
        if status == 'active':
            # Voucher yang sudah dipakai (first_login ada) dan belum expired
            where_clauses.append("first_login IS NOT NULL AND (expires_at IS NULL OR expires_at > NOW())")
        elif status == 'unused':
            # Voucher yang belum pernah digunakan sama sekali
            where_clauses.append("first_login IS NULL")
        elif status == 'expired':
            # Voucher yang sudah melebihi batas waktu
            where_clauses.append("expires_at IS NOT NULL AND expires_at < NOW()")

        where = " AND ".join(where_clauses)
        
        query = f"""
            SELECT id, voucher_code, username, password, duration_seconds, package_name, rate_limit, 
                   TO_CHAR(first_login AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as first_login,
                   TO_CHAR(expires_at AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as expires_at,
                   locked_mac, created_at,
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(h.username) AND a.acctstoptime IS NULL) as active_sessions,
                   CASE 
                       WHEN first_login IS NULL THEN 'unused'
                       WHEN expires_at < NOW() THEN 'expired'
                       ELSE 'active'
                   END as status,
                   CASE
                       WHEN first_login IS NULL THEN duration_seconds
                       WHEN expires_at < NOW() THEN 0
                       ELSE GREATEST(0, EXTRACT(EPOCH FROM (expires_at - NOW()))::INTEGER)
                   END as remaining_seconds
            FROM hotspot_vouchers h
            WHERE {where}
            ORDER BY id DESC LIMIT %s OFFSET %s
        """
        
        vouchers = execute_query(query, tuple(params + [limit, offset]))
        total = execute_query(f"SELECT COUNT(*) as total FROM hotspot_vouchers WHERE {where}", tuple(params))[0]['total']
        
        return jsonify({
            'vouchers': vouchers or [],
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': (total + limit - 1) // limit
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@users_bp.route('/api/hotspot/vouchers/generate', methods=['POST'])
@login_required
def api_hotspot_generate():
    import random
    import string
    try:
        data = get_request_data()
        qty = int(data.get('qty') or data.get('count') or 10)
        
        # Package mapping
        pkg = data.get('package', '1h')
        pkg_map = {
            '3h': (10800, 'Quick 3 Jam'),
            '1d': (86400, 'Daily 24 Jam'),
            '7d': (604800, 'Weekly 7 Hari'),
            '30d': (2592000, 'Monthly Unlimited')
        }
        duration, package_name = pkg_map.get(pkg, (3600, 'Default-1H'))
        
        if 'duration_seconds' in data and data['duration_seconds']:
            try:
                duration = int(data['duration_seconds'])
            except:
                pass
        if 'package_name' in data and data['package_name']:
            package_name = data['package_name']
            
        rate_limit = data.get('rate_limit', '2M/2M')
        if not rate_limit:
            rate_limit = '2M/2M'
            
        prefix = data.get('prefix', '').strip()
        code_length = int(data.get('length', 6))

        vouchers_generated = []
        for _ in range(qty):
            # Generate random voucher code
            chars = string.ascii_uppercase + string.digits
            code = prefix + ''.join(random.choice(chars) for _ in range(code_length))
            
            # RADIUS username & password = voucher code (standard hotspot behavior)
            username = code
            password = code
            
            # Simpan ke tabel hotspot_vouchers
            execute_query("""
                INSERT INTO hotspot_vouchers (voucher_code, username, password, duration_seconds, package_name, rate_limit)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (code, username, password, duration, package_name, rate_limit), fetch=False)
            
            # Simpan ke radcheck agar FreeRADIUS mengenalnya
            execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s) ON CONFLICT (username, attribute) DO UPDATE SET value = EXCLUDED.value", (username, password), fetch=False)
            # Link ke group hotspot (agar menggunakan server sql_hotspot)
            execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'hotspot', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)
            
            vouchers_generated.append({
                'voucher_code': code,
                'username': username,
                'password': password,
                'package': package_name,
                'rate': rate_limit
            })
            
        return jsonify({'success': True, 'message': f'Berhasil mengenerate {qty} voucher hotspot!', 'vouchers': vouchers_generated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/hotspot/vouchers/create', methods=['POST'])
@login_required
def api_hotspot_create():
    import random
    import string
    try:
        data = get_request_data()
        
        code = data.get('voucher_code', '').strip().upper()
        if not code:
            chars = string.ascii_uppercase + string.digits
            code = ''.join(random.choice(chars) for _ in range(6))
            
        username = data.get('username', '').strip()
        if not username:
            username = code
            
        password = data.get('password', '').strip()
        if not password:
            chars = string.ascii_uppercase + string.digits
            password = ''.join(random.choice(chars) for _ in range(6))

        # Package mapping
        pkg = data.get('package', '1h')
        pkg_map = {
            '3h': (10800, 'Quick 3 Jam'),
            '1d': (86400, 'Daily 24 Jam'),
            '7d': (604800, 'Weekly 7 Hari'),
            '30d': (2592000, 'Monthly Unlimited')
        }
        duration, package_name = pkg_map.get(pkg, (3600, 'Custom'))
        
        if 'duration_seconds' in data and data['duration_seconds']:
            try:
                duration = int(data['duration_seconds'])
            except:
                pass
        if 'package_name' in data and data['package_name']:
            package_name = data['package_name']

        rate_limit = data.get('rate_limit', '2M/2M')
        if not rate_limit:
            rate_limit = '2M/2M'

        existing = execute_query("SELECT id FROM hotspot_vouchers WHERE username = %s AND deleted_at IS NULL", (username,))
        if existing:
            return jsonify({'success': False, 'error': f'Voucher code atau username {username} sudah ada'}), 400

        execute_query("""
            INSERT INTO hotspot_vouchers (voucher_code, username, password, duration_seconds, package_name, rate_limit)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (code, username, password, duration, package_name, rate_limit), fetch=False)

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s) ON CONFLICT (username, attribute) DO UPDATE SET value = EXCLUDED.value", (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'hotspot', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)

        return jsonify({'success': True, 'message': 'Voucher Hotspot berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/hotspot/vouchers/delete/<int:vid>', methods=['DELETE'])
@login_required
def api_hotspot_delete(vid):
    try:
        voucher = execute_query("SELECT username FROM hotspot_vouchers WHERE id = %s", (vid,))
        if not voucher:
            return jsonify({'success': False, 'error': 'Voucher tidak ditemukan'}), 404
            
        username = voucher[0]['username']
        execute_query("UPDATE hotspot_vouchers SET deleted_at = NOW() WHERE id = %s", (vid,), fetch=False)
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'hotspot'", (username,), fetch=False)

        return jsonify({'success': True, 'message': 'Voucher berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/hotspot/vouchers/delete-expired', methods=['DELETE'])
@login_required
def api_hotspot_delete_expired():
    try:
        expired_vouchers = execute_query("""
            SELECT username FROM hotspot_vouchers 
            WHERE deleted_at IS NULL AND expires_at < NOW()
        """) or []
        
        count = 0
        for v in expired_vouchers:
            uname = v['username']
            execute_query("UPDATE hotspot_vouchers SET deleted_at = NOW() WHERE username = %s", (uname,), fetch=False)
            execute_query("DELETE FROM radcheck WHERE username = %s", (uname,), fetch=False)
            execute_query("DELETE FROM radreply WHERE username = %s", (uname,), fetch=False)
            execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'hotspot'", (uname,), fetch=False)
            count += 1
            
        return jsonify({
            'success': True,
            'deleted': count,
            'message': f'Berhasil membersihkan {count} voucher kedaluwarsa!'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/hotspot/packages')
@login_required
def api_hotspot_packages():
    try:
        packages = execute_query("SELECT DISTINCT package_name, duration_seconds, rate_limit FROM hotspot_vouchers WHERE deleted_at IS NULL ORDER BY package_name") or []
        if not packages:
            packages = [
                {'package_name': 'Paket 1 Jam', 'duration_seconds': 3600, 'rate_limit': '2M/2M'},
                {'package_name': 'Paket 5 Jam', 'duration_seconds': 18000, 'rate_limit': '2M/2M'},
                {'package_name': 'Paket 24 Jam', 'duration_seconds': 86400, 'rate_limit': '3M/3M'},
                {'package_name': 'Paket 30 Hari', 'duration_seconds': 2592000, 'rate_limit': '5M/5M'}
            ]
        return jsonify(packages)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================
# VPN USER APIS
# ============================================================
@users_bp.route('/api/vpn/users')
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

        # Parse OpenVPN status for active sessions
        openvpn_usernames = set()
        status_file = '/tmp/openvpn-status.log'
        try:
            with open(status_file, 'r') as f:
                lines = f.readlines()
            in_client_section = False
            for line in lines:
                line = line.strip()
                if line.startswith('HEADER,CLIENT_LIST'):
                    in_client_section = True
                    continue
                if line.startswith('HEADER,') and in_client_section:
                    in_client_section = False
                    continue
                if in_client_section and line.startswith('CLIENT_LIST,'):
                    parts = line.split(',')
                    if len(parts) >= 8:
                        username = parts[9] if len(parts) > 9 and parts[9] and parts[9] != 'UNDEF' else parts[1]
                        if username != 'UNDEF':
                            openvpn_usernames.add(username.lower())
        except Exception:
            pass

        # Update active_sessions for users connected to OpenVPN
        for u in users:
            uname = u.get('username')
            if uname and uname.lower() in openvpn_usernames:
                u['active_sessions'] = max(u.get('active_sessions') or 0, 1)

        # Calculate global online count for VPN users
        # 1. Distinct online users in radacct belonging to group 'vpn'
        active_db_users = execute_query("""
            SELECT DISTINCT LOWER(a.username) as username 
            FROM radacct a 
            INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'vpn' 
            WHERE a.acctstoptime IS NULL
        """)
        active_db_usernames = {row['username'] for row in active_db_users}
        total_online = len(active_db_usernames.union(openvpn_usernames))

        return jsonify({
            'users': users,
            'total': total,
            'page': page,
            'limit': limit,
            'total_pages': (total + limit - 1) // limit,
            'online_count': total_online
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@users_bp.route('/api/vpn/users/add', methods=['POST'])
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

        # Cek apakah user sudah ada di group vpn
        in_vpn = execute_query(
            "SELECT 1 FROM radusergroup WHERE username = %s AND groupname = 'vpn' LIMIT 1", (username,)
        )
        if in_vpn:
            return jsonify({'success': False, 'error': 'User sudah terdaftar sebagai VPN user'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if not existing:
            # User baru sama sekali, insert radcheck
            execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)", (username, password), fetch=False)

        # Tambahkan ke group vpn (user mungkin sudah ada di group lain)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'vpn', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)", (username, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)", (username, framed_pool), fetch=False)
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)", (username, ip_address), fetch=False)

        _sync_ikev2_pool(username, ip_address)
        return jsonify({'success': True, 'message': 'VPN user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/vpn/users/update/<username>', methods=['PUT'])
@login_required
def api_vpn_update_user(username):
    try:
        data = get_request_data()
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'", (data['password'], username), fetch=False)
        for attr, key in [('Mikrotik-Rate-Limit', 'rate_limit'), ('Framed-Pool', 'framed_pool'), ('Framed-IP-Address', 'ip_address')]:
            if key in data:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = %s", (username, attr), fetch=False)
                if data[key]:
                    execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, %s, ':=', %s)", (username, attr, data[key]), fetch=False)
        
        # Sync IP to ikev2 pool
        ip_address = data.get('ip_address', '')
        _sync_ikev2_pool(username, ip_address)
        return jsonify({'success': True, 'message': 'VPN user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/vpn/users/delete/<username>', methods=['DELETE'])
@login_required
def api_vpn_delete_user(username):
    try:
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'vpn'", (username,), fetch=False)
        # Hanya hapus radcheck/radreply jika user tidak ada di group lain
        other_groups = execute_query(
            "SELECT 1 FROM radusergroup WHERE username = %s LIMIT 1", (username,)
        )
        if not other_groups:
            execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
            execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        
        _sync_ikev2_pool(username, None)  # Remove from pool
        return jsonify({'success': True, 'message': f'VPN user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/vpn/tunnels')
@login_required
def api_vpn_tunnels():
    """Real-time VPN tunnel monitoring from OpenVPN status file + L2TP/IKEv2 from radacct"""
    tunnels = []
    openvpn_usernames = set()

    # --- Source A: OpenVPN status file (CSV v2) ---
    status_file = '/tmp/openvpn-status.log'
    try:
        with open(status_file, 'r') as f:
            lines = f.readlines()
        in_client_section = False
        for line in lines:
            line = line.strip()
            if line.startswith('HEADER,CLIENT_LIST'):
                in_client_section = True
                continue
            if line.startswith('HEADER,') and in_client_section:
                in_client_section = False
                continue
            if in_client_section and line.startswith('CLIENT_LIST,'):
                parts = line.split(',')
                if len(parts) >= 8:
                    username = parts[9] if len(parts) > 9 and parts[9] and parts[9] != 'UNDEF' else parts[1]
                    if not username or username == 'UNDEF':
                        continue
                    openvpn_usernames.add(username.lower())
                    tunnels.append({
                        'type': 'openvpn',
                        'username': username,
                        'real_address': parts[2],
                        'virtual_address': parts[3],
                        'bytes_recv': int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0,
                        'bytes_sent': int(parts[6]) if len(parts) > 6 and parts[6].isdigit() else 0,
                        'connected_since': parts[7] if len(parts) > 7 else '',
                        'cipher': parts[12] if len(parts) > 12 and parts[12] else 'OpenVPN'
                    })
    except Exception:
        pass

    # --- Source B: L2TP/IKEv2 sessions from radacct ---
    try:
        results = execute_query("""
            SELECT a.username, a.framedipaddress, a.callingstationid,
                   a.calledstationid, a.nasipaddress, a.acctstarttime, a.acctsessiontime,
                   a.acctinputoctets, a.acctoutputoctets
            FROM radacct a
            WHERE a.acctstoptime IS NULL
              AND EXISTS (
                  SELECT 1 FROM radusergroup g
                  WHERE LOWER(g.username) = LOWER(a.username) AND g.groupname = 'vpn'
              )
            ORDER BY a.acctstarttime DESC
        """)
        if results:
            for row in results:
                uname = row['username']
                if uname.lower() in openvpn_usernames:
                    continue

                # Detect IKEv2: calledstationid dari strongswan format "IP=5B4500=5D" atau "IP[4500]"
                nas_ip = (row.get('nasipaddress') or '')
                called = (row.get('calledstationid') or '')
                vpn_type = 'ikev2' if ('=5B4500=5D' in called or '=5B500=5D' in called or '[4500]' in called or '[500]' in called) else 'l2tp'

                tunnels.append({
                    'type': vpn_type,
                    'username': uname,
                    'real_address': row.get('callingstationid') or '—',
                    'virtual_address': row.get('framedipaddress') or '-',
                    'bytes_recv': int(row.get('acctinputoctets') or 0),
                    'bytes_sent': int(row.get('acctoutputoctets') or 0),
                    'connected_since': row['acctstarttime'].strftime('%Y-%m-%d %H:%M:%S') if row.get('acctstarttime') else '',
                    'cipher': 'IKEv2/IPsec' if vpn_type == 'ikev2' else 'L2TP/IPsec'
                })
    except Exception:
        pass

    return jsonify({'tunnels': tunnels})


# ============================================================
# 802.1X USER APIS
# ============================================================
@users_bp.route('/api/8021x/users')
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

@users_bp.route('/api/8021x/users/add', methods=['POST'])
@login_required
def api_x8021x_add_user():
    try:
        data = get_request_data()
        username = data.get('username', '').strip().lower()
        password = data.get('password', '').strip()

        if not username or not password:
            return jsonify({'success': False, 'error': 'Username dan Password wajib diisi'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)", (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, '8021x', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)

        return jsonify({'success': True, 'message': '802.1X user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/8021x/users/update/<username>', methods=['PUT'])
@login_required
def api_x8021x_update_user(username):
    try:
        data = get_request_data()
        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'", (data['password'], username), fetch=False)
        return jsonify({'success': True, 'message': '802.1X user password berhasil diperbarui'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/8021x/users/delete/<username>', methods=['DELETE'])
@login_required
def api_x8021x_delete_user(username):
    try:
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = '8021x'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'802.1X user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================================
# WIRELESS USER APIS
# ============================================================
@users_bp.route('/api/wireless/users')
@login_required
def api_wireless_users():
    try:
        search = request.args.get('search', '')
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        offset = (page - 1) * limit

        base_where = "c.attribute = 'Cleartext-Password' AND EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname = 'wireless')"
        params = []
        if search:
            base_where += " AND c.username ILIKE %s"
            params.append(f'%{search}%')

        data_query = f"""
            SELECT c.id, c.username, c.value as password,
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

@users_bp.route('/api/wireless/users/add', methods=['POST'])
@login_required
def api_wireless_add_user():
    try:
        data = get_request_data()
        username = data.get('username', '').strip().lower()
        password = data.get('password', '').strip()

        if not username or not password:
            return jsonify({'success': False, 'error': 'Username dan Password wajib diisi'}), 400

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)", (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'wireless', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)

        return jsonify({'success': True, 'message': 'Wireless user berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/wireless/users/update/<username>', methods=['PUT'])
@login_required
def api_wireless_update_user(username):
    try:
        data = get_request_data()
        new_username = data.get('username', '').strip().lower()
        new_password = data.get('password', '').strip()

        if not new_username:
            return jsonify({'success': False, 'error': 'Username wajib diisi'}), 400

        # Update username if changed
        if new_username != username.lower():
            execute_query("UPDATE radcheck SET username = %s WHERE username = %s AND attribute = 'Cleartext-Password'", (new_username, username), fetch=False)
            execute_query("UPDATE radreply SET username = %s WHERE username = %s", (new_username, username), fetch=False)
            execute_query("UPDATE radusergroup SET username = %s WHERE username = %s AND groupname = 'wireless'", (new_username, username), fetch=False)

        # Update password if provided
        if new_password:
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'", (new_password, new_username), fetch=False)

        return jsonify({'success': True, 'message': 'Wireless user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/wireless/users/delete/<username>', methods=['DELETE'])
@login_required
def api_wireless_delete_user(username):
    try:
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'wireless'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'Wireless user {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/wireless/users/detail/<username>')
@login_required
def api_wireless_user_detail(username):
    try:
        sessions = execute_query("""
            SELECT 
                acctsessionid,
                nasipaddress,
                framedipaddress,
                calledstationid,
                callingstationid,
                acctstarttime,
                acctupdatetime,
                acctsessiontime,
                acctinputoctets,
                acctoutputoctets,
                acctterminatecause,
                acctstoptime
            FROM radacct
            WHERE LOWER(username) = LOWER(%s)
            ORDER BY acctstarttime DESC
            LIMIT 5
        """, (username,))

        def fmt_bytes(b):
            if not b:
                return '0 B'
            b = int(b)
            for unit in ['B','KB','MB','GB','TB']:
                if b < 1024:
                    return f"{b:.1f} {unit}"
                b /= 1024
            return f"{b:.1f} TB"

        def fmt_duration(secs):
            if not secs:
                return '0 detik'
            secs = int(secs)
            h = secs // 3600
            m = (secs % 3600) // 60
            s = secs % 60
            if h > 0:
                return f"{h} jam {m} menit"
            elif m > 0:
                return f"{m} menit {s} detik"
            return f"{s} detik"

        result = []
        for s in (sessions or []):
            result.append({
                'acctsessionid': s['acctsessionid'],
                'nasipaddress': str(s['nasipaddress']) if s['nasipaddress'] else '-',
                'framedipaddress': str(s['framedipaddress']) if s['framedipaddress'] else '-',
                'calledstationid': s['calledstationid'] or '-',
                'callingstationid': s['callingstationid'] or '-',
                'acctstarttime': s['acctstarttime'].strftime('%Y-%m-%d %H:%M:%S') if s['acctstarttime'] else '-',
                'acctupdatetime': s['acctupdatetime'].strftime('%Y-%m-%d %H:%M:%S') if s['acctupdatetime'] else '-',
                'duration': fmt_duration(s['acctsessiontime']),
                'download': fmt_bytes(s['acctinputoctets']),
                'upload': fmt_bytes(s['acctoutputoctets']),
                'status': 'Online' if not s['acctstoptime'] else 'Offline',
                'acctterminatecause': s['acctterminatecause'] or '-',
            })

        return jsonify({'success': True, 'username': username, 'sessions': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
