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
        projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeIP,VirtualParameters.getdeviceuptime,VirtualParameters.getpppuptime,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,InternetGatewayDevice.DeviceInfo.UpTime,Device.DeviceInfo.UpTime'
        
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
            'device_uptime': val(vp.get('getdeviceuptime', {})),
            'ppp_uptime': val(vp.get('getpppuptime', {})),
            'temperature': val(vp.get('gettemp', {})),
            'pon_mode': val(vp.get('getponmode', {})),
            'last_inform': d.get('_lastInform', 'N/A'),
            'ont_uptime_str': ont_uptime_str,
            'ont_uptime_days': ont_uptime_days
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
