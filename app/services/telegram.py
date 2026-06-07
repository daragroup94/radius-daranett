import os
import requests
import threading
from datetime import datetime
from flask import current_app
from extensions import socketio
from database import execute_query
from helpers import ping_nas
from services.genieacs import get_genieacs_device_info, get_genieacs_config
from services.mikrotik_coa import send_coa_disconnect

def _send_telegram_worker(message, reply_markup=None):
    token, chat_id = '', ''
    # Fallback to env file
    env_path = '/app/.telegram.env'
    if os.path.exists(env_path):
        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith('TELEGRAM_BOT_TOKEN='):
                        token = line.strip().split('=', 1)[1]
                    elif line.startswith('TELEGRAM_CHAT_ID='):
                        chat_id = line.strip().split('=', 1)[1]
        except Exception as e:
            print(f"Error reading .telegram.env: {e}")
            
    # Try current_app config if active
    try:
        if current_app:
            token = current_app.config.get('TELEGRAM_BOT_TOKEN', token) or token
            chat_id = current_app.config.get('TELEGRAM_CHAT_ID', chat_id) or chat_id
    except:
        pass
        
    # Fallback to Config class if not set
    if not token or not chat_id:
        try:
            from config import Config
            token = token or Config.TELEGRAM_BOT_TOKEN
            chat_id = chat_id or Config.TELEGRAM_CHAT_ID
        except:
            pass
            
    if not token or not chat_id:
        return
        
    try:
        import json as _json
        data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}
        if reply_markup:
            data['reply_markup'] = reply_markup
            
        # Gunakan requests.post asinkronus via thread daripada spawn subprocess curl
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json=data,
            timeout=10
        )
        if resp.status_code != 200:
            print(f"Telegram API warning: {resp.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def send_telegram_notification(message, reply_markup=None):
    threading.Thread(target=_send_telegram_worker, args=(message, reply_markup), daemon=True).start()

def background_telegram_bot():
    """Mendengar pesan Telegram masuk (bot polling) untuk melayani perintah teknisi secara interaktif."""
    print("[telegram-bot] Thread initialized, sleeping 20s for stability...")
    socketio.sleep(20) # Tunggu agar startup stabil
    print("[telegram-bot] Thread starting active polling...")
    offset = 0
    
    while True:
        try:
            token = None
            env_path = '/app/.telegram.env'
            if os.path.exists(env_path):
                try:
                    with open(env_path) as f:
                        for line in f:
                            if line.startswith('TELEGRAM_BOT_TOKEN='):
                                token = line.split('=', 1)[1].strip()
                                break
                except Exception as e:
                    print(f"[telegram-bot] Error reading .telegram.env: {e}")
            
            # Fallback to Config class if not set
            if not token:
                try:
                    from config import Config
                    token = Config.TELEGRAM_BOT_TOKEN
                except:
                    pass

            if not token:
                print("[telegram-bot] Warning: TELEGRAM_BOT_TOKEN not found!")
                socketio.sleep(10)
                continue
                
            # Long polling updates dari Telegram API
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {'offset': offset, 'timeout': 15}
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                socketio.sleep(5)
                continue
                
            updates = resp.json().get('result', [])
            for update in updates:
                offset = update['update_id'] + 1
                
                # Memproses Interaksi Tombol Callback Query
                if 'callback_query' in update:
                    cb = update['callback_query']
                    cb_id = cb.get('id')
                    cb_data = cb.get('data', '')
                    cb_chat_id = cb.get('message', {}).get('chat', {}).get('id')
                    
                    try:
                        requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", data={
                            'callback_query_id': cb_id
                        }, timeout=3)
                    except:
                        pass
                        
                    if cb_data and cb_chat_id:
                        if cb_data.startswith('ping_nas:'):
                            nas_ip = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ Sedang menguji ping ulang ke IP <code>{nas_ip}</code>...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            status = ping_nas(nas_ip)
                            status_emoji = "🟢 ONLINE" if status == "online" else "🔴 OFFLINE"
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ <b>Hasil Ping Ulang:</b>\nIP: <code>{nas_ip}</code>\nStatus: <b>{status_emoji}</b>",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                        elif cb_data.startswith('kick_session:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ Sedang memproses pemutusan sesi (CoA/Disconnect-Request) untuk pelanggan <code>{username}</code>...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            try:
                                sessions = execute_query("""
                                    SELECT acctsessionid, nasipaddress, framedipaddress FROM radacct 
                                    WHERE username = %s AND acctstoptime IS NULL
                                """, (username,))
                                
                                if not sessions:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"⚠️ <b>Tidak ada sesi aktif:</b> Pelanggan <code>{username}</code> saat ini sedang offline di RADIUS.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    coa_success = 0
                                    for s in sessions:
                                        nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
                                        secret = nas[0]['secret'] if nas else 'radiussecret'
                                        if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret, framed_ip=s.get('framedipaddress')):
                                            coa_success += 1
                                        execute_query("""
                                            UPDATE radacct 
                                            SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                                            WHERE acctsessionid = %s
                                        """, (s['acctsessionid'],), fetch=False)
                                    
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"✅ <b>Sesi Pelanggan Berhasil Diputus!</b>\n• Username: <code>{username}</code>\n• Sesi Aktif: <b>{len(sessions)}</b> (MikroTik CoA: {coa_success} terkirim).\n\nPelanggan akan dial-up ulang secara otomatis.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                            except Exception as ex:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"❌ <b>Gagal Kick Sesi:</b> {ex}",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                                
                        elif cb_data.startswith('reboot_ont:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"🔄 Sedang me-reboot ONT untuk user <code>{username}</code> via CWMP (GenieACS)...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            try:
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
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    d = devices[0]
                                    device_id = d.get('_id')
                                    if not device_id:
                                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                            'chat_id': cb_chat_id,
                                            'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid di GenieACS.",
                                            'parse_mode': 'HTML'
                                        }, timeout=5)
                                    else:
                                        import urllib.parse
                                        reboot_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
                                        try:
                                            resp_reboot = requests.post(reboot_url, json={"name": "reboot"}, auth=auth, timeout=15)
                                            if resp_reboot.status_code in (200, 201, 202):
                                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                    'chat_id': cb_chat_id,
                                                    'text': f"🚀 <b>Perintah Reboot Terkirim!</b>\nONT untuk user <code>{username}</code> berhasil di-reboot via CWMP.",
                                                    'parse_mode': 'HTML'
                                                }, timeout=5)
                                            else:
                                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                    'chat_id': cb_chat_id,
                                                    'text': f"❌ <b>Gagal:</b> Respon GenieACS API: {resp_reboot.status_code}",
                                                    'parse_mode': 'HTML'
                                                }, timeout=5)
                                        except requests.exceptions.Timeout:
                                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                'chat_id': cb_chat_id,
                                                'text': f"⏳ <b>Koneksi Lambat:</b> Hubungan langsung ke ONT <code>{username}</code> melampaui batas waktu, namun <b>perintah reboot telah masuk antrean</b> di GenieACS dan akan dijalankan otomatis saat ONT terhubung.",
                                                'parse_mode': 'HTML'
                                            }, timeout=5)
                            except Exception as ex:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"❌ <b>Error:</b> {ex}",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                    continue
                    
                msg_obj = update.get('message', {})
                text = msg_obj.get('text', '').strip()
                chat_id = msg_obj.get('chat', {}).get('id')
                
                if not text or not chat_id:
                    continue
                    
                # Hanya layani perintah yang dimulai dengan /
                if not text.startswith('/'):
                    continue
                    
                parts = text.split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                
                # Perintah /start atau /help
                if cmd in ('/start', '/help'):
                    help_msg = (
                        "🤖 <b>RADIUS & ACS Telegram Bot</b>\n"
                        "Layanan interaktif untuk Teknisi / Admin:\n\n"
                        "➡️ <b>/cek [username]</b> : Cek status online, IP, MAC, dan Redaman ONT.\n"
                        "➡️ <b>/reboot_ont [username]</b> : Reboot ONT pelanggan secara remote via CWMP.\n"
                        "➡️ <b>/nas</b> : Cek status kesehatan seluruh router NAS MikroTik."
                    )
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                        'chat_id': chat_id,
                        'text': help_msg,
                        'parse_mode': 'HTML'
                    })
                    continue
                
                # Perintah /nas
                if cmd == '/nas':
                    try:
                        nas_list = execute_query("SELECT nasname, shortname FROM nas ORDER BY id")
                        if not nas_list:
                            nas_msg = "🖥️ Tidak ada router NAS yang terdaftar."
                        else:
                            nas_msg = "🖥️ <b>Status Kesehatan NAS Router:</b>\n\n"
                            for nas in nas_list:
                                ip = nas['nasname']
                                name = nas['shortname'] or ip
                                is_online = (ping_nas(ip) == "online")
                                status_emoji = "🟢 ONLINE" if is_online else "🔴 OFFLINE"
                                nas_msg += f"• <b>{name}</b> ({ip}) : {status_emoji}\n"
                        
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': nas_msg,
                            'parse_mode': 'HTML'
                        })
                    except Exception as e:
                        print(f"Error /nas Telegram command: {e}")
                    continue
                
                # Perintah /cek [username]
                if cmd == '/cek':
                    if not arg:
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "⚠️ Format salah. Contoh: <code>/cek customer_pppoe</code>",
                            'parse_mode': 'HTML'
                        })
                        continue
                        
                    username = arg.strip()
                    
                    # 1. Query PostgreSQL untuk status sesi RADIUS
                    session_res = execute_query("""
                        SELECT * FROM radacct 
                        WHERE username = %s 
                        ORDER BY acctstarttime DESC LIMIT 1
                    """, (username,))
                    
                    is_online = False
                    nas_ip = "N/A"
                    nas_name = "N/A"
                    framed_ip = "N/A"
                    mac_address = "N/A"
                    uptime_str = "N/A"
                    session_days = 0
                    
                    if session_res:
                        s = session_res[0]
                        if s.get('acctstoptime') is None:
                            is_online = True
                            start_time = s.get('acctstarttime')
                            if start_time:
                                now = datetime.now(start_time.tzinfo) if start_time.tzinfo else datetime.now()
                                diff = now - start_time
                                hours, remainder = divmod(diff.seconds, 3600)
                                minutes, seconds = divmod(remainder, 60)
                                uptime_str = f"{diff.days}d {hours}h {minutes}m"
                                session_days = diff.days
                        
                        nas_ip = s.get('nasipaddress', 'N/A')
                        framed_ip = s.get('framedipaddress', 'N/A')
                        mac_address = s.get('callingstationid', 'N/A')
                        
                        # Dapatkan nama NAS
                        nas_info = execute_query("SELECT shortname FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
                        if nas_info:
                            nas_name = nas_info[0]['shortname'] or nas_ip
                    
                    # 2. Query GenieACS service dengan cache Redis
                    acs_data = get_genieacs_device_info(username)
                    acs_found = acs_data.get('found', False)
                    
                    # 3. Formulasikan Balasan HTML & Smart Diagnostics
                    status_str = "🟢 <b>ONLINE</b>" if is_online else "🔴 <b>OFFLINE</b>"
                    
                    reply = (
                        f"👤 <b>Informasi Pelanggan:</b>\n"
                        f"• Username: <code>{username}</code>\n"
                        f"• Status Sesi: {status_str}\n"
                    )
                    
                    if session_res:
                        reply += (
                            f"• Router NAS: <b>{nas_name}</b> (<code>{nas_ip}</code>)\n"
                            f"• IP Address: <code>{framed_ip}</code>\n"
                            f"• MAC Address: <code>{mac_address}</code>\n"
                            f"• Sesi Aktif Uptime: <code>{uptime_str}</code>\n"
                        )
                    else:
                        reply += "• <i>Log Sesi RADIUS tidak ditemukan.</i>\n"
                        
                    reply += "\n📌 <b>Informasi ONT (GenieACS):</b>\n"
                    if acs_found:
                        reply += (
                            f"• SN / Model: <code>{acs_data.get('serial','N/A')}</code> ({acs_data.get('manufacturer','N/A')} {acs_data.get('model','N/A')})\n"
                            f"• Redaman (RxPower): <b>{acs_data.get('rx_power','N/A')} dBm</b> ({acs_data.get('rx_status_emoji','⚪ Unknown')})\n"
                            f"• Uptime Perangkat: <code>{acs_data.get('ont_uptime_str','N/A')}</code>\n"
                            f"• Temperatur: <code>{acs_data.get('temperature','N/A')} °C</code>\n"
                            f"• Mode PON: <code>{acs_data.get('pon_mode','N/A')}</code>\n"
                        )
                    else:
                        reply += f"• <i>ONT Detail: {acs_data.get('message', 'Data tidak ditemukan')}</i>\n"
                        
                    # === NOC SMART DIAGNOSTICS & RECOMMENDATIONS ===
                    recommendations = []
                    
                    if acs_found:
                        try:
                            rx_float = float(acs_data.get('rx_power', 'N/A'))
                            if rx_float < -27.0:
                                recommendations.append(f"⚠️ <b>Redaman Buruk ({rx_float} dBm):</b> Sinyal berada di bawah batas normal (-27 dBm). Disarankan pengecekan sambungan fisik atau redrop kabel optik.")
                        except:
                            pass
                            
                        if acs_data.get('ont_uptime_days', 0) > 60:
                            recommendations.append(f"💡 <b>ONT Uptime Tinggi ({acs_data.get('ont_uptime_days', 0)} hari):</b> Perangkat ONT aktif terlalu lama tanpa reboot. Disarankan me-reboot ONT untuk optimalisasi kinerja memori.")
                            
                    if is_online and session_days > 60:
                        recommendations.append(f"🔌 <b>Sesi RADIUS Tinggi ({session_days} hari):</b> Sesi dial-up aktif terlalu lama tanpa refresh. Disarankan 'Kick Session' agar mendapat alokasi IP & rute segar.")
                        
                    if recommendations:
                        reply += "\n🧠 <b>Rekomendasi Pintar (NOC Diagnostic):</b>\n"
                        for rec in recommendations:
                            reply += f"• {rec}\n"
                            
                    # Tambahkan Tombol Interaktif
                    buttons = []
                    if is_online:
                        buttons.append({"text": "⚡ Kick Session", "callback_data": f"kick_session:{username}"})
                    if acs_found:
                        buttons.append({"text": "🔄 Reboot ONT", "callback_data": f"reboot_ont:{username}"})
                        
                    payload = {
                        'chat_id': chat_id,
                        'text': reply,
                        'parse_mode': 'HTML'
                    }
                    
                    if buttons:
                        import json as _json
                        payload['reply_markup'] = _json.dumps({"inline_keyboard": [buttons]})
                        
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)
                    continue
                    
                # Perintah /reboot_ont [username]
                if cmd == '/reboot_ont':
                    if not arg:
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "⚠️ Format salah. Contoh: <code>/reboot_ont customer_pppoe</code>",
                            'parse_mode': 'HTML'
                        })
                        continue
                        
                    username = arg.strip()
                    
                    # 1. Cari device_id di GenieACS
                    try:
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
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                                'parse_mode': 'HTML'
                            })
                            continue
                            
                        d = devices[0]
                        device_id = d.get('_id')
                        if not device_id:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                
                                'chat_id': chat_id,
                                'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid di GenieACS.",
                                'parse_mode': 'HTML'
                            })
                            continue
                            
                        # 2. Kirim task reboot ke GenieACS NBI
                        import urllib.parse
                        reboot_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
                        try:
                            resp_reboot = requests.post(reboot_url, json={"name": "reboot"}, auth=auth, timeout=15)
                            if resp_reboot.status_code in (200, 201, 202):
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': chat_id,
                                    'text': f"🚀 <b>Perintah Reboot Terkirim!</b>\nONT untuk user <code>{username}</code> berhasil di-reboot via CWMP (GenieACS).",
                                    'parse_mode': 'HTML'
                                })
                            else:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': chat_id,
                                    'text': f"❌ <b>Gagal:</b> Respon GenieACS API: {resp_reboot.status_code}",
                                    'parse_mode': 'HTML'
                                })
                        except requests.exceptions.Timeout:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': f"⏳ <b>Koneksi Lambat:</b> Hubungan langsung ke ONT <code>{username}</code> melampaui batas waktu, namun <b>perintah reboot telah masuk antrean</b> di GenieACS dan akan dijalankan otomatis saat ONT terhubung.",
                                'parse_mode': 'HTML'
                            })
                    except Exception as e:
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': f"❌ <b>Error:</b> {e}",
                            'parse_mode': 'HTML'
                        })
                    continue
                    
        except Exception as e:
            print(f"[telegram-bot] Error in polling loop: {e}")
            
        socketio.sleep(2)
