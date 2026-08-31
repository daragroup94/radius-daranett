import sys
import json
import requests
from services.genieacs import get_genieacs_config

if len(sys.argv) < 2:
    print("Penggunaan: python3 dump_device.py [username/SerialNumber]")
    sys.exit(1)
    
username = sys.argv[1]
config = get_genieacs_config()
GENIEACS_NBI = config['url']
auth = config['auth']

query = json.dumps({
    "$or": [
        {"VirtualParameters.pppoeUsername._value": username},
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})

print(f"Mencari perangkat '{username}' di GenieACS...")
resp = requests.get(
    f'{GENIEACS_NBI}/devices',
    params={'query': query},
    auth=auth,
    timeout=10
)

if resp.status_code == 200 and resp.json():
    data = resp.json()[0]
    out_name = f"/app/debug_dump_{username}.json"
    with open(out_name, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"✅ Berhasil dump data perangkat ke {out_name}")
else:
    print(f"❌ Perangkat tidak ditemukan atau API Error (Status: {resp.status_code})")
