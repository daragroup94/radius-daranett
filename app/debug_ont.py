import os
import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
print(f"GenieACS URL: {url}")
print(f"GenieACS Auth: {auth}")

username = "FHTT9646079C"
query = json.dumps({
    "$or": [
        {"VirtualParameters.pppoeUsername._value": username},
        {"VirtualParameters.pppoeUsername2._value": username},
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})

resp = requests.get(
    f"{url}/devices",
    params={"query": query},
    auth=auth,
    timeout=10
)

print(f"Status Code: {resp.status_code}")
if resp.status_code == 200:
    devices = resp.json()
    if not devices:
        print("Device not found")
    else:
        device = devices[0]
        # print metadata
        print(f"Manufacturer: {device.get('_deviceId', {}).get('_Manufacturer')}")
        print(f"ProductClass: {device.get('_deviceId', {}).get('_ProductClass')}")
        print(f"SerialNumber: {device.get('_deviceId', {}).get('_SerialNumber')}")
        
        # Save complete JSON dump to a file in the app directory
        out_path = "/app/debug_dump.json"
        with open(out_path, "w") as f:
            json.dump(device, f, indent=2)
        print(f"Dumped complete device info to {out_path}")
        
        # Print WANDevice structure
        wcd = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
        print("\n--- WAN Connection Devices ---")
        for wcd_idx, wcd_val in wcd.items():
            if not wcd_idx.isdigit():
                continue
            print(f"\nWANConnectionDevice.{wcd_idx}:")
            # GPON Link Config
            for k, v in wcd_val.items():
                if 'GponLink' in k or 'GPONLink' in k or 'LinkConfig' in k:
                    print(f"  {k}: {v}")
                    
            ppp = wcd_val.get("WANPPPConnection", {})
            for p_idx, p_val in ppp.items():
                if not p_idx.isdigit():
                    continue
                print(f"  WANPPPConnection.{p_idx}:")
                for pk, pv in p_val.items():
                    # only print _value or structure
                    if isinstance(pv, dict) and '_value' in pv:
                        print(f"    {pk}: {pv['_value']}")
                    elif not isinstance(pv, dict):
                        print(f"    {pk}: {pv}")
            ip_conn = wcd_val.get("WANIPConnection", {})
            for i_idx, i_val in ip_conn.items():
                if not i_idx.isdigit():
                    continue
                print(f"  WANIPConnection.{i_idx}:")
                for ik, iv in i_val.items():
                    if isinstance(iv, dict) and '_value' in iv:
                        print(f"    {ik}: {iv['_value']}")
                    elif not isinstance(iv, dict):
                        print(f"    {ik}: {iv}")
else:
    print(resp.text)
