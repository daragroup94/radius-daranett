#!/usr/bin/env python3
import requests
import json

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

resp = requests.get(
    f"{GENIEACS_URL}/devices?query=" + json.dumps({"_id": DEVICE_ID}),
    timeout=10
)

if resp.status_code == 200 and resp.json():
    device = resp.json()[0]
    wan_devices = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
    
    print(f"Available WANConnectionDevice slots:\n")
    for slot, data in wan_devices.items():
        if slot.isdigit():
            print(f"Slot {slot}:")
            
            # Check WANIPConnection
            wanip_instances = data.get("WANIPConnection", {})
            wanip_count = sum(1 for k in wanip_instances.keys() if k.isdigit())
            print(f"  - WANIPConnection instances: {wanip_count}")
            
            if wanip_count > 0:
                for idx, wanip in wanip_instances.items():
                    if idx.isdigit():
                        name = wanip.get("Name", {}).get("_value", "N/A")
                        conn_type = wanip.get("ConnectionType", {}).get("_value", "N/A")
                        enabled = wanip.get("Enable", {}).get("_value", False)
                        print(f"    [{idx}] {name} - {conn_type} - {'ON' if enabled else 'OFF'}")
            
            # Check WANPPPConnection  
            wanppp_instances = data.get("WANPPPConnection", {})
            wanppp_count = sum(1 for k in wanppp_instances.keys() if k.isdigit())
            if wanppp_count > 0:
                print(f"  - WANPPPConnection instances: {wanppp_count}")
            
            print()
else:
    print(f"Error: {resp.status_code}")
