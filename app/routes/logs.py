from flask import Blueprint, render_template, request, jsonify
from decorators import login_required
from database import execute_query

logs_bp = Blueprint('logs', __name__)

@logs_bp.route('/logs')
@login_required
def logs_page():
    return render_template('logs.html')

@logs_bp.route('/api/logs')
@login_required
def api_logs():
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        limit = min(limit, 500)  # Cap at 500 to prevent OOM
        search = request.args.get('search', '')
        reply_filter = request.args.get('reply', '')
        service_filter = request.args.get('service', '')
        offset = (page - 1) * limit

        where_clauses = []
        params = []

        if search:
            where_clauses.append("username ILIKE %s")
            params.append(f'%{search}%')
        if reply_filter:
            where_clauses.append("reply = %s")
            params.append(reply_filter)
        if service_filter:
            where_clauses.append("""
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
                ) = %s
            """)
            params.append(service_filter)

        where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

        logs = execute_query(f"""
            SELECT id, username, reply,
                   TO_CHAR(authdate AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as authdate,
                   calledstationid, callingstationid,
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
            {where_sql}
            ORDER BY authdate DESC, id DESC
            LIMIT %s OFFSET %s
        """, tuple(params + [limit, offset]))

        stats = execute_query(f"""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN reply = 'Access-Accept' THEN 1 ELSE 0 END) as accept,
                SUM(CASE WHEN reply = 'Access-Reject' THEN 1 ELSE 0 END) as reject
            FROM public.radpostauth
            {where_sql}
        """, tuple(params))

        total = stats[0]['total'] if stats else 0
        accept = stats[0]['accept'] if stats and stats[0]['accept'] is not None else 0
        reject = stats[0]['reject'] if stats and stats[0]['reject'] is not None else 0

        return jsonify({
            'logs': logs,
            'total': total,
            'accept': accept,
            'reject': reject,
            'page': page,
            'total_pages': (total + limit - 1) // limit
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@logs_bp.route('/api/logs/recent-failures')
@login_required
def api_recent_failures():
    """Lightweight endpoint for sidebar badge: auth failures in last 5 minutes."""
    try:
        result = execute_query("""
            SELECT COUNT(*) as count
            FROM radpostauth
            WHERE reply = 'Access-Reject'
              AND authdate >= NOW() - INTERVAL '5 minutes'
        """)
        count = result[0]['count'] if result else 0
        return jsonify({'count': count})
    except Exception as e:
        return jsonify({'count': 0})

@logs_bp.route('/api/logs/<int:log_id>', methods=['DELETE'])
@login_required
def api_delete_log(log_id):
    """Hapus satu entri log autentikasi."""
    try:
        execute_query("DELETE FROM public.radpostauth WHERE id = %s", (log_id,), fetch=False)
        return jsonify({'success': True, 'message': 'Log berhasil dihapus.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@logs_bp.route('/api/logs/cleanup', methods=['POST'])
@login_required
def api_cleanup_logs():
    """Bersihkan / pangkas riwayat auth log berdasarkan kriteria."""
    try:
        data = request.get_json() or {}
        mode = data.get('mode', 'days')

        if mode == 'all':
            execute_query("DELETE FROM public.radpostauth", fetch=False)
            message = "Semua riwayat auth log berhasil dibersihkan."
        elif mode == 'days':
            days = int(data.get('days', 30))
            if days < 1:
                return jsonify({'success': False, 'error': 'Jumlah hari minimal 1'}), 400
            execute_query("DELETE FROM public.radpostauth WHERE authdate < NOW() - make_interval(days => %s)", (days,), fetch=False)
            message = f"Auth log lebih dari {days} hari berhasil dibersihkan."
        elif mode == 'reject':
            execute_query("DELETE FROM public.radpostauth WHERE reply = 'Access-Reject'", fetch=False)
            message = "Semua riwayat log gagal (Access-Reject) berhasil dibersihkan."
        elif mode == 'username':
            username = data.get('username', '').strip()
            if not username:
                return jsonify({'success': False, 'error': 'Username tidak boleh kosong'}), 400
            execute_query("DELETE FROM public.radpostauth WHERE LOWER(username) = LOWER(%s)", (username,), fetch=False)
            message = f"Semua log untuk user '{username}' berhasil dibersihkan."
        else:
            return jsonify({'success': False, 'error': 'Mode pembersihan tidak valid'}), 400

        return jsonify({'success': True, 'message': message})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


