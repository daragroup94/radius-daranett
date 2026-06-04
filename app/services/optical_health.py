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

    # 1. Pastikan tabel ont_status untuk seluruh ONT ada di basis data
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_status (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                last_inform VARCHAR(100),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)
        print("[optical-health-scan] Table 'ont_status' verified/created.")
    except Exception as ex:
        print(f"[optical-health-scan] Failed to verify/create 'ont_status' table: {ex}")

    while True:
        try:
            GENIEACS_NBI = os.getenv('GENIEACS_NBI_URL', 'http://10.204.0.22:7557')
            projection = '_deviceId,_lastInform,VirtualParameters.RXPower,VirtualParameters.pppoeUsername,VirtualParameters.gettemp,VirtualParameters.getSerialNumber'
            resp = requests.get(
                f'{GENIEACS_NBI}/devices',
                params={'projection': projection},
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
                
                for d in devices:
                    vp = d.get('VirtualParameters', {})
                    dev_id = d.get('_deviceId', {})
                    
                    username = val(vp.get('pppoeUsername'))
                    rx = val(vp.get('RXPower'))
                    temp = val(vp.get('gettemp'))
                    serial = val(vp.get('getSerialNumber')) or dev_id.get('_SerialNumber', 'N/A')
                    model = dev_id.get('_ProductClass', 'N/A')
                    manufacturer = dev_id.get('_Manufacturer', 'N/A')
                    
                    # Filter device non-ONT: tidak punya RXPower valid (bukan ONT fiber)
                    rx_valid = rx and rx not in ('N/A', '', None)
                    if not rx_valid:
                        continue
                    
                    db_username = username.strip() if (username and username != 'N/A' and username.strip()) else f"unconfigured-{serial}"
                    
                    # 2. Upsert ke tabel ont_status untuk SEMUA ONT yang valid
                    try:
                        execute_query("""
                            INSERT INTO ont_status (username, serial, model, manufacturer, rx_power, temperature, last_inform, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                            ON CONFLICT (username) DO UPDATE 
                            SET serial = EXCLUDED.serial, model = EXCLUDED.model, manufacturer = EXCLUDED.manufacturer,
                                rx_power = EXCLUDED.rx_power, temperature = EXCLUDED.temperature, last_inform = EXCLUDED.last_inform, updated_at = NOW()
                        """, (db_username, serial, model, manufacturer, rx, temp, d.get('_lastInform', 'N/A')), fetch=False)
                    except Exception as upsert_err:
                        print(f"[optical-health-scan] Upsert ont_status failed for {db_username}: {upsert_err}")
                        
                    # 3. Masukkan ke tabel ont_health_issues jika redaman buruk
                    try:
                        rx_clean = rx.replace('dBm', '').strip()
                        rx_float = float(rx_clean)
                        # Sinyal dianggap buruk jika di bawah -25 dBm
                        if rx_float < -25.0:
                            execute_query("""
                                INSERT INTO ont_health_issues (username, serial, model, manufacturer, rx_power, temperature, last_inform)
                                VALUES (%s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (username) DO UPDATE 
                                SET rx_power = EXCLUDED.rx_power, temperature = EXCLUDED.temperature, last_inform = EXCLUDED.last_inform, detected_at = NOW()
                            """, (db_username, serial, model, manufacturer, rx, temp, d.get('_lastInform', 'N/A')), fetch=False)
                            
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
                    
                    import json as _json
                    reply_markup = _json.dumps({
                        "inline_keyboard": [
                            [{"text": "🖥️ Buka Dashboard Kesehatan ONT", "url": "https://radius.access.daragroup.cloud/optical-health"}]
                        ]
                    })
                    send_telegram_notification(msg, reply_markup=reply_markup)
                    
        except Exception as e:
            print(f"[optical-health-scan] Error: {e}")
            
        socketio.sleep(3600)
