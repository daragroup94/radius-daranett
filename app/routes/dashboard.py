import psutil
from datetime import datetime, timezone
from flask import Blueprint, render_template, jsonify
from decorators import login_required
from database import execute_query

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/')
@login_required
def dashboard_page():
    return render_template('dashboard.html')

@dashboard_bp.route('/api/stats')
@login_required
def api_stats():
    try:
        # PPPoE Stats (Users not in ipoe, vpn, 8021x, or hotspot groups)
        pppoe_users = execute_query("""
            SELECT COUNT(*) as count FROM radcheck c 
            WHERE c.attribute = 'Cleartext-Password' 
            AND NOT EXISTS (
                SELECT 1 FROM radusergroup g 
                WHERE LOWER(g.username) = LOWER(c.username) 
                AND g.groupname IN ('ipoe', 'vpn', '8021x', 'hotspot')
            )
        """)[0]['count']
        
        pppoe_sessions = execute_query("""
            SELECT COUNT(*) as count FROM radacct a 
            WHERE a.acctstoptime IS NULL 
            AND NOT EXISTS (
                SELECT 1 FROM radusergroup g 
                WHERE LOWER(g.username) = LOWER(a.username) 
                AND g.groupname IN ('ipoe', 'vpn', '8021x')
            ) 
            AND NOT EXISTS (
                SELECT 1 FROM hotspot_vouchers h 
                WHERE LOWER(h.username) = LOWER(a.username)
            )
        """)[0]['count']
        
        # IPoE Stats
        ipoe_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'ipoe'")[0]['count']
        ipoe_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'ipoe' WHERE a.acctstoptime IS NULL")[0]['count']

        # Hotspot Stats
        hotspot_users = execute_query("SELECT COUNT(*) as count FROM hotspot_vouchers WHERE deleted_at IS NULL")[0]['count']
        hotspot_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a WHERE a.acctstoptime IS NULL AND EXISTS (SELECT 1 FROM hotspot_vouchers h WHERE LOWER(h.username) = LOWER(a.username))")[0]['count']

        # VPN Stats
        vpn_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = 'vpn'")[0]['count']
        vpn_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = 'vpn' WHERE a.acctstoptime IS NULL")[0]['count']

        # 802.1X Stats
        x8021x_users = execute_query("SELECT COUNT(*) as count FROM radusergroup WHERE groupname = '8021x'")[0]['count']
        x8021x_sessions = execute_query("SELECT COUNT(*) as count FROM radacct a INNER JOIN radusergroup g ON LOWER(g.username) = LOWER(a.username) AND g.groupname = '8021x' WHERE a.acctstoptime IS NULL")[0]['count']

        total_users = pppoe_users + ipoe_users + hotspot_users + vpn_users + x8021x_users
        active_sessions = pppoe_sessions + ipoe_sessions + hotspot_sessions + vpn_sessions + x8021x_sessions
        online_users = execute_query("SELECT COUNT(DISTINCT username) as count FROM radacct WHERE acctstoptime IS NULL")[0]['count']
        today_auth = execute_query("SELECT COUNT(*) as count FROM radpostauth WHERE DATE(authdate AT TIME ZONE 'Asia/Jakarta') = CURRENT_DATE")[0]['count']

        try:
            total_nas = execute_query("SELECT COUNT(*) as count FROM nas")[0]['count']
        except:
            total_nas = 0

        return jsonify({
            'pppoe_users': pppoe_users,
            'pppoe_sessions': pppoe_sessions,
            'ipoe_users': ipoe_users,
            'ipoe_sessions': ipoe_sessions,
            'hotspot_users': hotspot_users,
            'hotspot_sessions': hotspot_sessions,
            'vpn_users': vpn_users,
            'vpn_sessions': vpn_sessions,
            'x8021x_users': x8021x_users,
            'x8021x_sessions': x8021x_sessions,
            'total_users': total_users,
            'active_sessions': active_sessions,
            'online_users': online_users,
            'today_auth': today_auth,
            'total_nas': total_nas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@dashboard_bp.route('/api/recent-activity')
@login_required
def api_recent_activity():
    try:
        activity = execute_query("""
            SELECT username, reply, authdate, 
                   calledstationid, callingstationid
            FROM radpostauth
            WHERE authdate >= NOW() - INTERVAL '30 minutes'
            ORDER BY authdate DESC
            LIMIT 10
        """)
        
        formatted_activity = []
        now = datetime.now(timezone.utc)
        
        for item in activity:
            is_success = item['reply'] == 'Access-Accept'
            icon = 'check-circle' if is_success else 'x-circle'
            color = 'green' if is_success else 'red'
            text = f"User {item['username']} {'login successful' if is_success else 'login failed'}"
            
            authdate = item['authdate']
            if authdate.tzinfo is None:
                authdate = authdate.replace(tzinfo=timezone.utc)
            
            time_diff = now - authdate
            
            if time_diff.total_seconds() < 60:
                time_text = 'Just now'
            else:
                minutes = int(time_diff.total_seconds() / 60)
                time_text = f"{minutes} min ago"
            
            formatted_activity.append({
                'type': color,
                'icon': icon,
                'text': text,
                'time': time_text,
                'color': color,
                'username': item['username'],
                'reply': item['reply'],
                'calledstationid': item.get('calledstationid', ''),
                'callingstationid': item.get('callingstationid', '')
            })
        
        return jsonify(formatted_activity)
    except Exception as e:
        print(f"Error in recent activity: {str(e)}")
        return jsonify([])

@dashboard_bp.route('/api/system-metrics')
@login_required
def api_system_metrics():
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        
        try:
            db_connections = execute_query("""
                SELECT count(*) as connections 
                FROM pg_stat_activity 
                WHERE state = 'active'
            """)[0]['connections']
        except:
            db_connections = 0
        
        try:
            # Menggunakan folder uploads untuk disk usage jika backups tidak ada
            disk_usage = psutil.disk_usage('/app')
            disk_percent = disk_usage.percent
        except:
            disk_usage = type('obj', (object,), {'used': 0, 'total': 1})()
            disk_percent = 0
        
        return jsonify({
            'cpu_percent': round(cpu_percent, 1),
            'memory_percent': round(memory.percent, 1),
            'memory_used': f"{round(memory.used / (1024**3), 1)}GB",
            'memory_total': f"{round(memory.total / (1024**3), 1)}GB",
            'db_connections': db_connections,
            'db_max_connections': 100,
            'disk_percent': round(disk_percent, 1),
            'disk_used': f"{round(disk_usage.used / (1024**3), 1)}GB",
            'disk_total': f"{round(disk_usage.total / (1024**3), 1)}GB"
        })
    except Exception as e:
        print(f"Error in system metrics: {str(e)}")
        return jsonify({
            'cpu_percent': 0, 'memory_percent': 0,
            'memory_used': '0GB', 'memory_total': '0GB',
            'db_connections': 0, 'db_max_connections': 100,
            'disk_percent': 0, 'disk_used': '0GB', 'disk_total': '0GB'
        })

@dashboard_bp.route('/api/auth-activity')
@login_required
def api_auth_activity():
    try:
        activity = execute_query("""
            WITH minutes AS (
                SELECT generate_series(
                    DATE_TRUNC('minute', NOW() - INTERVAL '30 minutes'),
                    DATE_TRUNC('minute', NOW()),
                    INTERVAL '1 minute'
                ) AS minute
            )
            SELECT 
                TO_CHAR(m.minute, 'HH24:MI') as time_label,
                COUNT(p.id) as attempts
            FROM minutes m
            LEFT JOIN radpostauth p ON DATE_TRUNC('minute', p.authdate) = m.minute
            GROUP BY m.minute, time_label
            ORDER BY m.minute
        """)
        
        labels = [item['time_label'] for item in activity]
        data = [item['attempts'] for item in activity]
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        print(f"Error in auth activity: {str(e)}")
        return jsonify({'labels': [], 'data': []})

@dashboard_bp.route('/api/sessions-by-nas')
@login_required
def api_sessions_by_nas():
    try:
        sessions = execute_query("""
            SELECT COALESCE(n.shortname, n.nasname, 'Unknown') as nasname,
                   COUNT(r.acctsessionid) as session_count
            FROM radacct r
            LEFT JOIN nas n ON HOST(r.nasipaddress) = n.nasname
            WHERE r.acctstoptime IS NULL
            GROUP BY COALESCE(n.shortname, n.nasname, 'Unknown')
            ORDER BY session_count DESC
        """)
        
        labels = []
        data = []
        for item in sessions:
            labels.append(item['nasname'])
            data.append(item['session_count'])
        
        return jsonify({'labels': labels, 'data': data})
    except Exception as e:
        print(f"Error in sessions by NAS: {str(e)}")
        return jsonify({'labels': ['Error'], 'data': [0]})
