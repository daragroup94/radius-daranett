import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

resp = requests.get(f"{url}/faults", auth=auth, timeout=10)
if resp.status_code == 200:
    faults = resp.json()
    print(f"Total Faults: {len(faults)}")
    
    # Group by fault message/details or device model
    for i, f in enumerate(faults):
        device_id = f.get('device')
        
        # Get device manufacturer/model
        dev_resp = requests.get(f"{url}/devices", params={"query": json.dumps({"_id": device_id})}, auth=auth, timeout=5)
        dev_info = "Unknown"
        if dev_resp.status_code == 200 and dev_resp.json():
            d = dev_resp.json()[0]
            mfg = d.get('_deviceId', {}).get('_Manufacturer', 'Unknown')
            pc = d.get('_deviceId', {}).get('_ProductClass', 'Unknown')
            dev_info = f"{mfg} ({pc})"
        
        print(f"\n[{i}] Device: {device_id} ({dev_info})")
        print(f"    Message: {f.get('message')}")
        print(f"    Detail: {json.dumps(f.get('detail'), indent=2)}")
        print(f"    Timestamp: {f.get('timestamp')}")
else:
    print(resp.text)
