import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def main():
    url = "https://gpon.access.daragroup.cloud/js/chunk-common.e7f5f453.js"
    r = requests.get(url, verify=False)
    js = r.text
    print(f"Downloaded chunk-common. Size: {len(js)} bytes")

    # Find indices of getModules
    pos = 0
    while True:
        pos = js.find("getModules", pos)
        if pos == -1:
            break
        print("\n" + "="*50)
        print(f"Found 'getModules' at position: {pos}")
        start = max(0, pos - 300)
        end = min(len(js), pos + 300)
        print(js[start:end])
        pos += len("getModules")

if __name__ == '__main__':
    main()
