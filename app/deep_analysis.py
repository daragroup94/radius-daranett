import json

with open("/app/debug_dump.json") as f:
    device = json.load(f)

# Check the GPON Link Config for each WAN
wcd = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})

print("=== GPON LINK CONFIG ANALYSIS ===")
for wcd_idx in sorted(wcd.keys(), key=lambda x: int(x) if x.isdigit() else 999):
    if not wcd_idx.isdigit():
        continue
    wcd_val = wcd[wcd_idx]
    print(f"\nWANConnectionDevice.{wcd_idx}:")
    for k, v in sorted(wcd_val.items()):
        if 'GponLink' in k or 'GPONLink' in k:
            print(f"  {k}:")
            if isinstance(v, dict):
                for gk, gv in sorted(v.items()):
                    print(f"    {gk}: {gv}")

# Check PPP Connection Type values
print("\n=== CONNECTION TYPE VALUES ===")
for wcd_idx in sorted(wcd.keys(), key=lambda x: int(x) if x.isdigit() else 999):
    if not wcd_idx.isdigit():
        continue
    wcd_val = wcd[wcd_idx]
    for conn_type in ["WANPPPConnection", "WANIPConnection"]:
        conns = wcd_val.get(conn_type, {})
        for c_idx in sorted(conns.keys(), key=lambda x: int(x) if x.isdigit() else 999):
            if not c_idx.isdigit():
                continue
            c_val = conns[c_idx]
            ct = c_val.get("ConnectionType", {})
            print(f"  WCD.{wcd_idx}.{conn_type}.{c_idx}.ConnectionType = {ct.get('_value', 'NO _VALUE')} (type: {ct.get('_type', 'N/A')})")
            
            # Check all keys with _value
            print(f"    Keys with _value:")
            keys_with_val = []
            keys_without_val = []
            for ck, cv in sorted(c_val.items()):
                if ck.startswith('_'):
                    continue
                if isinstance(cv, dict) and '_value' in cv:
                    keys_with_val.append(ck)
                elif isinstance(cv, dict) and '_writable' in cv:
                    keys_without_val.append(ck)
            print(f"      WITH _value: {keys_with_val}")
            print(f"      WITHOUT _value (writable only): {keys_without_val}")

# Check X_FH specific parameters in PPP Connection 2.1
print("\n=== WCD.2 PPP.1 X_FH Parameters ===")
ppp21 = wcd.get("2", {}).get("WANPPPConnection", {}).get("1", {})
for k in sorted(ppp21.keys()):
    if 'X_FH' in k:
        v = ppp21[k]
        if isinstance(v, dict):
            has_val = '_value' in v
            writable = v.get('_writable', False)
            print(f"  {k}: has_value={has_val}, writable={writable}, val={v.get('_value', 'N/A')}")

# Check WCD.1 IP.1 X_FH parameters
print("\n=== WCD.1 IP.1 X_FH Parameters ===")
ip11 = wcd.get("1", {}).get("WANIPConnection", {}).get("1", {})
for k in sorted(ip11.keys()):
    if 'X_FH' in k:
        v = ip11[k]
        if isinstance(v, dict):
            has_val = '_value' in v
            writable = v.get('_writable', False)
            print(f"  {k}: has_value={has_val}, writable={writable}, val={v.get('_value', 'N/A')}")
