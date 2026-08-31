import sys
sys.path.append('/app')
from database import execute_query
from helpers import connect_mikrotik

routers = execute_query("SELECT id, name, host, username, password, port FROM integrations WHERE type='mikrotik' AND is_active=true")
if not routers:
    print("No active MikroTik routers found in database.")
    sys.exit(0)

for r in routers:
    print(f"\n=== Router: {r['name']} ({r['host']}) ===")
    try:
        api = connect_mikrotik(r['host'], username=r['username'], password=r['password'], port=r['port'], timeout=10)
        
        # 1. Check active PPP sessions
        print("Checking active PPP sessions...")
        active_ppp = list(api('/ppp/active/print'))
        print(f"Total active PPP sessions on router: {len(active_ppp)}")
        
        ppp_counts = {}
        for p in active_ppp:
            uname = p.get('name', '').lower()
            if uname:
                ppp_counts[uname] = ppp_counts.get(uname, 0) + 1
                
        dupes = {k: v for k, v in ppp_counts.items() if v > 1}
        if dupes:
            print("Duplicate active PPP sessions on router:")
            for k, v in dupes.items():
                print(f"  - User: {k}, count: {v}")
                # Print details of the duplicates
                for p in active_ppp:
                    if p.get('name', '').lower() == k:
                        print(f"    Session ID: {p.get('.id')}, MAC/Caller: {p.get('caller-id')}, IP: {p.get('address')}, Uptime: {p.get('uptime')}")
        else:
            print("No duplicate PPP active users on router.")
            
        api.close()
    except Exception as e:
        print(f"Error connecting/querying: {e}")
