import psutil
from datetime import datetime
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
    """Mark stale sessions:
    - PPPoE: tidak ada interim-update >5 menit → Lost-Carrier
    - non-PPPoE (IPoE/dhcp2/IKEv2): tidak ada update >30 menit → Lost-Carrier
    - MikroTik live sync: cross-reference active sessions with RouterOS PPP active list & DHCP leases.
    """
    socketio.sleep(45)
    while True:
        try:
            # 1. Run live sync with MikroTik routers
            sync_active_sessions_with_routers()

            # 2. PPPoE: interim-update setiap 2 menit, threshold 10 menit (5x interval, toleransi network delay)
            execute_query("""
                UPDATE radacct
                SET acctstoptime = acctupdatetime, acctterminatecause = 'Lost-Carrier'
                WHERE acctstoptime IS NULL
                AND LOWER(calledstationid) LIKE '%pppoe%'
                AND acctupdatetime < NOW() - INTERVAL '10 minutes'
            """, fetch=False)

            # 3. non-PPPoE (L2TP/IPoE/dhcp2): hanya kill jika:
            # - sudah pernah dapat interim-update (acctupdatetime != acctstarttime) DAN tidak update >30 menit, ATAU
            # - belum pernah dapat interim sama sekali DAN sudah connect >2 jam (kemungkinan ghost session)
            execute_query("""
                UPDATE radacct
                SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()),
                    acctterminatecause = 'Lost-Carrier'
                WHERE acctstoptime IS NULL
                AND (calledstationid IS NULL OR LOWER(calledstationid) NOT LIKE '%pppoe%')
                AND (
                    (acctupdatetime != acctstarttime AND acctupdatetime < NOW() - INTERVAL '30 minutes')
                    OR
                    (acctupdatetime = acctstarttime AND acctstarttime < NOW() - INTERVAL '2 hours')
                )
            """, fetch=False)
        except Exception as e:
            print(f"[stale-session-cleaner] Error: {e}")
        socketio.sleep(30)

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
            # Only delete radreply/radusergroup for users that no longer have a radcheck entry at all
            execute_query("""
                DELETE FROM radreply WHERE username NOT IN (
                    SELECT username FROM radcheck
                )
            """, fetch=False)
            execute_query("""
                DELETE FROM radusergroup WHERE username NOT IN (
                    SELECT username FROM radcheck
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


def sync_active_sessions_with_routers():
    """Verify active database sessions against live MikroTik PPP active users and DHCP leases.
    Mark any stale/ghost sessions as disconnected.
    Also, reopen sessions that:
    1. Have acctupdatetime > acctstoptime within the last 15 minutes (indicating interim updates arrived after a stop).
    2. Are active on the router but marked as offline/closed in the database.
    """
    try:
        # 1. Self-healing: Reopen sessions that got interim updates after being marked closed (within last 15 minutes)
        try:
            reopened = execute_query("""
                UPDATE radacct
                SET acctstoptime = NULL, acctterminatecause = NULL
                WHERE acctstoptime IS NOT NULL
                  AND acctupdatetime > acctstoptime
                  AND acctupdatetime > NOW() - INTERVAL '15 minutes'
                RETURNING radacctid, username
            """)
            if reopened:
                for r in reopened:
                    print(f"[session-sync] Reopened session {r['radacctid']} for {r['username']} (interim update received after stop time)")
        except Exception as reopen_err:
            print(f"[session-sync] Error in interim-update self-healing: {reopen_err}")

        # Get active sessions in RADIUS
        active_sessions = execute_query("""
            SELECT radacctid, username, nasipaddress, acctsessionid, calledstationid, acctupdatetime, acctstarttime 
            FROM radacct 
            WHERE acctstoptime IS NULL
        """)
        if active_sessions is None:
            active_sessions = []
            
        # Get active MikroTik integrations
        routers = execute_query("SELECT id, name, host, username, password, port FROM integrations WHERE type='mikrotik' AND is_active=true")
        if not routers:
            return
            
        # Group active sessions by NAS IP
        sessions_by_nas = {}
        for s in active_sessions:
            nas_ip = s['nasipaddress']
            if nas_ip not in sessions_by_nas:
                sessions_by_nas[nas_ip] = []
            sessions_by_nas[nas_ip].append(s)
            
        import librouteros
        for r in routers:
            nas_ip = r['host']
            nas_sessions = sessions_by_nas.get(nas_ip, [])
            
            try:
                api = librouteros.connect(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=2)
                
                # Fetch active PPP users
                active_ppp_users = None
                try:
                    active_ppp = list(api('/ppp/active/print'))
                    active_ppp_users = {p.get('name', '').lower() for p in active_ppp if p.get('name')}
                except Exception as ppp_err:
                    print(f"[session-sync] Error fetching PPP from {r['name']}: {ppp_err}")
                    
                # Fetch active DHCP leases
                active_macs = None
                active_leases = []
                try:
                    active_leases = list(api('/ip/dhcp-server/lease/print'))
                    active_macs = {l.get('mac-address', '').lower().replace(':', '').replace('-', '').replace('_', '') for l in active_leases if l.get('status') == 'bound' and l.get('mac-address')}
                except Exception as lease_err:
                    print(f"[session-sync] Error fetching Leases from {r['name']}: {lease_err}")
                    
                api.close()
                
                # Build sets of active usernames/macs in the DB for this NAS
                db_active_ppp_unames = set()
                db_active_ipoe_macs_clean = set()
                for s in nas_sessions:
                    uname = s['username'].lower() if s.get('username') else ''
                    called = (s.get('calledstationid') or '').lower()
                    is_ppp = 'pppoe' in called or 'l2tp' in called or 'sstp' in called or 'pptp' in called or not called
                    if is_ppp:
                        db_active_ppp_unames.add(uname)
                    else:
                        clean_mac = uname.replace(':', '').replace('-', '').replace('_', '')
                        db_active_ipoe_macs_clean.add(clean_mac)
                
                # --- A. REOPEN SESSIONS (Router shows online, DB shows offline) ---
                
                # 1. PPP Active Users
                if active_ppp_users is not None:
                    for uname_router in active_ppp_users:
                        if uname_router not in db_active_ppp_unames:
                            # Router says active, DB says inactive. Find and reopen last closed session.
                            closed_s = execute_query("""
                                SELECT radacctid FROM radacct
                                WHERE LOWER(username) = %s AND nasipaddress = %s AND acctstoptime IS NOT NULL
                                ORDER BY acctstarttime DESC LIMIT 1
                            """, (uname_router, nas_ip))
                            if closed_s:
                                execute_query("""
                                    UPDATE radacct
                                    SET acctstoptime = NULL, acctterminatecause = NULL
                                    WHERE radacctid = %s
                                """, (closed_s[0]['radacctid'],), fetch=False)
                                print(f"[session-sync] Router active mismatch: Reopened closed PPP session {closed_s[0]['radacctid']} for {uname_router} on NAS {nas_ip}")
                                # Add to our local set so we treat it as active
                                db_active_ppp_unames.add(uname_router)
                                
                # 2. DHCP Active Leases
                if active_macs is not None:
                    for l in active_leases:
                        if l.get('status') == 'bound' and l.get('mac-address'):
                            mac_router = l.get('mac-address', '')
                            mac_clean = mac_router.lower().replace(':', '').replace('-', '').replace('_', '')
                            if mac_clean not in db_active_ipoe_macs_clean:
                                # Find and reopen last closed session for this MAC
                                mac_with_colons = mac_router.upper()
                                mac_without_colons = mac_clean.upper()
                                closed_s = execute_query("""
                                    SELECT radacctid FROM radacct
                                    WHERE (LOWER(username) = %s OR LOWER(username) = %s)
                                      AND nasipaddress = %s
                                      AND acctstoptime IS NOT NULL
                                    ORDER BY acctstarttime DESC LIMIT 1
                                """, (mac_with_colons.lower(), mac_without_colons.lower(), nas_ip))
                                if closed_s:
                                    execute_query("""
                                        UPDATE radacct
                                        SET acctstoptime = NULL, acctterminatecause = NULL
                                        WHERE radacctid = %s
                                    """, (closed_s[0]['radacctid'],), fetch=False)
                                    print(f"[session-sync] Router active mismatch: Reopened closed IPoE session {closed_s[0]['radacctid']} for {mac_router} on NAS {nas_ip}")
                                    db_active_ipoe_macs_clean.add(mac_clean)
                
                # --- B. CLOSE STALE SESSIONS (DB shows online, Router shows offline) ---
                for s in nas_sessions:
                    uname = s['username'].lower() if s.get('username') else ''
                    called = (s.get('calledstationid') or '').lower()
                    is_ppp = 'pppoe' in called or 'l2tp' in called or 'sstp' in called or 'pptp' in called or not called
                    
                    is_online = False
                    if is_ppp:
                        if active_ppp_users is not None:
                            is_online = uname in active_ppp_users
                        else:
                            is_online = True
                    else:
                        if active_macs is not None:
                            clean_uname = uname.replace(':', '').replace('-', '').replace('_', '')
                            is_online = clean_uname in active_macs
                        else:
                            is_online = True
                            
                    if not is_online:
                        # Prevent closing brand new sessions (less than 2 minutes old) to avoid auth race conditions
                        start_time = s.get('acctstarttime')
                        if start_time:
                            if start_time.tzinfo is not None:
                                now_tz = datetime.now(start_time.tzinfo)
                                age_seconds = (now_tz - start_time).total_seconds()
                            else:
                                age_seconds = (datetime.now() - start_time).total_seconds()
                            if age_seconds < 120:
                                continue
                                
                        # Mark session as closed in database
                        stop_time = s.get('acctupdatetime') or datetime.now()
                        execute_query("""
                            UPDATE radacct 
                            SET acctstoptime = %s, 
                                acctterminatecause = 'Lost-Carrier' 
                            WHERE radacctid = %s AND acctstoptime IS NULL
                        """, (stop_time, s['radacctid']), fetch=False)
                        print(f"[session-sync] Marked stale session {s['radacctid']} ({s['username']}) on NAS {nas_ip} as disconnected")
            except Exception as router_err:
                print(f"[session-sync] Error connecting to router {r['name']} ({nas_ip}): {router_err}")
    except Exception as e:
        print(f"[session-sync] Error: {e}")
