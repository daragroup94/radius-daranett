import re
import subprocess
import socket as _socket
from flask import request

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
