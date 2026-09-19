import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

resp = requests.get(f"{url}/devices", auth=auth, timeout=5)
if resp.status_code == 200:
    devices = resp.json()
    print(f"Total devices in GenieACS: {len(devices)}")
    for d in devices:
        dev_id = d.get('_id')
        manufacturer = d.get('_deviceId', {}).get('_Manufacturer', 'Unknown')
        product_class = d.get('_deviceId', {}).get('_ProductClass', 'Unknown')
        serial = d.get('_deviceId', {}).get('_SerialNumber', 'Unknown')
        
        has_igd = 'InternetGatewayDevice' in d
        has_device = 'Device' in d
        
        print(f"Device ID: {dev_id}")
        print(f"  Manufacturer: {manufacturer}, Model: {product_class}, Serial: {serial}")
        print(f"  Has InternetGatewayDevice (TR-098): {has_igd}")
        print(f"  Has Device (TR-181): {has_device}")
        
        # Check last inform and any faults
        f_resp = requests.get(f"{url}/faults", params={"query": json.dumps({"device": dev_id})}, auth=auth, timeout=5)
        fault_count = len(f_resp.json()) if f_resp.status_code == 200 else 'Error'
        print(f"  Active Faults: {fault_count}")
        print("-" * 40)
else:
    print(resp.text)
