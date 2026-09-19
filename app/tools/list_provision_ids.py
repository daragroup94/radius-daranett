import requests
import json
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

resp = requests.get(f"{url}/provisions", auth=auth, timeout=5)
if resp.status_code == 200:
    for provision in resp.json():
        print(f"ID: {provision['_id']}, Length: {len(provision.get('script', ''))}")
else:
    print(resp.text)
