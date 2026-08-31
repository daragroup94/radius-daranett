import psutil
from datetime import datetime
from extensions import socketio
from database import execute_query
from services.mikrotik_coa import send_coa_disconnect

_latest_cpu_percent = 0.0

def get_latest_cpu_percent():
    global _latest_cpu_percent
    return _latest_cpu_percent

def api_system_metrics_data():
    """Helper to get system metrics without request context"""
    global _latest_cpu_percent
    try:
        cpu_percent = psutil.cpu_percent()
        if cpu_percent is not None:
            _latest_cpu_percent = cpu_percent
        memory = psutil.virtual_memory()
        # Count connections in pg pool if available
        conn_count = 0
        try:
            res = execute_query("SELECT count(*) as count FROM pg_stat_activity")
            if res:
                conn_count = res[0]['count']
        except Exception:
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

            # 2. PPPoE (Dihapus): Pembersihan PPPoE dimatikan di Python untuk mencegah bug dan redundancy.
            # Pembersihan PPPoE sekarang sepenuhnya ditangani secara stabil oleh cronjob (cleanup-stale-sessions.sh).

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
            new_logs = execute_query("""
                SELECT id, username, reply, authdate, callingstationid,
                       COALESCE(
                           (SELECT CASE 
                                WHEN g.groupname = 'ipoe' THEN 'IPoE'
                                WHEN g.groupname = 'vpn' THEN 'VPN'
                                WHEN g.groupname = '8021x' THEN '802.1X'
                                WHEN g.groupname = 'hotspot' THEN 'Hotspot'
                                WHEN g.groupname = 'wireless' THEN 'Wireless'
                                WHEN g.groupname IN ('mikrotik-full', 'mikrotik-write', 'mikrotik-read') THEN 'MikroTik Admin'
                                ELSE 'PPPoE'
                            END
                            FROM radusergroup g 
                            WHERE LOWER(g.username) = LOWER(radpostauth.username) 
                            LIMIT 1
                           ), 'PPPoE'
                       ) as service
                FROM public.radpostauth 
                WHERE id > %s 
                ORDER BY id ASC 
                LIMIT 50
            """, (last_id,))
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
        # 1. Self-healing: Reopen sessions that got interim updates after being marked closed (within last 15 minutes).
        # HANYA berlaku untuk PPPoE — wireless/IPoE/L2TP tidak menggunakan pola interim-update yang sama.
        # Guard tambahan: NOT EXISTS sesi aktif lain untuk username yg sama, mencegah double session.
        try:
            reopened = execute_query("""
                UPDATE radacct
                SET acctstoptime = NULL, acctterminatecause = NULL
                WHERE acctstoptime IS NOT NULL
                  AND LOWER(calledstationid) LIKE '%pppoe%'
                  AND acctupdatetime > acctstoptime
                  AND acctupdatetime > NOW() - INTERVAL '10 minutes'
                  AND acctstoptime > NOW() - INTERVAL '10 minutes'
                  AND NOT EXISTS (
                      SELECT 1 FROM radacct r2
                      WHERE LOWER(r2.username) = LOWER(radacct.username)
                        AND r2.nasipaddress = radacct.nasipaddress
                        AND r2.acctstoptime IS NULL
                        AND r2.radacctid != radacct.radacctid
                  )
                RETURNING radacctid, username
            """)
            if reopened:
                for r in reopened:
                    print(f"[session-sync] Reopened PPPoE session {r['radacctid']} for {r['username']} (interim update received after stop time)")
        except Exception as reopen_err:
            print(f"[session-sync] Error in interim-update self-healing: {reopen_err}")

        # Get active sessions in RADIUS
        # [EDIT 1] Tambah callingstationid agar MAC address client wireless bisa diambil
        active_sessions = execute_query("""
            SELECT radacctid, username, nasipaddress, acctsessionid, calledstationid, callingstationid, acctupdatetime, acctstarttime 
            FROM radacct 
            WHERE acctstoptime IS NULL
        """)
        if active_sessions is None:
            active_sessions = []
            
        # Build sets of all globally active usernames and MAC addresses in DB to prevent double sessions on reopen
        global_active_unames = {s['username'].lower() for s in active_sessions if s.get('username')}
        global_active_macs = set()
        for s in active_sessions:
            mac = (s.get('callingstationid') or '').replace(':', '').replace('-', '').replace('_', '').lower()
            if mac:
                global_active_macs.add(mac)
            uname = s['username'].lower() if s.get('username') else ''
            clean_uname = uname.replace(':', '').replace('-', '').replace('_', '').lower()
            if len(clean_uname) == 12:
                global_active_macs.add(clean_uname)
            
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
            
        from helpers import connect_mikrotik
        for r in routers:
            nas_ip = r['host']
            nas_sessions = sessions_by_nas.get(nas_ip, [])
            
            try:
                api = connect_mikrotik(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=10)
                
                # Fetch active PPP users and sessions
                active_ppp_users = None
                active_ppp_sessions = {}
                try:
                    active_ppp = list(api('/ppp/active/print'))
                    active_ppp_users = {p.get('name', '').lower() for p in active_ppp if p.get('name')}
                    for p in active_ppp:
                        uname = p.get('name', '').lower()
                        sid = p.get('session-id')
                        if uname and sid:
                            clean_sid = sid.lower().replace('0x', '')
                            if uname not in active_ppp_sessions:
                                active_ppp_sessions[uname] = set()
                            active_ppp_sessions[uname].add(clean_sid)
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

                # [EDIT 2] Fetch active Wireless clients dari registration table Mikrotik
                active_wireless_macs = None
                try:
                    api2 = connect_mikrotik(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=10)
                    wireless_macs = set()
                    for endpoint in ['/interface/wireless/registration-table/print', '/interface/wifi/registration-table/print']:
                        try:
                            for w in api2(endpoint):
                                mac = w.get('mac-address', '').lower().replace(':', '').replace('-', '').replace('_', '')
                                if mac:
                                    wireless_macs.add(mac)
                        except Exception:
                            pass
                    api2.close()
                    active_wireless_macs = wireless_macs
                except Exception as w_err:
                    print(f"[session-sync] Error fetching Wireless from {r['name']}: {w_err}")
                
                # Build sets of active usernames/macs in the DB for this NAS.
                # Catatan klasifikasi tipe sesi:
                #   - PPPoE/L2TP/SSTP/PPTP: calledstationid berisi keyword protokol
                #   - Wireless (802.1X/WPA-Enterprise): calledstationid format "MAC:SSID" (mengandung ':')
                #   - IPoE/DHCP: calledstationid berisi nama interface/pool, tanpa ':'
                #   - NULL calledstationid: TIDAK dianggap PPP — bisa wireless/IPoE baru
                db_active_ppp_unames = set()
                db_active_ipoe_macs_clean = set()
                for s in nas_sessions:
                    uname = s['username'].lower() if s.get('username') else ''
                    called = (s.get('calledstationid') or '').lower()
                    # is_ppp hanya True jika calledstationid benar-benar ada DAN berisi kata kunci PPP
                    is_ppp = bool(called) and (
                        'pppoe' in called or 'l2tp' in called or
                        'sstp' in called or 'pptp' in called
                    )
                    if is_ppp:
                        db_active_ppp_unames.add(uname)
                    else:
                        # IPoE + Wireless: keduanya kejar MAC dari username atau callingstationid
                        clean_mac = uname.replace(':', '').replace('-', '').replace('_', '').lower()
                        db_active_ipoe_macs_clean.add(clean_mac)
                        calling_mac = (s.get('callingstationid') or '').replace(':', '').replace('-', '').replace('_', '').lower()
                        if calling_mac:
                            db_active_ipoe_macs_clean.add(calling_mac)
                
                # --- A. CLOSE STALE SESSIONS (DB shows online, Router shows offline) ---
                # Jalankan Close block terlebih dahulu agar jika ada session ganda (misal session lama yang ghost),
                # session tersebut ditutup dulu dari database. Dengan begitu, sisa session aktif di database
                # akan akurat sebelum kita mengevaluasi block Reopen.
                closed_any = False
                for s in nas_sessions:
                    uname = s['username'].lower() if s.get('username') else ''
                    called = (s.get('calledstationid') or '').lower()
                    # Konsisten dengan blok Build Sets di atas:
                    # is_ppp hanya True jika calledstationid ada DAN mengandung keyword PPP
                    is_ppp = bool(called) and (
                        'pppoe' in called or 'l2tp' in called or
                        'sstp' in called or 'pptp' in called
                    )
                    # Wireless: calledstationid format "MAC:SSID" mengandung ':', ATAU NULL (sesi awal wireless)
                    is_wireless = (':' in called or not called) and not is_ppp
                    
                    is_online = False
                    if is_ppp:
                        if active_ppp_users is not None:
                            # Cek secara presisi menggunakan session ID jika tersedia
                            router_sids = active_ppp_sessions.get(uname, set())
                            db_sid = (s.get('acctsessionid') or '').lower()
                            if router_sids and db_sid:
                                is_online = db_sid in router_sids
                            else:
                                is_online = uname in active_ppp_users
                        else:
                            is_online = True
                    # [EDIT 4] Blok khusus wireless, tidak menyentuh logika IPoE di bawahnya
                    elif is_wireless:
                        mac = (s.get('callingstationid') or '').replace(':', '').replace('-', '').replace('_', '').lower()
                        if active_wireless_macs:
                            is_online = mac in active_wireless_macs
                        else:
                            is_online = True
                    else:
                        if active_macs is not None:
                            mac = s.get('callingstationid') or ''
                            clean_mac = mac.replace(':', '').replace('-', '').replace('_', '').lower()
                            clean_uname = uname.replace(':', '').replace('-', '').replace('_', '').lower()
                            is_online = (clean_uname in active_macs) or (clean_mac in active_macs)
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
                        closed_any = True
                    else:
                        # Session is confirmed online on router. Prevent stale-session cleaner from closing it.
                        last_update = s.get('acctupdatetime') or s.get('acctstarttime')
                        needs_update = False
                        if not last_update:
                            needs_update = True
                        else:
                            if last_update.tzinfo is not None:
                                age = (datetime.now(last_update.tzinfo) - last_update).total_seconds()
                            else:
                                age = (datetime.now() - last_update).total_seconds()
                            if age > 300: # 5 minutes
                                needs_update = True
                        if needs_update:
                            execute_query("""
                                UPDATE radacct
                                SET acctupdatetime = NOW()
                                WHERE radacctid = %s
                            """, (s['radacctid'],), fetch=False)

                if closed_any:
                    # Query ulang database untuk mendapatkan status session aktif terbaru pasca pembersihan session stale
                    active_sessions_fresh = execute_query("""
                        SELECT radacctid, username, nasipaddress, acctsessionid, calledstationid, callingstationid, acctupdatetime, acctstarttime 
                        FROM radacct 
                        WHERE acctstoptime IS NULL
                    """) or []
                    global_active_unames = {s['username'].lower() for s in active_sessions_fresh if s.get('username')}
                    global_active_macs = set()
                    db_active_ppp_unames = set()
                    db_active_ipoe_macs_clean = set()
                    for s in active_sessions_fresh:
                        mac = (s.get('callingstationid') or '').replace(':', '').replace('-', '').replace('_', '').lower()
                        if mac:
                            global_active_macs.add(mac)
                        uname = s['username'].lower() if s.get('username') else ''
                        clean_uname = uname.replace(':', '').replace('-', '').replace('_', '').lower()
                        if len(clean_uname) == 12:
                            global_active_macs.add(clean_uname)
                            
                        if s['nasipaddress'] == nas_ip:
                            called = (s.get('calledstationid') or '').lower()
                            is_ppp = bool(called) and (
                                'pppoe' in called or 'l2tp' in called or
                                'sstp' in called or 'pptp' in called
                            )
                            if is_ppp:
                                db_active_ppp_unames.add(uname)
                            else:
                                db_active_ipoe_macs_clean.add(clean_uname)
                                calling_mac = (s.get('callingstationid') or '').replace(':', '').replace('-', '').replace('_', '').lower()
                                if calling_mac:
                                    db_active_ipoe_macs_clean.add(calling_mac)

                # --- B. REOPEN SESSIONS (Router shows online, DB shows offline) ---
                
                # 1. PPP Active Users
                if active_ppp_users is not None:
                    for uname_router in active_ppp_users:
                        # Only reopen if user is neither active on this NAS in DB nor globally active in DB
                        if uname_router not in db_active_ppp_unames and uname_router not in global_active_unames:
                            # ⚠️ Fresh DB check untuk mencegah race condition:
                            # FreeRADIUS mungkin sudah buat session baru SETELAH query awal dijalankan.
                            # Jika ada session aktif baru, SKIP reopen untuk menghindari double session.
                            fresh_active = execute_query("""
                                SELECT COUNT(*) as cnt FROM radacct
                                WHERE LOWER(username) = %s AND nasipaddress = %s AND acctstoptime IS NULL
                            """, (uname_router, nas_ip))
                            if fresh_active and fresh_active[0]['cnt'] > 0:
                                # Session baru sudah ada (dibuat FreeRADIUS saat sync berjalan) — skip
                                db_active_ppp_unames.add(uname_router)
                                global_active_unames.add(uname_router)
                                continue
                            # Router says active, DB says inactive. Find and reopen last closed session.
                            router_sids = active_ppp_sessions.get(uname_router, set())
                            closed_s = None
                            if router_sids:
                                closed_s = execute_query("""
                                    SELECT radacctid FROM radacct
                                    WHERE LOWER(username) = %s 
                                      AND nasipaddress = %s 
                                      AND acctsessionid = ANY(%s) 
                                      AND acctstoptime IS NOT NULL
                                    ORDER BY acctstarttime DESC LIMIT 1
                                """, (uname_router, nas_ip, list(router_sids)))
                            if not closed_s:
                                closed_s = execute_query("""
                                    SELECT radacctid FROM radacct
                                    WHERE LOWER(username) = %s AND nasipaddress = %s AND acctstoptime IS NOT NULL
                                    ORDER BY acctstarttime DESC LIMIT 1
                                """, (uname_router, nas_ip))
                            if closed_s:
                                execute_query("""
                                    UPDATE radacct
                                    SET acctstoptime = NULL, acctterminatecause = NULL, acctupdatetime = NOW()
                                    WHERE radacctid = %s
                                """, (closed_s[0]['radacctid'],), fetch=False)
                                print(f"[session-sync] Router active mismatch: Reopened closed PPP session {closed_s[0]['radacctid']} for {uname_router} on NAS {nas_ip}")
                                # Add to our local set so we treat it as active
                                db_active_ppp_unames.add(uname_router)
                                global_active_unames.add(uname_router)
                                
                # 2. DHCP Active Leases
                if active_macs is not None:
                    for l in active_leases:
                        if l.get('status') == 'bound' and l.get('mac-address'):
                            mac_router = l.get('mac-address', '')
                            mac_clean = mac_router.lower().replace(':', '').replace('-', '').replace('_', '')
                            # Only reopen if MAC is neither active on this NAS in DB nor globally active in DB
                            if mac_clean not in db_active_ipoe_macs_clean and mac_clean not in global_active_macs:
                                # ⚠️ Fresh DB check untuk mencegah race condition (sama seperti PPP di atas)
                                fresh_active = execute_query("""
                                    SELECT COUNT(*) as cnt FROM radacct
                                    WHERE REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g') = %s
                                      AND nasipaddress = %s AND acctstoptime IS NULL
                                """, (mac_clean, nas_ip))
                                if fresh_active and fresh_active[0]['cnt'] > 0:
                                    db_active_ipoe_macs_clean.add(mac_clean)
                                    global_active_macs.add(mac_clean)
                                    continue
                                # Find and reopen last closed session for this MAC
                                closed_s = execute_query("""
                                    SELECT radacctid FROM radacct
                                    WHERE REGEXP_REPLACE(LOWER(username), '[^a-z0-9]', '', 'g') = %s
                                      AND nasipaddress = %s
                                      AND acctstoptime IS NOT NULL
                                    ORDER BY acctstarttime DESC LIMIT 1
                                """, (mac_clean, nas_ip))
                                if closed_s:
                                    execute_query("""
                                        UPDATE radacct
                                        SET acctstoptime = NULL, acctterminatecause = NULL, acctupdatetime = NOW()
                                        WHERE radacctid = %s
                                    """, (closed_s[0]['radacctid'],), fetch=False)
                                    print(f"[session-sync] Router active mismatch: Reopened closed IPoE session {closed_s[0]['radacctid']} for {mac_router} on NAS {nas_ip}")
                                    db_active_ipoe_macs_clean.add(mac_clean)
                                    global_active_macs.add(mac_clean)
            except Exception as router_err:
                print(f"[session-sync] Error connecting to router {r['name']} ({nas_ip}): {router_err}")
    except Exception as e:
        print(f"[session-sync] Error: {e}")


