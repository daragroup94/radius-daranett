import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

resp = requests.get(f"{url}/virtualParameters", auth=auth, timeout=5)
if resp.status_code == 200:
    for vp in resp.json():
        print(f"==================================================")
        print(f"VP ID: {vp['_id']}")
        print(f"==================================================")
        print(json.dumps(vp, indent=2))
        print("\n")
else:
    print(resp.text)
