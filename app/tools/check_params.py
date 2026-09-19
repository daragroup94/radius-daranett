import os
import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

username = "FHTT9646079C"
query = json.dumps({
    "$or": [
        {"VirtualParameters.pppoeUsername._value": username},
        {"VirtualParameters.pppoeUsername2._value": username},
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})

resp = requests.get(
    f"{url}/devices",
    params={"query": query},
    auth=auth,
    timeout=10
)

if resp.status_code == 200 and resp.json():
    device = resp.json()[0]
    out_path = "/app/debug_dump.json"
    with open(out_path, "w") as f:
        json.dump(device, f, indent=2)
    print("Device document refreshed successfully!")
    
    # Check if we have more values now
    wcd = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
    has_details = False
    for wcd_idx, wcd_val in wcd.items():
        if not wcd_idx.isdigit():
            continue
        ppp = wcd_val.get("WANPPPConnection", {})
        for p_idx, p_val in ppp.items():
            if not p_idx.isdigit():
                continue
            if "Username" in p_val:
                has_details = True
                break
    if has_details:
        print("Details are now present in the document!")
    else:
        print("Details are still not present. ONT might not have connected yet or task is still queued.")
else:
    print("Error querying device")
