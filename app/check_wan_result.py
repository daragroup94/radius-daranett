#!/usr/bin/env python3
import requests
import json

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

print(f"Checking WAN configuration for {DEVICE_ID}...\n")

resp = requests.get(
    f"{GENIEACS_URL}/devices?query=" + json.dumps({"_id": DEVICE_ID}),
    timeout=10
)

if resp.status_code == 200 and resp.json():
    device = resp.json()[0]
    
    # Check WANConnectionDevice.2
    wcd2 = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get("2", {})
    
    if wcd2:
        print("✅ WANConnectionDevice.2 EXISTS\n")
        
        # Check VLAN config
        gpon = wcd2.get("X_ZTE-COM_WANGponLinkConfig", {})
        if gpon:
            vlan = gpon.get("VLANIDMark", {}).get("_value")
            print(f"VLAN Config: {vlan}")
        
        # Check WANIPConnection
        wanip = wcd2.get("WANIPConnection", {}).get("1", {})
        if wanip:
            print("\n✅ WANIPConnection.1 EXISTS")
            print(f"Name: {wanip.get('Name', {}).get('_value')}")
            print(f"Enable: {wanip.get('Enable', {}).get('_value')}")
            print(f"ConnectionType: {wanip.get('ConnectionType', {}).get('_value')}")
            print(f"VLAN ID: {wanip.get('X_ZTE-COM_VLANID', {}).get('_value')}")
            print(f"Binding: {wanip.get('X_ZTE-COM_BindLan', {}).get('_value')}")
        else:
            print("\n❌ WANIPConnection.1 NOT FOUND")
    else:
        print("❌ WANConnectionDevice.2 NOT FOUND")
        print("\nKemungkinan:")
        print("1. Task belum dieksekusi (ONT belum inform)")
        print("2. Perlu trigger connection request manual")
        print("3. Ada error saat eksekusi task")
else:
    print(f"Error: {resp.status_code}")
