import sys
from services.genieacs import get_genieacs_device_info
from database import execute_query

username = 'FHTTC02CE8FB'
acs_data = get_genieacs_device_info(username)
print("ACS DATA:", acs_data)

if acs_data.get('found'):
    pppoe = acs_data.get('pppoe_username')
    print("PPPOE USERNAME:", pppoe)
    
    # Query RADIUS
    if pppoe:
        res = execute_query("SELECT radacctid, username, acctstarttime, acctstoptime FROM radacct WHERE username = %s ORDER BY acctstarttime DESC LIMIT 5", (pppoe,))
        print("RADIUS DATA for PPPOE:", res)
    
    res2 = execute_query("SELECT radacctid, username, acctstarttime, acctstoptime FROM radacct WHERE username = %s ORDER BY acctstarttime DESC LIMIT 5", (username,))
    print("RADIUS DATA for SN:", res2)

