import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"

resp = requests.get(
    f"{url}/faults",
    params={"query": json.dumps({"device": device_id})},
    auth=auth,
    timeout=10
)

print(f"Status Code: {resp.status_code}")
if resp.status_code == 200:
    faults = resp.json()
    print(f"Faults count: {len(faults)}")
    for f in faults:
        print(f"Fault ID: {f.get('_id')}, Timestamp: {f.get('timestamp')}, Message: {f.get('message')}, Detail: {f.get('detail')}")
else:
    print(resp.text)
