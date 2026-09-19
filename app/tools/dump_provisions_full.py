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

for endpoint in ["scripts", "presets", "provisions"]:
    try:
        resp = requests.get(f"{url}/{endpoint}", auth=auth, timeout=5)
        print(f"\n=======================================================")
        print(f"--- Endpoint: /{endpoint} (Status: {resp.status_code}) ---")
        print(f"=======================================================")
        if resp.status_code == 200:
            items = resp.json()
            print(f"Count: {len(items)}")
            for item in items:
                print(f"\nID: {item.get('_id')}")
                if 'script' in item:
                    print("--- Script Content ---")
                    print(item['script'])
                else:
                    print(json.dumps(item, indent=2))
        else:
            print(resp.text)
    except Exception as e:
        print(f"Failed to fetch {endpoint}: {e}")
