#!/usr/bin/env python3
import requests
import json

GENIEACS_URL = "http://216.176.238.196:7557"
DEVICE_ID = "F42D06-F670L-ZTEGD4A107EE"

# 1. Cek current VLAN config
print("Checking current VLAN config...\n")
r = requests.get(f'{GENIEACS_URL}/devices?query={json.dumps({"_id": DEVICE_ID})}', timeout=10)
d = r.json()[0]

wcd = d['InternetGatewayDevice']['WANDevice']['1']['WANConnectionDevice']['1']

# Cek X_ZTE-COM_WANGponLinkConfig
print("X_ZTE-COM_WANGponLinkConfig instances:")
for key in wcd.keys():
    if 'GponLink' in key or 'GPONLink' in key:
        print(f"\n{key}:")
        gpon_cfg = wcd[key]
        if isinstance(gpon_cfg, dict):
            for idx, cfg in gpon_cfg.items():
                if idx.isdigit():
                    vlan = cfg.get('VLANIDMark', {}).get('_value', 'N/A')
                    mode = cfg.get('X_ZTE-COM_VLANMode', {}).get('_value', 'N/A')
                    print(f"  [{idx}] VLAN: {vlan}, Mode: {mode}")

# 2. Set VLAN di GPON Link untuk instance 2 (sesuai WANPPPConnection.2)
print("\n\nSetting VLAN 55 di X_ZTE-COM_WANGponLinkConfig.2...")
resp = requests.post(
    f"{GENIEACS_URL}/devices/{DEVICE_ID}/tasks?connection_request",
    json={
        "name": "setParameterValues",
        "parameterValues": [
            ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_ZTE-COM_WANGponLinkConfig.2.VLANIDMark", 55, "xsd:int"],
            ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_ZTE-COM_WANGponLinkConfig.2.X_ZTE-COM_VLANMode", "Tag", "xsd:string"],
            ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.X_ZTE-COM_WANGponLinkConfig.2.X_ZTE-COM_8021PbitMark", 0, "xsd:int"]
        ]
    },
    timeout=10
)

print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    print("✅ VLAN config set!")
else:
    print(f"Response: {resp.text}")
