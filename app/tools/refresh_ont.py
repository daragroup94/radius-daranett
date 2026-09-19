import os
import requests
import json
import urllib.parse
import time
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6145D2-FHTTC02CE8FB"
cr_url = "http://10.0.254.80:30005/0"

print("GenieACS URL:", url)
print("Device ID:", device_id)
print("Connection Request URL:", cr_url)

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

# 2. Queue getParameterValues for the parent path
print("Queuing getParameterValues for InternetGatewayDevice.WANDevice.1.WANConnectionDevice. ...")
task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks"
payload = {
    "name": "getParameterValues",
    "parameterNames": ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice."]
}

task_id = None
try:
    resp = requests.post(task_url, json=payload, auth=auth, timeout=5)
    print(f"Queue Response: {resp.status_code} - {resp.text}")
    if resp.status_code in (200, 201, 202):
        task_id = resp.json().get('_id')
except requests.exceptions.ReadTimeout:
    print("Post timed out, but task is likely queued.")
    time.sleep(2)
    t_resp = requests.get(f"{url}/tasks", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
    if t_resp.status_code == 200 and t_resp.json():
        task_id = t_resp.json()[0]['_id']
        print(f"Found queued task ID: {task_id}")
except Exception as e:
    print(f"Error queuing: {e}")

if not task_id:
    try:
        t_resp = requests.get(f"{url}/tasks", params={"query": json.dumps({"device": device_id})}, auth=auth, timeout=10)
        if t_resp.status_code == 200 and t_resp.json():
            task_id = t_resp.json()[0]['_id']
            print(f"Found queued task ID on retry: {task_id}")
    except:
        pass

if task_id:
    # Trigger connection request
    print("Triggering Connection Request...")
    try:
        cr_resp = requests.get(cr_url, auth=requests.auth.HTTPDigestAuth('msn', 'msn'), timeout=10)
        print(f"Connection Request response: {cr_resp.status_code}")
    except Exception as e:
        print(f"Error sending Connection Request: {e}")
        
    # Wait for task to complete
    print("Waiting for task to complete...")
    completed = False
    for i in range(25):
        try:
            chk = requests.get(f"{url}/tasks", params={"query": json.dumps({"_id": task_id})}, auth=auth, timeout=5)
            if chk.status_code == 200:
                if not chk.json():
                    print("Task completed and removed from queue!")
                    completed = True
                    break
                else:
                    # check if task failed or has faults
                    task_data = chk.json()[0]
                    if 'fault' in task_data:
                        print(f"Task fault detected: {task_data['fault']}")
                        break
                    print(f"Task still in queue (retry {i+1}/25)... status: {task_data.get('status') or 'unknown'}")
            else:
                print(f"Error checking task: {chk.status_code}")
        except Exception as e:
            print(f"Error checking task: {e}")
        time.sleep(2)

# Refresh device document
print("Refreshing device document...")
try:
    resp = requests.get(f"{url}/devices", params={"query": json.dumps({"_id": device_id})}, auth=auth, timeout=10)
    if resp.status_code == 200 and resp.json():
        device_data = resp.json()[0]
        with open("/app/debug_dump_fiberhome_refreshed.json", "w") as f:
            json.dump(device_data, f, indent=2)
        print("Device document saved successfully to /app/debug_dump_fiberhome_refreshed.json")
        
        # Check structure
        wcd = device_data.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
        print("WANConnectionDevice keys after refresh:", [k for k in wcd.keys() if not k.startswith('_')])
        for k in wcd:
            if k.startswith('_'): continue
            print(f"Index {k}:")
            print("  WANIPConnection:", [x for x in wcd[k].get('WANIPConnection', {}).keys() if not x.startswith('_')])
            print("  WANPPPConnection:", [x for x in wcd[k].get('WANPPPConnection', {}).keys() if not x.startswith('_')])
            
    else:
        print("Failed to fetch device document")
except Exception as e:
    print(f"Error fetching device: {e}")
