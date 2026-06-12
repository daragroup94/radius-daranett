import os
import requests
import json as _json
from flask import current_app

def get_genieacs_config():
    """
    Mengambil konfigurasi GenieACS dari database integrations.
    Jika tidak aktif/tidak ada, return fallback menggunakan environment variables.
    """
    try:
        from database import execute_query
        rows = execute_query("SELECT host, port, username, password FROM integrations WHERE type='genieacs' AND is_active=true ORDER BY id DESC LIMIT 1")
        if rows:
            host = rows[0]['host']
            port = rows[0]['port']
            username = rows[0]['username']
            password = rows[0]['password']
            if not host.startswith(('http://', 'https://')):
                host = f"http://{host}"
            url = f"{host}:{port}" if port else host
            auth = (username, password) if username else None
            return {'url': url, 'auth': auth}
    except Exception as e:
        print(f"Error querying GenieACS config: {e}")
        
    # Fallback to environment variables
    url = os.getenv('GENIEACS_NBI_URL', 'http://10.204.0.22:7557')
    return {'url': url, 'auth': None}

def get_genieacs_device_info(username):
    """
    Query GenieACS NBI untuk info device berdasarkan pppoeUsername dengan caching Redis (30 detik).
    """
    if not username:
        return {'found': False, 'message': 'Username kosong'}
        
    cache_key = f"genieacs:device:{username}"
    
    # Try fetching Redis client from application context, fallback to Config if outside context
    redis_client = None
    try:
        redis_client = current_app.config.get('SESSION_REDIS')
    except RuntimeError:
        try:
            from config import Config
            redis_client = Config.SESSION_REDIS
        except:
            pass
    
    # 1. Coba ambil dari Redis Cache
    if redis_client:
        try:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                return _json.loads(cached_data)
        except Exception as e:
            print(f"Redis cache GET error: {e}")
            
    # 2. Jika tidak ada di cache, query GenieACS
    try:
        config = get_genieacs_config()
        GENIEACS_NBI = config['url']
        auth = config['auth']
        query = _json.dumps({
            "$or": [
                {"VirtualParameters.pppoeUsername._value": username},
                {"VirtualParameters.pppoeUsername2._value": username},
                {"_deviceId._SerialNumber": username},
                {"VirtualParameters.getSerialNumber._value": username}
            ]
        })
        projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeIP,VirtualParameters.getdeviceuptime,VirtualParameters.getpppuptime,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,InternetGatewayDevice.DeviceInfo.UpTime,Device.DeviceInfo.UpTime,InternetGatewayDevice.WANDevice.1.WANConnectionDevice'
        
        resp = requests.get(
            f'{GENIEACS_NBI}/devices',
            params={'query': query, 'projection': projection},
            auth=auth,
            timeout=3
        )
        
        if resp.status_code != 200:
            return {'found': False, 'message': f'GenieACS API error ({resp.status_code})'}
            
        devices = resp.json()
        if not devices:
            res_data = {'found': False, 'message': 'Device not found in GenieACS'}
            # Cache negative result for 15 seconds to prevent spamming GenieACS
            if redis_client:
                try:
                    redis_client.setex(cache_key, 15, _json.dumps(res_data))
                except:
                    pass
            return res_data
            
        d = devices[0]
        vp = d.get('VirtualParameters', {})
        di = d.get('DeviceInfo', {})
        dev_id = d.get('_deviceId', {})
        
        def val(obj):
            return obj.get('_value', 'N/A') if isinstance(obj, dict) else 'N/A'
            
        rx = val(vp.get('RXPower', {}))
        
        # Determine status
        try:
            rx_float = float(rx)
            if rx_float >= -23.0:
                rx_status = 'good'
                rx_status_emoji = "🟢 Bagus (Optimum)"
            elif -27.0 <= rx_float < -23.0:
                rx_status = 'warning'
                rx_status_emoji = "🟡 Sedang (Warning)"
            else:
                rx_status = 'bad'
                rx_status_emoji = "🔴 Buruk (Loss/Drop)"
        except (ValueError, TypeError):
            rx_status = 'unknown'
            rx_status_emoji = "⚪ Unknown"
            
        # Parse uptime
        ont_uptime_sec = None
        igd_uptime = d.get('InternetGatewayDevice', {}).get('DeviceInfo', {}).get('UpTime', {})
        dev_uptime = d.get('Device', {}).get('DeviceInfo', {}).get('UpTime', {})
        
        if isinstance(igd_uptime, dict) and '_value' in igd_uptime:
            ont_uptime_sec = igd_uptime['_value']
        elif isinstance(dev_uptime, dict) and '_value' in dev_uptime:
            ont_uptime_sec = dev_uptime['_value']
        elif 'InternetGatewayDevice.DeviceInfo.UpTime' in d:
            val_obj = d['InternetGatewayDevice.DeviceInfo.UpTime']
            ont_uptime_sec = val_obj.get('_value') if isinstance(val_obj, dict) else val_obj
        elif 'Device.DeviceInfo.UpTime' in d:
            val_obj = d['Device.DeviceInfo.UpTime']
            ont_uptime_sec = val_obj.get('_value') if isinstance(val_obj, dict) else val_obj
            
        ont_uptime_str = "N/A"
        ont_uptime_days = 0
        if ont_uptime_sec is not None:
            try:
                uptime_val = int(ont_uptime_sec)
                ont_uptime_days = uptime_val // 86400
                hours, remainder = divmod(uptime_val % 86400, 3600)
                minutes, _ = divmod(remainder, 60)
                ont_uptime_str = f"{ont_uptime_days}d {hours}h {minutes}m"
            except:
                pass
                
        # Extract WAN MAC Address
        mac_addr = 'N/A'
        try:
            wcd = d.get('InternetGatewayDevice', {}).get('WANDevice', {}).get('1', {}).get('WANConnectionDevice', {})
            for wcd_idx, wcd_val in wcd.items():
                if not wcd_idx.isdigit():
                    continue
                # check IP connection
                ip_conn = wcd_val.get('WANIPConnection', {})
                for i_idx, i_val in ip_conn.items():
                    if not i_idx.isdigit():
                        continue
                    val_mac = i_val.get('MACAddress', {})
                    if isinstance(val_mac, dict) and '_value' in val_mac and val_mac['_value']:
                        mac_addr = val_mac['_value']
                        break
                if mac_addr != 'N/A':
                    break
                # check PPP connection
                ppp_conn = wcd_val.get('WANPPPConnection', {})
                for p_idx, p_val in ppp_conn.items():
                    if not p_idx.isdigit():
                        continue
                    val_mac = p_val.get('MACAddress', {})
                    if isinstance(val_mac, dict) and '_value' in val_mac and val_mac['_value']:
                        mac_addr = val_mac['_value']
                        break
                if mac_addr != 'N/A':
                    break
        except Exception as mac_err:
            print(f"Error parsing MAC address from document: {mac_err}")
                
        res_data = {
            'found': True,
            'serial': val(vp.get('getSerialNumber', {})) or dev_id.get('_SerialNumber', 'N/A'),
            'manufacturer': dev_id.get('_Manufacturer', 'N/A'),
            'model': dev_id.get('_ProductClass', 'N/A'),
            'firmware': val(di.get('SoftwareVersion', {})),
            'rx_power': rx,
            'rx_status': rx_status,
            'rx_status_emoji': rx_status_emoji,
            'pppoe_ip': val(vp.get('pppoeIP', {})),
            'pppoe_username': val(vp.get('pppoeUsername', {})),
            'device_uptime': val(vp.get('getdeviceuptime', {})),
            'ppp_uptime': val(vp.get('getpppuptime', {})),
            'temperature': val(vp.get('gettemp', {})),
            'pon_mode': val(vp.get('getponmode', {})),
            'last_inform': d.get('_lastInform', 'N/A'),
            'ont_uptime_str': ont_uptime_str,
            'ont_uptime_days': ont_uptime_days,
            'mac_address': mac_addr,
            'id': d.get('_id')
        }
        
        # Cache hasil selama 30 detik
        if redis_client:
            try:
                redis_client.setex(cache_key, 30, _json.dumps(res_data))
            except Exception as e:
                print(f"Redis cache SET error: {e}")
                
        return res_data
        
    except requests.exceptions.ConnectionError:
        return {'found': False, 'message': 'GenieACS tidak dapat dihubungi'}
    except Exception as e:
        return {'found': False, 'message': str(e)}

