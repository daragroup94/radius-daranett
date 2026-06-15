import eventlet
from datetime import datetime
from flask import Blueprint, render_template, jsonify
from decorators import login_required
from database import execute_query
from helpers import get_request_data, ping_nas
from services.telegram import send_telegram_notification

nas_bp = Blueprint('nas', __name__)

@nas_bp.route('/nas')
@login_required
def nas_page():
    return render_template('nas.html')

@nas_bp.route('/api/nas')
@login_required
def api_nas():
    try:
        nas_list = execute_query("""
            SELECT n.*,
                   COUNT(r.acctsessionid) as session_count
            FROM nas n
            LEFT JOIN radacct r ON HOST(r.nasipaddress) = n.nasname AND r.acctstoptime IS NULL
            GROUP BY n.id
            ORDER BY n.id
        """)
        return jsonify(nas_list)
    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify([])
        return jsonify({'error': str(e)}), 500

@nas_bp.route('/api/nas/add', methods=['POST'])
@login_required
def api_add_nas():
    try:
        data = get_request_data()
        
        try:
            execute_query("SELECT limit_proxy_state, require_ma FROM nas LIMIT 1")
            execute_query("""
                INSERT INTO nas (nasname, shortname, type, ports, secret, server, community, description, limit_proxy_state, require_ma)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'auto', 'auto')
            """, (data['nasname'], data['shortname'], data['type'], data['ports'], data['secret'],
                  data.get('server', ''), data.get('community', ''), data.get('description', '')), fetch=False)
        except Exception as inner_e:
            print(f"[api_add_nas] Extended columns not available ({inner_e}), falling back to basic insert")
            execute_query("""
                INSERT INTO nas (nasname, shortname, type, ports, secret, server, community, description)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (data['nasname'], data['shortname'], data['type'], data['ports'], data['secret'],
                  data.get('server', ''), data.get('community', ''), data.get('description', '')), fetch=False)
        
        send_telegram_notification(f"🖥️ <b>NAS Added</b>\nName: {data['nasname']}\nShort: {data['shortname']}\nType: {data['type']}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': 'NAS added successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/edit/<int:nas_id>', methods=['PUT'])
@login_required
def api_edit_nas(nas_id):
    try:
        data = get_request_data()
        
        # Safe integer parsing for ports
        ports = data.get('ports')
        try:
            ports = int(ports) if ports is not None and str(ports).strip() != '' else 1812
        except (ValueError, TypeError):
            ports = 1812

        execute_query("""
            UPDATE nas SET nasname=%s, shortname=%s, type=%s, ports=%s, secret=%s, description=%s
            WHERE id=%s
        """, (data['nasname'], data['shortname'], data['type'], ports,
              data['secret'], data.get('description', ''), nas_id), fetch=False)
        return jsonify({'success': True, 'message': 'NAS updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/delete/<int:nas_id>', methods=['DELETE'])
@login_required
def api_delete_nas(nas_id):
    try:
        nas_info = execute_query("SELECT nasname, shortname FROM nas WHERE id = %s", (nas_id,))
        if nas_info:
            # Check for active sessions on this NAS
            active = execute_query(
                "SELECT COUNT(*) as count FROM radacct WHERE nasipaddress = %s AND acctstoptime IS NULL",
                (nas_info[0]['nasname'],)
            )
            if active and active[0]['count'] > 0:
                return jsonify({
                    'success': False,
                    'error': f"Tidak bisa hapus NAS: masih ada {active[0]['count']} session aktif. Disconnect semua session terlebih dahulu."
                }), 400
        execute_query("DELETE FROM nas WHERE id = %s", (nas_id,), fetch=False)
        if nas_info:
            send_telegram_notification(f"🗑️ <b>NAS Deleted</b>\nName: {nas_info[0]['nasname']}\nShort: {nas_info[0]['shortname']}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': 'NAS deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/ping/<int:nas_id>', methods=['POST'])
@login_required
def api_ping_nas(nas_id):
    try:
        nas_info = execute_query("SELECT nasname FROM nas WHERE id = %s", (nas_id,))
        if not nas_info:
            return jsonify({'success': False, 'error': 'NAS device not found'}), 404
        
        nasname = nas_info[0]['nasname']
        status = ping_nas(nasname)
        
        # Update status in cache/Redis
        from services.nas_monitor import set_nas_status_to_cache, get_nas_status_time_info
        set_nas_status_to_cache(nasname, status)
        last_seen, online_since = get_nas_status_time_info(nasname)
        
        return jsonify({
            'success': True,
            'status': status,
            'last_seen': last_seen,
            'online_since': online_since
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/status', methods=['GET'])
@login_required
def get_nas_status():
    try:
        # Import Redis cache functions
        from services.nas_monitor import get_nas_status_from_cache
        
        nas_list = execute_query("SELECT * FROM nas ORDER BY id")
        if not nas_list:
            return jsonify({'success': True, 'nas_status': []})
        
        nas_status_list = []
        from services.nas_monitor import get_nas_status_time_info
        for nas in nas_list:
            # Get cached status from Redis or default to 'offline'
            cached_status = get_nas_status_from_cache(nas['nasname']) or 'offline'
            last_seen, online_since = get_nas_status_time_info(nas['nasname'])
            nas_status_list.append({
                'id': nas['id'],
                'nasname': nas['nasname'],
                'shortname': nas['shortname'],
                'status': cached_status,
                'last_seen': last_seen,
                'online_since': online_since
            })
            
        return jsonify({'success': True, 'nas_status': nas_status_list})

    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify({'success': True, 'nas_status': []})
        return jsonify({'success': False, 'error': str(e)}), 500
