import os
import socket
import requests
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, current_app
from decorators import login_required
from database import execute_query
from helpers import get_request_data, connect_mikrotik
from services.telegram import send_telegram_notification


integrations_bp = Blueprint('integrations', __name__)

def encode_length(length):
    if length < 128:
        return bytes([length])
    else:
        temp = []
        while length > 0:
            temp.append(length & 0xff)
            length >>= 8
        temp.reverse()
        return bytes([0x80 | len(temp)] + temp)

def make_snmp_request(community, oid, pdu_type=0xa0):
    parts = [int(x) for x in oid.split('.')]
    oid_bytes = bytearray()
    oid_bytes.append(parts[0] * 40 + parts[1])
    for part in parts[2:]:
        if part < 128:
            oid_bytes.append(part)
        else:
            temp = []
            while part > 0:
                temp.append((part & 0x7f) | 0x80)
                part >>= 7
            temp[0] &= 0x7f
            oid_bytes.extend(reversed(temp))

    varbind_payload = bytes([0x06, len(oid_bytes)]) + oid_bytes + bytes([0x05, 0x00])
    varbind = bytes([0x30]) + encode_length(len(varbind_payload)) + varbind_payload
    varbind_list = bytes([0x30]) + encode_length(len(varbind)) + varbind
    req_id = bytes([0x02, 0x04, 0x12, 0x34, 0x56, 0x78])
    err_status = bytes([0x02, 0x01, 0x00])
    err_index = bytes([0x02, 0x01, 0x00])
    pdu_payload = req_id + err_status + err_index + varbind_list
    pdu = bytes([pdu_type]) + encode_length(len(pdu_payload)) + pdu_payload
    version = bytes([0x02, 0x01, 0x01])
    comm_bytes = community.encode('utf-8')
    comm = bytes([0x04]) + encode_length(len(comm_bytes)) + comm_bytes
    msg_payload = version + comm + pdu
    msg = bytes([0x30]) + encode_length(len(msg_payload)) + msg_payload
    return msg

def parse_length(data, pos):
    if pos >= len(data):
        return 0, pos
    val = data[pos]
    if val < 128:
        return val, pos + 1
    num_bytes = val & 0x7f
    length = 0
    for i in range(num_bytes):
        length = (length << 8) + data[pos + 1 + i]
    return length, pos + 1 + num_bytes

def parse_tlv(data, pos):
    if pos >= len(data):
        return None, None, pos
    tag = data[pos]
    length, next_pos = parse_length(data, pos + 1)
    val = data[next_pos:next_pos + length]
    return tag, val, next_pos + length

