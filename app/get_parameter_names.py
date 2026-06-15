import os
import requests
import json
import urllib.parse
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"

# 1. Fetch and delete any pending tasks for this device
print("Checking and deleting existing tasks...")
try:
    resp = requests.get(f"{url}/tasks", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
    if resp.status_code == 200:
        for t in resp.json():
            t_id = t['_id']
            print(f"Deleting task {t_id}...")
            requests.delete(f"{url}/tasks/{t_id}", auth=auth, timeout=10)
except Exception as e:
    print(f"Error deleting tasks: {e}")

# 2. Fetch and delete any faults for this device
print("Checking and deleting existing faults...")
try:
    resp = requests.get(f"{url}/faults", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
    if resp.status_code == 200:
        for f in resp.json():
            f_id = f['_id']
            print(f"Deleting fault {f_id}...")
            requests.delete(f"{url}/faults/{f_id}", auth=auth, timeout=10)
except Exception as e:
    print(f"Error deleting faults: {e}")

# 3. Queue getParameterNames tasks (with large timeout in case NBI blocks)
parents = [
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_FH_WANGponLinkConfig."
]

for p in parents:
    print(f"Queuing getParameterNames for {p}...")
    task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks"
    payload = {
        "name": "getParameterNames",
        "parameterName": p,
        "nextLevel": False
    }
    try:
        resp = requests.post(task_url, json=payload, auth=auth, timeout=40)
        print(f"Response: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"Error queuing: {e}")
