import re
import subprocess
import socket as _socket
import threading
import time
from flask import request

# ---------------------------------------------------------------------------
# MikroTik persistent connection cache
# Menyimpan koneksi aktif agar tidak login/logout setiap polling.
# Koneksi akan di-recycle otomatis jika sudah idle > MAX_AGE detik.
# ---------------------------------------------------------------------------
_mk_lock = threading.Lock()
_mk_cache: dict = {}   # key=(host,port,username) → {api, born, last_used}
_MK_MAX_AGE = 300      # detik — koneksi dipakai ulang max 5 menit

def get_request_data():
    try:
        if request.is_json:
            return request.get_json()
        else:
            data = request.form.to_dict()
            for key in data:
                if data[key] == '':
                    data[key] = None
            return data
    except Exception as e:
        print(f"DEBUG: Error getting request data: {str(e)}")
        return {}

def convert_data_limit_to_bytes(data_limit_str):
    """Convert data limit string (e.g., '10GB', '500MB') to bytes"""
    if not data_limit_str:
        return None
    try:
        data_limit_str = data_limit_str.strip().upper()
        match = re.match(r'^(\d+(?:\.\d+)?)\s*([KMGT]?B?)$', data_limit_str)
        if not match:
            return None
        value = float(match.group(1))
        unit = match.group(2)
        
        multipliers = {
            'B': 1, 'KB': 1024, 'MB': 1024 ** 2, 'GB': 1024 ** 3, 'TB': 1024 ** 4,
            'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3, 'T': 1024 ** 4
        }
        multiplier = multipliers.get(unit, 1)
        return int(value * multiplier)
    except Exception as e:
        print(f"DEBUG: Error converting data limit: {str(e)}")
        return None

def format_file_size(size_bytes):
    if size_bytes == 0:
        return '0 B'
    size_names = ['B', 'KB', 'MB', 'GB']
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1
    return f"{size_bytes:.2f} {size_names[i]}"

def ping_nas(ip_address):
    """
    Cek konektivitas NAS: standard ICMP ping dulu, jika gagal coba nsenter (container host fallback), lalu TCP fallback.
    """
    import os

    # 1. Coba standard ICMP ping dulu (karena container sudah memiliki route jika diatur CAP_NET_ADMIN)
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "1", ip_address],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1.5
        )
        if result.returncode == 0:
            return "online"
    except Exception:
        pass

    # 2. Fallback: Coba nsenter jika dalam container (untuk system dengan route di host saja)
    if os.path.exists('/.dockerenv'):
        try:
            result = subprocess.run(
                ["nsenter", "-t", "1", "-n", "ping", "-c", "1", "-W", "1", ip_address],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2
            )
            if result.returncode == 0:
                return "online"
        except Exception:
            pass

    # 3. Fallback: Coba TCP ke port umum router (Winbox, API, HTTP, SSH)
    for port in [8291, 8728, 80, 22]:
        try:
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            sock.settimeout(0.5)
            if sock.connect_ex((ip_address, port)) == 0:
                sock.close()
                return "online"
            sock.close()
        except Exception:
            pass

    return "offline"

def _mk_raw_connect(host, username, password, port_num, timeout):
    """Buat koneksi baru ke MikroTik (tanpa cache). Internal use only."""
    import ssl
    import librouteros

    if port_num == 8729:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            return librouteros.connect(
                host=host, username=username, password=password,
                port=port_num, timeout=timeout, ssl_wrapper=ctx.wrap_socket
            )
        except Exception as e:
            err_str = str(e).lower()
            if 'cipher' in err_str or 'ssl' in err_str or 'handshake' in err_str or 'protocol' in err_str:
                try:
                    ctx_legacy = ssl.create_default_context()
                    ctx_legacy.check_hostname = False
                    ctx_legacy.verify_mode = ssl.CERT_NONE
                    ctx_legacy.set_ciphers('DEFAULT:@SECLEVEL=0:ADH')
                    return librouteros.connect(
                        host=host, username=username, password=password,
                        port=port_num, timeout=timeout, ssl_wrapper=ctx_legacy.wrap_socket
                    )
                except Exception:
                    pass
            raise e
    else:
        return librouteros.connect(
            host=host, username=username, password=password,
            port=port_num, timeout=timeout
        )