_last_online_users = set()

def background_map_status_sync():
    """Poll database every 5 seconds to find changes in user online status, then emit to socketio"""
    global _last_online_users
    socketio.sleep(10) # wait for startup
    
    # Initialize initial state
    try:
        active = execute_query("SELECT DISTINCT username FROM radacct WHERE acctstoptime IS NULL") or []
        _last_online_users = {a['username'] for a in active}
    except Exception as e:
        print(f"[map-status-sync] Error initializing: {e}")
        
    while True:
        try:
            active = execute_query("SELECT DISTINCT username FROM radacct WHERE acctstoptime IS NULL") or []
            current_online = {a['username'] for a in active}
            
            # Detect logins (online now, but was offline)
            logins = current_online - _last_online_users
            for username in logins:
                socketio.emit('client_status_update', {'username': username, 'is_online': True})
                print(f"[map-status-sync] Event: {username} went online")
                
            # Detect logouts (offline now, but was online)
            logouts = _last_online_users - current_online
            for username in logouts:
                socketio.emit('client_status_update', {'username': username, 'is_online': False})
                print(f"[map-status-sync] Event: {username} went offline")
                
            _last_online_users = current_online
        except Exception as e:
            print(f"[map-status-sync] Error: {e}")
            
        socketio.sleep(5)
