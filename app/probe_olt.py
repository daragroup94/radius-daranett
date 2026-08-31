import os
import sys
import json
import requests
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db_connection():
    host = os.getenv('DB_HOST', 'postgres')
    name = os.getenv('DB_NAME', 'radius')
    user = os.getenv('DB_USER', 'radius')
    password = os.getenv('DB_PASSWORD', 'radiuspass')
    port = os.getenv('DB_PORT', '5432')
    conn = psycopg2.connect(host=host, database=name, user=user, password=password, port=port, cursor_factory=RealDictCursor)
    return conn

def main():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM integrations WHERE type='tenda-olt' AND is_active=true LIMIT 1")
        olt = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Failed to connect to database: {e}")
        return

    if not olt:
        print("No active Tenda OLT integration found in database.")
        return

    host = olt['host']
    port = olt['port']
    is_https = False
    if host.startswith('https://'):
        is_https = True
        host = host[8:]
    elif host.startswith('http://'):
        host = host[7:]
    if str(port) == '443':
        is_https = True
    if is_https and str(port) == '80':
        port = 443
    scheme = 'https' if is_https else 'http'
    url = f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"
    url = url.rstrip('/')

    password_md5 = hashlib.md5(f"an3400{olt['password']}".encode()).hexdigest()
    session = requests.Session()
    login_payload = {"login": {"username": olt['username'], "password": password_md5}}
    
    try:
        resp = session.post(f"{url}/setModules", json=login_payload, timeout=5, verify=False, allow_redirects=False)
        if resp.status_code != 200:
            print(f"Login failed: HTTP {resp.status_code}")
            return
        
        token = resp.headers.get('Token')
        if token:
            session.headers.update({'token': token})
    except Exception as e:
        print(f"Connection error: {e}")
        return

    # Probing OLT light info
    payloads = [
        {"oltLightInfo": {"port": "pon"}},
        {"uplinkLightInfo": {"port": "uplink"}},
        {"deviceInfo": "", "customInfo": ""}
    ]

    print("\nQuerying OLT module data...")
    for payload in payloads:
        try:
            r = session.post(f"{url}/getModules", json=payload, timeout=4, verify=False, allow_redirects=False)
            if r.status_code == 200:
                print(f"\n[SUCCESS] Payload {list(payload.keys())} returned:")
                print(json.dumps(r.json(), indent=2))
            else:
                print(f"\n[FAIL] Payload {list(payload.keys())} returned HTTP {r.status_code}")
        except Exception as e:
            print(f"\n[ERROR] Payload {list(payload.keys())}: {e}")

if __name__ == '__main__':
    main()
