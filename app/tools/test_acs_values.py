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
data = resp.json()[0]

def print_paths(d, path=""):
    res = []
    if isinstance(d, dict):
        if '_value' in d:
            res.append(f"{path} = {d['_value']}")
        else:
            for k, v in d.items():
                if not k.startswith('_'):
                    new_path = f"{path}.{k}" if path else k
                    res.extend(print_paths(v, new_path))
    return res

for line in print_paths(data):
    if "WANDevice" in line or "WANPPP" in line or "IP" in line or "PPP" in line:
        print(line)
