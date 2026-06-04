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
        search = request.args.get('search', '')
        reply_filter = request.args.get('reply', '')
        offset = (page - 1) * limit

        where_clauses = []
        params = []

        if search:
            where_clauses.append("username ILIKE %s")
            params.append(f'%{search}%')
        if reply_filter:
            where_clauses.append("reply = %s")
            params.append(reply_filter)

        where_sql = ('WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

        logs = execute_query(f"""
            SELECT id, username, reply,
                   TO_CHAR(authdate AT TIME ZONE 'Asia/Jakarta', 'YYYY-MM-DD HH24:MI:SS') as authdate,
                   calledstationid, callingstationid
            FROM public.radpostauth
            {where_sql}
            ORDER BY authdate DESC, id DESC
            LIMIT %s OFFSET %s
        """, tuple(params + [limit, offset]))

        total = execute_query(f"SELECT COUNT(*) as total FROM public.radpostauth {where_sql}", tuple(params))[0]['total']

        return jsonify({
            'logs': logs,
            'total': total,
            'page': page,
            'total_pages': (total + limit - 1) // limit
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
