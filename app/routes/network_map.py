from flask import Blueprint, render_template, jsonify, request
from decorators import login_required
from database import execute_query

network_map_bp = Blueprint('network_map', __name__)

@network_map_bp.route('/network-map')
@login_required
def network_map_page():
    return render_template('network-map.html')

@network_map_bp.route('/api/map/devices')
@login_required
def api_map_devices():
    try:
        # 1. Fetch NAS list and coordinates
        nas_list = execute_query("""
            SELECT n.id, n.nasname, n.shortname, n.type,
                   c.latitude, c.longitude
            FROM nas n
            LEFT JOIN nas_coordinates c ON c.nasname = n.nasname
        """)
        
        nas_map = {}
        for nas in nas_list:
            lat = float(nas['latitude']) if nas['latitude'] is not None else None
            lng = float(nas['longitude']) if nas['longitude'] is not None else None
            nas_map[nas['nasname']] = {
                'id': nas['id'],
                'nasname': nas['nasname'],
                'shortname': nas['shortname'] or nas['nasname'],
                'latitude': lat,
                'longitude': lng,
                'status': 'online'
            }

        # Ensure ont_status table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_status (
                username VARCHAR(100) PRIMARY KEY,
                serial VARCHAR(100),
                model VARCHAR(100),
                manufacturer VARCHAR(100),
                rx_power VARCHAR(20),
                temperature VARCHAR(20),
                last_inform VARCHAR(100),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 2. Fetch ALL Users and coordinates
        users = execute_query("""
            SELECT rc.username, 
                   cc.latitude, cc.longitude,
                   os.rx_power, os.temperature, os.model, os.manufacturer,
                   (SELECT groupname FROM radusergroup g WHERE g.username = rc.username LIMIT 1) as groupname,
                   (SELECT COUNT(*) FROM radacct a WHERE a.username = rc.username AND a.acctstoptime IS NULL) as active_sessions,
                   (SELECT nasipaddress FROM radacct a WHERE a.username = rc.username ORDER BY a.acctstarttime DESC LIMIT 1) as last_nas
            FROM radcheck rc
            LEFT JOIN customer_coordinates cc ON cc.username = rc.username
            LEFT JOIN ont_status os ON os.username = rc.username
            WHERE rc.attribute = 'Cleartext-Password'
        """)

        devices = []
        
        for u in users:
            is_active = u['active_sessions'] > 0
            group = u['groupname'] or 'pppoe'
            last_nas = u['last_nas']
            
            # Check if has coordinates
            has_coords = u['latitude'] is not None and u['longitude'] is not None
            lat = float(u['latitude']) if has_coords else None
            lng = float(u['longitude']) if has_coords else None

            # Signal status determination
            rx = u['rx_power']
            rx_status = 'good'
            if rx and rx != 'N/A':
                try:
                    rx_clean = rx.replace('dBm', '').strip()
                    rx_val = float(rx_clean)
                    if rx_val < -28.0:
                        rx_status = 'bad'
                    elif rx_val < -25.0:
                        rx_status = 'warning'
                except:
                    pass

            devices.append({
                'username': u['username'],
                'type': group,
                'latitude': lat,
                'longitude': lng,
                'has_coordinates': has_coords,
                'is_online': is_active,
                'rx_power': rx or 'N/A',
                'temperature': u['temperature'] or 'N/A',
                'rx_status': rx_status,
                'model': u['model'] or 'N/A',
                'manufacturer': u['manufacturer'] or 'N/A',
                'last_nas': last_nas or 'N/A'
            })

        return jsonify({
            'success': True,
            'nas': list(nas_map.values()),
            'clients': devices
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/update-coordinates', methods=['POST'])
@login_required
def api_update_coordinates():
    try:
        data = request.get_json() or {}
        username = data.get('username')
        nasname = data.get('nasname')
        lat = data.get('latitude')
        lng = data.get('longitude')

        if lat is None or lng is None:
            return jsonify({'success': False, 'error': 'Latitude and longitude are required'}), 400

        try:
            lat = float(lat)
            lng = float(lng)
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
                return jsonify({'success': False, 'error': 'Coordinates out of bounds (-90 to 90 lat, -180 to 180 lng)'}), 400
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Latitude and longitude must be valid float numbers'}), 400

        if username:
            execute_query("""
                INSERT INTO customer_coordinates (username, latitude, longitude, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (username) DO UPDATE 
                SET latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, updated_at = NOW()
            """, (username, lat, lng), fetch=False)
            return jsonify({'success': True, 'message': f'Coordinates updated for client: {username}'})
            
        elif nasname:
            execute_query("""
                INSERT INTO nas_coordinates (nasname, latitude, longitude, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (nasname) DO UPDATE 
                SET latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, updated_at = NOW()
            """, (nasname, lat, lng), fetch=False)
            return jsonify({'success': True, 'message': f'Coordinates updated for NAS: {nasname}'})

        return jsonify({'success': False, 'error': 'Invalid request parameters'}), 400

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/delete-coordinates', methods=['POST'])
@login_required
def api_delete_coordinates():
    try:
        data = request.get_json() or {}
        username = data.get('username')
        nasname = data.get('nasname')

        if username:
            execute_query("DELETE FROM customer_coordinates WHERE username = %s", (username,), fetch=False)
            return jsonify({'success': True, 'message': f'Lokasi pelanggan {username} berhasil dihapus/reset'})
        elif nasname:
            execute_query("DELETE FROM nas_coordinates WHERE nasname = %s", (nasname,), fetch=False)
            return jsonify({'success': True, 'message': f'Lokasi NAS {nasname} berhasil dihapus/reset'})

        return jsonify({'success': False, 'error': 'Invalid request parameters'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/infrastructure', methods=['GET'])
@login_required
def api_get_infrastructure():
    try:
        # Ensure table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.infrastructure_coordinates (
                id SERIAL PRIMARY KEY,
                name VARCHAR(150) NOT NULL,
                type VARCHAR(50) NOT NULL,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        execute_query("""
            ALTER TABLE public.infrastructure_coordinates ADD COLUMN IF NOT EXISTS icon VARCHAR(50)
        """, fetch=False)

        devices = execute_query("""
            SELECT id, name, type, latitude, longitude, details, icon
            FROM infrastructure_coordinates
            ORDER BY name ASC
        """) or []

        for d in devices:
            d['latitude'] = float(d['latitude'])
            d['longitude'] = float(d['longitude'])

        return jsonify({'success': True, 'devices': devices})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/infrastructure/add', methods=['POST'])
@login_required
def api_add_infrastructure():
    try:
        data = request.get_json() or {}
        name = data.get('name')
        dev_type = data.get('type')
        lat = data.get('latitude')
        lng = data.get('longitude')
        details = data.get('details', '')
        icon = data.get('icon', '')

        if not name or not dev_type or lat is None or lng is None:
            return jsonify({'success': False, 'error': 'Nama, tipe, latitude, dan longitude wajib diisi'}), 400

        try:
            lat = float(lat)
            lng = float(lng)
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
                return jsonify({'success': False, 'error': 'Koordinat di luar batas (-90 s/d 90 Lat, -180 s/d 180 Lng)'}), 400
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Latitude dan longitude harus berupa angka desimal valid'}), 400

        if dev_type not in ('olt', 'odc', 'odp', 'nas'):
            return jsonify({'success': False, 'error': 'Tipe infrastruktur tidak valid (olt, odc, odp, nas)'}), 400

        execute_query("""
            INSERT INTO infrastructure_coordinates (name, type, latitude, longitude, details, icon)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (name, dev_type, lat, lng, details, icon), fetch=False)

        return jsonify({'success': True, 'message': f'Infrastruktur {name} berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/infrastructure/update-coordinates', methods=['POST'])
@login_required
def api_update_infra_coordinates():
    try:
        data = request.get_json() or {}
        device_id = data.get('id')
        lat = data.get('latitude')
        lng = data.get('longitude')

        if not device_id or lat is None or lng is None:
            return jsonify({'success': False, 'error': 'ID, latitude, dan longitude wajib diisi'}), 400

        try:
            lat = float(lat)
            lng = float(lng)
            if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
                return jsonify({'success': False, 'error': 'Koordinat di luar batas (-90 s/d 90 Lat, -180 s/d 180 Lng)'}), 400
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Latitude dan longitude harus berupa angka desimal valid'}), 400

        execute_query("""
            UPDATE infrastructure_coordinates
            SET latitude = %s, longitude = %s, updated_at = NOW()
            WHERE id = %s
        """, (lat, lng, device_id), fetch=False)

        return jsonify({'success': True, 'message': 'Posisi infrastruktur berhasil diperbarui'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/infrastructure/delete', methods=['POST'])
@login_required
def api_delete_infrastructure():
    try:
        data = request.get_json() or {}
        device_id = data.get('id')

        if not device_id:
            return jsonify({'success': False, 'error': 'ID infrastruktur wajib diisi'}), 400

        execute_query("DELETE FROM infrastructure_coordinates WHERE id = %s", (device_id,), fetch=False)

        return jsonify({'success': True, 'message': 'Infrastruktur berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/cables', methods=['GET'])
@login_required
def api_get_cables():
    try:
        cables = execute_query("""
            SELECT cc.id, cc.odp_id, cc.customer_username,
                   ic.name as odp_name, ic.latitude as odp_lat, ic.longitude as odp_lng,
                   cust.latitude as customer_lat, cust.longitude as customer_lng
            FROM cable_connections cc
            JOIN infrastructure_coordinates ic ON ic.id = cc.odp_id
            LEFT JOIN customer_coordinates cust ON cust.username = cc.customer_username
            WHERE cust.latitude IS NOT NULL AND cust.longitude IS NOT NULL
        """) or []
        
        for c in cables:
            c['odp_lat'] = float(c['odp_lat'])
            c['odp_lng'] = float(c['odp_lng'])
            c['customer_lat'] = float(c['customer_lat'])
            c['customer_lng'] = float(c['customer_lng'])
        
        return jsonify({'success': True, 'cables': cables})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/cables/add', methods=['POST'])
@login_required
def api_add_cable():
    try:
        data = request.get_json() or {}
        odp_id = data.get('odp_id')
        customer_username = data.get('customer_username')
        
        if not odp_id or not customer_username:
            return jsonify({'success': False, 'error': 'ODP ID dan username customer wajib diisi'}), 400
        
        execute_query("""
            INSERT INTO cable_connections (odp_id, customer_username)
            VALUES (%s, %s)
            ON CONFLICT (odp_id, customer_username) DO NOTHING
        """, (odp_id, customer_username), fetch=False)
        
        return jsonify({'success': True, 'message': 'Kabel berhasil ditambahkan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/cables/delete', methods=['POST'])
@login_required
def api_delete_cable():
    try:
        data = request.get_json() or {}
        cable_id = data.get('id')
        
        if not cable_id:
            return jsonify({'success': False, 'error': 'ID kabel wajib diisi'}), 400
        
        execute_query("DELETE FROM cable_connections WHERE id = %s", (cable_id,), fetch=False)
        
        return jsonify({'success': True, 'message': 'Kabel berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
