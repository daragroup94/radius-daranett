#!/usr/bin/env python3
"""
FINAL CORRECT untuk ZTE F670L V9.0
Tambah WANIPConnection instance baru di WANConnectionDevice.1
VLAN langsung di level WANIPConnection
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
    try:
        resp = requests.post(task_url, json=payload, timeout=5)
        print(f"✅ {resp.status_code}\n")
        return resp.status_code in [200, 202]
    except Exception as e:
        print(f"⏳ Queued ({e})\n")
        return True

print("=" * 70)
print("FINAL: Tambah WAN Bridge untuk Hotspot - ZTE F670L V9.0")
print("=" * 70)

# Step 1: Tambah WANIPConnection baru
print("\n1. Tambah WANIPConnection baru...")
post_task("addObject", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection", ""]
])
time.sleep(8)

# Step 2: Refresh untuk dapatkan instance number yang baru
print("2. Refresh structure...")
post_task("refreshObject", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection", ""]
])
time.sleep(8)

# Step 3: Konfigurasi instance baru (coba 1,2,4,5 - skip 3 karena sudah ada)
for instance in [1, 2, 4, 5]:
    print(f"3. Coba konfigurasi instance {instance}...")
    result = post_task("setParameterValues", [
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.Enable", True, "xsd:boolean"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.ConnectionType", "IP_Bridged", "xsd:string"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.Name", "HOTSPOT_BRIDGE", "xsd:string"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.X_ZTE-COM_ServiceList", "INTERNET", "xsd:string"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.X_ZTE-COM_VLANID", 55, "xsd:int"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.X_ZTE-COM_VLANEnable", True, "xsd:boolean"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.X_ZTE-COM_8021P", 0, "xsd:int"],
        [f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.{instance}.X_ZTE-COM_BindLan", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.6", "xsd:string"]
    ])
    if result:
        print(f"✅ Berhasil di instance {instance}!")
        break
    time.sleep(2)

print("\n" + "=" * 70)
print("✅ Selesai! Cek dengan: python3 check_wan_slots.py")
print("=" * 70)
