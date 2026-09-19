#!/usr/bin/env python3
"""
Diagnosa mengapa ONT tidak bisa diremote dari WAN
Cek via GenieACS API
"""
import os, sys, json, requests

# GenieACS config
ACS_URL = "http://10.204.0.22:7557"

DEVICES = [
    "ZTEGC4385708",
    "ZTEGC8A4D5F3"
]

def get_device(serial):
    """Cari device di GenieACS berdasarkan serial number"""
    # Coba berbagai format device ID
    # Format: OUI-ModelName-SerialNumber
    # Untuk ZTE F670: B8DD71-F670-ZTEGC4385708
    # Untuk ZTE F609: 24586E-F609-ZTEGC8A4D5F3
    
    url = f"{ACS_URL}/devices"
    query = json.dumps({"_id": {"$regex": serial}})
    try:
        r = requests.get(url, params={"query": query}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception as e:
        print(f"  [ERROR] Gagal query ACS: {e}")
        return None

def get_device_by_id(device_id):
    """Ambil device berdasarkan full device ID"""
    url = f"{ACS_URL}/devices/{requests.utils.quote(device_id, safe='')}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [ERROR] Gagal get device: {e}")
        return None

def check_connection_request(device):
    """Periksa status Connection Request URL dan IP"""
    result = {}
    try:
        mgmt = device.get("InternetGatewayDevice", {}).get("ManagementServer", {})
        
        cr_url = mgmt.get("ConnectionRequestURL", {}).get("_value", "N/A")
        cr_user = mgmt.get("ConnectionRequestUsername", {}).get("_value", "N/A")
        cr_pass = mgmt.get("ConnectionRequestPassword", {}).get("_value", "N/A")
        acs_url = mgmt.get("URL", {}).get("_value", "N/A")
        
        result["ConnectionRequestURL"] = cr_url
        result["ConnectionRequestUsername"] = cr_user
        result["ConnectionRequestPassword"] = cr_pass  
        result["ACSURL"] = acs_url
        
        # Cek apakah IP di CR URL adalah private/NAT
        import re
        ip_match = re.search(r'http[s]?://([0-9.]+)', cr_url)
        if ip_match:
            ip = ip_match.group(1)
            result["cr_ip"] = ip
            # Cek private IP ranges
            if (ip.startswith("192.168.") or 
                ip.startswith("10.") or 
                ip.startswith("172.") or
                ip == "127.0.0.1"):
                result["ip_type"] = "PRIVATE/NAT ⚠️ - ACS tidak bisa reach ONT dari WAN!"
            else:
                result["ip_type"] = "PUBLIC ✅"
        else:
            result["cr_ip"] = "tidak terdeteksi"
            result["ip_type"] = "UNKNOWN ⚠️"
            
    except Exception as e:
        result["error"] = str(e)
    return result

def check_wan_ip(device):
    """Periksa WAN IP dan koneksi PPPoE"""
    result = {}
    try:
        wan_dev = device.get("InternetGatewayDevice", {}).get("WANDevice", {})
        
        for wan_id, wan_val in wan_dev.items():
            if not isinstance(wan_val, dict) or wan_id.startswith("_"):
                continue
            wan_conn_dev = wan_val.get("WANConnectionDevice", {})
            for conn_id, conn_val in wan_conn_dev.items():
                if not isinstance(conn_val, dict) or conn_id.startswith("_"):
                    continue
                    
                # PPPoE Connection
                pppoe = conn_val.get("WANPPPConnection", {})
                for ppp_id, ppp_val in pppoe.items():
                    if not isinstance(ppp_val, dict) or ppp_id.startswith("_"):
                        continue
                    status = ppp_val.get("ConnectionStatus", {}).get("_value", "N/A")
                    ext_ip = ppp_val.get("ExternalIPAddress", {}).get("_value", "N/A")
                    ppp_user = ppp_val.get("Username", {}).get("_value", "N/A")
                    name = ppp_val.get("Name", {}).get("_value", "N/A")
                    enable = ppp_val.get("Enable", {}).get("_value", "N/A")
                    nat_en = ppp_val.get("NATEnabled", {}).get("_value", "N/A")
                    
                    result[f"PPPoE.{wan_id}.{conn_id}.{ppp_id}"] = {
                        "Name": name,
                        "Enable": enable,
                        "Status": status,
                        "ExternalIP": ext_ip,
                        "Username": ppp_user,
                        "NATEnabled": nat_en
                    }
                    
                # IP Connection (DHCP/Static)
                ip_conn = conn_val.get("WANIPConnection", {})
                for ip_id, ip_val in ip_conn.items():
                    if not isinstance(ip_val, dict) or ip_id.startswith("_"):
                        continue
                    status = ip_val.get("ConnectionStatus", {}).get("_value", "N/A")
                    ext_ip = ip_val.get("ExternalIPAddress", {}).get("_value", "N/A")
                    name = ip_val.get("Name", {}).get("_value", "N/A")
                    enable = ip_val.get("Enable", {}).get("_value", "N/A")
                    nat_en = ip_val.get("NATEnabled", {}).get("_value", "N/A")
                    conn_type = ip_val.get("ConnectionType", {}).get("_value", "N/A")
                    
                    result[f"IPConn.{wan_id}.{conn_id}.{ip_id}"] = {
                        "Name": name,
                        "Enable": enable,
                        "Status": status,
                        "ExternalIP": ext_ip,
                        "ConnectionType": conn_type,
                        "NATEnabled": nat_en
                    }
    except Exception as e:
        result["error"] = str(e)
    return result

