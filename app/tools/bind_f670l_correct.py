#!/usr/bin/env python3
import requests

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

# Khusus ZTE F670L: binding harus include WAN interface sendiri + SSID 6
binding_value = "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.2,InternetGatewayDevice.LANDevice.1.WLANConfiguration.6"

print("Binding WAN Hotspot ke SSID 6 (format ZTE F670L)...")

resp = requests.post(
    f"{GENIEACS_URL}/devices/{DEVICE_ID}/tasks?connection_request",
    json={
        "name": "setParameterValues",
        "parameterValues": [
            ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.2.X_ZTE-COM_BindLan", binding_value, "xsd:string"]
        ]
    },
    timeout=10
)

print(f"Status: {resp.status_code}")
print(f"Binding: {binding_value}")
if resp.status_code == 200:
    print("✅ Binding berhasil!")
