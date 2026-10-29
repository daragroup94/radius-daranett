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
                   COUNT(r.acctsessionid) as session_count,
                   COALESCE(SUM(r.acctinputoctets), 0) as total_in_bytes,
                   COALESCE(SUM(r.acctoutputoctets), 0) as total_out_bytes,
                   COUNT(CASE WHEN LOWER(r.calledstationid) LIKE '%pppoe%' OR LOWER(r.calledstationid) LIKE '%ppp%' THEN 1 END) as pppoe_count,
                   COUNT(CASE WHEN LOWER(r.calledstationid) LIKE '%hotspot%' THEN 1 END) as hotspot_count
            FROM nas n
            LEFT JOIN radacct r ON HOST(r.nasipaddress) = n.nasname AND r.acctstoptime IS NULL
            GROUP BY n.id
            ORDER BY n.id
        """)
        
        from services.nas_monitor import get_nas_status_from_cache, get_nas_status_time_info, redis_client
        for nas in nas_list:
            ip = nas['nasname']
            nas['status'] = get_nas_status_from_cache(ip) or 'offline'
            last_seen, online_since = get_nas_status_time_info(ip)
            nas['last_seen'] = last_seen
            nas['online_since'] = online_since
            try:
                lat = redis_client.get(f"nas_latency:{ip}")
                nas['latency'] = float(lat.decode()) if lat else None
            except Exception:
                nas['latency'] = None

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
        
        # Update status & latency in cache/Redis
        from services.nas_monitor import set_nas_status_to_cache, get_nas_status_time_info, get_ping_latency, redis_client
        set_nas_status_to_cache(nasname, status)
        last_seen, online_since = get_nas_status_time_info(nasname)
        
        latency = None
        if status == 'online':
            latency = get_ping_latency(nasname)
            if latency is not None:
                try:
                    redis_client.set(f"nas_latency:{nasname}", str(latency), ex=300)
                except Exception:
                    pass
        else:
            try:
                redis_client.delete(f"nas_latency:{nasname}")
            except Exception:
                pass
        
        return jsonify({
            'success': True,
            'status': status,
            'latency': latency,
            'last_seen': last_seen,
            'online_since': online_since
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/status', methods=['GET'])
@login_required
def get_nas_status():
    try:
        from services.nas_monitor import get_nas_status_from_cache, get_nas_status_time_info, redis_client
        
        nas_list = execute_query("SELECT * FROM nas ORDER BY id")
        if not nas_list:
            return jsonify({'success': True, 'nas_status': []})
        
        nas_status_list = []
        for nas in nas_list:
            ip = nas['nasname']
            cached_status = get_nas_status_from_cache(ip) or 'offline'
            last_seen, online_since = get_nas_status_time_info(ip)
            try:
                lat = redis_client.get(f"nas_latency:{ip}")
                latency = float(lat.decode()) if lat else None
            except Exception:
                latency = None

            nas_status_list.append({
                'id': nas['id'],
                'nasname': ip,
                'shortname': nas['shortname'],
                'status': cached_status,
                'latency': latency,
                'last_seen': last_seen,
                'online_since': online_since
            })
            
        return jsonify({'success': True, 'nas_status': nas_status_list})

    except Exception as e:
        if 'relation "nas" does not exist' in str(e):
            return jsonify({'success': True, 'nas_status': []})
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/bulk-ping', methods=['POST'])
@login_required
def api_bulk_ping_nas():
    try:
        from concurrent.futures import ThreadPoolExecutor
        from services.nas_monitor import set_nas_status_to_cache, get_nas_status_time_info, get_ping_latency, redis_client

        nas_list = execute_query("SELECT id, nasname, shortname FROM nas ORDER BY id")
        if not nas_list:
            return jsonify({'success': True, 'results': {}})

        results = {}
        def ping_single(nas):
            ip = nas['nasname']
            status = ping_nas(ip)
            set_nas_status_to_cache(ip, status)
            last_seen, online_since = get_nas_status_time_info(ip)
            latency = None
            if status == 'online':
                latency = get_ping_latency(ip)
                if latency is not None:
                    try:
                        redis_client.set(f"nas_latency:{ip}", str(latency), ex=300)
                    except Exception:
                        pass
            else:
                try:
                    redis_client.delete(f"nas_latency:{ip}")
                except Exception:
                    pass

            results[nas['id']] = {
                'id': nas['id'],
                'nasname': ip,
                'shortname': nas['shortname'],
                'status': status,
                'latency': latency,
                'last_seen': last_seen,
                'online_since': online_since
            }

        with ThreadPoolExecutor(max_workers=10) as executor:
            list(executor.map(ping_single, nas_list))

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@nas_bp.route('/api/nas/details/<int:nas_id>', methods=['GET'])
@login_required
def api_nas_details(nas_id):
    try:
        nas_info = execute_query("SELECT * FROM nas WHERE id = %s", (nas_id,))
        if not nas_info:
            return jsonify({'success': False, 'error': 'NAS device not found'}), 404

        nas = nas_info[0]
        ip = nas['nasname']

        # Get active sessions statistics
        summary = execute_query("""
            SELECT 
                COUNT(*) as active_count,
                COALESCE(SUM(acctinputoctets), 0) as total_in_bytes,
                COALESCE(SUM(acctoutputoctets), 0) as total_out_bytes,
                COUNT(CASE WHEN LOWER(calledstationid) LIKE '%pppoe%' OR LOWER(calledstationid) LIKE '%ppp%' THEN 1 END) as pppoe_count,
                COUNT(CASE WHEN LOWER(calledstationid) LIKE '%hotspot%' THEN 1 END) as hotspot_count,
                COUNT(CASE WHEN LOWER(calledstationid) NOT LIKE '%pppoe%' AND LOWER(calledstationid) NOT LIKE '%ppp%' AND LOWER(calledstationid) NOT LIKE '%hotspot%' THEN 1 END) as other_count
            FROM radacct
            WHERE nasipaddress = %s AND acctstoptime IS NULL
        """, (ip,))

        # Get top 5 active consumers
        top_consumers = execute_query("""
            SELECT username, framedipaddress, acctsessiontime,
                   acctinputoctets, acctoutputoctets,
                   (COALESCE(acctinputoctets, 0) + COALESCE(acctoutputoctets, 0)) as total_octets
            FROM radacct
            WHERE nasipaddress = %s AND acctstoptime IS NULL
            ORDER BY total_octets DESC
            LIMIT 5
        """, (ip,))

        from services.nas_monitor import get_nas_status_from_cache, get_nas_status_time_info, redis_client
        cached_status = get_nas_status_from_cache(ip) or 'offline'
        last_seen, online_since = get_nas_status_time_info(ip)
        try:
            lat = redis_client.get(f"nas_latency:{ip}")
            latency = float(lat.decode()) if lat else None
        except Exception:
            latency = None

        return jsonify({
            'success': True,
            'nas': nas,
            'status': cached_status,
            'latency': latency,
            'last_seen': last_seen,
            'online_since': online_since,
            'summary': summary[0] if summary else {},
            'top_consumers': top_consumers or []
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
