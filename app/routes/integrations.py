import os
import requests
import librouteros
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, current_app
from decorators import login_required
from database import execute_query
from helpers import get_request_data
from services.telegram import send_telegram_notification

integrations_bp = Blueprint('integrations', __name__)

# Table integrations initialized globally on startup via database.py

@integrations_bp.route('/integrations')
@login_required
def integrations_page():
    return render_template('integrations.html')

@integrations_bp.route('/api/integrations')
@login_required
def api_integrations():
    try:
        rows = execute_query("SELECT id, name, type, host, port, username, description, is_active, created_at FROM integrations ORDER BY id")
        return jsonify(rows or [])
    except Exception as e:
        if 'does not exist' in str(e):
            return jsonify([])
        return jsonify({'error': str(e)}), 500

@integrations_bp.route('/api/integrations/add', methods=['POST'])
@login_required
def api_add_integration():
    data = get_request_data()
    if not data:
        return jsonify({'success': False, 'error': 'Payload data is required'}), 400
    try:
        itype = data.get('type', 'mikrotik')
        port_raw = data.get('port')
        if port_raw and str(port_raw).strip():
            port = int(port_raw)
        else:
            port = 7557 if itype == 'genieacs' else 8728

        execute_query("""
            INSERT INTO integrations (name, type, host, port, username, password, description, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (data['name'], itype, data['host'], port,
              data.get('username'), data.get('password'), data.get('description',''), data.get('is_active', True)), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/api/integrations/edit/<int:iid>', methods=['PUT'])
@login_required
def api_edit_integration(iid):
    data = get_request_data()
    if not data:
        return jsonify({'success': False, 'error': 'Payload data is required'}), 400
    try:
        itype = data.get('type', 'mikrotik')
        port_raw = data.get('port')
        if port_raw and str(port_raw).strip():
            port = int(port_raw)
        else:
            port = 7557 if itype == 'genieacs' else 8728

        if data.get('password'):
            execute_query("""
                UPDATE integrations SET name=%s, type=%s, host=%s, port=%s, username=%s, password=%s, description=%s, is_active=%s
                WHERE id=%s
            """, (data['name'], itype, data['host'], port,
                  data.get('username'), data['password'], data.get('description',''), data.get('is_active', True), iid), fetch=False)
        else:
            execute_query("""
                UPDATE integrations SET name=%s, type=%s, host=%s, port=%s, username=%s, description=%s, is_active=%s
                WHERE id=%s
            """, (data['name'], itype, data['host'], port,
                  data.get('username'), data.get('description',''), data.get('is_active', True), iid), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/api/integrations/delete/<int:iid>', methods=['DELETE'])
@login_required
def api_delete_integration(iid):
    try:
        execute_query("DELETE FROM integrations WHERE id=%s", (iid,), fetch=False)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/api/integrations/test/<int:iid>', methods=['POST'])
@login_required
def api_test_integration(iid):
    try:
        rows = execute_query("SELECT * FROM integrations WHERE id=%s", (iid,))
        if not rows:
            return jsonify({'success': False, 'error': 'Integration not found'}), 404
        ig = rows[0]
        if ig['type'] == 'mikrotik':
            api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
            identity = list(api('/system/identity/print'))
            api.close()
            return jsonify({'success': True, 'message': f"Connected! Identity: {identity[0].get('name','?')}"})
        elif ig['type'] == 'genieacs':
            host = ig['host']
            if not host.startswith(('http://', 'https://')):
                host = f"http://{host}"
            url = f"{host}:{ig['port']}" if ig['port'] else host
            auth = (ig['username'], ig['password']) if ig['username'] else None
            
            resp = requests.get(f"{url}/devices?limit=1", auth=auth, timeout=3)
            if resp.status_code == 200:
                return jsonify({'success': True, 'message': 'Connected to GenieACS NBI successfully!'})
            else:
                return jsonify({'success': False, 'error': f"GenieACS returned HTTP status {resp.status_code}"})
        return jsonify({'success': False, 'error': 'Unsupported type'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/api/mikrotik/active')
@login_required
def api_mikrotik_active():
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

    return jsonify({'success': True, 'data': results, 'total': len([r for r in results if 'error' not in r])})

@integrations_bp.route('/api/telegram/settings', methods=['GET'])
@login_required
def api_telegram_settings_get():
    return jsonify({
        'bot_token': current_app.config.get('TELEGRAM_BOT_TOKEN', ''),
        'chat_id': current_app.config.get('TELEGRAM_CHAT_ID', '')
    })

@integrations_bp.route('/api/telegram/settings', methods=['POST'])
@login_required
def api_telegram_settings_save():
    try:
        data = get_request_data()
        bot_token = data.get('bot_token', '').strip()
        chat_id = data.get('chat_id', '').strip()

        current_app.config['TELEGRAM_BOT_TOKEN'] = bot_token
        current_app.config['TELEGRAM_CHAT_ID'] = chat_id

        env_path = '/app/.telegram.env'
        with open(env_path, 'w') as f:
            f.write(f"TELEGRAM_BOT_TOKEN={bot_token}\n")
            f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")

        return jsonify({'success': True, 'message': 'Telegram settings saved'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/api/telegram/test', methods=['POST'])
@login_required
def api_telegram_test():
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
        data = {'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}
        
        # Gunakan requests.post langsung
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json=data,
            timeout=10
        )
        if resp.status_code == 200:
            return jsonify({'success': True, 'message': 'Pesan test berhasil dikirim ke Telegram!'})
        else:
            resp_json = resp.json()
            return jsonify({'success': False, 'error': f"Telegram: {resp_json.get('description', 'Unknown error')}"}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@integrations_bp.route('/notifications')
@login_required
def notifications_page():
    return render_template('notifications.html')
