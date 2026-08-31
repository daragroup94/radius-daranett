import re
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    url = "https://gpon.access.daragroup.cloud/js/chunk-common.e7f5f453.js"
    r = requests.get(url, verify=False)
    js = r.text
    print(f"Downloaded chunk-common. Size: {len(js)} bytes")

    # Find patterns like modules:["a","b"] or modules:[a,b]
    # In minified code, it might look like modules:[p,m] where p and m are variables,
    # or modules:["deviceInfo","customInfo"]
    
    # Let's find all instances of modules: followed by bracket
    matches = re.finditer(r'modules\s*:\s*\[', js)
    for m in matches:
        start_idx = m.start()
        # Find matching close bracket
        end_idx = js.find(']', start_idx)
        if end_idx != -1:
            slice_str = js[start_idx:end_idx+1]
            print("\n" + "="*40)
            print(f"Match: {slice_str}")
            # Look at context around it (100 characters before)
            context_start = max(0, start_idx - 200)
            print(f"Context: {js[context_start:start_idx]}")

if __name__ == '__main__':
    main()
