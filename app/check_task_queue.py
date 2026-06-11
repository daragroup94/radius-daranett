import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"

resp = requests.get(
    f"{url}/tasks",
    params={"query": json.dumps({"device": device_id})},
    auth=auth,
    timeout=10
)

print(f"Status Code: {resp.status_code}")
if resp.status_code == 200:
    tasks = resp.json()
    print(f"Task queue length: {len(tasks)}")
    for t in tasks:
        print(f"Task ID: {t.get('_id')}, Name: {t.get('name')}, ParameterNames: {t.get('parameterNames') or t.get('parameterValues') or 'N/A'}")
else:
    print(resp.text)
