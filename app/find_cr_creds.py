import json

with open("/app/debug_dump.json") as f:
    device = json.load(f)

ms = device.get("InternetGatewayDevice", {}).get("ManagementServer", {})
print("Management Server Configuration:")
for k, v in ms.items():
    if "ConnectionRequest" in k:
        if isinstance(v, dict):
            print(f"  {k}: {v.get('_value')}")
        else:
            print(f"  {k}: {v}")
