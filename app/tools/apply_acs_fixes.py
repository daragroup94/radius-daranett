import requests
import sys
import os
import bson

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']

print(f"Applying stable BSON backup parameters to GenieACS at {url}...")

# Resolve parameter folder path
base_dir = os.path.dirname(os.path.abspath(__file__))
parameter_dir = None
search_paths = [
    os.path.join(base_dir, "parameter"),
    os.path.join(base_dir, "../parameter"),
]

for path in search_paths:
    if os.path.exists(path) and os.path.isdir(path):
        parameter_dir = path
        break

if not parameter_dir:
    print("Error: parameter backup directory not found!")
    sys.exit(1)

print(f"Using backup directory: {parameter_dir}")

# 1. Restore Provisions
provisions_path = os.path.join(parameter_dir, "provisions.bson")
if os.path.exists(provisions_path):
    print("\n--- Restoring Provisions ---")
    with open(provisions_path, "rb") as bf:
        provisions = bson.decode_all(bf.read())
        for prov in provisions:
            prov_id = prov['_id']
            script = prov['script']
            print(f"Uploading Provision: {prov_id}...")
            resp = requests.put(
                f"{url}/provisions/{prov_id}",
                data=script,
                auth=auth,
                headers={"Content-Type": "text/plain"},
                timeout=10
            )
            print(f"  Result: {resp.status_code}")
else:
    print("provisions.bson not found")

# 2. Restore Virtual Parameters
vps_path = os.path.join(parameter_dir, "virtualParameters.bson")
if os.path.exists(vps_path):
    print("\n--- Restoring Virtual Parameters ---")
    with open(vps_path, "rb") as bf:
        vps = bson.decode_all(bf.read())
        for vp in vps:
            vp_id = vp['_id']
            script = vp['script']
            print(f"Uploading Virtual Parameter: {vp_id}...")
            resp = requests.put(
                f"{url}/virtual_parameters/{vp_id}",
                data=script,
                auth=auth,
                headers={"Content-Type": "text/plain"},
                timeout=10
            )
            print(f"  Result: {resp.status_code}")
else:
    print("virtualParameters.bson not found")

# 3. Restore Presets
presets_path = os.path.join(parameter_dir, "presets.bson")
if os.path.exists(presets_path):
    print("\n--- Restoring Presets ---")
    with open(presets_path, "rb") as bf:
        presets = bson.decode_all(bf.read())
        for preset in presets:
            preset_id = preset['_id']
            payload = {k: v for k, v in preset.items() if k != '_id'}
            print(f"Uploading Preset: {preset_id}...")
            resp = requests.put(
                f"{url}/presets/{preset_id}",
                json=payload,
                auth=auth,
                timeout=10
            )
            print(f"  Result: {resp.status_code}")
else:
    print("presets.bson not found")

print("\nAll stable ACS parameters restored successfully!")
