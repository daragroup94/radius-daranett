import os
import re
import librouteros
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from decorators import login_required
from database import execute_query
from helpers import get_request_data, convert_data_limit_to_bytes
from services.telegram import send_telegram_notification
from services.mikrotik_coa import send_coa_update, send_coa_update_async
from services.genieacs import get_genieacs_device_info

users_bp = Blueprint('users', __name__)

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
                   (SELECT COUNT(*) FROM radacct a WHERE a.username = c.username AND a.acctstoptime IS NULL) as active_sessions
            FROM radcheck c
            WHERE c.attribute = 'Cleartext-Password'
            AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname IN ('ipoe', 'vpn', '8021x', 'hotspot'))
        """
        
        count_query = "SELECT COUNT(*) as total FROM radcheck c WHERE c.attribute = 'Cleartext-Password' AND NOT EXISTS (SELECT 1 FROM radusergroup g WHERE g.username = c.username AND g.groupname IN ('ipoe', 'vpn', '8021x', 'hotspot'))"

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

        if data.get('data_limit'):
            data_limit_bytes = convert_data_limit_to_bytes(data['data_limit'])
            if data_limit_bytes:
                execute_query("DELETE FROM radreply WHERE username = %s AND attribute = 'Mikrotik-Total-Limit'", (username,), fetch=False)
                execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Total-Limit', ':=', %s)",
                             (username, str(data_limit_bytes)), fetch=False)

        if data.get('password'):
            execute_query("UPDATE radcheck SET value = %s WHERE username = %s AND attribute = 'Cleartext-Password'",
                         (data['password'], username), fetch=False)

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

@users_bp.route('/api/ipoe/users/add', methods=['POST'])
@login_required
def api_ipoe_add_user():
    try:
        data = get_request_data()
        mac = data.get('mac_address', '').strip().lower()
        password = data.get('password', '').strip()
        ip_address = data.get('ip_address', '').strip()
        rate_limit = data.get('rate_limit', '').strip()
        framed_pool = data.get('framed_pool', '').strip()
        name = data.get('name', '').strip()

        if not mac or not password:
            return jsonify({'success': False, 'error': 'MAC Address dan Password wajib diisi'}), 400

        # Normalisasi MAC
        mac = mac.replace(':', '-').replace('_', '-')
        
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
        execute_query("DELETE FROM radcheck WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radreply WHERE username = %s", (username,), fetch=False)
        execute_query("DELETE FROM radusergroup WHERE username = %s AND groupname = 'ipoe'", (username,), fetch=False)
        return jsonify({'success': True, 'message': f'User IPoE {username} berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/ipoe/get-name')
@users_bp.route('/api/ipoe/get-name/<mac>')
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

@users_bp.route('/api/ipoe/stats')
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
                   (SELECT COUNT(*) FROM radacct a WHERE LOWER(a.username) = LOWER(h.username) AND a.acctstoptime IS NULL) as active_sessions
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
        qty = int(data.get('qty', 10))
        duration = int(data.get('duration_seconds', 3600))
        rate_limit = data.get('rate_limit', '2M/2M')
        package_name = data.get('package_name', 'Default-1H')
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
                'code': code,
                'package': package_name,
                'rate': rate_limit
            })
            
        return jsonify({'success': True, 'message': f'Berhasil mengenerate {qty} voucher hotspot!', 'vouchers': vouchers_generated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/hotspot/vouchers/create', methods=['POST'])
@login_required
def api_hotspot_create():
    try:
        data = get_request_data()
        code = data.get('voucher_code', '').strip().upper()
        password = data.get('password', '').strip()
        duration = int(data.get('duration_seconds', 3600))
        rate_limit = data.get('rate_limit', '2M/2M')
        package_name = data.get('package_name', 'Custom')

        if not code or not password:
            return jsonify({'success': False, 'error': 'Voucher Code dan Password wajib diisi'}), 400

        existing = execute_query("SELECT id FROM hotspot_vouchers WHERE username = %s AND deleted_at IS NULL", (code,))
        if existing:
            return jsonify({'success': False, 'error': 'Voucher code sudah ada'}), 400

        execute_query("""
            INSERT INTO hotspot_vouchers (voucher_code, username, password, duration_seconds, package_name, rate_limit)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (code, code, password, duration, package_name, rate_limit), fetch=False)

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s) ON CONFLICT (username, attribute) DO UPDATE SET value = EXCLUDED.value", (code, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'hotspot', 1) ON CONFLICT DO NOTHING", (code,), fetch=False)

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
            
        return jsonify({'success': True, 'message': f'Berhasil membersihkan {count} voucher kedaluwarsa!'})
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
        return jsonify({'users': users, 'total': total, 'page': page, 'limit': limit,
                        'total_pages': (total + limit - 1) // limit})
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

        existing = execute_query("SELECT id FROM radcheck WHERE username = %s AND attribute = 'Cleartext-Password'", (username,))
        if existing:
            return jsonify({'success': False, 'error': 'Username sudah ada'}), 400

        execute_query("INSERT INTO radcheck (username, attribute, op, value) VALUES (%s, 'Cleartext-Password', ':=', %s)", (username, password), fetch=False)
        execute_query("INSERT INTO radusergroup (username, groupname, priority) VALUES (%s, 'vpn', 1) ON CONFLICT DO NOTHING", (username,), fetch=False)
        if rate_limit:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Mikrotik-Rate-Limit', ':=', %s)", (username, rate_limit), fetch=False)
        if framed_pool:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-Pool', ':=', %s)", (username, framed_pool), fetch=False)
        if ip_address:
            execute_query("INSERT INTO radreply (username, attribute, op, value) VALUES (%s, 'Framed-IP-Address', ':=', %s)", (username, ip_address), fetch=False)

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
        return jsonify({'success': True, 'message': 'VPN user berhasil diupdate'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@users_bp.route('/api/vpn/users/delete/<username>', methods=['DELETE'])
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
