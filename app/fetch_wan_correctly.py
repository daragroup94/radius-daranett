import os
import requests
import json
import urllib.parse
import time
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

# 3. Queue getParameterValues for the parent path
print("Queuing getParameterValues for InternetGatewayDevice.WANDevice.1.WANConnectionDevice. ...")
task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks"
payload = {
    "name": "getParameterValues",
    "parameterNames": ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice."]
}

task_id = None
try:
    # Set a small timeout (5s) and catch ReadTimeout.
    # The NBI might block, but the task gets written to DB immediately.
    resp = requests.post(task_url, json=payload, auth=auth, timeout=5)
    print(f"Queue Response: {resp.status_code} - {resp.text}")
    if resp.status_code in (200, 201, 202):
        task_id = resp.json().get('_id')
except requests.exceptions.ReadTimeout:
    print("Post timed out, but task is likely queued.")
    # Fetch tasks to get the ID
    time.sleep(2)
    t_resp = requests.get(f"{url}/tasks", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
    if t_resp.status_code == 200 and t_resp.json():
        task_id = t_resp.json()[0]['_id']
        print(f"Found queued task ID: {task_id}")
except Exception as e:
    print(f"Error queuing: {e}")

if not task_id:
    # Try once more to find the task
    try:
        t_resp = requests.get(f"{url}/tasks", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
        if t_resp.status_code == 200 and t_resp.json():
            task_id = t_resp.json()[0]['_id']
            print(f"Found queued task ID on retry: {task_id}")
    except:
        pass

if task_id:
    # Trigger connection request using curl/requests to ONT IP
    print("Triggering Connection Request via ONT Management Port...")
    try:
        cr_resp = requests.get("http://10.0.254.24:7547/0", auth=requests.auth.HTTPDigestAuth('msn', 'msn'), timeout=5)
        print(f"Connection Request response: {cr_resp.status_code}")
    except Exception as e:
        print(f"Error sending Connection Request: {e}")
        
    # Wait for task to complete
    print("Waiting for task to complete...")
    for i in range(20):
        try:
            chk = requests.get(f"{url}/tasks", params={"query": json.dumps({"_id": task_id})}, auth=auth, timeout=5)
            if chk.status_code == 200:
                if not chk.json():
                    print("Task completed and removed from queue!")
                    break
                else:
                    print(f"Task still in queue (retry {i+1}/20)...")
            else:
                print(f"Error checking task: {chk.status_code}")
        except Exception as e:
            print(f"Error checking task: {e}")
        time.sleep(3)

# Refresh device document
print("Refreshing device document...")
try:
    resp = requests.get(f"{url}/devices", params={"query": json.dumps({"_id": device_id})}, auth=auth, timeout=10)
    if resp.status_code == 200 and resp.json():
        with open("/app/debug_dump.json", "w") as f:
            json.dump(resp.json()[0], f, indent=2)
        print("Device document saved successfully!")
    else:
        print("Failed to fetch device document")
except Exception as e:
    print(f"Error fetching device: {e}")
