import psutil
from extensions import socketio
from database import execute_query
from services.mikrotik_coa import send_coa_disconnect

def api_system_metrics_data():
    """Helper to get system metrics without request context"""
    try:
        cpu_percent = psutil.cpu_percent()
        memory = psutil.virtual_memory()
        # Count connections in pg pool if available
        conn_count = 0
        try:
            res = execute_query("SELECT count(*) as count FROM pg_stat_activity")
            if res:
                conn_count = res[0]['count']
        except:
            pass
            
        return {
            'cpu_percent': round(cpu_percent, 1),
            'memory_percent': round(memory.percent, 1),
            'db_connections': conn_count,
            'db_max_connections': 100,
            'disk_percent': round(psutil.disk_usage('/').percent, 1)
        }
    except Exception as e:
        print(f"Error in metrics read: {e}")
        return {
            'cpu_percent': 0,
            'memory_percent': 0,
            'db_connections': 0,
            'db_max_connections': 100,
            'disk_percent': 0
        }

def background_metrics():
    """Emit system metrics every 5 seconds via socketio background task"""
    while True:
        try:
            metrics = api_system_metrics_data()
            socketio.emit('system_metrics', metrics)
        except Exception as e:
            print(f"Error in background metrics: {e}")
        socketio.sleep(5)

def background_stale_sessions():
    """Mark stale sessions setelah 24 jam tidak ada interim-update."""
    socketio.sleep(60)
    while True:
        try:
            execute_query("""
                UPDATE radacct
                SET acctstoptime = acctupdatetime, acctterminatecause = 'Lost-Carrier'
                WHERE acctstoptime IS NULL
                AND acctupdatetime < NOW() - INTERVAL '24 hours'
            """, fetch=False)
        except Exception as e:
            print(f"[stale-session-cleaner] Error: {e}")
        socketio.sleep(300)

def background_expired_disconnect():
    """Setiap 60 detik: disconnect sesi aktif milik user yang Expiration-nya sudah lewat, lalu hapus akun."""
    socketio.sleep(30)
    while True:
        try:
            expired_sessions = execute_query("""
                SELECT a.acctsessionid, a.nasipaddress, a.username, a.framedipaddress
                FROM radacct a
                JOIN radcheck rc ON LOWER(rc.username) = LOWER(a.username) AND rc.attribute = 'Expiration'
                WHERE a.acctstoptime IS NULL
                  AND to_timestamp(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
            """)
            for s in (expired_sessions or []):
                nas_ip = str(s['nasipaddress'])
                nas_row = execute_query("SELECT secret FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
                secret = nas_row[0]['secret'] if nas_row else 'testing123'
                ok = send_coa_disconnect(nas_ip, s['acctsessionid'], secret, framed_ip=s.get('framedipaddress'))
                execute_query("UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Session-Timeout' WHERE acctsessionid = %s AND acctstoptime IS NULL",
                              (s['acctsessionid'],), fetch=False)
                print(f"[expired-disconnect] {s['username']} @ {nas_ip} | CoA={'OK' if ok else 'FAIL'}")

            execute_query("""
                DELETE FROM radcheck WHERE username IN (
                    SELECT rc.username FROM radcheck rc
                    WHERE rc.attribute = 'Expiration'
                      AND to_timestamp(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
                      AND NOT EXISTS (SELECT 1 FROM radacct a WHERE a.username = rc.username AND a.acctstoptime IS NULL)
                )
            """, fetch=False)
            execute_query("""
                DELETE FROM radreply WHERE username NOT IN (
                    SELECT username FROM radcheck WHERE attribute = 'Cleartext-Password'
                )
            """, fetch=False)
            execute_query("""
                DELETE FROM radusergroup WHERE username NOT IN (
                    SELECT username FROM radcheck WHERE attribute = 'Cleartext-Password'
                )
            """, fetch=False)
        except Exception as e:
            print(f"[expired-disconnect] Error: {e}")
        socketio.sleep(60)

def background_auth_log_tail():
    """Poll radpostauth for new logs and emit them via Socket.IO"""
    print("[auth-log-tail] Initializing log tail worker...")
    socketio.sleep(10)
    
    last_id = 0
    try:
        res = execute_query("SELECT MAX(id) as max_id FROM public.radpostauth")
        if res and res[0]['max_id']:
            last_id = res[0]['max_id']
    except Exception as e:
        print(f"[auth-log-tail] Error fetching max log id: {e}")
        
    print(f"[auth-log-tail] Starting tail from ID: {last_id}")
    while True:
        try:
            new_logs = execute_query(
                "SELECT id, username, reply, authdate, callingstationid FROM public.radpostauth WHERE id > %s ORDER BY id ASC LIMIT 50",
                (last_id,)
            )
            if new_logs:
                for log in new_logs:
                    last_id = max(last_id, log['id'])
                    # Convert date to string format safely
                    if log['authdate']:
                        if hasattr(log['authdate'], 'strftime'):
                            log['authdate'] = log['authdate'].strftime('%Y-%m-%d %H:%M:%S')
                        else:
                            log['authdate'] = str(log['authdate'])
                    socketio.emit('new_auth_log', log)
        except Exception as e:
            print(f"[auth-log-tail] Error polling auth logs: {e}")
            
        socketio.sleep(2)
