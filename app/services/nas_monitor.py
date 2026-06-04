from extensions import socketio
from database import execute_query
from helpers import ping_nas
from services.telegram import send_telegram_notification
from datetime import datetime

# Local state to track previous NAS health states to avoid spamming Telegram
_nas_health_state = {}

def background_nas_monitor():
    """Memantau status ping ke setiap IP NAS setiap 1 menit dan mengirim notifikasi Telegram jika berubah."""
    global _nas_health_state
    print("[nas-monitor] Thread initialized, sleeping 15s for startup...")
    socketio.sleep(15)
    print("[nas-monitor] Starting NAS health monitor loop...")
    
    while True:
        try:
            # Query all NAS
            nas_list = execute_query("SELECT nasname, shortname FROM nas ORDER BY id")
            if nas_list:
                for nas in nas_list:
                    ip = nas['nasname']
                    name = nas['shortname'] or ip
                    
                    # Test ping
                    status = ping_nas(ip)
                    prev_status = _nas_health_state.get(ip)
                    
                    # Initial state tracking
                    if prev_status is None:
                        _nas_health_state[ip] = status
                        continue
                        
                    # State changed!
                    if status != prev_status:
                        _nas_health_state[ip] = status
                        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        
                        if status == 'offline':
                            msg = (
                                f"🔴 <b>WARNING: NAS DOWN</b>\n"
                                f"• Name: <b>{name}</b>\n"
                                f"• IP Address: <code>{ip}</code>\n"
                                f"• Time: {time_str}\n\n"
                                f"🚨 Router NAS tidak merespon Ping ICMP dan TCP. Harap periksa jaringan dan catu daya!"
                            )
                            # Custom buttons
                            import json as _json
                            reply_markup = _json.dumps({
                                "inline_keyboard": [[
                                    {"text": "⚡ Coba Ping Ulang", "callback_data": f"ping_nas:{ip}"}
                                ]]
                            })
                            send_telegram_notification(msg, reply_markup=reply_markup)
                        else:
                            msg = (
                                f"🟢 <b>RECOVERY: NAS UP</b>\n"
                                f"• Name: <b>{name}</b>\n"
                                f"• IP Address: <code>{ip}</code>\n"
                                f"• Time: {time_str}\n\n"
                                f"✅ Router NAS kembali online dan terhubung dengan normal."
                            )
                            send_telegram_notification(msg)
                            
        except Exception as e:
            print(f"[nas-monitor] Error: {e}")
            
        socketio.sleep(60)
