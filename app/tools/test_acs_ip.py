import sys
from database import execute_query

res = execute_query("SELECT radacctid, username, framedipaddress, acctstarttime, acctstoptime FROM radacct WHERE framedipaddress = '10.0.254.80' ORDER BY acctstarttime DESC LIMIT 5")
print("RADIUS DATA by IP:", res)

# Let's also check all active sessions to see what usernames are actually there
res2 = execute_query("SELECT username, framedipaddress FROM radacct WHERE acctstoptime IS NULL LIMIT 10")
print("ACTIVE SESSIONS:", res2)
