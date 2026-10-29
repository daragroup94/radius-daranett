import os
import requests
from datetime import datetime
from extensions import socketio
from database import execute_query
from services.telegram import send_telegram_notification

def background_optical_health_scan():
    """Scan seluruh ONT di GenieACS setiap 1 jam untuk mendeteksi sinyal buruk"""
    # Tunggu startup stabil sebentar
    socketio.sleep(5)
    print("[optical-health-scan] Thread active.")

    # 1. Pastikan tabel ont_status dan ont_health_issues ada di basis data
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_status (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                pon_mode VARCHAR(50),
                last_inform VARCHAR(100),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)
        execute_query("ALTER TABLE public.ont_status ADD COLUMN IF NOT EXISTS pon_mode VARCHAR(50)", fetch=False)
        
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_health_issues (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                pon_mode VARCHAR(50),
                last_inform VARCHAR(100),
                detected_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)
        execute_query("ALTER TABLE public.ont_health_issues ADD COLUMN IF NOT EXISTS pon_mode VARCHAR(50)", fetch=False)
        print("[optical-health-scan] Tables verified/created.")
    except Exception as ex:
        print(f"[optical-health-scan] Failed to verify/create tables: {ex}")

    while True:
        try:
            from services.genieacs import get_genieacs_config
            config = get_genieacs_config()
            GENIEACS_NBI = config['url']
            auth = config['auth']
            projection = '_deviceId,_lastInform,VirtualParameters.RXPower,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber'
            resp = requests.get(
                f'{GENIEACS_NBI}/devices',
                params={'projection': projection},
                auth=auth,
                timeout=15
            )
            devices = resp.json()
            if isinstance(devices, list):
                bad_onts = []
                
                def val(obj):
                    if isinstance(obj, dict):
                        return obj.get('_value', 'N/A')
                    return obj if obj is not None else 'N/A'
                
                # Bersihkan data lama di tabel isu kesehatan (agar sinkron dengan pemindaian terbaru)
                execute_query("DELETE FROM ont_health_issues", fetch=False)
                
                # Ensure ont_serial_mapping table exists
                execute_query("""
                    CREATE TABLE IF NOT EXISTS public.ont_serial_mapping (
                        username VARCHAR(100) PRIMARY KEY,
                        serial_number VARCHAR(100) UNIQUE NOT NULL,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                """, fetch=False)
 
                # Load manual serial mappings from database
                mappings = execute_query("SELECT username, serial_number FROM ont_serial_mapping") or []
                serial_map = {m['serial_number'].strip(): m['username'].strip() for m in mappings if m['serial_number'] and m['username']}
 
                for d in devices:
                    vp = d.get('VirtualParameters', {})
                    dev_id = d.get('_deviceId', {})
                    
                    username = val(vp.get('pppoeUsername'))
                    if not username or username == 'N/A' or username == '':
                        username = val(vp.get('pppoeUsername2'))
                    rx = val(vp.get('RXPower'))
                    temp = val(vp.get('gettemp'))
                    pon_mode = val(vp.get('getponmode'))
                    serial = val(vp.get('getSerialNumber')) or dev_id.get('_SerialNumber', 'N/A')
                    model = dev_id.get('_ProductClass', 'N/A')
                    manufacturer = dev_id.get('_Manufacturer', 'N/A')
                    
                    # Filter device non-ONT: tidak punya RXPower valid (bukan ONT fiber)
                    rx_valid = rx and rx not in ('N/A', '', None)
                    if not rx_valid:
                        continue
                    
                    # Determine DB username: check manual serial mapping first, then fallback to pppoeUsername
                    serial_clean = serial.strip() if serial else 'N/A'
                    if serial_clean in serial_map:
                        db_username = serial_map[serial_clean]
                    elif username and username != 'N/A' and username.strip():
                        db_username = username.strip()
                    else:
                        db_username = f"unconfigured-{serial_clean}"
 
                    # Clean up old duplicate entries for this serial in ont_status
                    try:
                        execute_query("DELETE FROM ont_status WHERE serial = %s AND username != %s", (serial_clean, db_username), fetch=False)
                    except Exception:
                        pass
                    
                    # 2. Upsert ke tabel ont_status untuk SEMUA ONT yang valid
                    try:
                        execute_query("""
                            INSERT INTO ont_status (username, serial, model, manufacturer, rx_power, temperature, pon_mode, last_inform, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (username) DO UPDATE 
                            SET serial = EXCLUDED.serial, model = EXCLUDED.model, manufacturer = EXCLUDED.manufacturer,
                                rx_power = EXCLUDED.rx_power, temperature = EXCLUDED.temperature, pon_mode = EXCLUDED.pon_mode, last_inform = EXCLUDED.last_inform, updated_at = NOW()
                        """, (db_username, serial_clean, model, manufacturer, rx, temp, pon_mode, d.get('_lastInform', 'N/A')), fetch=False)

                        # Catat riwayat kekuatan sinyal (Rx Power)
                        execute_query("""
                            INSERT INTO ont_signal_history (username, rx_power, recorded_at)
                            VALUES (%s, %s, NOW())
                        """, (db_username, rx), fetch=False)

                        # Batasi riwayat maksimal 30 baris per pelanggan agar database tidak bengkak
                        execute_query("""
                            DELETE FROM ont_signal_history
                            WHERE id IN (
                                SELECT id FROM (
                                    SELECT id, ROW_NUMBER() OVER (PARTITION BY username ORDER BY recorded_at DESC) as rn
                                    FROM ont_signal_history
                                    WHERE username = %s
                                ) t WHERE t.rn > 30
                            )
                        """, (db_username,), fetch=False)
                    except Exception as upsert_err:
                        print(f"[optical-health-scan] Upsert/History failed for {db_username}: {upsert_err}")
                        
                    # 3. Masukkan ke tabel ont_health_issues jika redaman buruk
                    try:
                        rx_clean = rx.replace('dBm', '').strip()
                        rx_float = float(rx_clean)
                        # Sinyal dianggap buruk/warning jika di bawah -25.0 dBm
                        if rx_float < -25.0:
                            execute_query("""
                                INSERT INTO ont_health_issues (username, serial, model, manufacturer, rx_power, temperature, pon_mode, last_inform)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (username) DO UPDATE 
                                SET rx_power = EXCLUDED.rx_power, temperature = EXCLUDED.temperature, pon_mode = EXCLUDED.pon_mode, last_inform = EXCLUDED.last_inform, detected_at = NOW()
                            """, (db_username, serial, model, manufacturer, rx, temp, pon_mode, d.get('_lastInform', 'N/A')), fetch=False)
                            
                            # Hanya kirim notifikasi Telegram jika redaman buruk (di atas -25 dBm / <= -25.0 dBm)
                            if rx_float <= -25.0:
                                display_name = username.strip() if (username and username != 'N/A' and username.strip()) else f"SN: {serial} (Belum Dial)"
                                bad_onts.append((display_name, rx))
                    except (ValueError, TypeError):
                        pass

                # Kirim notifikasi jika ada redaman buruk terdeteksi
                if bad_onts:
                    msg = f"⚠️ <b>[NOC ALERT]</b> Terdeteksi <b>{len(bad_onts)} ONT pelanggan</b> memiliki redaman buruk (di atas -25 dBm):\n\n"
                    for uname, rx_val in bad_onts[:10]:
                        msg += f"• 👤 <code>{uname}</code> | Sinyal: <b>{rx_val} dBm</b>\n"
                    if len(bad_onts) > 10:
                        msg += f"• <i>...dan {len(bad_onts) - 10} perangkat lainnya.</i>\n"
                    msg += f"\nSilakan periksa halaman <b>Kesehatan ONT</b> di Web Dashboard untuk detail lebih lanjut!"
                    allowed_origins = os.getenv('ALLOWED_ORIGINS', 'https://radius.go.daragroup.cloud')
                    base_url = allowed_origins.split(',')[0].strip()
                    
                    import json as _json
                    reply_markup = _json.dumps({
                        "inline_keyboard": [
                            [{"text": "🖥️ Buka Dashboard Kesehatan ONT", "url": f"{base_url}/optical-health"}]
                        ]
                    })
                    send_telegram_notification(msg, reply_markup=reply_markup)
                    
        except Exception as e:
            print(f"[optical-health-scan] Error: {e}")
            
        socketio.sleep(3600)
