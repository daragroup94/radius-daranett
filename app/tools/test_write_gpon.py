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
        device_id = resp.json()[0]["_id"]
        print("Device ID:", device_id)
        
        # We also need to get the password they set, wait, did the password fail to write because of another issue?
        # Let's set the Password as well just in case!
        import urllib.parse
        task_url = f"{url}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
        
        payload = {
            "name": "setParameterValues",
            "parameterValues": [
                ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.WANPPPConnection.1.Password", "helloworld1994"],
                ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_FH_WANGponLinkConfig.Enable", True],
                ["InternetGatewayDevice.WANDevice.1.WANConnectionDevice.2.X_FH_WANGponLinkConfig.Mode", 2]
            ]
        }
        
        resp_push = requests.post(task_url, json=payload, auth=auth, timeout=15)
        print("Push status:", resp_push.status_code)
        print("Response:", resp_push.text)
    else:
        print("Device not found")
except Exception as e:
    print("Error:", e)
