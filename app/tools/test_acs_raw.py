import sys
import json
from services.genieacs import get_genieacs_config
import requests

username = 'FHTTC02CE8FB'
config = get_genieacs_config()
GENIEACS_NBI = config['url']
auth = config['auth']
query = json.dumps({
    "$or": [
        {"_deviceId._SerialNumber": username},
        {"VirtualParameters.getSerialNumber._value": username}
    ]
})
projection = '_deviceId,_lastInform,DeviceInfo.SoftwareVersion,DeviceInfo.UpTime,VirtualParameters.RXPower,VirtualParameters.pppoeIP,VirtualParameters.getdeviceuptime,VirtualParameters.getpppuptime,VirtualParameters.gettemp,VirtualParameters.getponmode,VirtualParameters.getSerialNumber,VirtualParameters.pppoeUsername,VirtualParameters.pppoeUsername2,InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username'

resp = requests.get(
    f'{GENIEACS_NBI}/devices',
    params={'query': query, 'projection': projection},
    auth=auth,
    timeout=3
)
print("RAW ACS DATA:", json.dumps(resp.json(), indent=2))
