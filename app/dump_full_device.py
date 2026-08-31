import json
from services.genieacs import get_genieacs_config
import requests

username = 'FHTTC02CE8FB'
config = get_genieacs_config()
GENIEACS_NBI = config['url']
auth = config['auth']
query = json.dumps({
    "_deviceId._SerialNumber": username
})

resp = requests.get(
    f'{GENIEACS_NBI}/devices',
    params={'query': query},
    auth=auth,
    timeout=5
)
if resp.status_code == 200 and resp.json():
    data = resp.json()[0]
    with open('/app/debug_dump_fiberhome.json', 'w') as f:
        json.dump(data, f, indent=2)
    print("Successfully dumped device data to /app/debug_dump_fiberhome.json")
else:
    print(f"Error fetching device: {resp.status_code} - {resp.text}")
