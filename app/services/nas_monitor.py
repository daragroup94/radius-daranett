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


def check_odp_outages():
    """Mengecek jika ada ODP dengan >= 3 pelanggan offline secara bersamaan (korelasi padam massal)"""
    try:
        results = execute_query("""
            SELECT 
                ic.id as odp_id,
                ic.name as odp_name,
                COUNT(cc.customer_username) as total_clients,
                SUM(CASE WHEN (SELECT COUNT(*) FROM radacct a WHERE a.username = cc.customer_username AND a.acctstoptime IS NULL) = 0 THEN 1 ELSE 0 END) as offline_clients
            FROM cable_connections cc
            JOIN infrastructure_coordinates ic ON ic.id = cc.odp_id
            GROUP BY ic.id, ic.name
        """) or []
        
        for r in results:
            odp_id = r['odp_id']
            odp_name = r['odp_name']
            total = int(r['total_clients'])
            offline = int(r['offline_clients'])
            
            # Key status di Redis
            redis_key = f"odp_outage_state:{odp_id}"
            
            # Cek status sebelumnya
            prev_status = None
            try:
                cached = redis_client.get(redis_key)
                if cached:
                    prev_status = cached.decode()
            except Exception:
                pass
            
            # Kriteria padam:
            # - Ada minimal 3 pelanggan terhubung ke ODP ini
            # - Minimal 3 pelanggan offline
            # - Persentase pelanggan offline >= 75%
            is_outage = (total >= 3 and offline >= 3 and (offline / total) >= 0.75)
            status = 'outage' if is_outage else 'online'
            
            # Update database status
            execute_query("UPDATE infrastructure_coordinates SET status = %s WHERE id = %s", (status, odp_id), fetch=False)
            
            if prev_status is None:
                # Set initial state
                try:
                    redis_client.set(redis_key, status)
                except Exception:
                    pass
                continue
                
            if status != prev_status:
                try:
                    redis_client.set(redis_key, status)
                except Exception:
                    pass
                
                # Emit socket event
                socketio.emit('odp_outage_update', {
                    'odp_id': odp_id,
                    'odp_name': odp_name,
                    'status': status,
                    'offline_count': offline,
                    'total_count': total
                })
                
                # Kirim Telegram Alert
                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if status == 'outage':
                    percent = (offline / total) * 100
                    msg = (
                        f"⚠️ <b>[NOC ALERT] DUGAAN ODP PADAM (OUTAGE)</b>\n"
                        f"• ODP: <b>{odp_name}</b> (ID: {odp_id})\n"
                        f"• Total Pelanggan: <b>{total}</b>\n"
                        f"• Pelanggan Offline: <b>{offline} ({percent:.1f}%)</b>\n"
                        f"• Waktu Terdeteksi: {time_str}\n\n"
                        f"🚨 Semua atau sebagian besar pelanggan di ODP ini terputus bersamaan. Kemungkinan penyebab: Listrik padam area ODP atau kabel distribusi utama putus."
                    )
                    send_telegram_notification(msg)
                else:
                    msg = (
                        f"✅ <b>RECOVERY: ODP NORMAL KEMBALI</b>\n"
                        f"• ODP: <b>{odp_name}</b> (ID: {odp_id})\n"
                        f"• Waktu Pulih: {time_str}\n\n"
                        f"Koneksi pelanggan di ODP ini telah kembali normal."
                    )
                    send_telegram_notification(msg)
                    
    except Exception as e:
        print(f"[nas-monitor] Error checking ODP outages: {e}")


def get_ping_latency(ip_address):
    """Returns ping latency in ms (float) or None if offline"""
    import subprocess
    import re
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip_address],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1.5
        )
        if result.returncode == 0:
            out = result.stdout.decode()
            match = re.search(r"time=([\d.]+)\s*ms", out)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    
    try:
        import os
        if os.path.exists('/.dockerenv'):
            result = subprocess.run(
                ["nsenter", "-t", "1", "-n", "ping", "-c", "1", "-W", "1", ip_address],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2
            )
            if result.returncode == 0:
                out = result.stdout.decode()
                match = re.search(r"time=([\d.]+)\s*ms", out)
                if match:
                    return float(match.group(1))
    except Exception:
        pass
        
    return None


def background_nas_monitor():
    """Memantau status ping ke setiap IP NAS dan OLT setiap 1 menit."""
    print("[nas-monitor] Thread initialized, sleeping 15s for startup...")
    socketio.sleep(15)
    print("[nas-monitor] Starting NAS and OLT health monitor loop...")
    
    from concurrent.futures import ThreadPoolExecutor
    import json
    
    while True:
        # 1. Monitor NAS
        try:
            nas_list = execute_query("SELECT nasname, shortname FROM nas ORDER BY id")
            if nas_list:
                for nas in nas_list:
                    ip = nas['nasname']
                    name = nas['shortname'] or ip
                    
                    # Test ping
                    status = ping_nas(ip)
                    prev_status = get_nas_status_from_cache(ip)
                    
                    # Store latency in Redis
                    try:
                        latency = get_ping_latency(ip)
                        if latency is not None:
                            redis_client.set(f"nas_latency:{ip}", str(latency), ex=300)
                            socketio.emit('nas_latency_update', {'nasname': ip, 'latency': latency})
                        else:
                            redis_client.delete(f"nas_latency:{ip}")
                            socketio.emit('nas_latency_update', {'nasname': ip, 'latency': None})
                    except Exception as lat_err:
                        print(f"[nas-monitor] Error storing latency: {lat_err}")
                    
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
                            reply_markup = json.dumps({
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
            print(f"[nas-monitor] Error monitoring NAS: {e}")

        # 2. Monitor OLTs in background in parallel
        try:
            olts = execute_query("SELECT id, name, details FROM infrastructure_coordinates WHERE type = 'olt'") or []
            
            def check_olt(olt):
                if olt['details']:
                    try:
                        det = json.loads(olt['details'])
                        ip = det.get('ip_address')
                        if ip:
                            status = ping_nas(ip)
                            execute_query("UPDATE infrastructure_coordinates SET status = %s WHERE id = %s", (status, olt['id']), fetch=False)
                    except Exception as e:
                        print(f"[nas-monitor] Error monitoring OLT {olt['name']}: {e}")
            
            with ThreadPoolExecutor(max_workers=5) as executor:
                list(executor.map(check_olt, olts))
        except Exception as e:
            print(f"[nas-monitor] Error monitoring OLTs: {e}")

        # 3. Monitor ODP outages
        try:
            check_odp_outages()
        except Exception as e:
            print(f"[nas-monitor] Error in check_odp_outages: {e}")
            
        socketio.sleep(60)
