#!/usr/bin/env python3
import requests

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

print(f"Triggering connection request for {DEVICE_ID}...")

# Trigger connection request via GenieACS
resp = requests.post(
    f"{GENIEACS_URL}/devices/{DEVICE_ID}/tasks?connection_request",
    json={"name": "getParameterValues", "parameterValues": [["InternetGatewayDevice.DeviceInfo.SerialNumber", ""]]},
    timeout=30
)

print(f"Status: {resp.status_code}")
print(f"Response: {resp.text}")

if resp.status_code in [200, 202]:
    print("\n✅ Connection request berhasil! Tunggu 10-30 detik agar ONT melakukan inform dan task dieksekusi.")
else:
    print("\n❌ Connection request gagal!")
