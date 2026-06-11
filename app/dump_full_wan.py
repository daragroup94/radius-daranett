import json

with open("/app/debug_dump.json") as f:
    device = json.load(f)

def print_values(d, path=""):
    if not isinstance(d, dict):
        return
    if "_value" in d:
        print(f"{path} = {d['_value']}")
    for k, v in d.items():
        if k.startswith("_"):
            continue
        new_path = f"{path}.{k}" if path else k
        print_values(v, new_path)

print_values(device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}), "InternetGatewayDevice.WANDevice.1")
