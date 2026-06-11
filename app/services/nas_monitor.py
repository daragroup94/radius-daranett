import redis
from extensions import socketio
from database import execute_query
from helpers import ping_nas
from services.telegram import send_telegram_notification
from datetime import datetime
import os

# Use Redis for persistent state with in-memory fallback
redis_client = redis.Redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0'))
_nas_health_state_local = {}

def get_nas_status_from_cache(ip):
    """Get NAS status from Redis cache with in-memory fallback"""
    try:
        status = redis_client.get(f"nas_status:{ip}")
        if status is not None:
            return status.decode()
    except Exception as e:
        print(f"[nas-monitor] Redis error getting status: {e}")
    return _nas_health_state_local.get(ip)

def get_nas_status_time_info(ip):
    """Get last seen and online since times"""
    last_seen = None
    online_since = None
    try:
        ls = redis_client.get(f"nas_last_seen:{ip}")
        os = redis_client.get(f"nas_online_since:{ip}")
        if ls:
            last_seen = ls.decode()
        if os:
            online_since = os.decode()
    except Exception as e:
        print(f"[nas-monitor] Redis error getting status time info: {e}")
    
    if not last_seen:
        last_seen = _nas_health_state_local.get(f"{ip}:last_seen")
    if not online_since:
        online_since = _nas_health_state_local.get(f"{ip}:online_since")
    return last_seen, online_since

def set_nas_status_to_cache(ip, status):
    """Set NAS status to Redis cache and local memory"""
    prev_status = get_nas_status_from_cache(ip)
    _nas_health_state_local[ip] = status
    now_str = datetime.now().isoformat()
    try:
        redis_client.set(f"nas_status:{ip}", status, ex=300)  # 5 min TTL
        if status == 'online':
            redis_client.set(f"nas_last_seen:{ip}", now_str)
            _nas_health_state_local[f"{ip}:last_seen"] = now_str
            if prev_status != 'online':
                redis_client.set(f"nas_online_since:{ip}", now_str)
                _nas_health_state_local[f"{ip}:online_since"] = now_str
    except Exception as e:
        print(f"[nas-monitor] Redis error setting status: {e}")


def background_nas_monitor():
    """Memantau status ping ke setiap IP NAS setiap 1 menit dan mengirim notifikasi Telegram jika berubah."""
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
                    prev_status = get_nas_status_from_cache(ip)
                    
                    # Update cache
                    set_nas_status_to_cache(ip, status)
                    
                    # Initial state tracking
                    if prev_status is None:
                        continue
                        
                    # State changed!
                    if status != prev_status:
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
