import time
import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"
task_id = "6a2aae3da3f65c8896713b5d"

print("Waiting for task to complete...")
for i in range(24): # 2 minutes max
    resp = requests.get(
        f"{url}/tasks",
        params={"query": json.dumps({"_id": task_id})},
        auth=auth,
        timeout=10
    )
    if resp.status_code == 200:
        tasks = resp.json()
        if not tasks:
            print("Task completed (removed from queue)!")
            break
        else:
            print(f"Task is still in queue. Retrying in 5 seconds... ({i+1}/24)")
    else:
        print(f"Error checking tasks: {resp.text}")
        break
    time.sleep(5)

# Refresh device document
resp = requests.get(
    f"{url}/devices",
    params={"query": json.dumps({"_id": device_id})},
    auth=auth,
    timeout=10
)
if resp.status_code == 200 and resp.json():
    device = resp.json()[0]
    with open("/app/debug_dump.json", "w") as f:
        json.dump(device, f, indent=2)
    print("Device document refreshed!")
else:
    print("Failed to refresh device document")
