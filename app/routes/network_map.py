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
        # Ensure coordinates tables exist
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.customer_coordinates (
                username VARCHAR(100) PRIMARY KEY,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        execute_query("""
            CREATE TABLE IF NOT EXISTS public.nas_coordinates (
                nasname VARCHAR(100) PRIMARY KEY,
                latitude NUMERIC(12, 9) NOT NULL,
                longitude NUMERIC(12, 9) NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 1. Fetch NAS list and coordinates
        nas_list = execute_query("""
            SELECT n.id, n.nasname, n.shortname, n.type,
                   c.latitude, c.longitude
            FROM nas n
            LEFT JOIN nas_coordinates c ON c.nasname = n.nasname
            WHERE n.nasname NOT ILIKE '%%test%%' AND n.nasname NOT ILIKE '%%demo%%' AND n.nasname NOT ILIKE '%%mock%%' AND n.nasname NOT ILIKE '%%dummy%%'
              AND n.shortname NOT ILIKE '%%test%%' AND n.shortname NOT ILIKE '%%demo%%' AND n.shortname NOT ILIKE '%%mock%%' AND n.shortname NOT ILIKE '%%dummy%%'
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

        # Ensure ont_serial_mapping table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_serial_mapping (
                username VARCHAR(100) PRIMARY KEY,
                serial_number VARCHAR(100) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        # 2. Fetch ALL Users and coordinates
        users = execute_query("""
            SELECT rc.username, 
                   cc.latitude, cc.longitude,
                   os.rx_power, os.temperature, os.model, os.manufacturer,
                   COALESCE(osm.serial_number, os.serial) as ont_serial,
                   (SELECT groupname FROM radusergroup g WHERE g.username = rc.username LIMIT 1) as groupname,
                   (SELECT COUNT(*) FROM radacct a WHERE a.username = rc.username AND a.acctstoptime IS NULL) as active_sessions,
                   (SELECT nasipaddress FROM radacct a WHERE a.username = rc.username ORDER BY a.acctstarttime DESC LIMIT 1) as last_nas
            FROM radcheck rc
            LEFT JOIN customer_coordinates cc ON cc.username = rc.username
            LEFT JOIN ont_serial_mapping osm ON osm.username = rc.username
            LEFT JOIN ont_status os ON (os.username = rc.username OR os.serial = osm.serial_number)
            WHERE rc.attribute = 'Cleartext-Password'
              AND rc.username NOT ILIKE '%%test%%'
              AND rc.username NOT ILIKE '%%demo%%'
              AND rc.username NOT ILIKE '%%mock%%'
              AND rc.username NOT ILIKE '%%dummy%%'
              AND rc.username NOT ILIKE '%%temp%%'
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
                    if rx_val < -27.0:
                        rx_status = 'bad'
                    elif rx_val < -23.0:
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
                'last_nas': last_nas or 'N/A',
                'ont_serial': u.get('ont_serial') or 'N/A'
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
            WHERE name NOT ILIKE '%%test%%' AND name NOT ILIKE '%%demo%%' AND name NOT ILIKE '%%mock%%' AND name NOT ILIKE '%%dummy%%'
            ORDER BY name ASC
        """) or []

        from concurrent.futures import ThreadPoolExecutor
        from helpers import ping_nas
        import json

        def process_device(d):
            d['latitude'] = float(d['latitude'])
            d['longitude'] = float(d['longitude'])
            d['status'] = 'online' # Default
            if d['type'] == 'olt' and d['details']:
                try:
                    det = json.loads(d['details'])
                    ip = det.get('ip_address')
                    if ip:
                        status = ping_nas(ip)
                        d['status'] = status
                except Exception:
                    pass
            return d

        with ThreadPoolExecutor(max_workers=5) as executor:
            processed_devices = list(executor.map(process_device, devices))

        return jsonify({'success': True, 'devices': processed_devices})
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

        if dev_type not in ('olt', 'odc', 'oit', 'odp', 'nas'):
            return jsonify({'success': False, 'error': 'Tipe infrastruktur tidak valid (olt, odc, oit, odp, nas)'}), 400

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

@network_map_bp.route('/api/map/infrastructure/edit', methods=['POST'])
@login_required
def api_edit_infrastructure():
    try:
        data = request.get_json() or {}
        device_id = data.get('id')
        name = data.get('name')
        dev_type = data.get('type')
        lat = data.get('latitude')
        lng = data.get('longitude')
        details = data.get('details', '')
        icon = data.get('icon', '')

        if not device_id or not name or not dev_type or lat is None or lng is None:
            return jsonify({'success': False, 'error': 'ID, nama, tipe, latitude, dan longitude wajib diisi'}), 400

        try:
            lat = float(lat)
            lng = float(lng)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Latitude dan longitude harus berupa angka desimal valid'}), 400

        execute_query("""
            UPDATE infrastructure_coordinates
            SET name = %s, type = %s, latitude = %s, longitude = %s, details = %s, icon = %s, updated_at = NOW()
            WHERE id = %s
        """, (name, dev_type, lat, lng, details, icon, device_id), fetch=False)

        return jsonify({'success': True, 'message': 'Detail infrastruktur berhasil diperbarui'})
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

        # Ensure table exists before attempting DELETE
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.infrastructure_connections (
                id SERIAL PRIMARY KEY,
                src_id INTEGER NOT NULL,
                dst_id INTEGER NOT NULL,
                coordinates JSONB DEFAULT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(src_id, dst_id)
            )
        """, fetch=False)

        # Clean up related cables and infrastructure connections first to avoid orphans
        execute_query("DELETE FROM cable_connections WHERE odp_id = %s", (device_id,), fetch=False)
        execute_query("DELETE FROM infrastructure_connections WHERE src_id = %s OR dst_id = %s", (device_id, device_id), fetch=False)
        execute_query("DELETE FROM infrastructure_coordinates WHERE id = %s", (device_id,), fetch=False)

        return jsonify({'success': True, 'message': 'Infrastruktur berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/cables', methods=['GET'])
@login_required
def api_get_cables():
    try:
        # Ensure cable_connections table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.cable_connections (
                id SERIAL PRIMARY KEY,
                odp_id INTEGER NOT NULL,
                customer_username VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(odp_id, customer_username)
            )
        """, fetch=False)

        # Ensure coordinates column exists in cable_connections
        execute_query("""
            ALTER TABLE public.cable_connections ADD COLUMN IF NOT EXISTS coordinates JSONB DEFAULT NULL
        """, fetch=False)

        cables = execute_query("""
            SELECT cc.id, cc.odp_id, cc.customer_username, cc.coordinates,
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
            if c.get('coordinates'):
                try:
                    c['coordinates'] = [[float(pt[0]), float(pt[1])] for pt in c['coordinates']]
                except:
                    c['coordinates'] = None
        
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
        coordinates = data.get('coordinates')
        
        if not odp_id or not customer_username:
            return jsonify({'success': False, 'error': 'ODP ID dan username customer wajib diisi'}), 400
        
        # Validasi: coordinates wajib ada dan tidak boleh array kosong
        # Ini untuk memastikan kabel harus ditarik manual, tidak otomatis garis lurus
        if not coordinates or not isinstance(coordinates, list) or len(coordinates) == 0:
            return jsonify({'success': False, 'error': 'Rute kabel wajib ditarik manual! Klik beberapa titik di peta untuk membuat rute lekukan.'}), 400
        
        import json
        coords_json = json.dumps(coordinates)

        execute_query("""
            INSERT INTO cable_connections (odp_id, customer_username, coordinates)
            VALUES (%s, %s, %s)
            ON CONFLICT (odp_id, customer_username) DO UPDATE
            SET coordinates = EXCLUDED.coordinates
        """, (odp_id, customer_username, coords_json), fetch=False)
        
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

@network_map_bp.route('/api/map/ont-mappings/unconfigured', methods=['GET'])
@login_required
def api_get_unconfigured_onts():
    try:
        # Fetch all ONTs with username starting with 'unconfigured-'
        onts = execute_query("""
            SELECT username, serial, model, manufacturer, rx_power 
            FROM ont_status 
            WHERE username LIKE 'unconfigured-%%'
            ORDER BY updated_at DESC
        """) or []
        return jsonify({'success': True, 'onts': onts})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/ont-mappings/save', methods=['POST'])
@login_required
def api_save_ont_mapping():
    try:
        # Ensure table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.ont_serial_mapping (
                username VARCHAR(100) PRIMARY KEY,
                serial_number VARCHAR(100) UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """, fetch=False)

        data = request.get_json() or {}
        username = data.get('username')
        serial_number = data.get('serial_number')

        if not username or not serial_number:
            return jsonify({'success': False, 'error': 'Username dan Serial Number wajib diisi'}), 400

        username_clean = username.strip()
        serial_clean = serial_number.strip()

        # 1. Delete any existing entry for this username in ont_status (since username is PRIMARY KEY)
        # to avoid unique key constraint violation when we update another row to this username
        execute_query("DELETE FROM ont_status WHERE username = %s", (username_clean,), fetch=False)

        # 2. Update the row that has this serial to have the new username (so it preserves the metrics)
        execute_query("""
            UPDATE ont_status 
            SET username = %s 
            WHERE serial = %s
        """, (username_clean, serial_clean), fetch=False)

        # 3. Delete any other rows with this serial that don't match the new username
        execute_query("DELETE FROM ont_status WHERE serial = %s AND username != %s", (serial_clean, username_clean), fetch=False)

        # 4. Save mapping in ont_serial_mapping
        execute_query("""
            INSERT INTO ont_serial_mapping (username, serial_number)
            VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE 
            SET serial_number = EXCLUDED.serial_number
        """, (username_clean, serial_clean), fetch=False)

        return jsonify({'success': True, 'message': f'ONT Serial {serial_clean} berhasil dikaitkan ke {username_clean}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@network_map_bp.route('/api/map/ont-mappings/delete', methods=['POST'])
@login_required
def api_delete_ont_mapping():
    try:
        data = request.get_json() or {}
        username = data.get('username')

        if not username:
            return jsonify({'success': False, 'error': 'Username wajib diisi'}), 400

        # Get serial first to revert in ont_status
        mapping = execute_query("SELECT serial_number FROM ont_serial_mapping WHERE username = %s", (username,))
        if mapping:
            serial = mapping[0]['serial_number']
            # Revert username in ont_status back to unconfigured-serial
            execute_query("""
                UPDATE ont_status 
                SET username = %s 
                WHERE serial = %s
            """, (f"unconfigured-{serial}", serial), fetch=False)

        execute_query("DELETE FROM ont_serial_mapping WHERE username = %s", (username,), fetch=False)
        return jsonify({'success': True, 'message': 'Pemetaan Serial ONT berhasil dihapus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/infra-connections', methods=['GET'])
@login_required
def api_get_infra_connections():
    try:
        # Ensure infrastructure_connections table exists
        execute_query("""
            CREATE TABLE IF NOT EXISTS public.infrastructure_connections (
                id SERIAL PRIMARY KEY,
                src_id INTEGER NOT NULL,
                dst_id INTEGER NOT NULL,
                coordinates JSONB DEFAULT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(src_id, dst_id)
            )
        """, fetch=False)

        # Ensure is_disconnected column exists
        execute_query("""
            ALTER TABLE public.infrastructure_connections ADD COLUMN IF NOT EXISTS is_disconnected BOOLEAN DEFAULT FALSE
        """, fetch=False)

        connections = execute_query("""
            SELECT ic.id, ic.src_id, ic.dst_id, ic.coordinates, ic.is_disconnected,
                   src.name as src_name, src.type as src_type, src.latitude as src_lat, src.longitude as src_lng,
                   dst.name as dst_name, dst.type as dst_type, dst.latitude as dst_lat, dst.longitude as dst_lng
            FROM infrastructure_connections ic
            JOIN infrastructure_coordinates src ON src.id = ic.src_id
            JOIN infrastructure_coordinates dst ON dst.id = ic.dst_id
        """) or []

        for conn in connections:
            conn['src_lat'] = float(conn['src_lat'])
            conn['src_lng'] = float(conn['src_lng'])
            conn['dst_lat'] = float(conn['dst_lat'])
            conn['dst_lng'] = float(conn['dst_lng'])
            conn['is_disconnected'] = bool(conn.get('is_disconnected', False))
            if conn.get('coordinates'):
                try:
                    import json
                    if isinstance(conn['coordinates'], str):
                        conn['coordinates'] = json.loads(conn['coordinates'])
                    conn['coordinates'] = [[float(pt[0]), float(pt[1])] for pt in conn['coordinates']]
                except:
                    conn['coordinates'] = None

        return jsonify({'success': True, 'connections': connections})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/infra-connections/add', methods=['POST'])
@login_required
def api_add_infra_connection():
    try:
        data = request.get_json() or {}
        src_id = data.get('src_id')
        dst_id = data.get('dst_id')
        coordinates = data.get('coordinates')

        if not src_id or not dst_id:
            return jsonify({'success': False, 'error': 'ID sumber dan ID tujuan wajib diisi'}), 400

        # Validasi: coordinates wajib ada dan tidak boleh array kosong
        # Ini untuk memastikan kabel harus ditarik manual, tidak otomatis garis lurus
        if not coordinates or not isinstance(coordinates, list) or len(coordinates) == 0:
            return jsonify({'success': False, 'error': 'Rute kabel wajib ditarik manual! Klik beberapa titik di peta untuk membuat rute lekukan.'}), 400

        import json
        coords_json = json.dumps(coordinates)

        execute_query("""
            INSERT INTO infrastructure_connections (src_id, dst_id, coordinates, is_disconnected)
            VALUES (%s, %s, %s, FALSE)
            ON CONFLICT (src_id, dst_id) DO UPDATE
            SET coordinates = EXCLUDED.coordinates, is_disconnected = FALSE
        """, (src_id, dst_id, coords_json), fetch=False)

        return jsonify({'success': True, 'message': 'Koneksi kabel infrastruktur berhasil disimpan'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/infra-connections/delete', methods=['POST'])
@login_required
def api_delete_infra_connection():
    try:
        data = request.get_json() or {}
        conn_id = data.get('id')
        src_id = data.get('src_id')
        dst_id = data.get('dst_id')

        if conn_id:
            # Mark the existing connection as disconnected and clear coords
            execute_query("""
                UPDATE infrastructure_connections 
                SET is_disconnected = TRUE, coordinates = NULL 
                WHERE id = %s
            """, (conn_id,), fetch=False)
        elif src_id and dst_id:
            # Create a disconnected placeholder row
            execute_query("""
                INSERT INTO infrastructure_connections (src_id, dst_id, is_disconnected)
                VALUES (%s, %s, TRUE)
                ON CONFLICT (src_id, dst_id) DO UPDATE
                SET is_disconnected = TRUE, coordinates = NULL
            """, (src_id, dst_id), fetch=False)
        else:
            return jsonify({'success': False, 'error': 'ID atau src_id & dst_id wajib diisi'}), 400

        return jsonify({'success': True, 'message': 'Koneksi kabel infrastruktur berhasil diputus'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

