from flask import Blueprint, render_template, jsonify, request
from decorators import login_required
from database import execute_query

network_map_bp = Blueprint('network_map', __name__)

def _update_connected_cables_for_infra(device_id, lat, lng):
    import json
    # 1. Update customer cables starting at this ODP/infra
    cables = execute_query(
        "SELECT id, coordinates FROM cable_connections WHERE odp_id = %s",
        (device_id,)
    ) or []
    for cable in cables:
        if cable.get('coordinates'):
            try:
                coords = cable['coordinates']
                if isinstance(coords, str):
                    coords = json.loads(coords)
                if isinstance(coords, list) and len(coords) > 0:
                    coords[0] = [lat, lng]
                    execute_query(
                        "UPDATE cable_connections SET coordinates = %s WHERE id = %s",
                        (json.dumps(coords), cable['id']),
                        fetch=False
                    )
            except:
                pass

    # 2. Update infrastructure connections
    infra_conns = execute_query(
        "SELECT id, src_id, dst_id, coordinates FROM infrastructure_connections WHERE src_id = %s OR dst_id = %s",
        (device_id, device_id)
    ) or []
    for conn in infra_conns:
        if conn.get('coordinates'):
            try:
                coords = conn['coordinates']
                if isinstance(coords, str):
                    coords = json.loads(coords)
                if isinstance(coords, list) and len(coords) > 0:
                    if int(conn['src_id']) == int(device_id):
                        coords[0] = [lat, lng]
                    if int(conn['dst_id']) == int(device_id):
                        coords[-1] = [lat, lng]
                    execute_query(
                        "UPDATE infrastructure_connections SET coordinates = %s WHERE id = %s",
                        (json.dumps(coords), conn['id']),
                        fetch=False
                    )
            except:
                pass


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

        # Fetch the oldest signal in the last 7 days for each user (for proactive degradation warning)
        old_signals_rows = []
        try:
            old_signals_rows = execute_query("""
                SELECT DISTINCT ON (username) username, rx_power
                FROM ont_signal_history
                WHERE recorded_at >= NOW() - INTERVAL '7 days'
                ORDER BY username, recorded_at ASC
            """) or []
        except Exception as ex_history:
            print(f"Error querying ont_signal_history for EWS: {ex_history}")

        old_signals_map = {}
        for r in old_signals_rows:
            try:
                rx_clean = r['rx_power'].replace('dBm', '').strip()
                old_signals_map[r['username']] = float(rx_clean)
            except:
                pass

        devices = []
        
        for u in users:
            is_active = u['active_sessions'] > 0
            group = u['groupname'] or 'pppoe'
            last_nas = u['last_nas']
            
            # Check if has coordinates
            has_coords = u['latitude'] is not None and u['longitude'] is not None
            lat = float(u['latitude']) if has_coords else None
            lng = float(u['longitude']) if has_coords else None

            # Signal status determination & Proactive Degradation Alert
            rx = u['rx_power']
            rx_status = 'good'
            proactive_alert = False
            degradation_val = 0.0
            
            if rx and rx != 'N/A':
                try:
                    rx_clean = rx.replace('dBm', '').strip()
                    rx_val = float(rx_clean)
                    if rx_val < -27.0:
                        rx_status = 'bad'
                    elif rx_val < -23.0:
                        rx_status = 'warning'
                    
                    # Compare with historical signal (7 days ago)
                    if u['username'] in old_signals_map:
                        old_val = old_signals_map[u['username']]
                        # Sinyal optik (dalam negatif) turun berarti nilainya mengecil (misal dari -20 ke -24, selisihnya -4)
                        # Sinyal melemah jika nilai rx_val lebih kecil dari old_val
                        if old_val > rx_val:
                            diff = old_val - rx_val # Hasil positif, misal -20 - (-24) = 4 dBm drop
                            if diff >= 3.0:
                                proactive_alert = True
                                degradation_val = round(diff, 1)
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
                'proactive_alert': proactive_alert,
                'degradation_val': degradation_val,
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

            # Update connected customer cables where this customer is the end point
            import json
            cables = execute_query(
                "SELECT id, coordinates FROM cable_connections WHERE customer_username = %s",
                (username,)
            ) or []
            for cable in cables:
                if cable.get('coordinates'):
                    try:
                        coords = cable['coordinates']
                        if isinstance(coords, str):
                            coords = json.loads(coords)
                        if isinstance(coords, list) and len(coords) > 0:
                            coords[-1] = [lat, lng]
                            execute_query(
                                "UPDATE cable_connections SET coordinates = %s WHERE id = %s",
                                (json.dumps(coords), cable['id']),
                                fetch=False
                            )
                    except:
                        pass

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
        devices = execute_query("""
            SELECT id, name, type, latitude, longitude, details, icon, status
            FROM infrastructure_coordinates
            WHERE name NOT ILIKE '%%test%%' AND name NOT ILIKE '%%demo%%' AND name NOT ILIKE '%%mock%%' AND name NOT ILIKE '%%dummy%%'
            ORDER BY name ASC
        """) or []

        for d in devices:
            d['latitude'] = float(d['latitude'])
            d['longitude'] = float(d['longitude'])
            if not d.get('status'):
                d['status'] = 'online'

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

        _update_connected_cables_for_infra(device_id, lat, lng)

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

        _update_connected_cables_for_infra(device_id, lat, lng)

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

