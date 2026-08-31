#!/usr/bin/env python3
import sys
import json
import requests
import time
from requests.auth import HTTPDigestAuth

ACS_URL = "http://216.176.238.196:7557"
ACS_AUTH = ("admin", "acsadmin1994")

def change_web_port(device_id):
    r = requests.get(
        f"{ACS_URL}/devices",
        params={"query": json.dumps({"_id": device_id}), "projection": "InternetGatewayDevice.ManagementServer"},
        auth=ACS_AUTH,
        timeout=10
    )
    dev_data = r.json()
    mgt = dev_data[0].get('InternetGatewayDevice', {}).get('ManagementServer', {})
    cr_url = mgt.get('ConnectionRequestURL', {}).get('_value')
    cr_user = mgt.get('ConnectionRequestUsername', {}).get('_value', 'msn')
    cr_pass = mgt.get('ConnectionRequestPassword', {}).get('_value', 'msn')

    # Kita ubah port WEB (80) menjadi 8080, lalu aktifkan ACL-nya
    print("Queueing task to change WEB port to 8080 and enable ACL...")
    task_payload = {
        "name": "setParameterValues",
        "parameterValues": [
            ["InternetGatewayDevice.X_ZTE-COM_Security.IPV4RemoteServicePortControl.1.Port", 8080, "xsd:unsignedInt"],
            ["InternetGatewayDevice.X_ZTE-COM_Security.AclServices.1.Service", "WEB", "xsd:string"],
            ["InternetGatewayDevice.X_ZTE-COM_Security.AclServices.1.Enable", True, "xsd:boolean"],
            ["InternetGatewayDevice.X_ZTE-COM_Security.AclServices.1.INCName", "WAN_ALL", "xsd:string"],
            ["InternetGatewayDevice.X_ZTE-COM_Security.AclServices.1.MinSrcIp", "0.0.0.0", "xsd:string"],
            ["InternetGatewayDevice.X_ZTE-COM_Security.AclServices.1.MaxSrcIp", "0.0.0.0", "xsd:string"]
        ]
    }
    
    # Hapus task lama
    requests.get(f"{ACS_URL}/tasks", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
    
    r = requests.post(
        f"{ACS_URL}/devices/{device_id}/tasks",
        json=task_payload,
        auth=ACS_AUTH,
        timeout=10
    )
    
    try:
        requests.get(cr_url, auth=HTTPDigestAuth(cr_user, cr_pass), timeout=8)
    except Exception as e:
        print(f"CR trigger failed: {e}")
        
    time.sleep(8)
    
    # Cek fault
    rf = requests.get(f"{ACS_URL}/faults", params={"query": json.dumps({"device": device_id})}, auth=ACS_AUTH)
    faults = rf.json()
    if faults:
        print(f"❌ FAILED! Fault: {faults[0].get('message')}")
        requests.delete(f"{ACS_URL}/faults/{faults[0].get('_id')}", auth=ACS_AUTH)
    else:
        print("🎉 SUCCESS! WEB port changed to 8080 and ACL enabled successfully!")

if __name__ == "__main__":
    change_web_port(sys.argv[1])
