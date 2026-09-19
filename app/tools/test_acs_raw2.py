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
# Find anything containing "Username" or "username"
def find_keys(d, target, path=""):
    res = []
    if isinstance(d, dict):
        for k, v in d.items():
            new_path = f"{path}.{k}" if path else k
            if target.lower() in k.lower():
                res.append((new_path, v))
            res.extend(find_keys(v, target, new_path))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            new_path = f"{path}[{i}]"
            res.extend(find_keys(v, target, new_path))
    return res

print(find_keys(data, "username"))
