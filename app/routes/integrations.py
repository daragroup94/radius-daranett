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
        elif ig['type'] == 'tenda-olt':
            host = ig['host']
            if not host.startswith(('http://', 'https://')):
                host = f"http://{host}"
            url = f"{host}:{ig['port']}" if ig['port'] else host
            url = url.rstrip('/')
            
            import hashlib
            password_md5 = hashlib.md5(ig['password'].encode()).hexdigest()
            
            session = requests.Session()
            login_payload = {"login": {"username": ig['username'], "password": password_md5}}
            resp = session.post(f"{url}/getModules", json=login_payload, timeout=5, verify=False)
            if resp.status_code == 200:
                # Test query system info
                info_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=5, verify=False)
                if info_resp.status_code == 200:
                    info_data = info_resp.json()
                    dev_name = info_data.get('deviceInfo', {}).get('deviceName', 'GPON-OLT')
                    prod_type = info_data.get('deviceInfo', {}).get('productType', 'TES7002')
                    temp = info_data.get('deviceInfo', {}).get('deviceTemperature', '?')
                    return jsonify({'success': True, 'message': f"Connected to Tenda OLT! Device: {dev_name} ({prod_type}), Temp: {temp}°C"})
                else:
                    return jsonify({'success': False, 'error': f"Connected but failed to fetch system info. Code: {info_resp.status_code}"})
            else:
                return jsonify({'success': False, 'error': f"Failed to login. HTTP status: {resp.status_code}"})
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

@integrations_bp.route('/api/tenda-olt/status')
@login_required
def api_tenda_olt_status():
    olt_id = request.args.get('olt_id', type=int)
    try:
        if olt_id:
            olts = execute_query("SELECT id, name, type, host, port, username, password, is_active FROM integrations WHERE id=%s AND type='tenda-olt' AND is_active=true", (olt_id,)) or []
        else:
            olts = execute_query("SELECT id, name, type, host, port, username, password, is_active FROM integrations WHERE type='tenda-olt' AND is_active=true ORDER BY id") or []
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    results = []
    for olt in olts:
        try:
            host = olt['host']
            if not host.startswith(('http://', 'https://')):
                host = f"http://{host}"
            url = f"{host}:{olt['port']}" if olt['port'] else host
            url = url.rstrip('/')

            import hashlib
            password_md5 = hashlib.md5(olt['password'].encode()).hexdigest()

            session = requests.Session()
            login_payload = {"login": {"username": olt['username'], "password": password_md5}}
            login_resp = session.post(f"{url}/getModules", json=login_payload, timeout=4, verify=False)
            if login_resp.status_code != 200:
                raise Exception(f"Login HTTP status {login_resp.status_code}")

            # Get system info
            sys_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=4, verify=False)
            sys_data = sys_resp.json() if sys_resp.status_code == 200 else {}
            dev_info = sys_data.get('deviceInfo', {})

            # Get PON stats
            pon_resp = session.post(f"{url}/getModules", json={"ponStatistics": "", "ontNumInfo": ""}, timeout=4, verify=False)
            pon_data = pon_resp.json() if pon_resp.status_code == 200 else {}
            pon_stats = pon_data.get('ponStatistics', [])
            ont_info = pon_data.get('ontNumInfo', [])

            ports_data = []
            stats_by_pon = {item.get('ponNumber'): item for item in pon_stats}
            ont_by_pon = {item.get('ponPort'): item for item in ont_info}

            for pon_id in (1, 2):
                p_stat = stats_by_pon.get(pon_id, {})
                p_ont = ont_by_pon.get(pon_id, {})
                
                ports_data.append({
                    'pon_id': pon_id,
                    'name': f"PON {pon_id}",
                    'up_speed': p_stat.get('upSpeed', '0.00'),
                    'down_speed': p_stat.get('downSpeed', '0.00'),
                    'ont_online': p_ont.get('ontOnline', 0),
                    'ont_offline': p_ont.get('ontOffline', 0),
                    'ont_total': p_ont.get('ontTotal', 0)
                })

            results.append({
                'olt_id': olt['id'],
                'name': olt['name'],
                'host': olt['host'],
                'temperature': dev_info.get('deviceTemperature', 'N/A'),
                'soft_version': dev_info.get('softVersion', 'N/A'),
                'uptime_days': round(dev_info.get('runTime', 0) / (24 * 3600), 1) if dev_info.get('runTime') else 0,
                'ports': ports_data
            })
        except Exception as e:
            results.append({
                'olt_id': olt['id'],
                'name': olt['name'],
                'host': olt['host'],
                'error': str(e)
            })

    return jsonify({'success': True, 'data': results})
