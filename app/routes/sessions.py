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
            SELECT DISTINCT ON (username, acctsessionid)
                   radacctid, acctsessionid, acctuniqueid, username,
                   nasipaddress, nasportid, nasporttype, acctstarttime,
                   acctupdatetime, acctsessiontime, acctinputoctets, acctoutputoctets,
                   calledstationid, callingstationid, framedipaddress,
                   framedprotocol, servicetype, connectinfo_start,
                   CASE WHEN acctstoptime IS NULL THEN 'active' ELSE 'inactive' END as status
            FROM radacct
            WHERE acctstoptime IS NULL
            ORDER BY username, acctsessionid, acctstarttime DESC
        """)
        return jsonify(sessions)
    except Exception as e:
        print(f"Error in api_sessions: {str(e)}")
        return jsonify({'error': str(e)}), 500

@sessions_bp.route('/api/sessions/cleanup', methods=['POST'])
@login_required
def api_cleanup_sessions():
    try:
        deleted = execute_query("""
            DELETE FROM radacct 
            WHERE acctstoptime IS NULL 
            AND acctupdatetime < NOW() - INTERVAL '30 minutes'
            RETURNING radacctid
        """)
        
        count = len(deleted) if deleted else 0
        return jsonify({'success': True, 'message': f'{count} stale sessions cleaned up (no update for 30+ minutes)'})
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
            SET acctstoptime = acctupdatetime, acctterminatecause = 'Lost-Carrier'
            WHERE acctstoptime IS NULL 
            AND acctupdatetime < NOW() - (INTERVAL '1 minute' * %s)
            RETURNING radacctid, username
        """, (threshold,))
        
        count = len(updated) if updated else 0
        return jsonify({'success': True, 'message': f'{count} stale sessions marked as disconnected', 'count': count})
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
            SELECT acctsessionid, nasipaddress FROM radacct 
            WHERE username = %s AND acctstoptime IS NULL
        """, (username,))

        coa_success = 0
        for s in sessions:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret):
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
                   array_agg(nasipaddress ORDER BY acctstarttime DESC) as nas_ips,
                   array_agg(framedipaddress ORDER BY acctstarttime DESC) as framed_ips,
                   array_agg(acctstarttime ORDER BY acctstarttime DESC) as start_times
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
        dupes = execute_query("""
            WITH ranked AS (
                SELECT acctsessionid, nasipaddress, username,
                       ROW_NUMBER() OVER (PARTITION BY username ORDER BY acctstarttime DESC) AS rn
                FROM radacct WHERE acctstoptime IS NULL
            )
            SELECT acctsessionid, nasipaddress, username FROM ranked WHERE rn > 1
        """)
        if not dupes:
            return jsonify({'success': True, 'message': 'Tidak ada session duplikat', 'count': 0})

        coa_success = 0
        for s in dupes:
            nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
            secret = nas[0]['secret'] if nas else 'radiussecret'
            if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret):
                coa_success += 1
            execute_query("""
                UPDATE radacct SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                WHERE acctsessionid = %s
            """, (s['acctsessionid'],), fetch=False)

        return jsonify({
            'success': True,
            'message': f'{len(dupes)} session duplikat di-disconnect ({coa_success} CoA sent)',
            'count': len(dupes)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
