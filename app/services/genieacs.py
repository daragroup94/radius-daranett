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

def trigger_connection_request_by_device_id(device_id):
    """
    Mengambil dokumen device dari GenieACS dan memicu Connection Request secara langsung
    dari server lokal ini (karena server GenieACS berada di cloud/remote dan tidak dapat
    menjangkau IP private/lokal ONT).
    """
    try:
        config = get_genieacs_config()
        url = config['url']
        auth = config['auth']
        
        # Ambil document device untuk mencari ConnectionRequestURL dan kredensialnya
        dev_resp = requests.get(f"{url}/devices", params={"query": _json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        if dev_resp.status_code != 200 or not dev_resp.json():
            print(f"Device {device_id} not found in GenieACS to trigger connection request")
            return False, "Device not found"
            
        dev_doc = dev_resp.json()[0]
        
        mgt = None
        for p in ["InternetGatewayDevice", "Device"]:
            if p in dev_doc and "ManagementServer" in dev_doc[p]:
                mgt = dev_doc[p]["ManagementServer"]
                break
                
        if not mgt:
            return False, "ManagementServer not found"
            
        cr_url = mgt.get("ConnectionRequestURL", {}).get("_value")
        username = mgt.get("ConnectionRequestUsername", {}).get("_value", "msn")
        password = mgt.get("ConnectionRequestPassword", {}).get("_value", "msn")
        
        if not cr_url:
            return False, "ConnectionRequestURL is empty"
            
        print(f"Direct Connection Request to {cr_url} using auth {username}:{password}")
        
        from requests.auth import HTTPDigestAuth
        resp = requests.get(cr_url, auth=HTTPDigestAuth(username, password), timeout=5)
        if resp.status_code == 200:
            print("Direct Connection Request succeeded!")
            return True, "Success"
        else:
            print(f"Direct Connection Request returned status: {resp.status_code}")
            return False, f"HTTP Status {resp.status_code}"
    except Exception as e:
        print(f"Direct Connection Request error: {e}")
        return False, str(e)

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
        projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeIP,VirtualParameters.getdeviceuptime,VirtualParameters.getpppuptime,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,InternetGatewayDevice.DeviceInfo.UpTime,Device.DeviceInfo.UpTime,InternetGatewayDevice.WANDevice.1.WANConnectionDevice,InternetGatewayDevice.LANDevice.1.WLANConfiguration,Device.WiFi.AccessPoint'
        
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
                
        # Calculate wifi_active_count from WLANConfiguration / AccessPoint TotalAssociations
        wifi_active_count = 0
        has_wifi_associations = False
        
        try:
            # Check TR-098
            wlan_conf = d.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
            if wlan_conf:
                for ssid_idx, ssid_val in wlan_conf.items():
                    if ssid_idx.isdigit() and isinstance(ssid_val, dict):
                        ta_val = ssid_val.get("TotalAssociations", {}).get("_value", None)
                        if ta_val is not None:
                            try:
                                wifi_active_count += int(ta_val)
                                has_wifi_associations = True
                            except:
                                pass
            
            # Check TR-181
            ap_conf = d.get("Device", {}).get("WiFi", {}).get("AccessPoint", {})
            if ap_conf:
                for ssid_idx, ssid_val in ap_conf.items():
                    if ssid_idx.isdigit() and isinstance(ssid_val, dict):
                        ta_val = ssid_val.get("AssociatedDeviceNumberOfEntries", {}).get("_value", None)
                        if ta_val is None:
                            ta_val = ssid_val.get("TotalAssociations", {}).get("_value", None)
                        if ta_val is not None:
                            try:
                                wifi_active_count += int(ta_val)
                                has_wifi_associations = True
                            except:
                                pass
        except Exception as wifi_err:
            print(f"Error parsing TotalAssociations in device info: {wifi_err}")
                
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
            'wifi_active_count': wifi_active_count if has_wifi_associations else 'N/A',
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
    task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
    
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
            trigger_connection_request_by_device_id(device_id)
    except requests.exceptions.ReadTimeout:
        # Timeout bisa terjadi tapi task tetap masuk
        time.sleep(2)
        try:
            t_resp = requests.get(f"{url}/tasks", params={"query": _json.dumps({"device": device_id})}, auth=auth, timeout=5)
            if t_resp.status_code == 200 and t_resp.json():
                task_id = t_resp.json()[0]['_id']
                trigger_connection_request_by_device_id(device_id)
        except:
            pass
    except Exception as e:
        return {'success': False, 'message': f'Gagal mengirim perintah scan: {e}'}
        
    # Connection Request is handled by GenieACS NBI using the ?connection_request query parameter
        
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
            trigger_connection_request_by_device_id(device_id)
    except Exception as e:
        print(f"Error queuing getParameterValues: {e}")
        
    # Connection Request is handled by GenieACS NBI using the ?connection_request query parameter
        
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
            trigger_connection_request_by_device_id(device_id)
            return True, f"Channel {channel} berhasil dikirim ke antrean ONT."
        else:
            return False, f"Respon API GenieACS: {resp.status_code}"
    except Exception as e:
        return False, str(e)

def get_wlan_clients_realtime(device_id):
    """
    Pemicuan getParameterValues untuk mendapatkan daftar AssociatedDevice dan TotalAssociations terupdate dari ONT,
    kemudian mengembalikan daftar perangkat yang terkoneksi dan jumlah total asosiasi Wi-Fi aktif.
    """
    import urllib.parse
    import time
    
    config = get_genieacs_config()
    url = config['url']
    auth = config['auth']
    
    # 1. Ambil dokumen device untuk mengetahui WLANConfiguration / AccessPoint mana saja yang ada
    wlan_instances = []
    is_tr181 = False
    cr_url = None
    try:
        dev_resp = requests.get(f"{url}/devices", params={"query": _json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        if dev_resp.status_code == 200 and dev_resp.json():
            dev_doc = dev_resp.json()[0]
            
            # Deteksi ModelName untuk penanganan khusus
            model_name = ""
            for p in ["InternetGatewayDevice", "Device"]:
                if p in dev_doc and "DeviceInfo" in dev_doc[p] and "ModelName" in dev_doc[p]["DeviceInfo"]:
                    model_name = dev_doc[p]["DeviceInfo"]["ModelName"].get("_value", "")
                    break

            if "InternetGatewayDevice" in dev_doc:
                wlan_conf = dev_doc.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
                for k in wlan_conf.keys():
                    if k.isdigit():
                        ssid_data = wlan_conf.get(k, {})
                        
                        # Only query if WLAN instance is active/enabled
                        is_enabled = ssid_data.get("Enable", {}).get("_value")
                        is_disabled = False
                        if is_enabled is False:
                            is_disabled = True
                        elif isinstance(is_enabled, str) and is_enabled.lower() in ('false', '0'):
                            is_disabled = True
                        
                        if is_disabled:
                            print(f"[wlan-clients] Skipping WLANConfiguration.{k}: disabled")
                            continue

                        # Validasi: hanya masukkan index yang benar-benar memiliki sub-parameter valid
                        # (TotalAssociations atau AssociatedDevice) di dokumen perangkat.
                        # Ini mencegah error "Invalid parameter path" pada perangkat yang tidak
                        # mendukung semua 8 slot SSID (contoh: HG6145D2 hanya 4 slot).
                        has_total = isinstance(ssid_data.get("TotalAssociations"), dict)
                        has_assoc = isinstance(ssid_data.get("AssociatedDevice"), dict)
                        has_ssid = isinstance(ssid_data.get("SSID"), dict)
                        if has_total or has_assoc or has_ssid:
                            wlan_instances.append(k)
                        else:
                            print(f"[wlan-clients] Skipping WLANConfiguration.{k}: no valid sub-parameters found in device document")
            elif "Device" in dev_doc:
                wlan_conf = dev_doc.get("Device", {}).get("WiFi", {}).get("AccessPoint", {})
                for k in wlan_conf.keys():
                    if k.isdigit():
                        ap_data = wlan_conf.get(k, {})
                        
                        # Only query if AccessPoint is active/enabled
                        is_enabled = ap_data.get("Enable", {}).get("_value")
                        is_disabled = False
                        if is_enabled is False:
                            is_disabled = True
                        elif isinstance(is_enabled, str) and is_enabled.lower() in ('false', '0'):
                            is_disabled = True
                        
                        if is_disabled:
                            print(f"[wlan-clients] Skipping AccessPoint.{k}: disabled")
                            continue

                        has_entries = isinstance(ap_data.get("AssociatedDeviceNumberOfEntries"), dict)
                        has_assoc = isinstance(ap_data.get("AssociatedDevice"), dict)
                        if has_entries or has_assoc:
                            wlan_instances.append(k)
                        else:
                            print(f"[wlan-clients] Skipping AccessPoint.{k}: no valid sub-parameters found in device document")
                is_tr181 = True
                
            for p in ["InternetGatewayDevice", "Device"]:
                cr_obj = dev_doc.get(p, {}).get("ManagementServer", {}).get("ConnectionRequestURL", {})
                if isinstance(cr_obj, dict) and cr_obj.get('_value'):
                     cr_url = cr_obj['_value']
                     break
                     
            if wlan_instances:
                print(f"[wlan-clients] Valid WLAN instances for {device_id}: {wlan_instances}")
            else:
                # Fallback konservatif: hanya slot utama 2.4G dan 5G
                wlan_instances = ["1"]
                print(f"[wlan-clients] No valid WLAN instances detected for {device_id}, falling back to {wlan_instances}")
    except Exception as e:
        print(f"Error checking WLAN existence for clients: {e}")
        
    if not wlan_instances:
        wlan_instances = ["1"]
        print(f"[wlan-clients] Using last-resort fallback WLAN instances: {wlan_instances}")
        
    # 2. Queue getParameterValues untuk AssociatedDevice dan TotalAssociations
    gp_params = []
    for k in wlan_instances:
        if is_tr181:
            gp_params.append(f"Device.WiFi.AccessPoint.{k}.AssociatedDeviceNumberOfEntries")
            gp_params.append(f"Device.WiFi.AccessPoint.{k}.AssociatedDevice")
        else:
            gp_params.append(f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{k}.TotalAssociations")
            gp_params.append(f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{k}.AssociatedDevice")
            
    # Tambahkan Hosts.Host untuk real-time status port LAN belakang ONT
    if is_tr181:
        gp_params.append("Device.Hosts.Host")
    else:
        gp_params.append("InternetGatewayDevice.LANDevice.1.Hosts.Host")
        
    if gp_params:
        task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
        payload = {
            "name": "getParameterValues",
            "parameterNames": gp_params
        }
        task_id = None
        try:
            resp = requests.post(task_url, json=payload, auth=auth, timeout=10)
            if resp.status_code in (200, 201, 202):
                task_id = resp.json().get('_id')
                trigger_connection_request_by_device_id(device_id)
        except Exception as e:
            print(f"Error queuing AssociatedDevice refresh: {e}")
            
        # Tunggu task selesai (maksimal 10 detik)
        task_completed = False
        if task_id:
            for _ in range(8):
                time.sleep(1.2)
                try:
                    chk = requests.get(f"{url}/tasks", params={"query": _json.dumps({"_id": task_id})}, auth=auth, timeout=5)
                    if chk.status_code == 200:
                        if not chk.json():
                            task_completed = True
                            break
                        else:
                            # Cek apakah task tersebut gagal (fault)
                            task_data = chk.json()[0]
                            if 'fault' in task_data:
                                print(f"Task AssociatedDevice failed: {task_data['fault']}")
                                break
                except:
                    pass
                    
    # 3. Ambil data terbaru dari GenieACS
    try:
        dev_resp = requests.get(f"{url}/devices", params={"query": _json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        if dev_resp.status_code == 200 and dev_resp.json():
            dev_doc = dev_resp.json()[0]
            
            # Ambil last inform time
            from datetime import datetime
            last_inform = dev_doc.get("_lastInform", "")
            last_inform_str = "N/A"
            if last_inform:
                try:
                    # format: "2026-06-12T03:06:00.325Z"
                    dt = datetime.strptime(last_inform.split(".")[0], "%Y-%m-%dT%H:%M:%S")
                    last_inform_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    last_inform_str = last_inform
            
            clients = []
            total_active_wifi = 0
            if is_tr181:
                wlan_conf = dev_doc.get("Device", {}).get("WiFi", {}).get("AccessPoint", {})
            else:
                wlan_conf = dev_doc.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
                
            for ssid_idx, ssid_val in wlan_conf.items():
                if not ssid_idx.isdigit() or not isinstance(ssid_val, dict):
                    continue
                
                # Baca TotalAssociations / AssociatedDeviceNumberOfEntries real-time
                if is_tr181:
                    ta_val = ssid_val.get("AssociatedDeviceNumberOfEntries", {}).get("_value", 0)
                    if ta_val == 0:
                        ta_val = ssid_val.get("TotalAssociations", {}).get("_value", 0)
                else:
                    ta_val = ssid_val.get("TotalAssociations", {}).get("_value", 0)
                    
                try:
                    total_active_wifi += int(ta_val)
                except:
                    pass
                    
                ssid_name = ssid_val.get("SSID", {}).get("_value", f"SSID {ssid_idx}")
                if is_tr181:
                    if not ssid_name or ssid_name.startswith("Device.WiFi.SSID."):
                        ssid_ref_idx = ssid_name.split(".")[-1] if ssid_name else None
                        if ssid_ref_idx and ssid_ref_idx.isdigit():
                            ssid_name = dev_doc.get("Device", {}).get("WiFi", {}).get("SSID", {}).get(ssid_ref_idx, {}).get("SSID", {}).get("_value", f"SSID {ssid_idx}")
                        else:
                            ssid_name = f"AccessPoint {ssid_idx}"
                            
                assoc_devs = ssid_val.get("AssociatedDevice", {})
                for client_idx, client_val in assoc_devs.items():
                    if not client_idx.isdigit() or not isinstance(client_val, dict):
                        continue
                    mac = client_val.get("AssociatedDeviceMACAddress", {}).get("_value", "")
                    if not mac:
                        mac = client_val.get("MACAddress", {}).get("_value", "")
                    ip = client_val.get("AssociatedDeviceIPAddress", {}).get("_value", "")
                    if not ip:
                        ip = client_val.get("IPAddress", {}).get("_value", "")
                    
                    # Extract RSSI/Signal strength with fallback keys to support all vendors
                    rssi_val = None
                    for k in ["X_FH_WLAN_ClientSignalStrength", "X_FH_WLAN_RSSI", "X_FH_RSSI", "X_HW_RSSI", "X_ZTE-COM_RSSI", "AssociatedDeviceRSSI"]:
                        val_obj = client_val.get(k)
                        if isinstance(val_obj, dict) and "_value" in val_obj:
                            val = val_obj["_value"]
                            if val is not None and val != "":
                                try:
                                    rssi_val = int(val)
                                    break
                                except ValueError:
                                    pass
                    if rssi_val is None:
                        for key, val_obj in client_val.items():
                            if key.upper().endswith("RSSI") or key.upper().endswith("SIGNALSTRENGTH") or key.upper().endswith("SIGNAL"):
                                if isinstance(val_obj, dict) and "_value" in val_obj:
                                    val = val_obj["_value"]
                                    if val is not None and val != "":
                                        try:
                                            rssi_val = int(val)
                                            break
                                        except ValueError:
                                            pass

                    # Extract Uptime with fallback keys to support all vendors
                    uptime_val = None
                    for k in ["X_FH_WLAN_UpTime", "X_FH_UpTime", "X_HW_Uptime", "X_ZTE-COM_UpTime", "AssociatedDeviceUptime", "AssociatedDeviceUpTime"]:
                        val_obj = client_val.get(k)
                        if isinstance(val_obj, dict) and "_value" in val_obj:
                            val = val_obj["_value"]
                            if val is not None and val != "":
                                try:
                                    uptime_val = int(val)
                                    break
                                except ValueError:
                                    pass
                    if uptime_val is None:
                        for key, val_obj in client_val.items():
                            if key.upper().endswith("UPTIME"):
                                if isinstance(val_obj, dict) and "_value" in val_obj:
                                    val = val_obj["_value"]
                                    if val is not None and val != "":
                                        try:
                                            uptime_val = int(val)
                                            break
                                        except ValueError:
                                            pass
                                            
                    # Filter ghost device: Signal = 0 dBm AND Uptime = 0
                    if rssi_val == 0 and uptime_val == 0:
                        continue
                        
                    # Handle display adjustment for 0 or invalid RSSI
                    display_rssi = rssi_val
                    if display_rssi in (0, -256, ""):
                        display_rssi = None
                    display_uptime = uptime_val
                    if display_uptime == 0:
                        display_uptime = None
                        
                    # Extract Device Name / Brand / Description
                    device_name = None
                    for k in ["X_FH_DeviceName", "X_HW_AssociatedDevicedescriptions", "X_ZTE-COM_DeviceName", "HostName"]:
                        val_obj = client_val.get(k)
                        if isinstance(val_obj, dict) and "_value" in val_obj:
                            val = val_obj["_value"]
                            if val is not None and val != "":
                                device_name = val
                                break
                    if not device_name:
                        for key, val_obj in client_val.items():
                            if key.upper().endswith("DEVICENAME") or key.upper().endswith("DEVICEDESCRIPTIONS") or key.upper().endswith("HOSTNAME"):
                                if isinstance(val_obj, dict) and "_value" in val_obj:
                                    val = val_obj["_value"]
                                    if val is not None and val != "":
                                        device_name = val
                                        break

                    if mac:
                        clients.append({
                            'ssid_idx': ssid_idx,
                            'ssid_name': ssid_name,
                            'mac': mac,
                            'ip': ip or "N/A",
                            'rssi': display_rssi,
                            'uptime': display_uptime,
                            'device_name': device_name or "N/A"
                        })
            
            # Ambil data LAN devices dari table Hosts
            lan_devices = []
            if is_tr181:
                hosts_conf = dev_doc.get("Device", {}).get("Hosts", {}).get("Host", {})
            else:
                hosts_conf = dev_doc.get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("Hosts", {}).get("Host", {})
                
            if isinstance(hosts_conf, dict):
                # Buat set MAC address Wi-Fi client yang aktif untuk pencocokan cepat
                wifi_macs = {c['mac'].lower().strip() for c in clients if c.get('mac')}
                
                for host_idx, host_val in hosts_conf.items():
                    if not host_idx.isdigit() or not isinstance(host_val, dict):
                        continue
                    active = host_val.get("Active", {}).get("_value")
                    is_active = False
                    if active is True:
                        is_active = True
                    elif isinstance(active, str) and active.lower() in ('true', '1'):
                        is_active = True
                        
                    if not is_active:
                        continue
                        
                    mac = host_val.get("MACAddress", {}).get("_value", "")
                    if not mac:
                        mac = host_val.get("PhysAddress", {}).get("_value", "")
                        
                    mac_clean = mac.lower().strip() if mac else ""
                    if not mac_clean:
                        continue
                        
                    # Jika MAC address TIDAK ada di list Wi-Fi client yang aktif, maka ini adalah perangkat LAN
                    if mac_clean not in wifi_macs:
                        ip = host_val.get("IPAddress", {}).get("_value", "")
                        name = host_val.get("HostName", {}).get("_value", "N/A")
                        itype = host_val.get("InterfaceType", {}).get("_value", "Ethernet")
                        
                        # Jika InterfaceType adalah 802.11 / WiFi tapi dia terdeteksi LAN (karena stale cache di ONT), 
                        # kita labeli sebagai LAN/Ethernet agar tidak membingungkan.
                        if itype and ("802.11" in str(itype) or "WiFi" in str(itype) or "Wireless" in str(itype)):
                            itype = "Ethernet"
                            
                        lan_devices.append({
                            'mac': mac,
                            'ip': ip or "N/A",
                            'interface_type': itype or "Ethernet",
                            'device_name': name or "N/A"
                        })
            
            is_stale = False
            if task_id and not task_completed:
                is_stale = True
                
            return {
                'success': True,
                'clients': clients,
                'lan_devices': lan_devices,
                'total_active_wifi': total_active_wifi,
                'stale': is_stale,
                'last_inform': last_inform_str
            }
    except Exception as e:
        return {'success': False, 'message': str(e)}
        
    return {'success': False, 'message': 'Gagal mengambil dokumen perangkat setelah refresh'}
