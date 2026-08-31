#!/usr/bin/env python3
import sys
import json
import requests
import time
from requests.auth import HTTPDigestAuth

ACS_URL = "http://216.176.238.196:7557"
ACS_AUTH = ("admin", "acsadmin1994")

def run_task(device_id, task_payload):
    # 1. Hapus task pending lama di ACS untuk device ini
    print(f"Clearing old tasks for {device_id}...")
    try:
        r = requests.get(f"{ACS_URL}/tasks", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
        if r.status_code == 200:
            for t in r.json():
                tid = t["_id"]
                requests.delete(f"{ACS_URL}/tasks/{tid}", auth=ACS_AUTH)
                print(f"Deleted task {tid}")
    except Exception as e:
        print(f"Error clearing tasks: {e}")

    # 2. Kirim task baru ke GenieACS (tanpa connection_request agar ACS tidak timeout)
    print(f"Queueing new task in GenieACS...")
    try:
        r = requests.post(
            f"{ACS_URL}/devices/{device_id}/tasks",
            json=task_payload,
            auth=ACS_AUTH,
            timeout=15
        )
        print(f"Queue Status: {r.status_code}")
        if r.status_code not in [200, 202]:
            print("Failed to queue task")
            return False
    except Exception as e:
        print(f"Error queueing task: {e}")
        return False

    # 3. Ambil data Connection Request dari ACS
    print(f"Fetching CR credentials from ACS...")
    try:
        r = requests.get(
            f"{ACS_URL}/devices",
            params={"query": json.dumps({"_id": device_id}), "projection": "InternetGatewayDevice.ManagementServer"},
            auth=ACS_AUTH,
            timeout=10
        )
        dev_data = r.json()
        if not dev_data:
            print("Device not found in ACS")
            return False
        
        mgt = dev_data[0].get('InternetGatewayDevice', {}).get('ManagementServer', {})
        cr_url = mgt.get('ConnectionRequestURL', {}).get('_value')
        cr_user = mgt.get('ConnectionRequestUsername', {}).get('_value', 'msn')
        cr_pass = mgt.get('ConnectionRequestPassword', {}).get('_value', 'msn')
        
        if not cr_url:
            print("ConnectionRequestURL is empty in ACS!")
            return False
            
        print(f"Found CR URL: {cr_url}")
        print(f"Triggering Connection Request from VPS using Digest Auth ({cr_user}:{cr_pass})...")
        
        # 4. Tembak HTTP GET Digest ke IP private ONT langsung dari VPS ini
        resp = requests.get(cr_url, auth=HTTPDigestAuth(cr_user, cr_pass), timeout=10)
        print(f"Direct CR HTTP Status: {resp.status_code}")
        
        if resp.status_code == 200:
            print("Connection Request succeeded! Waiting 15 seconds for task execution...")
            time.sleep(15)
            
            # Cek faults terbaru
            rf = requests.get(f"{ACS_URL}/faults", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
            faults = rf.json()
            if faults:
                print("⚠️ FAULTS:")
                for f in faults:
                    print(f"  - Code: {f.get('code')}, Message: {f.get('message')}")
            else:
                print("✅ No faults found.")
            
            # Cek sisa task
            rt = requests.get(f"{ACS_URL}/tasks", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
            tasks = rt.json()
            print(f"Remaining tasks in queue: {len(tasks)}")
            if len(tasks) == 0:
                print("🚀 Task completed successfully!")
            return True
        else:
            print(f"CR failed with status code {resp.status_code}")
            return False
            
    except Exception as e:
        print(f"Error triggering CR: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: ./run_cwmp_task.py <device_id> '<task_json_payload>'")
        sys.exit(1)
    
    dev_id = sys.argv[1]
    payload = json.loads(sys.argv[2])
    run_task(dev_id, payload)