def run_wifi_scan(device_id):
    """
    Memerintahkan ONT melakukan scanning jaringan Wi-Fi tetangga via GenieACS.
    """
    import urllib.parse
    import time
    
    config = get_genieacs_config()
    url = config['url']
    auth = config['auth']
    
    # 1. Hapus task pending untuk menghindari antrean bertumpuk
    try:
        resp = requests.get(f"{url}/tasks", params={"query": _json.dumps({"device": device_id})}, auth=auth, timeout=5)
        if resp.status_code == 200:
            for t in resp.json():
                requests.delete(f"{url}/tasks/{t['_id']}", auth=auth, timeout=5)
    except Exception as e:
        print(f"Error clearing tasks: {e}")

    # Cek WLAN mana saja yang aktif
    wlan1_path = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1"
    wlan5_path = "InternetGatewayDevice.LANDevice.1.WLANConfiguration.5"
    
    # Ambil document device terlebih dahulu untuk memastikan path WLAN ada
    has_wlan1 = False
    has_wlan5 = False
    cr_url = None
    try:
        dev_resp = requests.get(f"{url}/devices", params={"query": _json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        if dev_resp.status_code == 200 and dev_resp.json():
            dev_doc = dev_resp.json()[0]
            wlan_conf = dev_doc.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
            if "1" in wlan_conf:
                has_wlan1 = True
            if "5" in wlan_conf:
                has_wlan5 = True
                
            for p in ["InternetGatewayDevice", "Device"]:
                cr_obj = dev_doc.get(p, {}).get("ManagementServer", {}).get("ConnectionRequestURL", {})
                if isinstance(cr_obj, dict) and cr_obj.get('_value'):
                    cr_url = cr_obj['_value']
                    break
    except Exception as e:
        print(f"Error checking WLAN existence: {e}")
        has_wlan1 = True # default fallback
        
    tasks_queued = []
    task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks"
    
    # Kita buat setParameterValues untuk WLAN yang ada
    param_values = []
    if has_wlan1:
        param_values.append([f"{wlan1_path}.DiagnosticsState", "Requested"])
    if has_wlan5:
        param_values.append([f"{wlan5_path}.DiagnosticsState", "Requested"])
        
    if not param_values:
        return {'success': False, 'message': 'Tidak ada interface WLAN yang ditemukan.'}
        
    payload = {
        "name": "setParameterValues",
        "parameterValues": param_values
    }
    
    task_id = None
    try:
        resp = requests.post(task_url, json=payload, auth=auth, timeout=10)
        if resp.status_code in (200, 201, 202):
            task_id = resp.json().get('_id')
    except requests.exceptions.ReadTimeout:
        # Timeout bisa terjadi tapi task tetap masuk
        time.sleep(2)
        try:
            t_resp = requests.get(f"{url}/tasks", params={"query": _json.dumps({"device": device_id})}, auth=auth, timeout=5)
            if t_resp.status_code == 200 and t_resp.json():
                task_id = t_resp.json()[0]['_id']
        except:
            pass
    except Exception as e:
        return {'success': False, 'message': f'Gagal mengirim perintah scan: {e}'}
        
    # Trigger Connection Request
    try:
        if cr_url:
            requests.get(cr_url, auth=requests.auth.HTTPDigestAuth('msn', 'msn'), timeout=8)
    except Exception as cr_err:
        print(f"Connection Request trigger error: {cr_err}")
        
    # Tunggu task setParameterValues selesai (maksimal 15 detik)
    task_done = False
    if task_id:
        for _ in range(10):
            time.sleep(1.5)
            try:
                chk = requests.get(f"{url}/tasks", params={"query": _json.dumps({"_id": task_id})}, auth=auth, timeout=5)
                if chk.status_code == 200:
                    if not chk.json():
                        task_done = True
                        break
                    else:
                        task_data = chk.json()[0]
                        if 'fault' in task_data:
                            print(f"Task scan error: {task_data['fault']}")
                            break
            except Exception as chk_err:
                print(f"Error checking task status: {chk_err}")
                
    # Tunggu 3 detik tambahan agar ONT memproses scan internal
    time.sleep(3)
    
    # Sekarang kita queue getParameterValues untuk table Neighbor
    gp_params = []
    if has_wlan1:
        gp_params.append(f"{wlan1_path}.Neighbor.")
    if has_wlan5:
        gp_params.append(f"{wlan5_path}.Neighbor.")
        
    gp_payload = {
        "name": "getParameterValues",
        "parameterNames": gp_params
    }
    
    gp_task_id = None
    try:
        resp = requests.post(task_url, json=gp_payload, auth=auth, timeout=10)
        if resp.status_code in (200, 201, 202):
            gp_task_id = resp.json().get('_id')
    except Exception as e:
        print(f"Error queuing getParameterValues: {e}")
        
    # Trigger Connection Request lagi
    try:
        if cr_url:
            requests.get(cr_url, auth=requests.auth.HTTPDigestAuth('msn', 'msn'), timeout=8)
    except:
        pass
        
    # Tunggu getParameterValues selesai (maksimal 15 detik)
    if gp_task_id:
        for _ in range(10):
            time.sleep(1.5)
            try:
                chk = requests.get(f"{url}/tasks", params={"query": _json.dumps({"_id": gp_task_id})}, auth=auth, timeout=5)
                if chk.status_code == 200 and not chk.json():
                    break
            except:
                pass
                
    # Ambil data terbaru dari device document
    try:
        dev_resp = requests.get(f"{url}/devices", params={"query": _json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        if dev_resp.status_code == 200 and dev_resp.json():
            return {'success': True, 'device': dev_resp.json()[0]}
    except Exception as e:
        return {'success': False, 'message': f'Gagal mengunduh hasil scan: {e}'}
        
    return {'success': False, 'message': 'Tidak dapat mengambil hasil scan dari ONT.'}

def analyze_wifi_neighbors(device_doc):
    """
    Menganalisis data Neighbor untuk frekuensi 2.4 GHz dan 5 GHz,
    menghitung tingkat interferensi, dan merekomendasikan channel terbaik.
    """
    neighbors_2g = []
    neighbors_5g = []
    
    # Ambil current channel untuk 2.4G dan 5G
    wlan_conf = device_doc.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
    
    current_2g_channel = "Auto"
    current_5g_channel = "Auto"
    
    # Ambil channel 2.4G (WLAN 1)
    if "1" in wlan_conf:
        ch = wlan_conf["1"].get("Channel", {}).get("_value", "0")
        auto_ch = wlan_conf["1"].get("AutoChannelEnable", {}).get("_value", False)
        current_2g_channel = "Auto" if auto_ch == True or auto_ch == "true" or ch == "0" or ch == 0 else str(ch)
        
    # Ambil channel 5G (WLAN 5)
    if "5" in wlan_conf:
        ch = wlan_conf["5"].get("Channel", {}).get("_value", "0")
        auto_ch = wlan_conf["5"].get("AutoChannelEnable", {}).get("_value", False)
        current_5g_channel = "Auto" if auto_ch == True or auto_ch == "true" or ch == "0" or ch == 0 else str(ch)

    # Iterasi semua WLANConfiguration untuk mencari Neighbor
    for wlan_idx, wlan_val in wlan_conf.items():
        if not wlan_idx.isdigit():
            continue
        neighbor_table = wlan_val.get("Neighbor", {})
        for n_idx, n_val in neighbor_table.items():
            if not n_idx.isdigit():
                continue
                
            def val(obj):
                return obj.get('_value') if isinstance(obj, dict) else obj
                
            ssid = val(n_val.get("SSID")) or "Hidden SSID"
            bssid = val(n_val.get("BSSID")) or "N/A"
            
            try:
                channel = int(val(n_val.get("Channel")) or 0)
            except:
                channel = 0
                
            rssi = -95
            for rssi_key in ("SignalStrength", "RSSI", "Signal", "SignalStrengthScore"):
                if rssi_key in n_val:
                    try:
                        rssi = int(val(n_val[rssi_key]))
                        break
                    except:
                        pass
            
            if channel == 0:
                continue
                
            neighbor_info = {
                'ssid': ssid,
                'bssid': bssid,
                'channel': channel,
                'rssi': rssi
            }
            
            if channel <= 14:
                neighbors_2g.append(neighbor_info)
            else:
                neighbors_5g.append(neighbor_info)
                
    # ANALISIS 2.4 GHz
    # Channel target yang disarankan untuk 2.4G: 1, 6, 11 (Non-overlapping)
    # Kita hitung skor interferensi untuk masing-masing
    interf_2g = {1: 0, 6: 0, 11: 0}
    for target in [1, 6, 11]:
        for n in neighbors_2g:
            ch = n['channel']
            rssi = n['rssi']
            diff = abs(ch - target)
            if diff < 5:
                overlap_factor = (5 - diff) / 5
                weight = max(0, rssi + 100)
                interf_2g[target] += weight * overlap_factor
                
    # Rekomendasi 2.4 GHz: cari dengan score terkecil
    best_2g_channel = min(interf_2g, key=interf_2g.get)
    
    # ANALISIS 5 GHz
    # Channel target yang disarankan untuk 5G: 36, 40, 44, 48, 149, 153, 157, 161
    targets_5g = [36, 40, 44, 48, 149, 153, 157, 161]
    interf_5g = {t: 0 for t in targets_5g}
    for target in targets_5g:
        for n in neighbors_5g:
            ch = n['channel']
            rssi = n['rssi']
            if ch == target:
                interf_5g[target] += max(0, rssi + 100)
            elif abs(ch - target) <= 4:
                interf_5g[target] += max(0, rssi + 100) * 0.5
                
    best_5g_channel = min(interf_5g, key=interf_5g.get)
    
    return {
        'neighbors_2g_count': len(neighbors_2g),
        'neighbors_5g_count': len(neighbors_5g),
        'neighbors_2g': sorted(neighbors_2g, key=lambda x: x['rssi'], reverse=True)[:10], # 10 terkuat
        'neighbors_5g': sorted(neighbors_5g, key=lambda x: x['rssi'], reverse=True)[:10],
        'current_2g_channel': current_2g_channel,
        'current_5g_channel': current_5g_channel,
        'best_2g_channel': best_2g_channel,
        'best_5g_channel': best_5g_channel,
        'scores_2g': interf_2g,
        'scores_5g': interf_5g
    }

def set_wifi_channel(device_id, wlan_instance, channel):
    """
    Menerapkan channel Wi-Fi tertentu ke ONT.
    """
    import urllib.parse
    config = get_genieacs_config()
    url = config['url']
    auth = config['auth']
    
    task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
    
    # Standard: AutoChannelEnable = False, Channel = channel
    payload = {
        "name": "setParameterValues",
        "parameterValues": [
            [f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{wlan_instance}.AutoChannelEnable", False],
            [f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{wlan_instance}.Channel", int(channel)]
        ]
    }
    
    try:
        resp = requests.post(task_url, json=payload, auth=auth, timeout=15)
        if resp.status_code in (200, 201, 202):
            return True, f"Channel {channel} berhasil dikirim ke antrean ONT."
        else:
            return False, f"Respon API GenieACS: {resp.status_code}"
    except Exception as e:
        return False, str(e)
