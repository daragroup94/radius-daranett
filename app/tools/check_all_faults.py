import requests
import json
import sys
import os

# Adjust path to import services
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

print(f"GenieACS URL: {url}")
print(f"GenieACS Auth: {auth}")

try:
    resp = requests.get(
        f"{url}/faults",
        auth=auth,
        timeout=10
    )
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        faults = resp.json()
        print(f"Total Faults: {len(faults)}")
        for i, f in enumerate(faults[:20]):
            print(f"[{i}] Device: {f.get('device')}")
            print(f"    Message: {f.get('message')}")
            print(f"    Detail: {f.get('detail')}")
            print(f"    Timestamp: {f.get('timestamp')}")
    else:
        print(f"Error: {resp.text}")
except Exception as e:
    print(f"Connection failed: {e}")
