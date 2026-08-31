#!/usr/bin/env python3
import sys
import json
import requests
import time
from requests.auth import HTTPDigestAuth

ACS_URL = "http://216.176.238.196:7557"
ACS_AUTH = ("admin", "acsadmin1994")

def run_flow(device_id, task_payload):
    # 1. Ambil info CR dari ACS
    print(f"Fetching CR credentials for {device_id}...")
    try:
        r = requests.get(
            f"{ACS_URL}/devices",
            params={"query": json.dumps({"_id": device_id}), "projection": "InternetGatewayDevice.ManagementServer"},
            auth=ACS_AUTH,
            timeout=10
        )
        dev_data = r.json()
        if not dev_data:
            print("Device not found")
            return
        
        mgt = dev_data[0].get('InternetGatewayDevice', {}).get('ManagementServer', {})
        cr_url = mgt.get('ConnectionRequestURL', {}).get('_value')
        cr_user = mgt.get('ConnectionRequestUsername', {}).get('_value', 'msn')
        cr_pass = mgt.get('ConnectionRequestPassword', {}).get('_value', 'msn')
        
        if not cr_url:
            print("ConnectionRequestURL is empty!")
            return
            
    except Exception as e:
        print(f"Error fetching device data: {e}")
        return

    # 2. Hapus task pending lama
    print("Clearing old tasks...")
    try:
        r = requests.get(f"{ACS_URL}/tasks", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
        if r.status_code == 200:
            for t in r.json():
                requests.delete(f"{ACS_URL}/tasks/{t['_id']}", auth=ACS_AUTH)
    except:
        pass

    # 3. Pemicuan Connection Request dari VPS -> ONT
    print(f"Triggering Connection Request from VPS to {cr_url}...")
    try:
        resp = requests.get(cr_url, auth=HTTPDigestAuth(cr_user, cr_pass), timeout=8)
        print(f"ONT CR HTTP Status: {resp.status_code}")
    except Exception as e:
        print(f"Failed to reach ONT: {e}")
        return

    # 4. Tunggu 1.5 detik agar session CWMP mulai terbentuk di ACS
    print("Waiting 1.5 seconds for CWMP session to start...")
    time.sleep(1.5)

    # 5. Kirim task ke GenieACS (seharusnya langsung diproses karena session aktif)
    print("Queueing task in GenieACS...")
    try:
        r = requests.post(
            f"{ACS_URL}/devices/{device_id}/tasks",
            json=task_payload,
            auth=ACS_AUTH,
            timeout=10
        )
        print(f"Task Post Status: {r.status_code}")
        print(f"Task Post Response: {r.text}")
    except Exception as e:
        print(f"Error queueing task: {e}")

    # 6. Tunggu 10 detik dan cek status
    print("Waiting 10 seconds for completion...")
    time.sleep(10)
    
    try:
        # Cek sisa task
        r = requests.get(f"{ACS_URL}/tasks", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
        print(f"Sisa task di queue: {len(r.json())}")
        
        # Cek faults
        rf = requests.get(f"{ACS_URL}/faults", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
        faults = rf.json()
        if faults:
            print("⚠️ FAULTS:")
            for f in faults:
                print(f"  - Code: {f.get('code')}, Message: {f.get('message')}")
        else:
            print("✅ No faults.")
            
        # Ambil value terbaru jika getParameterValues
        if task_payload.get("name") == "getParameterValues":
            # Query parameter dari ACS
            path = task_payload.get("parameterNames")[0]
            print(f"\nFetching updated values for {path} from ACS database...")
            r_val = requests.get(
                f"{ACS_URL}/devices",
                params={"query": json.dumps({"_id": device_id}), "projection": path},
                auth=ACS_AUTH
            )
            print(json.dumps(r_val.json(), indent=2))
            
    except Exception as e:
        print(f"Error checking final status: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: ./trigger_and_run.py <device_id> '<task_json_payload>'")
        sys.exit(1)
    
    dev_id = sys.argv[1]
    payload = json.loads(sys.argv[2])
    run_flow(dev_id, payload)
