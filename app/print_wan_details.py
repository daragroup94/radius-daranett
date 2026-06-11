import json

with open("/app/debug_dump.json") as f:
    device = json.load(f)

wcd = device.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
print("=== DETAILED WAN ANALYSIS FOR FIBERHOME HG6245N ===")
for wcd_idx, wcd_val in sorted(wcd.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
    if not wcd_idx.isdigit():
        continue
    print(f"\n==================================================")
    print(f"WAN Connection Device index: {wcd_idx}")
    print(f"==================================================")
    
    # Check GPON Link Config
    gpon = None
    for k, v in wcd_val.items():
        if 'GponLink' in k or 'GPONLink' in k or 'LinkConfig' in k:
            gpon = (k, v)
            break
            
    if gpon:
        gpon_name, gpon_val = gpon
        print(f"GPON Link Config Object: {gpon_name}")
        for gk, gv in sorted(gpon_val.items()):
            if gk.startswith('_'):
                if gk in ('_timestamp',):
                    print(f"  {gk}: {gv}")
                continue
            if isinstance(gv, dict) and '_value' in gv:
                print(f"  {gk}: {gv['_value']}")
            else:
                print(f"  {gk}: {gv}")
    else:
        print("No GPON Link Config found.")

    # WAN PPP Connections
    ppp = wcd_val.get("WANPPPConnection", {})
    for p_idx, p_val in sorted(ppp.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        if not p_idx.isdigit():
            continue
        print(f"\n  WANPPPConnection.{p_idx}:")
        for pk, pv in sorted(p_val.items()):
            if pk.startswith('_') and pk != '_timestamp':
                continue
            if isinstance(pv, dict):
                val = pv.get('_value', 'N/A')
                print(f"    {pk}: {val}")
            else:
                print(f"    {pk}: {pv}")

    # WAN IP Connections
    ip_conn = wcd_val.get("WANIPConnection", {})
    for i_idx, i_val in sorted(ip_conn.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
        if not i_idx.isdigit():
            continue
        print(f"\n  WANIPConnection.{i_idx}:")
        for ik, iv in sorted(i_val.items()):
            if ik.startswith('_') and ik != '_timestamp':
                continue
            if isinstance(iv, dict):
                val = iv.get('_value', 'N/A')
                print(f"    {ik}: {val}")
            else:
                print(f"    {ik}: {iv}")