def connect_mikrotik(host, username, password, port, timeout=3.0):
    """
    Connect to MikroTik RouterOS API using librouteros.
    Supports both plain API (e.g. port 8728) and API-SSL (e.g. port 8729).
    For port 8729, it automatically configures SSLContext with a fallback
    for older RouterOS v6 ciphers.

    NOTE: Fungsi ini selalu membuat koneksi BARU — cocok untuk operasi
    one-shot (disconnect, CoA, dsb). Untuk polling periodik gunakan
    get_cached_mikrotik() agar tidak flood log MikroTik.
    """
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        port_num = 8728

    return _mk_raw_connect(host, username, password, port_num, timeout)


def get_cached_mikrotik(host, username, password, port, timeout=5.0):
    """
    Kembalikan koneksi MikroTik yang di-cache (persistent).
    Koneksi dibuat saat pertama kali dan dipakai ulang selama masih
    valid (max _MK_MAX_AGE detik). Jika koneksi drop/error akan
    otomatis reconnect — menghasilkan tepat 1 login di log MikroTik
    per sesi, bukan 1 login per polling.

    PENTING: Lock hanya dipakai untuk baca/tulis dict cache (sub-milidetik).
    Network I/O (health-check + reconnect) dilakukan di LUAR lock agar
    tidak memblokir thread lain saat jaringan lambat.
    """
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        port_num = 8728

    cache_key = (host, port_num, username)
    now = time.monotonic()

    # --- Fase 1: Ambil entry dari cache (dengan lock, sangat cepat) ---
    cached_api = None
    old_api = None
    with _mk_lock:
        entry = _mk_cache.get(cache_key)
        if entry:
            age = now - entry['born']
            if age >= _MK_MAX_AGE:
                # TTL habis — keluarkan dari cache, close di luar lock
                old_api = _mk_cache.pop(cache_key)['api']
            else:
                cached_api = entry['api']

    # Close koneksi expired di luar lock
    if old_api is not None:
        try:
            old_api.close()
        except Exception:
            pass

    # --- Fase 2: Validasi koneksi yang ada (di luar lock, bisa blocking) ---
    if cached_api is not None:
        try:
            list(cached_api('/system/identity/print'))
            # Koneksi masih hidup — update last_used
            with _mk_lock:
                if cache_key in _mk_cache:
                    _mk_cache[cache_key]['last_used'] = time.monotonic()
            return cached_api
        except Exception:
            # Koneksi mati — tutup dan hapus dari cache
            try:
                cached_api.close()
            except Exception:
                pass
            with _mk_lock:
                _mk_cache.pop(cache_key, None)

    # --- Fase 3: Buat koneksi baru (di luar lock, bisa blocking) ---
    new_api = _mk_raw_connect(host, username, password, port_num, timeout)
    with _mk_lock:
        _mk_cache[cache_key] = {'api': new_api, 'born': time.monotonic(), 'last_used': time.monotonic()}
    return new_api


def close_cached_mikrotik(host, port, username):
    """Tutup dan hapus koneksi cache secara eksplisit (misal saat integrasi dihapus)."""
    try:
        port_num = int(port)
    except (TypeError, ValueError):
        port_num = 8728
    cache_key = (host, port_num, username)
    with _mk_lock:
        entry = _mk_cache.pop(cache_key, None)
    if entry:
        try:
            entry['api'].close()
        except Exception:
            pass


def log_admin_activity(action, target=None, details=None):
    """Mencatat aktivitas admin ke dalam tabel admin_activity_log."""
    try:
        from flask import session, request
        from database import execute_query
        admin_username = session.get('username', 'system') if session else 'system'
        ip_addr = request.headers.get('X-Forwarded-For', request.remote_addr) if request else None
        if ip_addr and ',' in ip_addr:
            ip_addr = ip_addr.split(',')[0].strip()
        execute_query(
            "INSERT INTO admin_activity_log (admin_username, action, target, ip_address, details, created_at) VALUES (%s, %s, %s, %s, %s, NOW())",
            (admin_username, action, str(target) if target else None, ip_addr, str(details) if details else None),
            fetch=False
        )
    except Exception:
        pass


