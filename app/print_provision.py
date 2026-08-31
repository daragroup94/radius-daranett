import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

prov_id = sys.argv[1] if len(sys.argv) > 1 else 'bootstrap'

resp = requests.get(f"{url}/provisions", auth=auth, timeout=5)
if resp.status_code == 200:
    data = next((item for item in resp.json() if item['_id'] == prov_id), None)
    if data:
        print(f"==================================================")
        print(f"PROVISION ID: {data['_id']}")
        print(f"==================================================")
        print(data.get('script', ''))
    else:
        print(f"Provision {prov_id} not found in the list.")
else:
    print(f"Status: {resp.status_code}, error: {resp.text}")
