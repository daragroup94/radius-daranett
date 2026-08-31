#!/usr/bin/env python3
import requests
import json

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

print(f"Checking tasks for {DEVICE_ID}...\n")

resp = requests.get(f"{GENIEACS_URL}/tasks?query=" + json.dumps({"device": DEVICE_ID}), timeout=10)

if resp.status_code == 200:
    tasks = resp.json()
    print(f"Total tasks: {len(tasks)}\n")
    for task in tasks:
        print(f"Task ID: {task.get('_id')}")
        print(f"Name: {task.get('name')}")
        print(f"Timestamp: {task.get('timestamp')}")
        print("-" * 50)
else:
    print(f"Error: {resp.status_code}")
