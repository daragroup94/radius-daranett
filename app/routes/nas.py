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
        except:
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
        execute_query("""
            UPDATE nas SET nasname=%s, shortname=%s, type=%s, ports=%s, secret=%s, description=%s
            WHERE id=%s
        """, (data['nasname'], data['shortname'], data['type'], int(data['ports']),
              data['secret'], data.get('description', ''), nas_id), fetch=False)
        return jsonify({'success': True, 'message': 'NAS updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/delete/<int:nas_id>', methods=['DELETE'])
@login_required
def api_delete_nas(nas_id):
    try:
        nas_info = execute_query("SELECT nasname, shortname FROM nas WHERE id = %s", (nas_id,))
        execute_query("DELETE FROM nas WHERE id = %s", (nas_id,), fetch=False)
        if nas_info:
            send_telegram_notification(f"🗑️ <b>NAS Deleted</b>\nName: {nas_info[0]['nasname']}\nShort: {nas_info[0]['shortname']}\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return jsonify({'success': True, 'message': 'NAS deleted successfully'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/status', methods=['GET'])
@login_required
def get_nas_status():
    try:
        nas_list = execute_query("SELECT * FROM nas ORDER BY id")
        if not nas_list:
            return jsonify({'success': True, 'nas_status': []})
            
        pool = eventlet.GreenPool(50)
        
        def _check_single_nas(nas):
            status = ping_nas(nas['nasname'])
            return {
                'id': nas['id'],
                'nasname': nas['nasname'],
                'shortname': nas['shortname'],
                'status': status
            }
            
        nas_status_list = list(pool.imap(_check_single_nas, nas_list))
        return jsonify({'success': True, 'nas_status': nas_status_list})

    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify({'success': True, 'nas_status': []})
        return jsonify({'success': False, 'error': str(e)}), 500
