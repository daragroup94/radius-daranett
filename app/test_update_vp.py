import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

# We will try to update a test VP, or get the existing one first
vp_id = "userAdmin"
resp = requests.get(f"{url}/virtualParameters", auth=auth)
if resp.status_code == 200:
    vps = resp.json()
    existing = next((vp for vp in vps if vp['_id'] == vp_id), None)
    if existing:
        print(f"Existing {vp_id} script found.")
        # Let's try to update it with the same script to verify NBI API
        put_resp = requests.put(
            f"{url}/virtual_parameters/{vp_id}",
            data=existing['script'],
            auth=auth,
            headers={"Content-Type": "text/plain"}
        )
        print(f"PUT Status: {put_resp.status_code}")
        print(f"PUT Response: {put_resp.text}")
    else:
        print(f"VP {vp_id} not found.")
else:
    print(f"GET Status: {resp.status_code}, {resp.text}")
