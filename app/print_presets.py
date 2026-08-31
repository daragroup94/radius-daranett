import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

resp = requests.get(f"{url}/presets", auth=auth, timeout=5)
if resp.status_code == 200:
    for preset in resp.json():
        print(f"==================================================")
        print(f"PRESET ID: {preset['_id']}")
        print(f"==================================================")
        print(json.dumps(preset, indent=2))
        print("\n")
else:
    print(resp.text)
