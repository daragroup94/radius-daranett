import os
import requests
import json
import urllib.parse
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"
old_task_id = "6a2aae3da3f65c8896713b5d"

# 1. Delete the stuck task
print(f"Deleting task {old_task_id}...")
del_resp = requests.delete(
    f"{url}/tasks/{old_task_id}",
    auth=auth,
    timeout=10
)
print(f"Delete response: {del_resp.status_code}")

# 2. Queue specific getParameterValues
paths = [
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.AddressingType",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.Name",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.VLANIDMark",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.X_FH_ServiceList",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.X_FH_LanInterface",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.X_FH_IPMode",
    
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.Username",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.Password",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.Name",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.X_FH_ServiceList",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.X_FH_LanInterface",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.X_FH_IPMode",
    
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.WANIPConnection.1.AddressingType",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.WANIPConnection.1.Name",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.WANIPConnection.1.X_FH_ServiceList",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.WANIPConnection.1.X_FH_LanInterface",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.WANIPConnection.1.X_FH_IPMode",
    
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.WANPPPConnection.1.Username",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.WANPPPConnection.1.Name",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.WANPPPConnection.1.X_FH_ServiceList",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.WANPPPConnection.1.X_FH_LanInterface",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.WANPPPConnection.1.X_FH_IPMode",
    
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_FH_WANGponLinkConfig.",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_FH_WANGponLinkConfig.",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.3.X_FH_WANGponLinkConfig.",
    "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.4.X_FH_WANGponLinkConfig."
]

print("Queuing new task...")
task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
payload = {
    "name": "getParameterValues",
    "parameterNames": paths
}

task_resp = requests.post(task_url, json=payload, auth=auth, timeout=15)
print(f"Task Response Status: {task_resp.status_code}")
print(f"Task Response Text: {task_resp.text}")
if task_resp.status_code in (200, 201, 202):
    t_id = task_resp.json().get('_id')
    print(f"New Task ID: {t_id}")
