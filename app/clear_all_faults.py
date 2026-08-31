import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

print(f"GenieACS URL: {url}")

try:
    resp = requests.get(f"{url}/faults", auth=auth, timeout=10)
    if resp.status_code == 200:
        faults = resp.json()
        print(f"Total active faults found: {len(faults)}")
        for f in faults:
            f_id = f.get('_id')
            device_id = f.get('device')
            msg = f.get('message')
            import urllib.parse
            print(f"Deleting fault {f_id} (Device: {device_id}, Message: {msg})...")
            del_resp = requests.delete(f"{url}/faults/{urllib.parse.quote(f_id)}", auth=auth, timeout=10)
            print(f"  Result: {del_resp.status_code}")
        print("All faults cleared.")
    else:
        print(f"Error fetching faults: {resp.text}")
except Exception as e:
    print(f"Failed to clear faults: {e}")