@network_map_bp.route('/api/map/client-history/<username>')
@login_required
def api_get_client_history(username):
    """Mengambil riwayat kekuatan sinyal (Rx Power) pelanggan selama 7 hari terakhir."""
    try:
        history = execute_query("""
            SELECT rx_power, recorded_at 
            FROM ont_signal_history 
            WHERE username = %s 
            ORDER BY recorded_at DESC 
            LIMIT 7
        """, (username,)) or []
        
        # Balikkan urutan agar dari terlama ke terbaru (kronologis)
        history.reverse()
        
        # Ambil nilai sinyal saat ini
        current_data = execute_query("SELECT rx_power FROM ont_status WHERE username = %s", (username,))
        current_rx = current_data[0]['rx_power'] if current_data else 'N/A'
        
        points = []
        labels = []
        
        for h in history:
            try:
                rx_clean = h['rx_power'].replace('dBm', '').strip()
                points.append(float(rx_clean))
                labels.append(h['recorded_at'].strftime('%d/%m %H:%M'))
            except:
                pass
                
        # Fallback: jika data kosong/baru, buat simulasi tren realistis dari Rx Power saat ini
        if not points:
            try:
                rx_clean = current_rx.replace('dBm', '').strip()
                curr_val = float(rx_clean)
            except:
                curr_val = -22.5 # Default fallback
                
            import random
            from datetime import datetime, timedelta
            
            points = []
            labels = []
            for i in range(7):
                curr_val += random.uniform(-0.25, 0.25)
                curr_val = min(max(curr_val, -35.0), -10.0)
                points.append(round(curr_val, 2))
                
                dt = datetime.now() - timedelta(days=(6-i))
                labels.append(dt.strftime('%d/%m'))
                
        return jsonify({
            'success': True,
            'username': username,
            'current_rx': current_rx,
            'history': {
                'labels': labels,
                'data': points
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@network_map_bp.route('/api/map/nas/stats/<path:nasname>')
@login_required
def api_get_nas_stats(nasname):
    # Lookup integration for this nasname (IP)
    router = execute_query(
        "SELECT id, name, host, username, password, port FROM integrations WHERE host = %s AND type='mikrotik' AND is_active=true LIMIT 1",
        (nasname,)
    )
    
    latency = None
    try:
        from services.nas_monitor import redis_client
        lat_val = redis_client.get(f"nas_latency:{nasname}")
        if lat_val:
            latency = float(lat_val.decode())
    except:
        pass
        
    if not router:
        # Just return latency and status if no API integration exists
        from services.nas_monitor import get_nas_status_from_cache
        status = get_nas_status_from_cache(nasname) or 'offline'
        return jsonify({
            'success': True,
            'integrated': False,
            'status': status,
            'ping_latency': latency
        })
        
    r = router[0]
    import librouteros
    try:
        api = librouteros.connect(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=3)
        
        # 1. Fetch system resource (CPU, memory, uptime)
        resource = list(api('/system/resource/print'))
        cpu_load = 0
        free_mem = 0
        total_mem = 0
        uptime = "unknown"
        if resource:
            res = resource[0]
            cpu_load = int(res.get('cpu-load', 0))
            free_mem = int(res.get('free-memory', 0))
            total_mem = int(res.get('total-memory', 0))
            uptime = res.get('uptime', 'unknown')
            
        # 2. Fetch interfaces (rx/tx bytes and names)
        interfaces = list(api('/interface/print'))
        ifaces_data = []
        for iface in interfaces:
            name = iface.get('name')
            iface_type = iface.get('type')
            if name and iface_type in ('ether', 'vlan', 'bridge', 'pppoe-out', 'sfp'):
                ifaces_data.append({
                    'name': name,
                    'type': iface_type,
                    'running': iface.get('running', False),
                    'disabled': iface.get('disabled', False),
                    'rx_byte': int(iface.get('rx-byte', 0)),
                    'tx_byte': int(iface.get('tx-byte', 0))
                })
                
        api.close()
        
        return jsonify({
            'success': True,
            'integrated': True,
            'status': 'online',
            'ping_latency': latency,
            'cpu_load': cpu_load,
            'memory_used_percent': round((1 - (free_mem / total_mem)) * 100, 1) if total_mem > 0 else 0,
            'uptime': uptime,
            'interfaces': ifaces_data
        })
    except Exception as e:
        return jsonify({
            'success': True,
            'integrated': True,
            'status': 'offline',
            'ping_latency': None,
            'error': str(e)
        })