def check_firewall(device):
    """Cek apakah ada firewall yang memblock remote access"""
    result = {}
    try:
        # Cek upnp
        upnp = device.get("InternetGatewayDevice", {}).get("LANDevice", {})
        # Cek remote management
        mgmt_access = device.get("InternetGatewayDevice", {}).get("X_ZTE-COM_ManagementServer", {})
        result["ManagementExtra"] = {k: v.get("_value") for k, v in mgmt_access.items() if isinstance(v, dict) and "_value" in v}
        
        # Cek firewall config
        fw = device.get("InternetGatewayDevice", {}).get("X_ZTE-COM_Firewall", {})
        if fw:
            result["Firewall"] = {k: v.get("_value") for k, v in fw.items() if isinstance(v, dict) and "_value" in v}
    except Exception as e:
        result["error"] = str(e)
    return result

def check_last_inform(device):
    """Cek kapan terakhir device inform ke ACS"""
    try:
        ts = device.get("_lastInform")
        return ts if ts else "N/A"
    except:
        return "N/A"

def diagnose(serial):
    print(f"\n{'='*60}")
    print(f"🔍 DIAGNOSA ONT: {serial}")
    print(f"{'='*60}")
    
    # Cari device
    print("📡 Mencari device di ACS...")
    device = get_device(serial)
    
    if not device:
        print(f"  ❌ Device {serial} TIDAK DITEMUKAN di ACS!")
        print(f"  Kemungkinan: ONT tidak pernah connect ke ACS, salah OUI/ID")
        return
    
    device_id = device.get("_id", "N/A")
    print(f"  ✅ Device ditemukan: {device_id}")
    
    # Last inform
    last_inform = check_last_inform(device)
    print(f"\n📅 Last Inform ke ACS: {last_inform}")
    
    # Model info
    dev_info = device.get("InternetGatewayDevice", {}).get("DeviceInfo", {})
    model = dev_info.get("ModelName", {}).get("_value", "N/A")
    fw = dev_info.get("SoftwareVersion", {}).get("_value", "N/A")
    uptime = dev_info.get("UpTime", {}).get("_value", "N/A")
    print(f"📱 Model: {model} | Firmware: {fw} | Uptime: {uptime}s")
    
    # Connection Request URL analysis
    print(f"\n--- [1] CONNECTION REQUEST URL ---")
    cr_info = check_connection_request(device)
    for k, v in cr_info.items():
        if k == "ip_type":
            print(f"  🌐 IP Type: {v}")
        elif k == "ConnectionRequestURL":
            print(f"  🔗 CR URL: {v}")
        elif k == "ACSURL":
            print(f"  🖥️  ACS URL di ONT: {v}")
        elif k not in ("ConnectionRequestUsername", "ConnectionRequestPassword"):
            print(f"  {k}: {v}")
    
    # WAN IP
    print(f"\n--- [2] WAN CONNECTIONS ---")
    wan_info = check_wan_ip(device)
    if not wan_info:
        print("  ⚠️ Tidak ada data WAN ditemukan")
    for k, v in wan_info.items():
        status = v.get("Status", "N/A")
        ext_ip = v.get("ExternalIP", "N/A")
        name = v.get("Name", "N/A")
        enable = v.get("Enable", "N/A")
        nat = v.get("NATEnabled", "N/A")
        conn_type = v.get("ConnectionType", "")
        icon = "✅" if status == "Connected" else "❌"
        print(f"  {icon} [{k}] Name={name} | Status={status} | IP={ext_ip} | NAT={nat} {conn_type}")
    
    # Firewall
    print(f"\n--- [3] FIREWALL/MANAGEMENT ---")
    fw_info = check_firewall(device)
    for k, v in fw_info.items():
        if v:
            print(f"  {k}: {v}")
    
    # Kesimpulan
    print(f"\n--- 💡 ANALISIS & SOLUSI ---")
    cr_url = cr_info.get("ConnectionRequestURL", "")
    ip_type = cr_info.get("ip_type", "")
    
    if "PRIVATE" in ip_type:
        print(f"  ❌ MASALAH UTAMA: ONT menggunakan IP Private di Connection Request URL")
        print(f"  💡 SOLUSI:")
        print(f"     1. Pastikan WAN PPPoE/IP sudah terhubung dan dapat public IP")
        print(f"     2. Aktifkan fitur STUN di ManagementServer agar ACS tahu IP public ONT")
        print(f"     3. Gunakan Connection Request via ACS push (Task Queue), bukan langsung ke ONT")
        print(f"     4. Periksa apakah ISP/OLT memblokir port TR-069 (7547/7549) ke WAN")
    elif "PUBLIC" in ip_type:
        print(f"  ✅ IP Connection Request sudah PUBLIC")
        print(f"  💡 Kemungkinan masalah lain:")
        print(f"     1. Firewall ONT memblokir Connection Request dari WAN")
        print(f"     2. Port TR-069 (7547) di ONT tidak accessible dari WAN")
        print(f"     3. Credential Connection Request salah")
    else:
        print(f"  ⚠️ CR URL tidak terdeteksi dengan benar, perlu investigasi lebih lanjut")

print("🚀 GenieACS ONT Remote WAN Diagnoser")
print(f"   ACS URL: {ACS_URL}")
print(f"   Target: {', '.join(DEVICES)}")

for sn in DEVICES:
    diagnose(sn)

print(f"\n{'='*60}")
print("✅ Diagnosa selesai")
