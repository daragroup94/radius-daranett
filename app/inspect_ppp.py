import json

with open("/app/debug_dump.json") as f:
    device = json.load(f)

ppp = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get("2", {}).get("WANPPPConnection", {}).get("1", {})
print("--- WANPPPConnection.1 keys and values ---")
for k, v in sorted(ppp.items()):
    print(f"{k}: {type(v)} -> {v}")
