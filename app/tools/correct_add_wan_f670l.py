#!/usr/bin/env python3
"""
CORRECT: ZTE F670L V9.0 hanya punya 1 WANConnectionDevice
Kita tambah WANIPConnection instance baru di dalam slot 1
"""
import requests
import json
import time

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

def post_task(task_name, param_values):
    task_url = f"{GENIEACS_URL}/devices/{DEVICE_ID}/tasks?timeout=3000&connection_request"
    payload = {"name": task_name, "parameterValues": param_values}
    
    print(f"📤 {task_name}")
    print(f"   {json.dumps(param_values, indent=3)}")
    try:
        resp = requests.post(task_url, json=payload, timeout=5)
        print(f"✅ {resp.status_code}\n")
        return resp.status_code in [200, 202]
    except:
        print("⏳ Queued\n")
        return True

print("=" * 70)
print("BENAR: Tambah WANIPConnection di WANConnectionDevice.1")
print("=" * 70)

# Step 1: Tambah WANIPConnection instance baru
print("\n1. Tambah WANIPConnection baru di slot 1...")
post_task("addObject", [["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection", ""]])
time.sleep(5)

# Step 2: Set VLAN di X_ZTE-COM_WANGponLinkConfig (untuk instance baru, kemungkinan index 2)
print("2. Set VLAN...")
post_task("setParameterValues", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_ZTE-COM_WANGponLinkConfig.2.VLANIDMark", 55, "xsd:int"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_ZTE-COM_WANGponLinkConfig.2.X_ZTE-COM_VLANMode", "Tag", "xsd:string"]
])
time.sleep(3)

# Step 3: Konfigurasi WANIPConnection yang baru dibuat (asumsi jadi instance 4)
print("3. Konfigurasi WANIPConnection instance baru...")
post_task("setParameterValues", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.Enable", True, "xsd:boolean"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.ConnectionType", "IP_Bridged", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.Name", "HOTSPOT_BRIDGE", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.X_ZTE-COM_ServiceList", "INTERNET", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.X_ZTE-COM_VLANID", 55, "xsd:int"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.4.X_ZTE-COM_BindLan", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.6", "xsd:string"]
])

print("\n" + "=" * 70)
print("✅ Tunggu 30 detik, lalu cek dengan: python3 check_wan_slots.py")
print("=" * 70)
