import sys
import os
import librouteros
from database import execute_query

def main():
    ig = execute_query("SELECT id, host, username, password, port FROM integrations WHERE id = 3 AND type='mikrotik' AND is_active=true")[0]
    print(f"Connecting to NAS ID {ig['id']} ({ig['host']})...")
    api = librouteros.connect(
        ig['host'], 
        username=ig['username'], 
        password=ig['password'], 
        port=ig['port'], 
        timeout=5
    )
    print("Connected successfully!")
    
    profiles = list(api('/ip/hotspot/user/profile/print'))
    if profiles:
        print("\nHotspot User Profile keys available in RouterOS v6:")
        for k, v in profiles[0].items():
            print(f"- {k}: {v}")
    else:
        print("No profiles found.")
        
    api.close()

if __name__ == '__main__':
    main()
