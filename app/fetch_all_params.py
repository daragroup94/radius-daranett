import os
import requests
import json
import urllib.parse
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
print(f"GenieACS URL: {url}")
print(f"GenieACS Auth: {auth}")

username = "FHTT9646079C"
query = json.dumps({
    "$or": [
        {"VirtualParameters.pppoeUsername._value": username},
        {"VirtualParameters.pppoeUsername2._value": username},
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})

resp = requests.get(
    f"{url}/devices",
    params={"query": query, "projection": "_id"},
    auth=auth,
    timeout=10
)

if resp.status_code == 200 and resp.json():
    device_id = resp.json()[0]['_id']
    print(f"Device ID: {device_id}")
    
    # Queue getParameterValues task
    task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
    payload = {
        "name": "getParameterValues",
        "parameterNames": [
            "InternetGatewayDevice.WANDevice.1.WANConnectionDevice."
        ]
    }
    
    task_resp = requests.post(task_url, json=payload, auth=auth, timeout=15)
    print(f"Task Response Status: {task_resp.status_code}")
    print(f"Task Response Text: {task_resp.text}")
else:
    print("Device not found or error querying device")
