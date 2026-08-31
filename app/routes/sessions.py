from datetime import datetime
from flask import Blueprint, render_template, jsonify, request, session
from decorators import login_required
from database import execute_query
from services.telegram import send_telegram_notification
from services.mikrotik_coa import send_coa_disconnect

sessions_bp = Blueprint('sessions', __name__)

@sessions_bp.route('/sessions')
@login_required
def sessions_page():
    return render_template('sessions.html')

@sessions_bp.route('/api/sessions')
@login_required
def api_sessions():
    try:

        sessions = execute_query("""
            SELECT radacctid, acctsessionid, acctuniqueid, username,
                   nasipaddress, nasportid, nasporttype, acctstarttime,
                   acctupdatetime, acctsessiontime, acctinputoctets, acctoutputoctets,
                   calledstationid, callingstationid, framedipaddress,
                   framedprotocol, servicetype, connectinfo_start,
                   'active' as status
            FROM radacct
            WHERE acctstoptime IS NULL
            ORDER BY acctstarttime DESC
        """)

        # Fetch live data from MikroTik routers to merge real-time bytes & uptime
        try:
            routers = execute_query("SELECT id, name, host, username, password, port FROM integrations WHERE type='mikrotik' AND is_active=true")
            if routers and sessions:
                from helpers import get_cached_mikrotik
                import re

                def parse_uptime(ut_str):
                    if not ut_str: return 0
                    secs = 0
                    m = re.search(r'(\d+)w', ut_str)
                    if m: secs += int(m.group(1)) * 604800
                    m = re.search(r'(\d+)d', ut_str)
                    if m: secs += int(m.group(1)) * 86400
                    m = re.search(r'(\d+):(\d+):(\d+)', ut_str)
                    if m:
                        secs += int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                    else:
                        m = re.search(r'(\d+)h', ut_str)
                        if m: secs += int(m.group(1)) * 3600
                        m = re.search(r'(\d+)m', ut_str)
                        if m: secs += int(m.group(1)) * 60
                        m = re.search(r'(\d+)s', ut_str)
                        if m: secs += int(m.group(1))
                    return secs

                live_ppp_data = {}
                live_leases = {}

                for r in routers:
                    nas_ip = r['host']
                    try:
                        api = get_cached_mikrotik(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=1.5)
                        
                        # PPP active users
                        try:
                            active_ppp = list(api('/ppp/active/print'))
                        except Exception:
                            active_ppp = []

                        # PPPoE interface bytes
                        try:
                            ifaces = list(api('/interface/print'))
                            iface_map = {}
                            for iface in ifaces:
                                if iface.get('type') in ('pppoe-in', 'sstp-in', 'l2tp-in', 'ovpn-in'):
                                    iname = iface.get('name', '').strip('<>')
                                    uname = iname[6:] if iname.startswith('pppoe-') else iname
                                    iface_map[uname.lower()] = {
                                        'rx_byte': int(iface.get('rx-byte', 0)),
                                        'tx_byte': int(iface.get('tx-byte', 0))
                                    }
                        except Exception:
                            iface_map = {}

                        for s in active_ppp:
                            name = s.get('name', '').lower()
                            live_ppp_data[(nas_ip, name)] = {
                                'address': s.get('address', ''),
                                'uptime_secs': parse_uptime(s.get('uptime', '')),
                                'rx_byte': iface_map.get(name, {}).get('rx_byte', 0),
                                'tx_byte': iface_map.get(name, {}).get('tx_byte', 0)
                            }

                        # DHCP active leases
                        try:
                            leases = list(api('/ip/dhcp-server/lease/print'))
                            for l in leases:
                                mac = l.get('mac-address', '').lower()
                                if mac:
                                    live_leases[(nas_ip, mac)] = {
                                        'address': l.get('active-address') or l.get('address', ''),
                                        'status': l.get('status', '')
                                    }
                        except Exception:
                            pass

                        # Connection cached, do not close
                        pass
                    except Exception as conn_err:
                        print(f"Failed to fetch live stats in API from router {r['name']}: {conn_err}")

                # Merge live data into session structures
                for s in (sessions or []):
                    nas_ip = s['nasipaddress']
                    uname = s['username'].lower()
                    called = (s['calledstationid'] or '').lower()

                    # is_ppp hanya True jika calledstationid ada DAN berisi keyword PPP.
                    # Wireless (calledstationid='MAC:SSID' atau NULL) tidak masuk jalur PPP.
                    is_ppp = bool(called) and (
                        'pppoe' in called or 'l2tp' in called or
                        'sstp' in called or 'pptp' in called
                    )

                    if is_ppp:
                        live_key = (nas_ip, uname)
                        if live_key in live_ppp_data:
                            ldata = live_ppp_data[live_key]
                            s['acctsessiontime'] = ldata['uptime_secs']
                            s['framedipaddress'] = ldata['address']
                            if ldata['rx_byte'] > 0:
                                s['acctinputoctets'] = ldata['rx_byte']
                            if ldata['tx_byte'] > 0:
                                s['acctoutputoctets'] = ldata['tx_byte']
                    else:
                        # IPoE / DHCP / Wireless — coba cocokkan MAC via DHCP lease
                        clean_mac = uname.replace(':', '').replace('-', '').replace('_', '')
                        for (lip, lmac), ldata in live_leases.items():
                            if lip == nas_ip and clean_mac == lmac.replace(':', '').replace('-', '').replace('_', ''):
                                s['framedipaddress'] = ldata['address']
                                break
        except Exception as merge_err:
            print(f"Error merging live session data: {merge_err}")

        # Serialize datetime fields to ISO string for reliable JS parsing
        for s in (sessions or []):
            for field in ('acctstarttime', 'acctupdatetime'):
                if s.get(field) and hasattr(s[field], 'strftime'):
                    s[field] = s[field].strftime('%Y-%m-%dT%H:%M:%S')
        return jsonify(sessions)
    except Exception as e:
        print(f"Error in api_sessions: {str(e)}")
        return jsonify({'error': str(e)}), 500

