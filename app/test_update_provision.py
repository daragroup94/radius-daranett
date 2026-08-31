import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

prov_id = "inform"
resp = requests.get(f"{url}/provisions", auth=auth)
if resp.status_code == 200:
    provisions = resp.json()
    existing = next((p for p in provisions if p['_id'] == prov_id), None)
    if existing:
        print(f"Existing {prov_id} provision found.")
        
        # Test PUT with text/plain
        put_resp = requests.put(
            f"{url}/provisions/{prov_id}",
            data=existing['script'],
            auth=auth,
            headers={"Content-Type": "text/plain"}
        )
        print(f"PUT (text/plain) Status: {put_resp.status_code}")
        print(f"PUT (text/plain) Response: {put_resp.text}")
    else:
        print(f"Provision {prov_id} not found.")
else:
    print(f"GET Status: {resp.status_code}, {resp.text}")
