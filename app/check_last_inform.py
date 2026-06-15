import os
import requests
import json
from services.genieacs import get_genieacs_config

config = get_genieacs_config()
url = config['url']
auth = config['auth']
device_id = "000AC2-HG6245N-FHTT9646079C"

resp = requests.get(
    f"{url}/devices",
    params={"query": json.dumps({"_id": device_id})},
    auth=auth,
    timeout=10
)

if resp.status_code == 200 and resp.json():
    d = resp.json()[0]
    print(f"Device ID: {d.get('_id')}")
    print(f"Last Inform: {d.get('_lastInform')}")
    
    # Check connection request URL
    cr_url = d.get("InternetGatewayDevice", {}).get("ManagementServer", {}).get("ConnectionRequestURL", {})
    print(f"ConnectionRequestURL: {cr_url.get('_value') if isinstance(cr_url, dict) else 'N/A'}")
else:
    print("Error querying device")