@sessions_bp.route('/api/sessions/count')
@login_required
def api_sessions_count():
    """Lightweight endpoint for sidebar badge counter."""
    try:
        result = execute_query("""
            SELECT COUNT(*) as count FROM radacct WHERE acctstoptime IS NULL
        """)
        count = result[0]['count'] if result else 0
        return jsonify({'count': count})
    except Exception as e:
        return jsonify({'count': 0})

@sessions_bp.route('/api/sessions/cleanup', methods=['POST'])
@login_required
def api_cleanup_sessions():
    try:
        # UPDATE (bukan DELETE) agar histori sesi tetap tersimpan di database.
        # Hanya menarget PPPoE (pppoe/PPPoE) - L2TP/IPoE/Hotspot tidak boleh dimatikan paksa.
        updated = execute_query("""
            UPDATE radacct
            SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()), acctterminatecause = 'Lost-Carrier'
            WHERE acctstoptime IS NULL
            AND POSITION('pppoe' IN LOWER(calledstationid)) > 0
            AND acctupdatetime < NOW() - INTERVAL '30 minutes'
            RETURNING radacctid
        """)
        
        count = len(updated) if updated else 0
        return jsonify({'success': True, 'message': f'{count} stale PPPoE sessions marked as disconnected (no update for 30+ minutes)'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@sessions_bp.route('/api/sessions/fix-stale', methods=['POST'])
@login_required
def api_fix_stale_sessions():
    try:
        threshold = int(request.json.get('minutes', 15)) if request.is_json else 15
        threshold = max(5, min(threshold, 1440))
        updated = execute_query("""
            UPDATE radacct 
            SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, NOW()), acctterminatecause = 'Lost-Carrier'
            WHERE acctstoptime IS NULL 
            AND POSITION('pppoe' IN LOWER(calledstationid)) > 0
            AND acctupdatetime < NOW() - (INTERVAL '1 minute' * %s)
            RETURNING radacctid, username
        """, (threshold,))
        
        count = len(updated) if updated else 0
        return jsonify({'success': True, 'message': f'{count} stale PPPoE sessions marked as disconnected', 'count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@sessions_bp.route('/api/sessions/disconnect/<session_id>', methods=['POST'])
@login_required
def api_disconnect_session(session_id):
    try:
        session_data = execute_query("""
            SELECT acctsessionid, nasipaddress, framedipaddress, username FROM radacct 
            WHERE acctsessionid = %s AND acctstoptime IS NULL
        """, (session_id,))

        if not session_data:
            return jsonify({'success': False, 'error': 'Session not found'}), 404

        nas_ip = session_data[0]['nasipaddress']
        nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (nas_ip,))
        secret = nas[0]['secret'] if nas else 'radiussecret'

        # Kirim CoA Disconnect ke MikroTik
        coa_sent = send_coa_disconnect(nas_ip, session_id, secret, framed_ip=session_data[0].get('framedipaddress'))

        # Update database
        execute_query("""
            UPDATE radacct 
            SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
            WHERE acctsessionid = %s
        """, (session_id,), fetch=False)

        msg = 'Session disconnected (CoA sent to NAS)' if coa_sent else 'Session marked disconnected (CoA failed, user may still be online)'
        send_telegram_notification(f"🔌 <b>Session Disconnected</b>\nUsername: {session_data[0].get('username', '-')}\nNAS: {nas_ip}\nBy: {session.get('username','admin')}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': msg, 'coa_sent': coa_sent})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@sessions_bp.route('/api/sessions/disconnect-user/<username>', methods=['POST'])
@login_required
def api_disconnect_user_sessions(username):
    try:
        sessions = execute_query("""
            SELECT acctsessionid, nasipaddress, framedipaddress FROM radacct 
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))

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

        return jsonify({
            'success': True,
            'message': f'Disconnected {len(sessions)} sessions for {username} ({coa_success} CoA sent)'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@sessions_bp.route('/api/sessions/duplicates')
@login_required
def api_sessions_duplicates():
    try:
        dupes = execute_query("""
            SELECT username, COUNT(*) as session_count,
                   array_agg(acctsessionid ORDER BY acctstarttime DESC) as session_ids,
                   array_agg(HOST(nasipaddress) ORDER BY acctstarttime DESC) as nas_ips,
                   array_agg(HOST(framedipaddress) ORDER BY acctstarttime DESC) as framed_ips,
                   array_agg(TO_CHAR(acctstarttime AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') ORDER BY acctstarttime DESC) as start_times,
                   COUNT(*) FILTER (WHERE LOWER(calledstationid) LIKE '%pppoe%') as pppoe_count
            FROM radacct
            WHERE acctstoptime IS NULL
            GROUP BY username
            HAVING COUNT(*) > 1
            ORDER BY session_count DESC, username
        """)
        return jsonify(dupes or [])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@sessions_bp.route('/api/sessions/disconnect-expired', methods=['POST'])
@login_required
def api_disconnect_expired():
    try:
        expired_sessions = execute_query("""
            SELECT a.acctsessionid, a.nasipaddress, a.username
            FROM radacct a
            JOIN radcheck rc ON LOWER(rc.username) = LOWER(a.username)
                AND rc.attribute = 'Expiration'
            WHERE a.acctstoptime IS NULL
              AND TO_TIMESTAMP(rc.value, 'Mon DD YYYY HH24:MI:SS') < NOW()
        """)
        if not expired_sessions:
            return jsonify({'success': True, 'message': 'Tidak ada sesi expired yang aktif', 'count': 0})

        coa_ok = 0
        for s in expired_sessions:
            nas_ip = str(s['nasipaddress'])
            nas_row = execute_query("SELECT secret FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
            secret = nas_row[0]['secret'] if nas_row else 'testing123'
            if send_coa_disconnect(nas_ip, s['acctsessionid'], secret):
                coa_ok += 1
            execute_query("""
                UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Session-Timeout'
                WHERE acctsessionid = %s AND acctstoptime IS NULL
            """, (s['acctsessionid'],), fetch=False)

        return jsonify({
            'success': True,
            'message': f'{len(expired_sessions)} sesi expired di-disconnect ({coa_ok} CoA sent)',
            'count': len(expired_sessions)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@sessions_bp.route('/api/sessions/duplicates/disconnect', methods=['POST'])
@login_required
def api_disconnect_duplicates():
    try:
        # Menyisakan session terbaru, menutup semua session lama (duplikat/ghost) di database.
        dupes = execute_query("""
            WITH ranked AS (
                SELECT acctsessionid, nasipaddress, username, framedipaddress,
                       ROW_NUMBER() OVER (PARTITION BY username ORDER BY acctstarttime DESC) AS rn
                FROM radacct 
                WHERE acctstoptime IS NULL 
            )
            SELECT acctsessionid, nasipaddress, username, framedipaddress FROM ranked WHERE rn > 1
        """)
        if not dupes:
            return jsonify({'success': True, 'message': 'Tidak ada session duplikat', 'count': 0})

        count = 0
        for s in dupes:
            # ⚠️  PENTING — JANGAN kirim CoA Disconnect untuk session duplikat/ghost.
            # Alasan:
            # 1. CoA berbasis Framed-IP-Address → IP yang sama sedang dipakai oleh koneksi
            #    REAL user yang baru → MikroTik akan memutus koneksi user asli!
            # 2. Session ID lama sudah tidak ada di router (user sudah reconnect) →
            #    CoA tidak dibutuhkan sama sekali.
            # Cukup tandai sebagai selesai di database saja.
            execute_query("""
                UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                WHERE acctsessionid = %s AND acctstoptime IS NULL
            """, (s['acctsessionid'],), fetch=False)
            count += 1

        return jsonify({
            'success': True,
            'message': f'{count} session duplikat (ghost) ditutup di database tanpa CoA',
            'count': count
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
