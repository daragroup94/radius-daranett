#!/usr/bin/env python3
"""
Script perbaikan untuk ZTE F670L V9.0
Strategi: Refresh dulu, lalu gunakan index yang sudah ada
"""
import requests
import json
import time

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

def post_task(task_name, param_values):
    task_url = f"{GENIEACS_URL}/devices/{DEVICE_ID}/tasks?timeout=3000&connection_request"
    payload = {"name": task_name, "parameterValues": param_values}
    
    print(f"\n📤 {task_name}")
    try:
        resp = requests.post(task_url, json=payload, timeout=5)
        print(f"✅ {resp.status_code}")
        return resp.status_code in [200, 202]
    except:
        print("⏳ Task queued (timeout normal)")
        return True

print("=" * 70)
print("FIX: Konfigurasi WAN Bridge untuk ZTE F670L V9.0")
print("=" * 70)

# Step 1: Refresh untuk memastikan structure
print("\n1. Refresh WANConnectionDevice structure...")
post_task("refreshObject", [["InternetGatewayDevice.WANDevice.1.WANConnectionDevice", ""]])
time.sleep(5)

# Step 2: Gunakan slot yang tersedia (biasanya ada 8 slot)
# Coba gunakan slot 2 yang mungkin sudah ada tapi kosong
print("\n2. Set VLAN di WANConnectionDevice.2...")
post_task("setParameterValues", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_ZTE-COM_WANGponLinkConfig.VLANIDMark", 55, "xsd:int"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_ZTE-COM_WANGponLinkConfig.X_ZTE-COM_VLANMode", "Tag", "xsd:string"]
])
time.sleep(3)

# Step 3: Refresh WANIPConnection
print("\n3. Refresh WANIPConnection...")
post_task("refreshObject", [["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection", ""]])
time.sleep(5)

# Step 4: Konfigurasi connection
print("\n4. Konfigurasi WANIPConnection untuk bridge + bind SSID 6...")
post_task("setParameterValues", [
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.Enable", True, "xsd:boolean"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.ConnectionType", "IP_Bridged", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.Name", "HOTSPOT_BRIDGE", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_ServiceList", "INTERNET", "xsd:string"],
    ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANIPConnection.1.X_ZTE-COM_BindLan", "InternetGatewayDevice.LANDevice.1.WLANConfiguration.6", "xsd:string"]
])

print("\n" + "=" * 70)
print("✅ Selesai! Tunggu 30 detik lalu cek hasil dengan:")
print("python3 check_wan_result.py")
print("=" * 70)
