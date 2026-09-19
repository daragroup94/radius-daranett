#!/usr/bin/env python3
"""
Script standalone untuk menambahkan WAN Bridge untuk Hotspot pada ZTE F670L V9.0
Tujuan: Bridge untuk hotspot dengan tag VLAN 55, bind ke SSID 6
TIDAK MEMERLUKAN FLASK - bisa dijalankan langsung
"""
import requests
import json
import time

# Konfigurasi GenieACS
GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

# Konfigurasi WAN
WAN_CONFIG = {
    "vlan_id": 55,
    "priority": 0,
    "service_list": "INTERNET",
    "connection_type": "IP_Bridged",
    "ssid_binding": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.6"  # SSID 6
}

def post_task(device_id, task_name, param_values):
    """Post task ke GenieACS"""
    task_url = f"{GENIEACS_URL}/devices/{device_id}/tasks"
    payload = {"name": task_name, "parameterValues": param_values}
    
    print(f"\n📤 Posting task: {task_name}")
    print(f"Payload: {json.dumps(payload, indent=2)}\n")
    
    try:
        resp = requests.post(task_url, json=payload, timeout=10)
        print(f"✅ Response: {resp.status_code}")
        if resp.text:
            print(f"Response body: {resp.text}\n")
        return resp.status_code in [200, 201, 202]
    except Exception as e:
        print(f"❌ Error: {e}\n")
        return False

def add_wan_bridge_hotspot():
    """
    Menambahkan WAN Bridge untuk Hotspot pada ZTE F670L V9.0
    Strategi: Gunakan WANConnectionDevice.2 (buat baru jika belum ada)
    """
    
    # Step 1: Tambah object WANConnectionDevice baru
    print("=" * 70)
    print("STEP 1: Membuat WANConnectionDevice.2")
    print("=" * 70)
    
    add_wcd = post_task(
        DEVICE_ID,
        "addObject",
        [["InternetGatewayDevice.WANDevice.1.WANConnectionDevice", ""]]
    )
    
    if not add_wcd:
        print("⚠️  Gagal membuat WANConnectionDevice baru (mungkin sudah ada)")
        print("Melanjutkan dengan asumsi WANConnectionDevice.2 sudah ada...\n")
    
    time.sleep(3)
    
    # Step 2: Konfigurasi X_ZTE-COM_WANGponLinkConfig untuk VLAN
    print("=" * 70)
    print("STEP 2: Konfigurasi VLAN di WANGponLinkConfig")
    print("=" * 70)
    
    vlan_params = [
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_ZTE-COM_WANGponLinkConfig.VLANIDMark", WAN_CONFIG["vlan_id"], "xsd:unsignedInt"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_ZTE-COM_WANGponLinkConfig.X_ZTE-COM_VLANMode", "Tag", "xsd:string"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_ZTE-COM_WANGponLinkConfig.X_ZTE-COM_8021PbitMark", WAN_CONFIG["priority"], "xsd:unsignedInt"]
    ]
    
    post_task(DEVICE_ID, "setParameterValues", vlan_params)
    time.sleep(2)
    
    # Step 3: Tambah WANIPConnection object
    print("=" * 70)
    print("STEP 3: Membuat WANIPConnection")
    print("=" * 70)
    
    add_wanip = post_task(
        DEVICE_ID,
        "addObject",
        [["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection", ""]]
    )
    
    if not add_wanip:
        print("⚠️  Gagal membuat WANIPConnection (mungkin sudah ada)")
        print("Melanjutkan konfigurasi pada instance yang sudah ada...\n")
        
    time.sleep(3)
    
    # Step 4: Konfigurasi WANIPConnection untuk Bridge Mode
    print("=" * 70)
    print("STEP 4: Konfigurasi WANIPConnection untuk Bridge Mode + SSID Binding")
    print("=" * 70)
    
    wan_params = [
        # Basic config
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.Enable", True, "xsd:boolean"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.ConnectionType", "IP_Bridged", "xsd:string"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.Name", "HOTSPOT_BRIDGE", "xsd:string"],
        
        # Service List
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_ServiceList", WAN_CONFIG["service_list"], "xsd:string"],
        
        # VLAN di level connection
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_VLANID", WAN_CONFIG["vlan_id"], "xsd:unsignedInt"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_VLANEnable", True, "xsd:boolean"],
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_8021P", WAN_CONFIG["priority"], "xsd:unsignedInt"],
        
        # Binding ke SSID 6 - ini yang paling penting!
        ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_BindLan", WAN_CONFIG["ssid_binding"], "xsd:string"],
    ]
    
    wan_set = post_task(DEVICE_ID, "setParameterValues", wan_params)
    
    if not wan_set:
        print("❌ Gagal set WANIPConnection parameters")
        return False
    
    print("\n" + "=" * 70)
    print("✅ SELESAI! WAN Bridge untuk Hotspot telah dikonfigurasi")
    print("=" * 70)
    print(f"VLAN ID: {WAN_CONFIG['vlan_id']}")
    print(f"Priority: {WAN_CONFIG['priority']}")
    print(f"Service: {WAN_CONFIG['service_list']}")
    print(f"Binding: SSID 6")
    print(f"Type: {WAN_CONFIG['connection_type']}")
    print("=" * 70)
    
    return True

if __name__ == "__main__":
    print("\n🚀 Script Konfigurasi WAN Bridge untuk Hotspot")
    print(f"ONT: ZTE F670L V9.0 ({DEVICE_ID})")
    print(f"GenieACS URL: {GENIEACS_URL}\n")
    
    success = add_wan_bridge_hotspot()
    
    if success:
        print("\n✅ Konfigurasi berhasil!")
    else:
        print("\n❌ Konfigurasi gagal!")
