import os
import re
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    host = "gpon.access.daragroup.cloud"
    url = f"https://{host}/login.html"
    
    print(f"Fetching OLT login page: {url}")
    try:
        r = requests.get(url, timeout=5, verify=False)
        html = r.text
        print(f"Login HTML size: {len(html)} bytes")
    except Exception as e:
        print(f"Failed to fetch login page: {e}")
        return

    # Find scripts
    scripts = re.findall(r'src="([^"]+)"', html)
    scripts = list(set(scripts))
    print(f"Found scripts: {scripts}")

    # We also manually add typical Vue chunk script names that are commonly loaded in Vue apps
    # Let's search inside the script contents
    for script_path in scripts:
        script_url = f"https://{host}/{script_path.lstrip('/')}"
        print(f"\nDownloading script: {script_url}")
        try:
            sr = requests.get(script_url, timeout=15, verify=False)
            if sr.status_code == 200:
                js_content = sr.text
                print(f"Size: {len(js_content)} bytes")
                
                # Search for JSON keys: e.g. "deviceName": or deviceName:
                # Let's search for keys containing 'cpu', 'optical', 'temp', 'sfp'
                keys = re.findall(r'["\']([a-zA-Z0-9_]{3,25})["\']\s*:', js_content)
                keys = list(set(keys))
                interested = [k for k in keys if any(x in k.lower() for x in ['cpu', 'optical', 'temp', 'sfp', 'power', 'mem', 'ddm', 'laser', 'bias'])]
                print("Interested keys found:")
                print(sorted(interested))
                
                # Check for getModules
                get_mods = re.findall(r'getModules', js_content)
                print(f"Occurrences of getModules: {len(get_mods)}")
            else:
                print(f"Failed: HTTP {sr.status_code}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == '__main__':
    main()