def decode_snmp_response(data):
    try:
        tag, payload, _ = parse_tlv(data, 0)
        if tag != 0x30:
            return None, "Not a sequence"
        pos = 0
        ver_tag, ver_val, pos = parse_tlv(payload, pos)
        comm_tag, comm_val, pos = parse_tlv(payload, pos)
        pdu_tag, pdu_val, pos = parse_tlv(payload, pos)
        if pdu_tag != 0xa2:
            return None, f"Not a response PDU ({hex(pdu_tag)})"
        p_pos = 0
        req_tag, req_val, p_pos = parse_tlv(pdu_val, p_pos)
        err_tag, err_val, p_pos = parse_tlv(pdu_val, p_pos)
        err_idx_tag, err_idx_val, p_pos = parse_tlv(pdu_val, p_pos)
        vlist_tag, vlist_val, p_pos = parse_tlv(pdu_val, p_pos)
        if vlist_tag != 0x30:
            return None, "Varbind list not sequence"
        vl_pos = 0
        vbind_tag, vbind_val, vl_pos = parse_tlv(vlist_val, vl_pos)
        if vbind_tag != 0x30:
            return None, "Varbind not sequence"
        vb_pos = 0
        oid_tag, oid_val, vb_pos = parse_tlv(vbind_val, vb_pos)
        val_tag, val_val, vb_pos = parse_tlv(vbind_val, vb_pos)
        
        oid_parts = []
        if len(oid_val) > 0:
            oid_parts.append(str(oid_val[0] // 40))
            oid_parts.append(str(oid_val[0] % 40))
            val = 0
            for b in oid_val[1:]:
                if b & 0x80:
                    val = (val << 7) + (b & 0x7f)
                else:
                    val = (val << 7) + b
                    oid_parts.append(str(val))
                    val = 0
        oid_str = '.'.join(oid_parts)
        
        if val_tag == 0x04:
            return oid_str, val_val.decode('utf-8', errors='ignore')
        elif val_tag in (0x02, 0x41, 0x42, 0x43):
            v = 0
            for b in val_val:
                v = (v << 8) + b
            return oid_str, v
        return oid_str, val_val.hex()
    except Exception as e:
        return None, str(e)

def check_snmp(host, port, community, oid="1.3.6.1.2.1.1.5.0"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        req = make_snmp_request(community, oid, pdu_type=0xa0)
        sock.sendto(req, (host, int(port)))
        res, addr = sock.recvfrom(2048)
        ret = decode_snmp_response(res)
        if not ret or ret[0] is None:
            return False, (ret[1] if ret else "Decode error")
        return True, ret[1]
    except socket.timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)
    finally:
        sock.close()

def check_snmp_next(host, port, community, oid):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        req = make_snmp_request(community, oid, pdu_type=0xa1)
        sock.sendto(req, (host, int(port)))
        res, addr = sock.recvfrom(2048)
        ret = decode_snmp_response(res)
        if not ret or ret[0] is None:
            return False, (ret[1] if ret else "Decode error")
        return True, ret
    except socket.timeout:
        return False, "Timeout"
    except Exception as e:
        return False, str(e)
    finally:
        sock.close()


# HSGQ OLT MIB OID Map (Enterprise 50224)
# System: 1.3.6.1.4.1.50224.3.1.1.X.0
_HSGQ_OID = {
    'sys_temp':   '1.3.6.1.4.1.50224.3.1.1.18.0',
    'sys_model':  '1.3.6.1.4.1.50224.3.1.1.19.0',
    'sys_serial': '1.3.6.1.4.1.50224.3.1.1.20.0',
    'sys_uptime': '1.3.6.1.4.1.50224.3.1.1.3.0',
    'mem_total':  '1.3.6.1.4.1.50224.3.1.1.26.0',
    'mem_used':   '1.3.6.1.4.1.50224.3.1.1.27.0',
    'mem_free':   '1.3.6.1.4.1.50224.3.1.1.28.0',
    'fw_version': '1.3.6.1.4.1.50224.3.1.1.6.0',
}
# Port interface IDs (from snmpwalk ifDescr)
_HSGQ_PORTS = {
    'PON01': 16777472,
    'PON02': 16777728,
    'GE01':  16777984,
    'GE02':  16778240,
    'GE03':  16778496,
    'GE04':  16778752,
    'XGE01': 16779008,
}
# Port table OIDs: 1.3.6.1.4.1.50224.3.2.1.1.{col}.{portId}
# col 6 = admin status (1=up, 2=down)
# col 9 = link type (1=pon, 2=ge/sfp)
# col 5 = speed (Mbps)
# ONT table: 1.3.6.1.4.1.50224.3.12.2.1.{col}.{portId + onuId}
# col 2 = ONT name
# col 3 = ONT status (1=online)

def _snmp_get_hsgq_olt(host, port, community):
    """Return structured monitoring data from HSGQ OLT via SNMP."""
    def _get(oid):
        ok, val = check_snmp(host, port, community, oid)
        if ok:
            if isinstance(val, str):
                return val.strip().rstrip('\x00')
            return val
        return None

    result = {}
    # System info
    result['model']    = _get(_HSGQ_OID['sys_model']) or 'HSGQ-OLT'
    result['serial']   = _get(_HSGQ_OID['sys_serial']) or '-'
    result['firmware'] = (_get(_HSGQ_OID['fw_version']) or '-').replace('\x00', '')
    temp_raw = _get(_HSGQ_OID['sys_temp'])
    result['temperature'] = temp_raw if temp_raw is not None else 'N/A'
    uptime_cs = _get(_HSGQ_OID['sys_uptime'])
    result['uptime_days'] = round(uptime_cs / 8640000, 1) if isinstance(uptime_cs, int) else 0
    mem_total = _get(_HSGQ_OID['mem_total'])
    mem_used  = _get(_HSGQ_OID['mem_used'])
    result['mem_total_kb'] = mem_total or 0
    result['mem_used_kb']  = mem_used or 0
    result['mem_pct'] = round(mem_used / mem_total * 100) if (mem_total and mem_used) else 0

    # Discover PON ports dynamically or fallback to hardcoded list
    ports_to_check = []
    port_name_prefix = '1.3.6.1.4.1.50224.3.2.1.1.2'
    current_walk_oid = port_name_prefix
    try:
        # Walk to find all ports
        for _ in range(64):
            ok, ret = check_snmp_next(host, port, community, current_walk_oid)
            if not ok or not isinstance(ret, tuple):
                break
            next_oid, name_val = ret
            if not next_oid.startswith(port_name_prefix + '.'):
                break
            idx_str = next_oid[len(port_name_prefix) + 1:]
            try:
                port_id = int(idx_str)
                port_name = str(name_val).strip().rstrip('\x00')
                if port_name.upper().startswith('PON'):
                    ports_to_check.append((port_name, port_id))
            except ValueError:
                pass
            current_walk_oid = next_oid
    except Exception:
        pass

    if not ports_to_check:
        ports_to_check = [
            ('PON01', _HSGQ_PORTS.get('PON01', 16777472)),
            ('PON02', _HSGQ_PORTS.get('PON02', 16777728))
        ]

    # Port data: only PON ports
    ports_data = []
    for port_name, port_id in ports_to_check:
        port_status_raw = _get(f'1.3.6.1.4.1.50224.3.2.1.1.6.{port_id}')
        # HSGQ status column 6: 2 = UP, 1 = DOWN
        port_up = (port_status_raw == 2)

        # Count ONTs for this port: walk 3.12.2.1.3.{portId + i}
        # Port-based ONT index: portId * 1000 (approx), or we walk
        ont_online = 0
        ont_offline = 0
        # Walk ONT status table column .3 (online status) for this port
        # ONT index = port_id + ont_sequence (0-255)
        ont_table_prefix = '1.3.6.1.4.1.50224.3.12.2.1.3'
        try:
            current_walk_oid = f'{ont_table_prefix}.{port_id - 1}'
            for _ in range(256):
                ok, ret = check_snmp_next(host, port, community, current_walk_oid)
                if not ok or not isinstance(ret, tuple):
                    break
                next_oid, status_val = ret
                # Must remain in exact ONT status column
                if not next_oid or not next_oid.startswith(ont_table_prefix + '.'):
                    break
                idx_str = next_oid[len(ont_table_prefix) + 1:]
                # idx_str must be a single integer (no dots = single sub-id)
                if '.' in idx_str:
                    break
                try:
                    idx = int(idx_str)
                except ValueError:
                    break
                # Check this ONT belongs to this PON port (spacing 256)
                if idx >= port_id + 256:
                    break
                if idx < port_id:
                    current_walk_oid = next_oid
                    continue
                if status_val == 1:
                    ont_online += 1
                else:
                    ont_offline += 1
                current_walk_oid = next_oid
        except Exception:
            pass

        ports_data.append({
            'name':        port_name,
            'port_up':     port_up,
            'ont_online':  ont_online,
            'ont_offline': ont_offline,
            'ont_total':   ont_online + ont_offline,
        })

    result['ports'] = ports_data
    return result

@integrations_bp.route('/api/snmp-olt/status')
@login_required
def api_snmp_olt_status():
    olt_id = request.args.get('olt_id', type=int)
    cache_key = f"snmp_olt_status:{olt_id or 'all'}"
    cached = _cache_get(cache_key)
    if cached:
        try:
            import json as _json
            return jsonify(_json.loads(cached))
        except Exception:
            pass
    try:
        if olt_id:
            olts = execute_query(
                "SELECT id, name, type, host, port, username, is_active FROM integrations WHERE id=%s AND type='snmp' AND is_active=true",
                (olt_id,)) or []
        else:
            olts = execute_query(
                "SELECT id, name, type, host, port, username, is_active FROM integrations WHERE type='snmp' AND is_active=true ORDER BY id") or []
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    results = []
    for olt in olts:
        try:
            community = olt['username'] or 'public'
            data = _snmp_get_hsgq_olt(olt['host'], olt['port'] or 161, community)
            data['olt_id'] = olt['id']
            data['name']   = olt['name']
            data['host']   = olt['host']
            results.append(data)
        except Exception as e:
            results.append({'olt_id': olt['id'], 'name': olt['name'], 'host': olt['host'], 'error': str(e)})

    import json as _json
    output = {'success': True, 'data': results}
    _cache_set(cache_key, _json.dumps(output), ex=10)
    return jsonify(output)

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
            if itype == 'genieacs':
                port = 7557
            elif itype == 'snmp':
                port = 161
            else:
                port = 8728

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
            if itype == 'genieacs':
                port = 7557
            elif itype == 'snmp':
                port = 161
            else:
                port = 8728

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
            api = connect_mikrotik(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
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
            port = ig['port']
            is_https = False
            if host.startswith('https://'):
                is_https = True
                host = host[8:]
            elif host.startswith('http://'):
                host = host[7:]
            if str(port) == '443':
                is_https = True
            if is_https and str(port) == '80':
                port = 443
            scheme = 'https' if is_https else 'http'
            url = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
            url = url.rstrip('/')
            
            import hashlib
            password_md5 = hashlib.md5(f"an3400{ig['password']}".encode()).hexdigest()
            
            session = requests.Session()
            login_payload = {"login": {"username": ig['username'], "password": password_md5}}
            resp = session.post(f"{url}/setModules", json=login_payload, timeout=5, verify=False, allow_redirects=False)
            if resp.status_code == 200:
                token = resp.headers.get('Token')
                if token:
                    session.headers.update({'token': token})
                # Test query system info
                info_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=5, verify=False, allow_redirects=False)
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
        elif ig['type'] == 'snmp':
            community = ig['username'] or 'public'
            success_name, val_name = check_snmp(ig['host'], ig['port'] or 161, community, "1.3.6.1.2.1.1.5.0")
            if success_name:
                _, val_descr = check_snmp(ig['host'], ig['port'] or 161, community, "1.3.6.1.2.1.1.1.0")
                descr_str = f" ({val_descr})" if val_descr else ""
                return jsonify({'success': True, 'message': f"Connected to SNMP OLT! System Name: {val_name}{descr_str}"})
            else:
                success_descr, val_descr = check_snmp(ig['host'], ig['port'] or 161, community, "1.3.6.1.2.1.1.1.0")
                if success_descr:
                    return jsonify({'success': True, 'message': f"Connected to SNMP OLT! System Descr: {val_descr}"})
                return jsonify({'success': False, 'error': f"SNMP connection failed: {val_name}"})
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
            # Gunakan cached connection agar tidak login/logout setiap 3 detik
            from helpers import get_cached_mikrotik
            api = get_cached_mikrotik(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
            active = list(api('/ppp/active/print'))
            ifaces = list(api('/interface/print'))
            # Tidak perlu api.close() — koneksi di-keep untuk polling berikutnya

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

import json
import time
import redis
import hashlib

# Cache fallbacks
_local_cache = {}

def _get_redis_client():
    try:
        return redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
    except Exception:
        return None

def _cache_get(key):
    r = _get_redis_client()
    if r:
        try:
            val = r.get(key)
            if val is not None:
                return val.decode('utf-8')
        except Exception:
            pass
    
    # Fallback to local memory cache
    expiry_time = _local_cache.get(f"{key}:expiry")
    if expiry_time and time.time() > expiry_time:
        _local_cache.pop(key, None)
        _local_cache.pop(f"{key}:expiry", None)
        return None
    return _local_cache.get(key)

def _cache_set(key, val, ex=None):
    r = _get_redis_client()
    if r:
        try:
            if ex:
                r.setex(key, ex, val)
            else:
                r.set(key, val)
            return
        except Exception:
            pass
    
    _local_cache[key] = val
    if ex:
        _local_cache[f"{key}:expiry"] = time.time() + ex

def _cache_delete(key):
    r = _get_redis_client()
    if r:
        try:
            r.delete(key)
            return
        except Exception:
            pass
    _local_cache.pop(key, None)
    _local_cache.pop(f"{key}:expiry", None)

def _get_olt_session(olt):
    olt_id = olt['id']
    host = olt['host']
    port = olt['port']
    is_https = False
    if host.startswith('https://'):
        is_https = True
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]
    if str(port) == '443':
        is_https = True
    if is_https and str(port) == '80':
        port = 443
    scheme = 'https' if is_https else 'http'
    url = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
    url = url.rstrip('/')

    session = requests.Session()
    
    session_cache_key = f"tenda_olt_session:{olt_id}"
    cached_session_data = _cache_get(session_cache_key)
    
    cookies = {}
    token = None
    
    if cached_session_data:
        try:
            s_data = json.loads(cached_session_data)
            cookies = s_data.get('cookies', {})
            token = s_data.get('token')
        except Exception:
            pass

    if cookies and token:
        for k, v in cookies.items():
            session.cookies.set(k, v)
        session.headers.update({'token': token})
        return session, url, False
    
    # Fresh login
    password_md5 = hashlib.md5(f"an3400{olt['password']}".encode()).hexdigest()
    login_payload = {"login": {"username": olt['username'], "password": password_md5}}
    login_resp = session.post(f"{url}/setModules", json=login_payload, timeout=4, verify=False, allow_redirects=False)
    if login_resp.status_code != 200:
        raise Exception(f"Login HTTP status {login_resp.status_code}")
    
    token = login_resp.headers.get('Token')
    if token:
        session.headers.update({'token': token})
    
    cookies_dict = session.cookies.get_dict()
    session_data = {
        'cookies': cookies_dict,
        'token': token
    }
    _cache_set(session_cache_key, json.dumps(session_data), ex=900) # 15 minutes TTL
    
    return session, url, True

@integrations_bp.route('/api/tenda-olt/status')
@login_required
def api_tenda_olt_status():
    olt_id = request.args.get('olt_id', type=int)
    
    # 1. Check response status cache (TTL 25 seconds)
    cache_key = f"tenda_olt_status_resp:{olt_id if olt_id else 'all'}"
    cached_resp = _cache_get(cache_key)
    if cached_resp:
        try:
            return jsonify(json.loads(cached_resp))
        except Exception:
            pass

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
            session, url, did_login = _get_olt_session(olt)
            
            # Try system info
            try:
                sys_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=4, verify=False, allow_redirects=False)
                if not did_login and (sys_resp.status_code in (302, 401, 403) or 'login' in sys_resp.text.lower()):
                    # Stale session. Clear cache and try fresh login once.
                    _cache_delete(f"tenda_olt_session:{olt['id']}")
                    session, url, did_login = _get_olt_session(olt)
                    sys_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=4, verify=False, allow_redirects=False)
            except Exception:
                if not did_login:
                    _cache_delete(f"tenda_olt_session:{olt['id']}")
                    session, url, did_login = _get_olt_session(olt)
                    sys_resp = session.post(f"{url}/getModules", json={"deviceInfo": "", "customInfo": ""}, timeout=4, verify=False, allow_redirects=False)
                else:
                    raise

            sys_data = sys_resp.json() if sys_resp.status_code == 200 else {}
            dev_info = sys_data.get('deviceInfo', {})

            # Get PON stats
            try:
                pon_resp = session.post(f"{url}/getModules", json={"ponStatistics": "", "ontNumInfo": "", "oltLightInfo": {"port": "pon"}}, timeout=4, verify=False, allow_redirects=False)
                if not did_login and (pon_resp.status_code in (302, 401, 403) or 'login' in pon_resp.text.lower()):
                    _cache_delete(f"tenda_olt_session:{olt['id']}")
                    session, url, did_login = _get_olt_session(olt)
                    pon_resp = session.post(f"{url}/getModules", json={"ponStatistics": "", "ontNumInfo": "", "oltLightInfo": {"port": "pon"}}, timeout=4, verify=False, allow_redirects=False)
            except Exception:
                if not did_login:
                    _cache_delete(f"tenda_olt_session:{olt['id']}")
                    session, url, did_login = _get_olt_session(olt)
                    pon_resp = session.post(f"{url}/getModules", json={"ponStatistics": "", "ontNumInfo": "", "oltLightInfo": {"port": "pon"}}, timeout=4, verify=False, allow_redirects=False)
                else:
                    raise

            pon_data = pon_resp.json() if pon_resp.status_code == 200 else {}
            pon_stats = pon_data.get('ponStatistics', [])
            ont_info = pon_data.get('ontNumInfo', [])
            light_info = pon_data.get('oltLightInfo', [])

            ports_data = []
            stats_by_pon = {item.get('ponNumber'): item for item in pon_stats}
            ont_by_pon = {item.get('ponPort'): item for item in ont_info}
            light_by_pon = {item.get('port'): item for item in light_info}

            for pon_id in (1, 2):
                p_stat = stats_by_pon.get(pon_id, {})
                p_ont = ont_by_pon.get(pon_id, {})
                p_light = light_by_pon.get(f"1/{pon_id}", {})
                
                ports_data.append({
                    'pon_id': pon_id,
                    'name': f"PON {pon_id}",
                    'up_speed': p_stat.get('upSpeed', '0.00'),
                    'down_speed': p_stat.get('downSpeed', '0.00'),
                    'ont_online': p_ont.get('ontOnline', 0),
                    'ont_offline': p_ont.get('ontOffline', 0),
                    'ont_total': p_ont.get('ontTotal', 0),
                    'sfp_vendor': p_light.get('vendor', '').strip(),
                    'sfp_sn': p_light.get('sn', '').strip(),
                    'sfp_temp': p_light.get('temperature', 'N/A'),
                    'sfp_bias': p_light.get('biasCurrent', 'N/A'),
                    'sfp_voltage': p_light.get('voltage', 'N/A'),
                    'sfp_tx_power': p_light.get('sendPower', 'N/A')
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

    output_data = {'success': True, 'data': results}
    # Cache the final response for 5 seconds to give a real-time feel
    _cache_set(cache_key, json.dumps(output_data), ex=5)

    return jsonify(output_data)
