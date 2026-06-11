import requests
import json

url = "http://216.176.238.196:7557"
auth = ("admin", "acsadmin1994")
username = "FHTT962ABB40"

query = json.dumps({
    "$or": [
        {"VirtualParameters.pppoeUsername._value": username},
        {"VirtualParameters.pppoeUsername2._value": username},
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})

try:
    resp = requests.get(f"{url}/devices", params={"query": query}, auth=auth, timeout=10)
    if resp.status_code == 200 and resp.json():
        device = resp.json()[0]
        # Let's inspect WANConnectionDevice.1 and WANConnectionDevice.2 X_FH_WANGponLinkConfig
        wcd = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
        print("=== GPON LINK CONFIG ===")
        for idx, conn_dev in wcd.items():
            if not idx.isdigit():
                continue
            print(f"\nWANConnectionDevice.{idx} -> X_FH_WANGponLinkConfig:")
            gpon = conn_dev.get("X_FH_WANGponLinkConfig", {})
            for k, v in gpon.items():
                if not k.startswith("_"):
                    if isinstance(v, dict):
                        print(f"  {k}: {v.get('_value')}")
                    else:
                        print(f"  {k}: {v}")
    else:
        print("Device not found")
except Exception as e:
    print("Error:", e)
