import os
import time
import socket
import requests
import librouteros
import subprocess
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from flask import Blueprint, render_template, request, jsonify, current_app
from decorators import login_required
from database import execute_query
from helpers import get_request_data, ping_nas
from services.mikrotik_coa import send_coa_disconnect

diagnostics_bp = Blueprint('diagnostics', __name__)

@diagnostics_bp.route('/diagnostics')
@login_required
def diagnostics_page():
    routers = execute_query("SELECT id, name, host FROM integrations WHERE type='mikrotik' AND is_active=true ORDER BY id") or []
    return render_template('diagnostics.html', routers=routers)

@diagnostics_bp.route('/optical-health')
@login_required
def optical_health_page():
    return render_template('optical-health.html')

@diagnostics_bp.route('/api/acs/reboot/<username>', methods=['POST'])
@login_required
def api_acs_reboot_user(username):
    try:
        from services.genieacs import get_genieacs_config
        config = get_genieacs_config()
        GENIEACS_NBI = config['url']
        auth = config['auth']
        import json as _json
        query = _json.dumps({
            "$or": [
                {"VirtualParameters.pppoeUsername._value": username},
                {"VirtualParameters.pppoeUsername2._value": username}
            ]
        })
        resp_acs = requests.get(
            f'{GENIEACS_NBI}/devices',
            params={'query': query, 'projection': '_id'},
            auth=auth,
            timeout=3
        )
        devices = resp_acs.json()
        if not devices:
            return jsonify({'success': False, 'error': f'ONT untuk user {username} tidak ditemukan di GenieACS'}), 404
            
        device_id = devices[0].get('_id')
        if not device_id:
            return jsonify({'success': False, 'error': 'ID Perangkat tidak valid di GenieACS'}), 400
            
        import urllib.parse
        reboot_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
        
        try:
            resp_reboot = requests.post(reboot_url, json={"name": "reboot"}, auth=auth, timeout=10)
            if resp_reboot.status_code in (200, 201, 202):
                return jsonify({'success': True, 'message': 'Perintah reboot berhasil terkirim!'})
            else:
                return jsonify({'success': False, 'error': f'ACS API error: {resp_reboot.status_code}'}), 500
        except requests.exceptions.Timeout:
            return jsonify({'success': True, 'message': 'Koneksi lambat, namun perintah reboot telah masuk antrean di GenieACS.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/acs/optical-health/issues')
@login_required
def api_optical_health_issues():
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_health_issues (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                last_inform VARCHAR(100),
                detected_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        rows = execute_query("""
            SELECT username, serial, model, manufacturer, rx_power, temperature,
                   TO_CHAR(detected_at, 'YYYY-MM-DD HH24:MI:SS') as detected_at,
                   last_inform, 'critical' as rx_status
            FROM ont_health_issues
            ORDER BY detected_at DESC
        """) or []
        
        for r in rows:
            rx = r['rx_power']
            rx_status = 'critical'
            try:
                rx_clean = rx.replace('dBm', '').strip()
                rx_float = float(rx_clean)
                if rx_float >= -23.0:
                    rx_status = 'good'
                elif -27.0 <= rx_float < -23.0:
                    rx_status = 'warning'
                else:
                    rx_status = 'critical'
            except (ValueError, TypeError):
                if 'loss' in str(rx).lower():
                    rx_status = 'critical'
                else:
                    rx_status = 'unknown'
            r['rx_status'] = rx_status
            
        return jsonify({
            'success': True,
            'devices': rows
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/acs/optical-health')
@login_required
def api_optical_health():
    try:
        from services.genieacs import get_genieacs_config
        config = get_genieacs_config()
        GENIEACS_NBI = config['url']
        auth = config['auth']
        projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber'
        
        resp = requests.get(
            f'{GENIEACS_NBI}/devices',
            params={'projection': projection},
            auth=auth,
            timeout=8
        )
        devices = resp.json()
        if not isinstance(devices, list):
            return jsonify({'success': False, 'error': 'Respon tidak valid dari GenieACS'}), 500
            
        # Load manual serial mappings from database
        try:
            execute_query("""
                CREATE TABLE IF NOT EXISTS public.ont_serial_mapping (
                    username VARCHAR(100) PRIMARY KEY,
                    serial_number VARCHAR(100) UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """, fetch=False)
            mappings = execute_query("SELECT username, serial_number FROM ont_serial_mapping") or []
        except Exception:
            mappings = []
        serial_map = {m['serial_number'].strip(): m['username'].strip() for m in mappings if m['serial_number'] and m['username']}
        
        results = []
        stats = {'total': 0, 'good': 0, 'warning': 0, 'critical': 0, 'unknown': 0}
        
        def val(obj):
            if isinstance(obj, dict):
                return obj.get('_value', 'N/A')
            return obj if obj is not None else 'N/A'
            
        for d in devices:
            vp = d.get('VirtualParameters', {})
            di = d.get('DeviceInfo', {})
            dev_id = d.get('_deviceId', {})
            
            serial = val(vp.get('getSerialNumber')) or dev_id.get('_SerialNumber', 'N/A')
            serial_clean = serial.strip() if serial else 'N/A'
            
            username = val(vp.get('pppoeUsername'))
            if not username or username == 'N/A' or username == '':
                username = val(vp.get('pppoeUsername2'))
                
            if not username or username == 'N/A' or username == '':
                if serial_clean in serial_map:
                    username = serial_map[serial_clean]
                else:
                    username = f"unconfigured-{serial_clean}"
            rx = val(vp.get('RXPower'))
            temp = val(vp.get('gettemp'))
            pon_mode = val(vp.get('getponmode'))
            serial = val(vp.get('getSerialNumber')) or dev_id.get('_SerialNumber', 'N/A')
            model = dev_id.get('_ProductClass', 'N/A')
            manufacturer = dev_id.get('_Manufacturer', 'N/A')
            
            rx_valid = rx and rx not in ('N/A', '', None)
            if not rx_valid:
                continue
                
            rx_status = 'unknown'
            rx_float = None
            if rx != 'N/A':
                try:
                    rx_clean = rx.replace('dBm', '').strip()
                    rx_float = float(rx_clean)
                    if rx_float >= -23.0:
                        rx_status = 'good'
                        stats['good'] += 1
                    elif -27.0 <= rx_float < -23.0:
                        rx_status = 'warning'
                        stats['warning'] += 1
                    else:
                        rx_status = 'critical'
                        stats['critical'] += 1
                except (ValueError, TypeError):
                    rx_status = 'unknown'
                    stats['unknown'] += 1
            else:
                stats['unknown'] += 1
                
            stats['total'] += 1
            results.append({
                'deviceId': d.get('_id'),
                'username': username,
                'serial': serial,
                'model': model,
                'manufacturer': manufacturer,
                'rx_power': rx,
                'rx_float': rx_float,
                'rx_status': rx_status,
                'temperature': temp,
                'pon_mode': pon_mode,
                'last_inform': d.get('_lastInform', 'N/A')
            })
            
        return jsonify({
            'success': True,
            'stats': stats,
            'devices': results
        })
    except requests.exceptions.ConnectionError:
        return jsonify({'success': False, 'error': 'GenieACS tidak dapat dihubungi'}), 503
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/topology')
@login_required
def topology_page():
    return render_template('topology.html')

@diagnostics_bp.route('/ipam')
@login_required
def ipam_page():
    return render_template('ipam.html')

@diagnostics_bp.route('/api/ipam/pools')
@login_required
def api_ipam_pools():
    def parse_ip_range(range_str):
        ips = []
        try:
            for part in range_str.split(','):
                part = part.strip()
                if '/' in part:
                    net = ipaddress.ip_network(part, strict=False)
                    for ip in net.hosts():
                        ips.append(str(ip))
                elif '-' in part:
                    start_str, end_str = part.split('-')
                    start_str = start_str.strip()
                    end_str = end_str.strip()
                    if '.' not in end_str:
                        prefix = start_str.rsplit('.', 1)[0]
                        end_str = f"{prefix}.{end_str}"
                    start_ip = ipaddress.IPv4Address(start_str)
                    end_ip = ipaddress.IPv4Address(end_str)
                    curr = start_ip
                    while curr <= end_ip:
                        ips.append(str(curr))
                        curr += 1
                else:
                    ips.append(str(ipaddress.IPv4Address(part)))
        except Exception:
            pass
        return ips

    try:
        active_sessions = execute_query("""
            SELECT username, framedipaddress 
            FROM radacct 
            WHERE acctstoptime IS NULL AND framedipaddress IS NOT NULL
        """) or []
        
        active_ips = {s['framedipaddress']: s['username'] for s in active_sessions}
        
        integrations = execute_query("SELECT * FROM integrations WHERE type='mikrotik' AND is_active=true ORDER BY id") or []
        
        pools_data = []
        errors = []
        
        for ig in integrations:
            try:
                api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
                mk_pools = list(api('/ip/pool/print'))
                mk_leases = list(api('/ip/dhcp-server/lease/print'))
                mk_ppp_active = list(api('/ppp/active/print'))
                api.close()
                
                for l in mk_leases:
                    ip = l.get('active-address') or l.get('address')
                    host = l.get('host-name') or l.get('mac-address') or 'DHCP Client'
                    if ip and ip not in active_ips:
                        active_ips[ip] = f"DHCP: {host}"
                        
                for p in mk_ppp_active:
                    ip = p.get('address')
                    name = p.get('name')
                    if ip and ip not in active_ips:
                        active_ips[ip] = name

                for pool in mk_pools:
                    ranges = pool.get('ranges', '')
                    name = pool.get('name', 'Unnamed Pool')
                    all_ips = parse_ip_range(ranges)
                    
                    used_ips = []
                    free_ips = []
                    render_grid = len(all_ips) <= 1024
                    
                    for ip in all_ips:
                        user = active_ips.get(ip)
                        ip_obj = {'ip': ip, 'status': 'used' if user else 'free', 'username': user or ''}
                        if user:
                            used_ips.append(ip_obj)
                        else:
                            free_ips.append(ip_obj)
                            
                    pools_data.append({
                        'router': ig['name'],
                        'pool_name': name,
                        'ranges': ranges,
                        'total_ips': len(all_ips),
                        'used_count': len(used_ips),
                        'free_count': len(free_ips),
                        'render_grid': render_grid,
                        'grid': (used_ips + free_ips)[:1024] if render_grid else []
                    })
            except Exception as e:
                errors.append({'router': ig['name'], 'error': str(e)})
                
        try:
            static_rows = execute_query("""
                SELECT r.username, r.value as ip_address,
                       (SELECT value FROM radreply r2 WHERE r2.username = r.username AND r2.attribute = 'Description' LIMIT 1) as name
                FROM radreply r
                WHERE r.attribute = 'Framed-IP-Address'
            """) or []
            
            static_subnets = {}
            for row in static_rows:
                ip = row['ip_address'].strip()
                mac = row['username'].strip()
                name = row['name'] or f"Static-{mac}"
                try:
                    parts = ip.split('.')
                    if len(parts) == 4:
                        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"
                        if subnet not in static_subnets:
                            static_subnets[subnet] = {}
                        static_subnets[subnet][ip] = f"{name} ({mac})"
                except Exception:
                    pass
                    
            for subnet, ip_map in static_subnets.items():
                covered = False
                for p in pools_data:
                    if subnet in p['ranges']:
                        covered = True
                        if p['render_grid']:
                            for item in p['grid']:
                                ip = item['ip']
                                if ip in ip_map and not item['username']:
                                    item['status'] = 'used'
                                    item['username'] = ip_map[ip]
                                    p['used_count'] += 1
                                    p['free_count'] -= 1
                        break
                        
                if not covered:
                    grid = []
                    used_count = 0
                    for i in range(1, 255):
                        ip = f"{subnet}.{i}"
                        user = active_ips.get(ip) or ip_map.get(ip)
                        status = 'used' if user else 'free'
                        if user:
                            used_count += 1
                        grid.append({'ip': ip, 'status': status, 'username': user or ''})
                        
                    pools_data.append({
                        'router': 'Database RADIUS (IP Static)',
                        'pool_name': f"Subnet {subnet}.0/24",
                        'ranges': f"{subnet}.1-{subnet}.254",
                        'total_ips': 254,
                        'used_count': used_count,
                        'free_count': 254 - used_count,
                        'render_grid': True,
                        'grid': grid
                    })
        except Exception as e:
            errors.append({'router': 'Database Static IP Scan', 'error': str(e)})

        if not pools_data:
            detected_ips = list(active_ips.keys())
            subnets = {}
            for ip in detected_ips:
                try:
                    parts = ip.split('.')
                    if len(parts) == 4:
                        subnet = f"{parts[0]}.{parts[1]}.{parts[2]}"
                        if subnet not in subnets:
                            subnets[subnet] = []
                        subnets[subnet].append(ip)
                except Exception:
                    pass
            
            for subnet, ips in subnets.items():
                grid = []
                for i in range(1, 255):
                    ip = f"{subnet}.{i}"
                    user = active_ips.get(ip)
                    grid.append({'ip': ip, 'status': 'used' if user else 'free', 'username': user or ''})
                    
                used_count = sum(1 for x in grid if x['status'] == 'used')
                pools_data.append({
                    'router': 'Server RADIUS (Auto-Detected)',
                    'pool_name': f"Subnet {subnet}.0/24",
                    'ranges': f"{subnet}.1-{subnet}.254",
                    'total_ips': 254,
                    'used_count': used_count,
                    'free_count': 254 - used_count,
                    'render_grid': True,
                    'grid': grid
                })
                
        return jsonify({
            'success': True,
            'pools': pools_data,
            'errors': errors
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/topology/status')
@login_required
def api_topology_status():
    # Capture application configuration in the main thread (has context)
    db_host = current_app.config.get('DB_HOST', 'localhost')
    db_port = current_app.config.get('DB_PORT', 5432)
    redis_url = current_app.config.get('REDIS_URL', 'redis://localhost:6379/0')

    def check_latency(host, port=None):
        start = time.perf_counter()
        try:
            if port:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.0)
                res = s.connect_ex((host, port))
                s.close()
                if res == 0:
                    return True, int((time.perf_counter() - start) * 1000)
            else:
                import re as _re
                result = subprocess.run(
                    ["ping", "-c", "1", "-W", "1", host],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2
                )
                if result.returncode == 0:
                    m = _re.search(r'time=([\d.]+)', result.stdout.decode())
                    rtt_ms = round(float(m.group(1))) if m else 1
                    return True, rtt_ms
        except Exception:
            pass
        return False, 999

    def check_postgres():
        db_ok = False
        db_lat = 999
        start = time.perf_counter()
        try:
            execute_query("SELECT 1")
            db_ok = True
            db_lat = int((time.perf_counter() - start) * 1000)
        except Exception:
            pass
        return {
            'id': 'postgres',
            'name': 'Database Server',
            'type': 'database',
            'ip': db_host,
            'online': db_ok,
            'latency': db_lat,
            'info': f"PostgreSQL Port {db_port}"
        }

    def check_redis():
        redis_ok = False
        redis_lat = 999
        start = time.perf_counter()
        try:
            import redis
            r_pool = redis.ConnectionPool.from_url(redis_url, socket_timeout=1)
            r = redis.Redis(connection_pool=r_pool)
            if r.ping():
                redis_ok = True
                redis_lat = int((time.perf_counter() - start) * 1000)
        except Exception:
            pass
        return {
            'id': 'redis',
            'name': 'Redis Cache & Session',
            'type': 'redis',
            'ip': 'localhost',
            'online': redis_ok,
            'latency': redis_lat,
            'info': 'Redis Port 6379'
        }

    def check_freeradius():
        radius_ok = False
        radius_lat = 999
        for host in ['freeradius', 'freeradius-server', '10.204.0.54']:
            ok, lat = check_latency(host)
            if ok:
                radius_ok = True
                radius_lat = lat
                break
        return {
            'id': 'freeradius',
            'name': 'FreeRADIUS Engine',
            'type': 'radius',
            'ip': '10.204.0.54',
            'online': radius_ok,
            'latency': radius_lat,
            'info': 'Ports: 1812 (Auth), 1813 (Acct)'
        }

    def check_genieacs():
        acs_ok = False
        acs_lat = 999
        from services.genieacs import get_genieacs_config
        config = get_genieacs_config()
        acs_url = config['url']
        auth = config['auth']
        start = time.perf_counter()
        try:
            resp = requests.get(f"{acs_url}/devices", params={'limit': 1}, auth=auth, timeout=1.5)
            if resp.status_code == 200:
                acs_ok = True
                acs_lat = int((time.perf_counter() - start) * 1000)
        except Exception:
            pass
        return {
            'id': 'genieacs',
            'name': 'GenieACS Server (TR-069)',
            'type': 'acs',
            'ip': acs_url.replace('http://', '').replace('https://', '').split(':')[0],
            'online': acs_ok,
            'latency': acs_lat,
            'info': f"NBI Endpoint: {acs_url}"
        }

    def check_nas(nas):
        ip = nas['nasname']
        name = nas['shortname'] or ip
        nas_ok, nas_lat = False, 999
        for port in [8728, 8291, 80, 443]:
            ok, lat = check_latency(ip, port=port)
            if ok:
                nas_ok, nas_lat = ok, lat
                break
        if not nas_ok:
            nas_ok = (ping_nas(ip) == 'online')
            nas_lat = 999
        
        try:
            count_res = execute_query("SELECT COUNT(*) as count FROM radacct WHERE nasipaddress = %s AND acctstoptime IS NULL", (ip,))
            active_count = count_res[0]['count'] if count_res else 0
        except Exception:
            active_count = 0
            
        return {
            'id': f"nas-{nas['id']}",
            'name': name,
            'type': 'nas',
            'ip': ip,
            'online': nas_ok,
            'latency': nas_lat,
            'info': f"Clients: {active_count} active | {nas['description'] or ''}"
        }

    try:
        nodes = []
        nas_list = execute_query("SELECT id, nasname, shortname, description FROM nas") or []
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_postgres = executor.submit(check_postgres)
            future_redis = executor.submit(check_redis)
            future_freeradius = executor.submit(check_freeradius)
            future_genieacs = executor.submit(check_genieacs)
            
            future_nas_list = [executor.submit(check_nas, nas) for nas in nas_list]
            
            nodes.append(future_postgres.result())
            nodes.append(future_redis.result())
            nodes.append(future_freeradius.result())
            
            nodes.append({
                'id': 'dashboard',
                'name': 'Flask Web Dashboard',
                'type': 'dashboard',
                'ip': '0.0.0.0',
                'online': True,
                'latency': 0,
                'info': 'Port 5000 (Running)'
            })
            
            nodes.append(future_genieacs.result())
            
            for future_nas in future_nas_list:
                nodes.append(future_nas.result())
            
        return jsonify({
            'success': True,
            'nodes': nodes
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/ipam/conflicts')
@login_required
def api_ipam_conflicts():
    try:
        registered = execute_query("""
            SELECT r.username as mac_address, r.value as ip_address,
                   (SELECT value FROM radreply r2 WHERE r2.username = r.username AND r2.attribute = 'Description' LIMIT 1) as name
            FROM radreply r
            WHERE r.attribute = 'Framed-IP-Address'
        """) or []
        
        import re
        mac_pattern = re.compile(r'^([0-9a-fA-F]{2}[:-]?){5}[0-9a-fA-F]{2}$')
        
        reg_map = {}
        for row in registered:
            ip = row['ip_address'].strip()
            mac = row['mac_address'].strip().lower()
            
            if not mac_pattern.match(mac):
                continue
                
            name = row['name'] or f"Static-{mac}"
            reg_map[ip] = {'mac': mac, 'name': name}
            
        integrations = execute_query("SELECT * FROM integrations WHERE type='mikrotik' AND is_active=true ORDER BY id") or []
        conflicts = []
        
        for ig in integrations:
            try:
                api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
                arp_entries = list(api('/ip/arp/print'))
                api.close()
                
                for entry in arp_entries:
                    ip = entry.get('address', '').strip()
                    mac = entry.get('mac-address', '').strip().lower()
                    
                    if ip in reg_map:
                        expected_mac = reg_map[ip]['mac']
                        clean_mac = "".join(c for c in mac if c.isalnum())
                        clean_expected = "".join(c for c in expected_mac if c.isalnum())
                        
                        if clean_mac and clean_expected and clean_mac != clean_expected:
                            conflicts.append({
                                'ip': ip,
                                'registered_mac': expected_mac,
                                'active_mac': mac,
                                'registered_name': reg_map[ip]['name'],
                                'router': ig['name'],
                                'interface': entry.get('interface', ''),
                                'status': 'Conflict'
                            })
            except Exception:
                pass
                
        return jsonify({'success': True, 'conflicts': conflicts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/diagnostics/ping', methods=['POST'])
@login_required
def api_diagnostics_ping():
    try:
        data = get_request_data()
        router_id = data.get('router_id')
        router_name = data.get('router_name')
        target = data.get('target', '').strip()
        count = data.get('count', '5')
        
        if not target:
            return jsonify({'success': False, 'error': 'Target wajib diisi'}), 400
            
        ig = None
        if router_id:
            ig = execute_query("SELECT * FROM integrations WHERE id = %s AND type='mikrotik'", (router_id,))
        elif router_name:
            ig = execute_query("SELECT * FROM integrations WHERE name = %s AND type='mikrotik'", (router_name,))
            
        if not ig:
            return jsonify({'success': False, 'error': 'Router tidak ditemukan'}), 404
        ig = ig[0]
        
        api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=10)
        ping_res = list(api('/ping', address=target, count=str(count)))
        api.close()
        
        results = []
        for r in ping_res:
            results.append({
                'host': r.get('host', target),
                'size': r.get('size', '56'),
                'ttl': r.get('ttl', '64'),
                'time': r.get('time', '0ms'),
                'status': 'ok' if r.get('received') or r.get('status') is None else r.get('status', 'timeout')
            })
            
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/diagnostics/traceroute', methods=['POST'])
@login_required
def api_diagnostics_traceroute():
    try:
        data = get_request_data()
        router_id = data.get('router_id')
        target = data.get('target', '').strip()
        
        if not router_id or not target:
            return jsonify({'success': False, 'error': 'Router dan Target wajib diisi'}), 400
            
        ig = execute_query("SELECT * FROM integrations WHERE id = %s AND type='mikrotik'", (router_id,))
        if not ig:
            return jsonify({'success': False, 'error': 'Router tidak ditemukan'}), 404
        ig = ig[0]
        
        api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=15)
        trace_res = list(api('/tool/traceroute', address=target, count='1', timeout='1s'))
        api.close()
        
        results = []
        for r in trace_res:
            results.append({
                'hop': r.get('hop', ''),
                'address': r.get('address', '*'),
                'loss': r.get('loss', ''),
                'sent': r.get('sent', ''),
                'time': r.get('last', '') or r.get('avg', '') or 'timeout'
            })
            
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/diagnostics/arp', methods=['GET'])
@login_required
def api_diagnostics_arp():
    try:
        router_id = request.args.get('router_id')
        search = request.args.get('search', '').strip().lower()
        
        if not router_id:
            return jsonify({'success': False, 'error': 'Router ID wajib dipilih'}), 400
            
        ig = execute_query("SELECT * FROM integrations WHERE id = %s AND type='mikrotik'", (router_id,))
        if not ig:
            return jsonify({'success': False, 'error': 'Router tidak ditemukan'}), 404
        ig = ig[0]
        
        api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=5)
        arp_res = list(api('/ip/arp/print'))
        api.close()
        
        results = []
        for r in arp_res:
            ip = r.get('address', '')
            mac = r.get('mac-address', '')
            interface = r.get('interface', '')
            comment = r.get('comment', '')
            
            flags = ''
            if r.get('dynamic'): flags += 'D'
            if r.get('complete') == False: flags += 'I'
            
            if search:
                if search not in ip.lower() and search not in mac.lower() and search not in interface.lower() and search not in comment.lower():
                    continue
                    
            results.append({
                'address': ip,
                'mac_address': mac,
                'interface': interface,
                'comment': comment,
                'published': r.get('published', False),
                'flags': flags
            })
            
        return jsonify({'success': True, 'arp': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/diagnostics/fdb', methods=['GET'])
@login_required
def api_diagnostics_fdb():
    try:
        router_id = request.args.get('router_id')
        search = request.args.get('search', '').strip().lower()
        
        if not router_id:
            return jsonify({'success': False, 'error': 'Router ID wajib dipilih'}), 400
            
        ig = execute_query("SELECT * FROM integrations WHERE id = %s AND type='mikrotik'", (router_id,))
        if not ig:
            return jsonify({'success': False, 'error': 'Router tidak ditemukan'}), 404
        ig = ig[0]
        
        api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=5)
        fdb_res = list(api('/interface/bridge/host/print'))
        api.close()
        
        results = []
        for r in fdb_res:
            mac = r.get('mac-address', '')
            ports = r.get('on-interface', '') or r.get('interface', '')
            bridge = r.get('bridge', '')
            
            flags = ''
            if r.get('dynamic'): flags += 'D'
            if r.get('local'): flags += 'L'
            
            if search:
                if search not in mac.lower() and search not in ports.lower() and search not in bridge.lower():
                    continue
                    
            results.append({
                'mac_address': mac,
                'interface': ports,
                'bridge': bridge,
                'flags': flags,
                'age': r.get('age', '')
            })
            
        return jsonify({'success': True, 'fdb': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@diagnostics_bp.route('/api/diagnostics/kick', methods=['POST'])
@login_required
def api_diagnostics_kick():
    try:
        data = get_request_data()
        mac = data.get('mac', '').strip().lower()
        ip = data.get('ip', '').strip()
        
        if not mac and not ip:
            return jsonify({'success': False, 'error': 'MAC atau IP wajib diisi'}), 400
            
        username = None
        if mac:
            username = mac
        elif ip:
            user_row = execute_query("SELECT username FROM radreply WHERE attribute = 'Framed-IP-Address' AND value = %s LIMIT 1", (ip,))
            if user_row:
                username = user_row[0]['username']
                
        active_sess = []
        if username:
            active_sess = execute_query("SELECT acctsessionid, nasipaddress FROM radacct WHERE LOWER(username) = %s AND acctstoptime IS NULL", (username.lower(),)) or []
            
        disconnected_radius = False
        for sess in active_sess:
            try:
                nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (sess['nasipaddress'],))
                secret = nas[0]['secret'] if nas else 'radiussecret'
                # Menggunakan helper send_coa_disconnect secara bersih untuk menghindari subprocess shell injection
                ok = send_coa_disconnect(sess['nasipaddress'], sess['acctsessionid'], secret)
                if ok:
                    disconnected_radius = True
            except Exception:
                pass
                
        removed_leases = 0
        integrations = execute_query("SELECT * FROM integrations WHERE type='mikrotik' AND is_active=true") or []
        for ig in integrations:
            try:
                api = librouteros.connect(ig['host'], username=ig['username'], password=ig['password'], port=ig['port'], timeout=3)
                leases_to_remove = []
                if mac:
                    leases_to_remove = list(api('/ip/dhcp-server/lease/print', query=f"mac-address={mac.upper()}"))
                elif ip:
                    leases_to_remove = list(api('/ip/dhcp-server/lease/print', query=f"address={ip}"))
                    
                for l in leases_to_remove:
                    api('/ip/dhcp-server/lease/remove', **{'.id': l['.id']})
                    removed_leases += 1
                api.close()
            except Exception:
                pass
                
        return jsonify({
            'success': True,
            'message': f'Berhasil memutus koneksi. Kick RADIUS: {"Sukses" if disconnected_radius else "Tidak aktif/gagal"}, DHCP Lease dihapus: {removed_leases}'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
