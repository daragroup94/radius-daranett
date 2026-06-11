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
        wcd2 = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get("2", {})
        print("=== WANConnectionDevice.2 ===")
        # Print GPON link config
        gpon = wcd2.get("X_FH_WANGponLinkConfig", {})
        print("X_FH_WANGponLinkConfig:")
        for k, v in gpon.items():
            if not k.startswith("_"):
                print(f"  {k}: {v.get('_value') if isinstance(v, dict) else v}")
                
        # Print PPP Connection
        ppp = wcd2.get("WANPPPConnection", {}).get("1", {})
        print("\nWANPPPConnection.1:")
        for k, v in ppp.items():
            if not k.startswith("_"):
                print(f"  {k}: {v.get('_value') if isinstance(v, dict) else v}")
    else:
        print("Device not found")
except Exception as e:
    print("Error:", e)
