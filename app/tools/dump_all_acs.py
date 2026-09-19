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

def extract_strings(d, path=""):
    res = []
    if isinstance(d, dict):
        if '_value' in d:
            val = str(d['_value']).strip()
            if val and len(val) > 2 and val not in ['True', 'False', '0', '1']:
                res.append(f"{path} = {val}")
        else:
            for k, v in d.items():
                if not k.startswith('_'):
                    new_path = f"{path}.{k}" if path else k
                    res.extend(extract_strings(v, new_path))
    return res

for line in extract_strings(data):
    if "WANDevice" in line or "PPP" in line or "Name" in line or "User" in line or "Account" in line:
        print(line)
