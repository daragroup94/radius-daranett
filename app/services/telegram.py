import os
import requests
import threading
from datetime import datetime
from flask import current_app
from extensions import socketio
from database import execute_query
from helpers import ping_nas
from services.genieacs import get_genieacs_device_info, get_genieacs_config, run_wifi_scan, analyze_wifi_neighbors, set_wifi_channel, get_wlan_clients_realtime
from services.mikrotik_coa import send_coa_disconnect

_prov_states = {}

INTERFACE_MAP = {
    'LAN 1': 'InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.1',
    'LAN 2': 'InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.2',
    'LAN 3': 'InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.3',
    'LAN 4': 'InternetGatewayDevice.LANDevice.1.LANEthernetInterfaceConfig.4',
    'SSID 1': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.1',
    'SSID 2': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.2',
    'SSID 3': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.3',
    'SSID 4': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.4',
    'SSID 5': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.5',
    'SSID 6': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.6',
    'SSID 7': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.7',
    'SSID 8': 'InternetGatewayDevice.LANDevice.1.WLANConfiguration.8',
}

def make_binding_keyboard(selected_binds, prefix='prov'):
    keyboard = []
    # Row 1: LAN 1, LAN 2
    r1 = []
    for l in ['LAN 1', 'LAN 2']:
        check = "✅ " if l in selected_binds else "⬜ "
        r1.append({"text": f"{check}{l}", "callback_data": f"{prefix}_toggle_bind:{l}"})
    keyboard.append(r1)
    
    # Row 2: LAN 3, LAN 4
    r2 = []
    for l in ['LAN 3', 'LAN 4']:
        check = "✅ " if l in selected_binds else "⬜ "
        r2.append({"text": f"{check}{l}", "callback_data": f"{prefix}_toggle_bind:{l}"})
    keyboard.append(r2)
    
    # Row 3: SSID 1, SSID 5
    r3 = []
    for s in ['SSID 1', 'SSID 5']:
        check = "✅ " if s in selected_binds else "⬜ "
        r3.append({"text": f"{check}{s}", "callback_data": f"{prefix}_toggle_bind:{s}"})
    keyboard.append(r3)

    # Row 4: SSID 2, SSID 6
    r4 = []
    for s in ['SSID 2', 'SSID 6']:
        check = "✅ " if s in selected_binds else "⬜ "
        r4.append({"text": f"{check}{s}", "callback_data": f"{prefix}_toggle_bind:{s}"})
    keyboard.append(r4)
    
    keyboard.append([
        {"text": "➡️ Lanjut (Selesai Binding)", "callback_data": f"{prefix}_bind_done"},
        {"text": "❌ Batal", "callback_data": "prov_cancel"}
    ])
    return keyboard

def show_prov_confirm(chat_id, token):
    state_data = _prov_states.get(chat_id)
    if not state_data:
        return
        
    username = state_data['username']
    wtype = state_data['wan_type']
    
    msg = f"📝 <b>Konfirmasi Provisioning ONT</b>:\n\n" \
          f"• Pelanggan: <code>{username}</code>\n"
          
    if wtype == 'wifi':
        msg += f"• Tipe: <b>Wi-Fi Config</b>\n" \
               f"• Slot SSID: <b>SSID {state_data['wifi_slot']}</b>\n" \
               f"• Nama SSID: <code>{state_data['wifi_ssid']}</code>\n" \
               f"• Keamanan: <b>{state_data['wifi_sec']}</b>\n"
        if state_data['wifi_sec'] != 'None':
            msg += f"• Password Wi-Fi: <code>{state_data['wifi_key']}</code>\n"
    else:
        modes_map = {
            'ppp_routed': 'IP_Routed (PPPoE)',
            'ppp_bridged': 'PPPoE_Bridged',
            'ip_bridged': 'IP_Bridged',
            'ip_routed': 'IP_Routed (IPoE)'
        }
        msg += f"• Tipe WAN: <b>{modes_map.get(wtype, wtype)}</b>\n" \
               f"• Slot WAN: <b>Wan {state_data['slot']}</b>\n" \
               f"• Service List: <code>{state_data['service_list']}</code>\n"
               
        if wtype == 'ppp_routed':
            msg += f"• PPPoE Username: <code>{state_data['pppoe_user']}</code>\n" \
                   f"• PPPoE Password: <code>{state_data['pppoe_pass']}</code>\n"
        elif wtype == 'ip_routed':
            msg += f"• Mode IP: <b>{state_data['ipoe_addr']}</b>\n"
            if state_data['ipoe_addr'] == 'Static':
                msg += f"  - IP Address: <code>{state_data['static_ip']}</code>\n" \
                       f"  - Subnet Mask: <code>{state_data['static_mask']}</code>\n" \
                       f"  - Gateway: <code>{state_data['static_gw']}</code>\n" \
                       f"  - DNS: <code>{state_data['static_dns']}</code>\n"
                       
        if wtype in ('ppp_routed', 'ip_routed'):
            msg += f"• NAT: <b>{'Enabled' if state_data.get('nat_enabled') else 'Disabled'}</b>\n"
            
        msg += f"• VLAN Tag: <b>{'Enabled (' + str(state_data.get('vlan_id')) + ')' if state_data.get('vlan_enabled') else 'Disabled'}</b>\n"
        msg += f"• Priority WAN: <b>{state_data.get('priority', 0)}</b>\n"
        
        bind_list = state_data.get('selected_binds', [])
        msg += f"• Port Binding: <code>{', '.join(bind_list) if bind_list else 'Tanpa Bind'}</code>\n" \
               f"• ZTE F670L: <b>{'Ya' if state_data.get('is_f670l') else 'Tidak'}</b>\n"
               
    msg += "\nApakah Anda ingin mengirimkan konfigurasi ini?"
        
    buttons = [
        [
            {"text": "🚀 PUSH CONFIG", "callback_data": "prov_push"},
            {"text": "❌ Batal", "callback_data": "prov_cancel"}
        ]
    ]
    import json as _json
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
        'chat_id': chat_id,
        'text': msg,
        'parse_mode': 'HTML',
        'reply_markup': _json.dumps({"inline_keyboard": buttons})
    })
    state_data['state'] = 'CONFIRM_PUSH'

def find_existing_wan_path(device_doc):
    wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
    for wcd_idx, wcd_val in wcd.items():
        if not wcd_idx.isdigit():
            continue
        for c_type in ["WANPPPConnection", "WANIPConnection"]:
            conn_instances = wcd_val.get(c_type, {})
            for c_idx, c_val in conn_instances.items():
                if not c_idx.isdigit():
                    continue
                return f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{wcd_idx}.{c_type}.{c_idx}"
    return None

def check_param_exists_on_device(device_doc, base_path, leaf):
    def check_path_exists_local(doc, path):
        parts = path.split('.')
        current = doc
        for p in parts:
            if isinstance(current, dict) and p in current:
                current = current[p]
            else:
                return False
        return True

    if check_path_exists_local(device_doc, f"{base_path}.{leaf}"):
        return f"{base_path}.{leaf}"
        
    parent_path = ".".join(base_path.split(".")[:-2])
    if check_path_exists_local(device_doc, f"{parent_path}.{leaf}"):
        return f"{parent_path}.{leaf}"
        
    template_path = find_existing_wan_path(device_doc)
    if template_path:
        if check_path_exists_local(device_doc, f"{template_path}.{leaf}"):
            return f"{base_path}.{leaf}"
        template_parent_path = ".".join(template_path.split(".")[:-2])
        if check_path_exists_local(device_doc, f"{template_parent_path}.{leaf}"):
            return f"{parent_path}.{leaf}"
            
    return None

def _detect_fiberhome_conn_type(device_doc):
    """Detect the ConnectionType convention used by this FiberHome device.
    
    HG6243C uses 'IP_Routed' for PPPoE Routed connections,
    HG6245N uses 'PPPoE_Routed' for PPPoE Routed connections.
    We auto-detect by examining existing WAN PPP connections on the device.
    """
    manufacturer = device_doc.get("_deviceId", {}).get("_Manufacturer", "").upper()
    # Non-FiberHome devices (ZTE, Huawei, C-Data, Zyxel, etc.) all use standard PPPoE_Routed
    if "FIBERHOME" not in manufacturer and "FHTT" not in manufacturer:
        return "HG6245N_STYLE"

    wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
    for wcd_idx, wcd_val in wcd.items():
        if not wcd_idx.isdigit():
            continue
        ppp = wcd_val.get("WANPPPConnection", {})
        for p_idx, p_val in ppp.items():
            if not p_idx.isdigit():
                continue
            ct = p_val.get("ConnectionType", {})
            ct_val = ct.get("_value", "")
            if ct_val in ("PPPoE_Routed", "PPPoE_Bridged"):
                # Device uses FiberHome HG6245N-style ConnectionType naming
                return "HG6245N_STYLE"
            elif ct_val == "IP_Routed":
                # Check if this is actually an IP connection or a PPP connection
                # If it's under WANPPPConnection with IP_Routed, it's HG6243C-style
                return "HG6243C_STYLE"
    # Fallback: check _ProductClass for known models
    product_class = device_doc.get("_deviceId", {}).get("_ProductClass", "")
    # Known FiberHome models that use PPPoE_Routed naming convention
    HG6245N_STYLE_MODELS = ["HG6245N", "HG6245D", "HG6245K", "HG6256M"]
    for model in HG6245N_STYLE_MODELS:
        if model in product_class:
            return "HG6245N_STYLE"
    # Known FiberHome models that use IP_Routed naming convention
    HG6243C_STYLE_MODELS = ["HG6243C", "HG6145F", "HG6145D2"]
    for model in HG6243C_STYLE_MODELS:
        if model in product_class:
            return "HG6243C_STYLE"
    return None

def execute_prov_push(state_data):
    username = state_data['username']
    wtype = state_data['wan_type']
    
    try:
        config = get_genieacs_config()
        GENIEACS_NBI = config['url']
        auth = config['auth']
        import json as _json
        import urllib.parse
        
        # 1. Cari device di GenieACS untuk mendapatkan device_id dan data document
        query = _json.dumps({
            "$or": [
                {"VirtualParameters.pppoeUsername._value": username},
                {"VirtualParameters.pppoeUsername2._value": username},
                {"_deviceId._SerialNumber": username},
                {"VirtualParameters.getSerialNumber._value": username}
            ]
        })
        resp_acs = requests.get(
            f'{GENIEACS_NBI}/devices',
            params={'query': query},
            auth=auth,
            timeout=5
        )
        if resp_acs.status_code != 200:
            return False, f"GenieACS API error ({resp_acs.status_code})"
            
        devices = resp_acs.json()
        if not devices:
            return False, "Perangkat ONT tidak ditemukan di GenieACS."
            
        device_doc = devices[0]
        device_id = device_doc.get('_id')
        if not device_id:
            return False, "ID Perangkat tidak valid."
            
        # Helper untuk cek keaktifan parameter di data doc
        def check_path_exists(doc, path):
            parts = path.split('.')
            current = doc
            for p in parts:
                if isinstance(current, dict) and p in current:
                    current = current[p]
                else:
                    return False
            return True

        def queue_task(task_name, payload_data):
            task_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
            payload = {
                "name": task_name,
                **payload_data
            }
            resp = requests.post(task_url, json=payload, auth=auth, timeout=10)
            try:
                from services.genieacs import trigger_connection_request_by_device_id
                trigger_connection_request_by_device_id(device_id)
            except Exception as e:
                print(f"Error triggering direct connection request in queue_task: {e}")
            return resp

        parameter_values = []
        
        if wtype in ('ppp_routed', 'ppp_bridged', 'ip_bridged', 'ip_routed'):
            slot = state_data['slot']
            is_ppp = wtype.startswith('ppp')
            conn_type = "WANPPPConnection" if is_ppp else "WANIPConnection"
            
            if state_data.get('is_edit'):
                wcd_idx = state_data['wcd_idx']
                conn_idx = state_data['conn_idx']
                base_path = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{wcd_idx}.{conn_type}.{conn_idx}"
            else:
                # Determine connection scheme (Scheme A vs Scheme B)
                # Scheme A: WANConnectionDevice.{slot}.[conn_type].1
                # Scheme B: WANConnectionDevice.1.[conn_type].{slot}
                use_scheme_b = False
                wan_conn_1 = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get("1", {})
                
                # Check based on manufacturer first (ZTE & Huawei/HWTC always use Scheme B)
                manufacturer = device_doc.get("_deviceId", {}).get("_Manufacturer", "").upper()
                if "ZTE" in manufacturer or "HUAWEI" in manufacturer or "HWTC" in manufacturer:
                    use_scheme_b = True
                else:
                    if isinstance(wan_conn_1, dict):
                        for c_type in ["WANPPPConnection", "WANIPConnection"]:
                            c_instances = wan_conn_1.get(c_type, {})
                            if isinstance(c_instances, dict):
                                for k in c_instances.keys():
                                    try:
                                        if int(k) > 1:
                                            use_scheme_b = True
                                            break
                                    except ValueError:
                                        pass
                            if use_scheme_b:
                                break
                
                if use_scheme_b:
                    conn_instances = wan_conn_1.get(conn_type, {})
                    max_existing = 0
                    if isinstance(conn_instances, dict):
                        for k in conn_instances.keys():
                            try:
                                max_existing = max(max_existing, int(k))
                            except ValueError:
                                pass
                    
                    # Auto-correct slot: gunakan max_existing+1 agar hanya 1 addObject
                    # yang dibuat. Ini mencegah WAN hantu dari gap-filling.
                    if str(slot) not in (conn_instances if isinstance(conn_instances, dict) else {}):
                        slot = max_existing + 1
                        state_data['slot'] = slot  # Update state agar UI konsisten
                    
                    base_path = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.{conn_type}.{slot}"
                    if not isinstance(conn_instances, dict) or str(slot) not in conn_instances:
                        queue_task("addObject", {"objectName": f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.{conn_type}"})
                else:
                    wan_devs = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
                    existing_slots = []
                    if isinstance(wan_devs, dict):
                        for k in wan_devs.keys():
                            try:
                                existing_slots.append(int(k))
                            except ValueError:
                                pass
                    max_slot = max(existing_slots) if existing_slots else 0
                    
                    if slot > max_slot:
                        for _ in range(max_slot, slot):
                            queue_task("addObject", {"objectName": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice"})
                    
                    base_path = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{slot}.{conn_type}.1"
                    if slot > max_slot or not check_path_exists(device_doc, base_path):
                        queue_task("addObject", {"objectName": f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{slot}.{conn_type}"})
            
            parameter_values.append([f"{base_path}.Enable", True])
            has_gpon_link_config = False  # Flag: apakah GPON Link Config menangani VLAN

            # --- FiberHome GPON Link Config (X_FH_WANGponLinkConfig) ---
            # Pada perangkat FiberHome, layer 2 GPON link wajib dikonfigurasi agar
            # koneksi WAN bisa terhubung ke jaringan. Tanpa ini, PPPoE tidak akan dial.
            if not state_data.get('is_edit') and not use_scheme_b:
                wcd_path = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{slot}"
                gpon_cfg_key = None
                # Cari nama sub-object GPON link config dari template WAN yang sudah ada
                template_path = find_existing_wan_path(device_doc)
                if template_path:
                    template_wcd_idx = template_path.split(".")[4]  # WANConnectionDevice.X
                    template_wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get(template_wcd_idx, {})
                    for key in template_wcd:
                        if 'GponLinkConfig' in key or 'GPONLinkConfig' in key or 'WANGponLink' in key:
                            gpon_cfg_key = key
                            break
                
                if not gpon_cfg_key:
                    # Fallback: cek langsung di WCD baru
                    new_wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get(str(slot), {})
                    for key in new_wcd:
                        if 'GponLinkConfig' in key or 'GPONLinkConfig' in key or 'WANGponLink' in key:
                            gpon_cfg_key = key
                            break
                
                if gpon_cfg_key:
                    has_gpon_link_config = True
                    gpon_path = f"{wcd_path}.{gpon_cfg_key}"
                    parameter_values.append([f"{gpon_path}.Enable", True])
                    parameter_values.append([f"{gpon_path}.Mode", 2])  # Mode 2 = Tagged VLAN
                    # 802.1p priority mark
                    priority_val = int(state_data.get('priority', 0))
                    parameter_values.append([f"{gpon_path}.802-1pMark", priority_val])
                    if state_data.get('vlan_enabled') and state_data.get('vlan_id'):
                        vlan_val = int(state_data['vlan_id'])
                        parameter_values.append([f"{gpon_path}.VLANID", vlan_val])
                        # Cek apakah ada VLANIDMark (FiberHome specific)
                        if template_path:
                            t_wcd_idx = template_path.split(".")[4]
                            t_gpon = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get(t_wcd_idx, {}).get(gpon_cfg_key, {})
                            if "VLANIDMark" in t_gpon:
                                parameter_values.append([f"{gpon_path}.VLANIDMark", vlan_val])

            # --- FiberHome X_FH_IPMode ---
            # X_FH_IPMode = 1 (IPv4), diperlukan agar koneksi PPPoE/IPoE bisa dial
            ip_mode_leaf = None
            for leaf in ["X_FH_IPMode"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    ip_mode_leaf = found_path.split(".")[-1]
                    break
            if ip_mode_leaf:
                parameter_values.append([f"{base_path}.{ip_mode_leaf}", 1])
            
            svc_leaf = None
            for leaf in ["ServiceList", "X_FH_ServiceList", "X_HW_ServiceList", "X_ZTE_ServiceList", "X_CMCC_ServiceList"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    svc_leaf = found_path.split(".")[-1]
                    break
            if not svc_leaf:
                svc_leaf = "ServiceList"
            
            # Auto-detect FiberHome ConnectionType convention
            # HG6243C: PPPoE Route = IP_Routed, PPPoE Bridge = PPPoE_Bridged
            # HG6245N: PPPoE Route = PPPoE_Routed, PPPoE Bridge = PPPoE_Bridged
            fh_style = _detect_fiberhome_conn_type(device_doc)
            
            if wtype == 'ppp_routed':
                if fh_style == "HG6245N_STYLE":
                    parameter_values.append([f"{base_path}.ConnectionType", "PPPoE_Routed"])
                else:
                    parameter_values.append([f"{base_path}.ConnectionType", "IP_Routed"])
                parameter_values.append([f"{base_path}.Username", state_data['pppoe_user']])
                parameter_values.append([f"{base_path}.Password", state_data['pppoe_pass']])
                parameter_values.append([f"{base_path}.{svc_leaf}", state_data['service_list']])
            elif wtype == 'ppp_bridged':
                parameter_values.append([f"{base_path}.ConnectionType", "PPPoE_Bridged"])
                parameter_values.append([f"{base_path}.{svc_leaf}", state_data['service_list']])
            elif wtype == 'ip_bridged':
                parameter_values.append([f"{base_path}.ConnectionType", "IP_Bridged"])
                parameter_values.append([f"{base_path}.{svc_leaf}", state_data['service_list']])
            elif wtype == 'ip_routed':
                parameter_values.append([f"{base_path}.ConnectionType", "IP_Routed"])
                
                addr_leaf = None
                for leaf in ["AddressingType", "IPAddressMode", "IPAddressModeType"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        addr_leaf = found_path.split(".")[-1]
                        break
                if not addr_leaf:
                    addr_leaf = "AddressingType"
                parameter_values.append([f"{base_path}.{addr_leaf}", state_data['ipoe_addr']])
                parameter_values.append([f"{base_path}.{svc_leaf}", state_data['service_list']])
                
                if state_data['ipoe_addr'] == 'Static':
                    ip_leaf = None
                    for leaf in ["ExternalIPAddress", "IPAddress", "IP"]:
                        found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                        if found_path:
                            ip_leaf = found_path.split(".")[-1]
                            break
                    if not ip_leaf:
                        ip_leaf = "ExternalIPAddress"
                        
                    mask_leaf = None
                    for leaf in ["SubnetMask", "Mask", "Netmask"]:
                        found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                        if found_path:
                            mask_leaf = found_path.split(".")[-1]
                            break
                    if not mask_leaf:
                        mask_leaf = "SubnetMask"
                        
                    gw_leaf = None
                    for leaf in ["DefaultGateway", "Gateway", "GatewayIPAddress"]:
                        found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                        if found_path:
                            gw_leaf = found_path.split(".")[-1]
                            break
                    if not gw_leaf:
                        gw_leaf = "DefaultGateway"
                        
                    dns_leaf = None
                    for leaf in ["DNSServers", "DNS", "DNSServer"]:
                        found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                        if found_path:
                            dns_leaf = found_path.split(".")[-1]
                            break
                    if not dns_leaf:
                        dns_leaf = "DNSServers"
                        
                    parameter_values.append([f"{base_path}.{ip_leaf}", state_data['static_ip']])
                    parameter_values.append([f"{base_path}.{mask_leaf}", state_data['static_mask']])
                    parameter_values.append([f"{base_path}.{gw_leaf}", state_data['static_gw']])
                    parameter_values.append([f"{base_path}.{dns_leaf}", state_data['static_dns']])

            # Set NAT jika mode Routed
            if wtype in ('ppp_routed', 'ip_routed'):
                nat_leaf = None
                for leaf in ["NATEnabled", "NATEnable"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        nat_leaf = found_path.split(".")[-1]
                        break
                if not nat_leaf:
                    nat_leaf = "NATEnabled"
                parameter_values.append([f"{base_path}.{nat_leaf}", state_data.get('nat_enabled', True)])

            # Set ConnectionTrigger ke AlwaysOn agar PPPoE/IPoE auto-dial
            trigger_leaf = None
            for leaf in ["ConnectionTrigger"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    trigger_leaf = found_path.split(".")[-1]
                    break
            if not trigger_leaf:
                trigger_leaf = "ConnectionTrigger"
            parameter_values.append([f"{base_path}.{trigger_leaf}", "AlwaysOn"])

            # Set DNSEnabled agar DNS bisa resolve
            dns_enable_leaf = None
            for leaf in ["DNSEnabled", "DNSEnable"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    dns_enable_leaf = found_path.split(".")[-1]
                    break
            if dns_enable_leaf:
                parameter_values.append([f"{base_path}.{dns_enable_leaf}", True])

            # Set VLAN Tagging jika diaktifkan
            # Pada FiberHome (has_gpon_link_config=True), VLAN sudah di-handle oleh GPON Link Config.
            # Jangan set VLANEnable=True di level WAN connection karena menyebabkan double-tagging.
            if state_data.get('vlan_enabled'):
                vlan_enable_path = None
                for leaf in ["VLANEnabled", "VLANEnable", "VLANMark", "X_HW_VLAN_Enable", "X_ZTE_VLAN_Enable", "X_CMCC_VLAN_Enable"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        vlan_enable_path = found_path
                        break
                if not vlan_enable_path:
                    vlan_enable_path = f"{base_path}.VLANEnabled"
                    
                vlan_id_path = None
                for leaf in ["VLANIDMark", "VLANID", "VLANId", "X_HW_VLAN", "X_ZTE_VLAN", "X_CMCC_VLANIDMark", "X_CMCC_VLANID"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        vlan_id_path = found_path
                        break
                if not vlan_id_path:
                    vlan_id_path = f"{base_path}.VLANIDMark"
                    
                parameter_values.append([vlan_enable_path, True])
                parameter_values.append([vlan_id_path, int(state_data['vlan_id'])])
                
                # Set VLAN Priority if supported by device
                prio_path = None
                for leaf in ["VLANPriorityMark", "VLANPriority", "802-1pMark", "X_FH_802-1pMark", "X_HW_VLANPriority", "X_ZTE_VLANPriority", "X_CMCC_802-1pMark"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        prio_path = found_path
                        break
                if prio_path:
                    parameter_values.append([prio_path, int(state_data.get('priority', 0))])
            else:
                vlan_enable_path = None
                for leaf in ["VLANEnabled", "VLANEnable", "VLANMark"]:
                    found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                    if found_path:
                        vlan_enable_path = found_path
                        break
                if vlan_enable_path:
                    parameter_values.append([vlan_enable_path, False])

            # Set Port Binding (LAN/SSID)
            full_binds = []
            for b in state_data.get('selected_binds', []):
                if b in INTERFACE_MAP:
                    full_binds.append(INTERFACE_MAP[b])
            binding_str = ",".join(full_binds)
            
            binding_leaf = None
            for leaf in ["BindLANPort", "WANBindLANPort", "X_BROADCOM_COM_BindLans", "X_ZTE_BindLan", "X_HW_LANInterface", "X_FH_LanInterface", "X_FH_LanInterfaceBind", "X_CMCC_LanInterface"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    binding_leaf = found_path.split(".")[-1]
                    break
            if not binding_leaf:
                binding_leaf = "BindLANPort"
            parameter_values.append([f"{base_path}.{binding_leaf}", binding_str])
                
            # ZTE (and other models) WanInterface binding auto-detection
            wan_leaf = None
            for leaf in ["WanInterface", "WANInterface", "X_ZTE_WANInterface", "WanInterfaceBind"]:
                found_path = check_param_exists_on_device(device_doc, base_path, leaf)
                if found_path:
                    wan_leaf = found_path.split(".")[-1]
                    break
            if not wan_leaf and state_data.get('is_f670l'):
                wan_leaf = "WanInterface"
            if wan_leaf:
                parameter_values.append([f"{base_path}.{wan_leaf}", base_path])
                
        elif wtype == 'wifi':
            wifi_slot = state_data['wifi_slot']
            base_path = f"InternetGatewayDevice.LANDevice.1.WLANConfiguration.{wifi_slot}"
            
            parameter_values.append([f"{base_path}.Enable", True])
            parameter_values.append([f"{base_path}.SSID", state_data['wifi_ssid']])
            
            sec_mode = state_data['wifi_sec']
            if sec_mode == 'None':
                parameter_values.append([f"{base_path}.BeaconType", "None"])
                if check_path_exists(device_doc, f"{base_path}.BasicEncryptionModes"):
                    parameter_values.append([f"{base_path}.BasicEncryptionModes", "None"])
            else:
                parameter_values.append([f"{base_path}.PreSharedKey.1.KeyPassphrase", state_data['wifi_key']])
                
                auth_mode_path = f"{base_path}.PreSharedKey.1.WPAAuthMode"
                if check_path_exists(device_doc, auth_mode_path):
                    parameter_values.append([auth_mode_path, "PSK"])
                
                # Dynamic check for BeaconType
                beacon_leaf = None
                for leaf in ["BeaconType", "BeaconTypeOption"]:
                    if check_path_exists(device_doc, f"{base_path}.{leaf}"):
                        beacon_leaf = leaf
                        break
                if not beacon_leaf:
                    beacon_leaf = "BeaconType"
                parameter_values.append([f"{base_path}.{beacon_leaf}", sec_mode])
                
                if check_path_exists(device_doc, f"{base_path}.IEEE11iEncryptionModes"):
                    enc_mode = "AES" if sec_mode == '11i' else "TKIPandAES"
                    parameter_values.append([f"{base_path}.IEEE11iEncryptionModes", enc_mode])
                if sec_mode == 'WPAand11i' and check_path_exists(device_doc, f"{base_path}.WPAEncryptionModes"):
                    parameter_values.append([f"{base_path}.WPAEncryptionModes", "TKIPandAES"])

        # 2. Kirim task setParameterValues ke GenieACS
        task_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
        payload = {
            "name": "setParameterValues",
            "parameterValues": parameter_values
        }
        
        resp_push = requests.post(task_url, json=payload, auth=auth, timeout=25)
        if resp_push.status_code in (200, 201, 202):
            try:
                from services.genieacs import trigger_connection_request_by_device_id
                trigger_connection_request_by_device_id(device_id)
            except Exception as e:
                print(f"Error triggering direct connection request in execute_prov_push: {e}")
            return True, "Antrean task CWMP (setParameterValues) berhasil dibuat."
        else:
            return False, f"GenieACS NBI Error: {resp_push.status_code} - {resp_push.text}"
            
    except Exception as e:
        return False, f"Exception: {str(e)}"

def _send_telegram_worker(message, reply_markup=None):
    token, chat_id = '', ''
    # Fallback to env file
    env_path = '/app/.telegram.env'
    if os.path.exists(env_path):
        try:
            with open(env_path) as f:
                for line in f:
                    if line.startswith('TELEGRAM_BOT_TOKEN='):
                        token = line.strip().split('=', 1)[1]
                    elif line.startswith('TELEGRAM_CHAT_ID='):
                        chat_id = line.strip().split('=', 1)[1]
        except Exception as e:
            print(f"Error reading .telegram.env: {e}")
            
    # Try current_app config if active
    try:
        if current_app:
            token = current_app.config.get('TELEGRAM_BOT_TOKEN', token) or token
            chat_id = current_app.config.get('TELEGRAM_CHAT_ID', chat_id) or chat_id
    except:
        pass
        
    # Fallback to Config class if not set
    if not token or not chat_id:
        try:
            from config import Config
            token = token or Config.TELEGRAM_BOT_TOKEN
            chat_id = chat_id or Config.TELEGRAM_CHAT_ID
        except:
            pass
            
    if not token or not chat_id:
        return
        
    try:
        import json as _json
        data = {'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'}
        if reply_markup:
            data['reply_markup'] = reply_markup
            
        # Gunakan requests.post asinkronus via thread daripada spawn subprocess curl
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json=data,
            timeout=10
        )
        if resp.status_code != 200:
            print(f"Telegram API warning: {resp.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def send_telegram_notification(message, reply_markup=None):
    threading.Thread(target=_send_telegram_worker, args=(message, reply_markup), daemon=True).start()

def populate_state_from_wan(state_data, device_doc, wcd_idx, conn_type, conn_idx):
    wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {}).get(str(wcd_idx), {})
    conn_instances = wcd.get(f"WAN{conn_type}Connection", {})
    conn = conn_instances.get(str(conn_idx), {})
    
    if str(wcd_idx) == "1":
        slot = int(conn_idx)
    else:
        slot = int(wcd_idx)
    state_data['slot'] = slot
    state_data['wcd_idx'] = wcd_idx
    state_data['conn_type'] = conn_type
    state_data['conn_idx'] = conn_idx
    state_data['is_edit'] = True
    
    conn_type_val = conn.get("ConnectionType", {}).get("_value", "IP_Routed")
    if conn_type == "PPP":
        # Support both HG6243C (IP_Routed) and HG6245N (PPPoE_Routed) naming
        state_data['wan_type'] = 'ppp_routed' if conn_type_val in ('IP_Routed', 'PPPoE_Routed') else 'ppp_bridged'
    else:
        state_data['wan_type'] = 'ip_routed' if conn_type_val == 'IP_Routed' else 'ip_bridged'
        
    svc_val = "INTERNET"
    for leaf in ["ServiceList", "X_FH_ServiceList", "X_HW_ServiceList", "X_ZTE_ServiceList", "X_CMCC_ServiceList"]:
        if leaf in conn:
            svc_val = conn[leaf].get("_value", "INTERNET")
            break
    state_data['service_list'] = svc_val
    
    state_data['pppoe_user'] = conn.get("Username", {}).get("_value", "")
    state_data['pppoe_pass'] = conn.get("Password", {}).get("_value", "")
    
    state_data['ipoe_addr'] = conn.get("AddressingType", {}).get("_value", "DHCP")
    state_data['static_ip'] = conn.get("ExternalIPAddress", {}).get("_value", "")
    state_data['static_mask'] = conn.get("SubnetMask", {}).get("_value", "")
    state_data['static_gw'] = conn.get("DefaultGateway", {}).get("_value", "")
    state_data['static_dns'] = conn.get("DNSServers", {}).get("_value", "")
    
    state_data['nat_enabled'] = conn.get("NATEnabled", {}).get("_value", True)
    
    parent_path = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{wcd_idx}"
    vlan_id = 0
    # 1. Check WAN connection level for VLAN
    for leaf in ["VLANIDMark", "VLANID", "VLANId", "X_HW_VLAN", "X_ZTE_VLAN", "X_CMCC_VLANIDMark", "X_CMCC_VLANID"]:
        if leaf in conn:
            try:
                vlan_id = int(conn[leaf].get("_value", 0))
            except:
                pass
            if vlan_id > 0:
                break
        if leaf in wcd:
            try:
                vlan_id = int(wcd[leaf].get("_value", 0))
            except:
                pass
            if vlan_id > 0:
                break
    
    # 2. FiberHome: also check X_FH_WANGponLinkConfig for VLAN (HG6245N stores VLAN here)
    if vlan_id == 0:
        for key in wcd:
            if 'GponLinkConfig' in key or 'GPONLinkConfig' in key or 'WANGponLink' in key:
                gpon_cfg = wcd.get(key, {})
                for vlan_leaf in ["VLANID", "VLANIDMark"]:
                    if vlan_leaf in gpon_cfg:
                        try:
                            vlan_id = int(gpon_cfg[vlan_leaf].get("_value", 0))
                        except:
                            pass
                        if vlan_id > 0:
                            break
                break
    
    # 3. FiberHome: try to parse VLAN from WAN Name (e.g. "2_INTERNET_R_VID_200")
    if vlan_id == 0:
        wan_name = conn.get("Name", {}).get("_value", "")
        if "_VID_" in wan_name:
            try:
                vlan_id = int(wan_name.split("_VID_")[-1])
            except:
                pass
                
    state_data['vlan_id'] = vlan_id
    state_data['vlan_enabled'] = (vlan_id > 0)
    
    priority = 0
    for key in wcd:
        if 'GponLinkConfig' in key or 'GPONLinkConfig' in key or 'WANGponLink' in key:
            gpon_cfg = wcd.get(key, {})
            for prio_leaf in ["802-1pMark", "X_FH_802-1pMark", "PriorityMark", "Priority"]:
                if prio_leaf in gpon_cfg:
                    try:
                        priority = int(gpon_cfg[prio_leaf].get("_value", 0))
                        break
                    except:
                        pass
            if priority > 0:
                break
 
    if priority == 0:
        for leaf in ["VLANPriorityMark", "VLANPriority", "802-1pMark", "X_FH_802-1pMark", "X_HW_VLANPriority", "X_ZTE_VLANPriority", "X_CMCC_802-1pMark"]:
            if leaf in conn:
                try:
                    priority = int(conn[leaf].get("_value", 0))
                    break
                except:
                    pass
    state_data['priority'] = priority
    
    binding_str = ""
    for leaf in ["BindLANPort", "WANBindLANPort", "X_BROADCOM_COM_BindLans", "X_ZTE_BindLan", "X_HW_LANInterface", "X_FH_LanInterface", "X_FH_LanInterfaceBind", "X_CMCC_LanInterface"]:
        if leaf in conn:
            binding_str = conn[leaf].get("_value", "")
            break
            
    selected_binds = []
    if binding_str:
        bind_parts = [p.strip() for p in binding_str.split(",") if p.strip()]
        for friendly, full_path in INTERFACE_MAP.items():
            if full_path in bind_parts:
                selected_binds.append(friendly)
    state_data['selected_binds'] = selected_binds

def show_edit_menu(chat_id, token):
    state_data = _prov_states.get(chat_id)
    if not state_data:
        return
        
    username = state_data['username']
    wtype = state_data['wan_type']
    slot = state_data['slot']
    
    msg = f"📝 <b>Edit WAN [Wan {slot}] Pelanggan:</b> <code>{username}</code>\n\n"
    
    modes_map = {
        'ppp_routed': 'IP_Routed (PPPoE)',
        'ppp_bridged': 'PPPoE_Bridged',
        'ip_bridged': 'IP_Bridged',
        'ip_routed': 'IP_Routed (IPoE)'
    }
    msg += f"• Tipe WAN: <b>{modes_map.get(wtype, wtype)}</b>\n" \
           f"• Service List: <code>{state_data['service_list']}</code>\n"
           
    if wtype == 'ppp_routed':
        msg += f"• PPPoE Username: <code>{state_data['pppoe_user']}</code>\n" \
               f"• PPPoE Password: <code>{state_data['pppoe_pass']}</code>\n"
    elif wtype == 'ip_routed':
        msg += f"• Mode IP: <b>{state_data['ipoe_addr']}</b>\n"
        if state_data['ipoe_addr'] == 'Static':
            msg += f"  - IP Address: <code>{state_data['static_ip']}</code>\n" \
                   f"  - Subnet Mask: <code>{state_data['static_mask']}</code>\n" \
                   f"  - Gateway: <code>{state_data['static_gw']}</code>\n" \
                   f"  - DNS: <code>{state_data['static_dns']}</code>\n"
                   
    if wtype in ('ppp_routed', 'ip_routed'):
        msg += f"• NAT: <b>{'Enabled' if state_data.get('nat_enabled') else 'Disabled'}</b>\n"
        
    msg += f"• VLAN Tag: <b>{'Enabled (' + str(state_data.get('vlan_id')) + ')' if state_data.get('vlan_enabled') else 'Disabled'}</b>\n"
    msg += f"• Priority WAN: <b>{state_data.get('priority', 0)}</b>\n"
    
    bind_list = state_data.get('selected_binds', [])
    msg += f"• Port Binding: <code>{', '.join(bind_list) if bind_list else 'Tanpa Bind'}</code>\n" \
           f"• ZTE F670L: <b>{'Ya' if state_data.get('is_f670l') else 'Tidak'}</b>\n\n" \
           f"Silakan pilih parameter yang ingin diubah:"
           
    buttons = []
    if wtype == 'ppp_routed':
        buttons.append([{"text": "✏️ PPPoE Credentials", "callback_data": "edit_field:pppoe"}])
    elif wtype == 'ip_routed' and state_data['ipoe_addr'] == 'Static':
        buttons.append([{"text": "✏️ IP Address Static", "callback_data": "edit_field:static_ip"}])
        
    buttons.append([
        {"text": "✏️ VLAN Tag", "callback_data": "edit_field:vlan"},
        {"text": "✏️ Priority WAN", "callback_data": "edit_field:priority"},
        {"text": "✏️ Port Binding", "callback_data": "edit_field:bind"}
    ])
    
    buttons.append([
        {"text": "🚀 PUSH CONFIG", "callback_data": "prov_push"},
        {"text": "❌ Batal", "callback_data": "prov_cancel"}
    ])
    
    import json as _json
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
        'chat_id': chat_id,
        'text': msg,
        'parse_mode': 'HTML',
        'reply_markup': _json.dumps({"inline_keyboard": buttons})
    })
    state_data['state'] = 'EDIT_WAN_MENU'

def execute_delete_wan(device_id, wcd_idx, conn_type, conn_idx):
    try:
        config = get_genieacs_config()
        GENIEACS_NBI = config['url']
        auth = config['auth']
        import urllib.parse
        
        task_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
        
        if int(wcd_idx) == 1:
            obj_name = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WAN{conn_type}Connection.{conn_idx}"
        else:
            obj_name = f"InternetGatewayDevice.WANDevice.1.WANConnectionDevice.{wcd_idx}"
            
        payload = {
            "name": "deleteObject",
            "objectName": obj_name
        }
        
        resp = requests.post(task_url, json=payload, auth=auth, timeout=10)
        if resp.status_code in (200, 201, 202):
            try:
                from services.genieacs import trigger_connection_request_by_device_id
                trigger_connection_request_by_device_id(device_id)
            except Exception as e:
                print(f"Error triggering direct connection request in execute_delete_wan: {e}")
            return True, f"Antrean task CWMP (deleteObject untuk {obj_name}) berhasil dibuat."
        else:
            return False, f"GenieACS NBI Error: {resp.status_code} - {resp.text}"
    except Exception as e:
        return False, f"Exception: {str(e)}"

def handle_cek_command(chat_id, username, token):
    """
    Menangani perintah /cek dengan lookup data RADIUS & GenieACS,
    mendukung caching serial-username di Redis untuk respon cepat (<100ms).
    """
    # 1. Cek cache mapping di Redis dahulu untuk pencarian instan
    resolved_username = username
    resolved_serial = None
    
    redis_client = None
    try:
        from flask import current_app
        redis_client = current_app.config.get('SESSION_REDIS')
    except RuntimeError:
        try:
            from config import Config
            redis_client = Config.SESSION_REDIS
        except:
            pass
            
    if redis_client:
        try:
            # Coba cari apakah input adalah username PPPoE
            cached_sn = redis_client.get(f"mapping:user_to_sn:{username}")
            if cached_sn:
                resolved_serial = cached_sn.decode('utf-8') if isinstance(cached_sn, bytes) else cached_sn
            else:
                # Coba cari apakah input adalah Serial Number
                cached_un = redis_client.get(f"mapping:sn_to_user:{username}")
                if cached_un:
                    resolved_username = cached_un.decode('utf-8') if isinstance(cached_un, bytes) else cached_un
                    resolved_serial = username
        except Exception as cache_err:
            print(f"[telegram-bot] Error reading mapping cache: {cache_err}")
            
    # Jika mapping ditemukan di Redis, cari GenieACS dengan Serial Number secara langsung (sangat cepat)
    acs_data = None
    acs_found = False
    if resolved_serial:
        acs_data = get_genieacs_device_info(resolved_serial)
        acs_found = acs_data.get('found', False)
    else:
        # Query GenieACS service dengan username input langsung
        acs_data = get_genieacs_device_info(username)
        acs_found = acs_data.get('found', False)
        
    # Fallback jika pencarian via PPPoE username tidak ketemu di GenieACS:
    # Cek apakah username ini terdaftar di ont_serial_mapping untuk mencari Serial Number-nya
    if not acs_found:
        try:
            mapping_res = execute_query("SELECT serial_number FROM ont_serial_mapping WHERE username = %s LIMIT 1", (username,))
            if mapping_res:
                db_serial = mapping_res[0]['serial_number'].strip()
                acs_data = get_genieacs_device_info(db_serial)
                acs_found = acs_data.get('found', False)
                if acs_found and redis_client:
                    # Simpan ke Redis cache agar selanjutnya instan
                    try:
                        redis_client.setex(f"mapping:user_to_sn:{username}", 86400, db_serial)
                        redis_client.setex(f"mapping:sn_to_user:{db_serial}", 86400, username)
                    except:
                        pass
            else:
                # Coba cari sebaliknya di db: input adalah serial_number
                mapping_res = execute_query("SELECT username FROM ont_serial_mapping WHERE serial_number = %s LIMIT 1", (username,))
                if mapping_res:
                    db_user = mapping_res[0]['username'].strip()
                    # query by serial number directly
                    acs_data = get_genieacs_device_info(username)
                    acs_found = acs_data.get('found', False)
                    if acs_found and redis_client:
                        try:
                            redis_client.setex(f"mapping:user_to_sn:{db_user}", 86400, username)
                            redis_client.setex(f"mapping:sn_to_user:{username}", 86400, db_user)
                        except:
                            pass
        except Exception as e:
            print(f"[telegram-bot] Error mapping username/serial in DB: {e}")
            
    # Simpan hasil mapping ke Redis jika berhasil diselesaikan tapi belum ada di Redis
    if acs_found and redis_client:
        try:
            sn_val = acs_data.get('serial')
            un_val = acs_data.get('pppoe_username')
            if sn_val and sn_val != 'N/A' and un_val and un_val != 'N/A' and un_val.strip():
                redis_client.setex(f"mapping:user_to_sn:{un_val}", 86400, sn_val)
                redis_client.setex(f"mapping:sn_to_user:{sn_val}", 86400, un_val)
        except Exception as cache_err:
            print(f"[telegram-bot] Error saving resolved mapping to cache: {cache_err}")

    # Dapatkan pppoe_username jika tersedia dari GenieACS
    radius_username = username
    if acs_found and acs_data.get('pppoe_username') and acs_data.get('pppoe_username') not in ('N/A', ''):
        radius_username = acs_data.get('pppoe_username')
    else:
        # Fallback 1 jika pencarian via SN tapi pppoe_username kosong di GenieACS (karena bug firmware):
        # Cek database ont_serial_mapping untuk mendapatkan username PPPoE yang terikat
        try:
            mapping_res = execute_query("SELECT username FROM ont_serial_mapping WHERE serial_number = %s LIMIT 1", (username,))
            if mapping_res:
                radius_username = mapping_res[0]['username'].strip()
                # Push tag ke GenieACS NBI agar muncul di GenieACS Dashboard
                try:
                    config_acs = get_genieacs_config()
                    url_acs = config_acs['url']
                    auth_acs = config_acs['auth']
                    import urllib.parse
                    device_id_acs = acs_data.get('id') or f"{acs_data.get('manufacturer','000AC2')}-{acs_data.get('model','HG6145D2')}-{username}"
                    tag_url = f"{url_acs}/devices/{urllib.parse.quote(device_id_acs)}/tags/{urllib.parse.quote(radius_username)}"
                    requests.post(tag_url, auth=auth_acs, timeout=3)
                except Exception as tag_err:
                    print(f"[telegram-bot] Error pushing tag in fallback 1: {tag_err}")
            elif acs_found and acs_data.get('mac_address') and acs_data.get('mac_address') != 'N/A':
                # Fallback 2 (Otomatis & Cerdas): Cari kecocokan MAC address prefix di radacct
                mac_base = acs_data.get('mac_address').strip()
                if len(mac_base) >= 14:
                    mac_prefix = mac_base[:14] # Ambil 5 octet pertama
                    mac_query = execute_query("SELECT username FROM radacct WHERE callingstationid LIKE %s ORDER BY acctstarttime DESC LIMIT 1", (mac_prefix + '%',))
                    if mac_query:
                        radius_username = mac_query[0]['username']
                        # Simpan otomatis ke ont_serial_mapping agar pencarian berikutnya instan
                        try:
                            execute_query("INSERT INTO ont_serial_mapping (username, serial_number) VALUES (%s, %s) ON CONFLICT (username) DO UPDATE SET serial_number = EXCLUDED.serial_number", (radius_username, username), fetch=False)
                            # Push tag ke GenieACS NBI
                            try:
                                config_acs = get_genieacs_config()
                                url_acs = config_acs['url']
                                auth_acs = config_acs['auth']
                                import urllib.parse
                                device_id_acs = acs_data.get('id') or f"{acs_data.get('manufacturer','000AC2')}-{acs_data.get('model','HG6145D2')}-{username}"
                                tag_url = f"{url_acs}/devices/{urllib.parse.quote(device_id_acs)}/tags/{urllib.parse.quote(radius_username)}"
                                requests.post(tag_url, auth=auth_acs, timeout=3)
                            except Exception as tag_err:
                                print(f"[telegram-bot] Error pushing tag in fallback 2: {tag_err}")
                        except Exception as db_err:
                            print(f"[telegram-bot] Error inserting auto-mapping: {db_err}")
        except Exception as e:
            print(f"[telegram-bot] Error mapping serial to username: {e}")
            
    # 2. Query PostgreSQL untuk status sesi RADIUS
    session_res = execute_query("""
        SELECT * FROM radacct 
        WHERE username = %s 
        ORDER BY acctstarttime DESC LIMIT 1
    """, (radius_username,))
    
    # Jika masih tidak ketemu, coba username aslinya jika berbeda
    if not session_res and radius_username != username:
        session_res = execute_query("""
            SELECT * FROM radacct 
            WHERE username = %s 
            ORDER BY acctstarttime DESC LIMIT 1
        """, (username,))
        
    is_online = False
    nas_ip = "N/A"
    nas_name = "N/A"
    framed_ip = "N/A"
    mac_address = "N/A"
    uptime_str = "N/A"
    session_days = 0
    
    if session_res:
        s = session_res[0]
        if s.get('acctstoptime') is None:
            is_online = True
            start_time = s.get('acctstarttime')
            if start_time:
                now = datetime.now(start_time.tzinfo) if start_time.tzinfo else datetime.now()
                diff = now - start_time
                hours, remainder = divmod(diff.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                uptime_str = f"{diff.days}d {hours}h {minutes}m"
                session_days = diff.days
                
        nas_ip = s.get('nasipaddress', 'N/A')
        framed_ip = s.get('framedipaddress', 'N/A')
        mac_address = s.get('callingstationid', 'N/A')
        
        # Dapatkan nama NAS
        nas_info = execute_query("SELECT shortname FROM nas WHERE nasname = %s LIMIT 1", (nas_ip,))
        if nas_info:
            nas_name = nas_info[0]['shortname'] or nas_ip
            
    # 3. Formulasikan Balasan HTML & Smart Diagnostics
    status_str = "🟢 <b>ONLINE</b>" if is_online else "🔴 <b>OFFLINE</b>"
    
    reply = (
        f"👤 <b>Informasi Pelanggan:</b>\n"
        f"• Username: <code>{username}</code>\n"
    )
    if radius_username != username:
        reply += f"• PPPoE Account: <code>{radius_username}</code>\n"
        
    reply += f"• Status Sesi: {status_str}\n"
    
    if session_res:
        reply += (
            f"• Router NAS: <b>{nas_name}</b> (<code>{nas_ip}</code>)\n"
            f"• IP Address: <code>{framed_ip}</code>\n"
            f"• MAC Address: <code>{mac_address}</code>\n"
            f"• Sesi Aktif Uptime: <code>{uptime_str}</code>\n"
        )
    else:
        reply += "• <i>Log Sesi RADIUS tidak ditemukan.</i>\n"
        
    reply += "\n📌 <b>Informasi ONT (GenieACS):</b>\n"
    if acs_found:
        reply += (
            f"• SN / Model: <code>{acs_data.get('serial','N/A')}</code> ({acs_data.get('manufacturer','N/A')} {acs_data.get('model','N/A')})\n"
            f"• Redaman (RxPower): <b>{acs_data.get('rx_power','N/A')} dBm</b> ({acs_data.get('rx_status_emoji','⚪ Unknown')})\n"
            f"• Uptime Perangkat: <code>{acs_data.get('ont_uptime_str','N/A')}</code>\n"
            f"• Temperatur: <code>{acs_data.get('temperature','N/A')} °C</code>\n"
            f"• Mode PON: <code>{acs_data.get('pon_mode','N/A')}</code>\n"
            f"• Perangkat Wi-Fi Aktif: <b>{acs_data.get('wifi_active_count','N/A')} perangkat</b>\n"
        )
    else:
        reply += f"• <i>ONT Detail: {acs_data.get('message', 'Data tidak ditemukan')}</i>\n"
        
    # === NOC SMART DIAGNOSTICS & RECOMMENDATIONS ===
    recommendations = []
    
    if acs_found:
        try:
            rx_float = float(acs_data.get('rx_power', 'N/A'))
            if rx_float < -27.0:
                recommendations.append(f"⚠️ <b>Redaman Buruk ({rx_float} dBm):</b> Sinyal berada di bawah batas normal (-27 dBm). Disarankan pengecekan sambungan fisik atau redrop kabel optik.")
        except:
            pass
            
        if acs_data.get('ont_uptime_days', 0) > 60:
            recommendations.append(f"💡 <b>ONT Uptime Tinggi ({acs_data.get('ont_uptime_days', 0)} hari):</b> Perangkat ONT aktif terlalu lama tanpa reboot. Disarankan me-reboot ONT untuk optimalisasi kinerja memori.")
            
    if is_online and session_days > 60:
        recommendations.append(f"🔌 <b>Sesi RADIUS Tinggi ({session_days} hari):</b> Sesi dial-up aktif terlalu lama tanpa refresh. Disarankan 'Kick Session' agar mendapat alokasi IP & rute segar.")
        
    if recommendations:
        reply += "\n🧠 <b>Rekomendasi Pintar (NOC Diagnostic):</b>\n"
        for rec in recommendations:
            reply += f"• {rec}\n"
            
    # Tambahkan Tombol Interaktif
    inline_keyboard = []
    row = []
    if is_online:
        row.append({"text": "⚡ Kick Session", "callback_data": f"kick_session:{username}"})
    if acs_found:
        row.append({"text": "🔄 Reboot ONT", "callback_data": f"reboot_ont:{username}"})
    if row:
        inline_keyboard.append(row)
        
    row2 = []
    if acs_found:
        row2.append({"text": "👥 List Device Wi-Fi", "callback_data": f"list_wifi_clients:{username}"})
        row2.append({"text": "📡 Scan Wi-Fi Sekitar", "callback_data": f"scan_wifi:{username}"})
    if row2:
        inline_keyboard.append(row2)
        
    row3 = []
    if acs_found:
        row3.append({"text": "🛠️ Provisioning Wizard", "callback_data": f"prov_menu:{username}"})
    if row3:
        inline_keyboard.append(row3)
        
    payload = {
        'chat_id': chat_id,
        'text': reply,
        'parse_mode': 'HTML'
    }
    
    if inline_keyboard:
        import json as _json
        payload['reply_markup'] = _json.dumps({"inline_keyboard": inline_keyboard})
        
    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data=payload)

def _wifi_scan_worker(chat_id, username, token):
    try:
        # 1. Cari device info (agar dapat device_id)
        acs_data = get_genieacs_device_info(username)
        if not acs_data.get('found'):
            # Fallback DB
            try:
                mapping_res = execute_query("SELECT serial_number FROM ont_serial_mapping WHERE username = %s LIMIT 1", (username,))
                if mapping_res:
                    db_serial = mapping_res[0]['serial_number'].strip()
                    acs_data = get_genieacs_device_info(db_serial)
            except Exception as db_err:
                print(f"Error mapping username in scan worker: {db_err}")
                
        if not acs_data.get('found'):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                'parse_mode': 'HTML'
            })
            return
            
        device_id = acs_data.get('id')
        if not device_id:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid.",
                'parse_mode': 'HTML'
            })
            return
            
        # Kirim update progress
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
            'chat_id': chat_id,
            'text': f"📡 <b>[1/3] Pemicuan Scan...</b>\nONT sedang memindai jaringan Wi-Fi tetangga via CWMP. Mohon tunggu...",
            'parse_mode': 'HTML'
        })
        
        # 2. Jalankan scan
        scan_res = run_wifi_scan(device_id)
        if not scan_res.get('success'):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal Scan Wi-Fi:</b> {scan_res.get('message', 'Timeout / Error NBI')}",
                'parse_mode': 'HTML'
            })
            return
            
        # Kirim update progress
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
            'chat_id': chat_id,
            'text': f"🧠 <b>[2/3] Analisis Interferensi...</b>\nMenganalisis kekuatan sinyal & tumpang tindih channel tetangga...",
            'parse_mode': 'HTML'
        })
        
        # 3. Analisis
        analysis = analyze_wifi_neighbors(scan_res['device'])
        
        # 4. Susun Balasan
        reply = (
            f"📶 <b>Hasil Analisis Wi-Fi:</b>\n"
            f"• Pelanggan: <code>{username}</code>\n"
            f"• Wi-Fi 2.4 GHz saat ini: Channel <b>{analysis['current_2g_channel']}</b>\n"
            f"• Wi-Fi 5 GHz saat ini: Channel <b>{analysis['current_5g_channel']}</b>\n\n"
        )
        
        # Tampilkan rekomendasi 2.4 GHz
        scores_2g = analysis['scores_2g']
        reply += (
            f"💡 <b>Rekomendasi 2.4 GHz (Non-overlapping):</b>\n"
            f"• Channel 1 : Interferensi Score <b>{scores_2g[1]:.1f}</b>\n"
            f"• Channel 6 : Interferensi Score <b>{scores_2g[6]:.1f}</b>\n"
            f"• Channel 11: Interferensi Score <b>{scores_2g[11]:.1f}</b>\n"
            f"👉 <b>Rekomendasi Terbaik: Channel {analysis['best_2g_channel']}</b>\n\n"
        )
        
        # Tampilkan rekomendasi 5 GHz
        scores_5g = analysis['scores_5g']
        reply += (
            f"💡 <b>Rekomendasi 5 GHz (Non-DFS):</b>\n"
            f"👉 <b>Rekomendasi Terbaik: Channel {analysis['best_5g_channel']}</b>\n"
            f"  (Interferensi terendah pada channel {analysis['best_5g_channel']} dengan score {scores_5g[analysis['best_5g_channel']]:.1f})\n\n"
        )
        
        # Tampilkan list Neighbor terkuat (jika ada)
        neighbors_2g = analysis['neighbors_2g']
        if neighbors_2g:
            reply += "📡 <b>Sinyal Tetangga 2.4 GHz Terkuat:</b>\n"
            for n in neighbors_2g[:5]:
                reply += f"• <code>{n['ssid']}</code> (Ch {n['channel']}, {n['rssi']} dBm)\n"
            reply += "\n"
            
        neighbors_5g = analysis['neighbors_5g']
        if neighbors_5g:
            reply += "📡 <b>Sinyal Tetangga 5 GHz Terkuat:</b>\n"
            for n in neighbors_5g[:5]:
                reply += f"• <code>{n['ssid']}</code> (Ch {n['channel']}, {n['rssi']} dBm)\n"
            reply += "\n"
            
        reply += "⚙️ <b>Terapkan Channel Rekomendasi Secara Instan:</b>"
        
        # Tombol interaktif untuk menerapkan channel
        inline_keyboard = []
        row_apply = [
            {"text": f"📡 Terapkan Ch {analysis['best_2g_channel']} (2.4G)", "callback_data": f"set_channel:{username}:1:{analysis['best_2g_channel']}"}
        ]
        # check if 5G exists on device
        wlan_conf = scan_res['device'].get("InternetGatewayDevice", {}).get("LANDevice", {}).get("1", {}).get("WLANConfiguration", {})
        if "5" in wlan_conf:
            row_apply.append({"text": f"📡 Terapkan Ch {analysis['best_5g_channel']} (5G)", "callback_data": f"set_channel:{username}:5:{analysis['best_5g_channel']}"})
        inline_keyboard.append(row_apply)
        
        # Tambahkan baris tombol kembali / re-scan
        inline_keyboard.append([
            {"text": "🔄 Scan Ulang", "callback_data": f"scan_wifi:{username}"},
            {"text": "🔙 Menu Cek", "callback_data": f"cek_back:{username}"}
        ])
        
        import json as _json
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            'chat_id': chat_id,
            'text': reply,
            'parse_mode': 'HTML',
            'reply_markup': {"inline_keyboard": inline_keyboard}
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
            'chat_id': chat_id,
            'text': f"❌ <b>Error dalam Wi-Fi Analyzer:</b> {e}",
            'parse_mode': 'HTML'
        })

def _wlan_clients_worker(chat_id, username, token):
    try:
        # 1. Cari device info (agar dapat device_id)
        acs_data = get_genieacs_device_info(username)
        if not acs_data.get('found'):
            # Fallback DB
            try:
                mapping_res = execute_query("SELECT serial_number FROM ont_serial_mapping WHERE username = %s LIMIT 1", (username,))
                if mapping_res:
                    db_serial = mapping_res[0]['serial_number'].strip()
                    acs_data = get_genieacs_device_info(db_serial)
            except Exception as db_err:
                print(f"Error mapping username in clients worker: {db_err}")
                
        if not acs_data.get('found'):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                'parse_mode': 'HTML'
            })
            return
            
        device_id = acs_data.get('id')
        if not device_id:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid.",
                'parse_mode': 'HTML'
            })
            return
            
        # 2. Panggil get_wlan_clients_realtime untuk pemicuan refresh & pengambilan data
        res = get_wlan_clients_realtime(device_id)
        if not res.get('success'):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                'chat_id': chat_id,
                'text': f"❌ <b>Gagal mendapatkan list Wi-Fi client:</b> {res.get('message', 'Unknown error')}",
                'parse_mode': 'HTML'
            })
            return
            
        clients = res.get('clients', [])
        total_active_wifi = res.get('total_active_wifi', 0)
        is_stale = res.get('stale', False)
        last_inform = res.get('last_inform', 'N/A')
        
        # Tombol kembali
        inline_keyboard = [[
            {"text": "🔄 Refresh", "callback_data": f"list_wifi_clients:{username}"},
            {"text": "🔙 Menu Cek", "callback_data": f"cek_back:{username}"}
        ]]
        
        reply = ""
        if is_stale:
            reply += f"⚠️ <b>PERINGATAN:</b> Gagal menghubungi ONT secara langsung (Connection Request terhambat).\nData di bawah ini adalah data terakhir terekam (Cache) pada: <code>{last_inform}</code>\n\n"
        else:
            if clients or total_active_wifi > 0:
                reply += f"✅ <b>Data Real-time Berhasil Diambil!</b>\n\n"
            
        if total_active_wifi == 0 and not clients:
            reply += f"⚠️ <b>Tidak ada perangkat (Wi-Fi Client) yang terhubung aktif</b> pada ONT pelanggan <code>{username}</code> saat ini."
            
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                'chat_id': chat_id,
                'text': reply,
                'parse_mode': 'HTML',
                'reply_markup': {"inline_keyboard": inline_keyboard}
            })
            return
            
        reply += (
            f"👥 <b>Daftar Perangkat Terkoneksi Wi-Fi:</b>\n"
            f"• Pelanggan: <code>{username}</code>\n"
            f"• Total Aktif: <b>{total_active_wifi} perangkat</b>\n\n"
        )
        
        if not clients:
            reply += "ℹ️ <i>Detail perangkat (IP/MAC) sedang diperbarui atau belum dilaporkan oleh ONT.</i>"
            
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                'chat_id': chat_id,
                'text': reply,
                'parse_mode': 'HTML',
                'reply_markup': {"inline_keyboard": inline_keyboard}
            })
            return
            
        # 3. Susun balasan list client secara terorganisasi berdasarkan SSID
        by_ssid = {}
        for c in clients:
            ssid = c['ssid_name']
            if ssid not in by_ssid:
                by_ssid[ssid] = []
            by_ssid[ssid].append(c)
            
        for ssid_name, dev_list in by_ssid.items():
            reply += f"📶 <b>SSID: {ssid_name}</b>\n"
            for idx, dev in enumerate(dev_list, 1):
                reply += f"  {idx}. IP: <code>{dev['ip']}</code>\n"
                if dev.get('device_name') and dev['device_name'] != "N/A":
                    reply += f"     Nama/Merk: <b>{dev['device_name']}</b>\n"
                reply += f"     MAC: <code>{dev['mac']}</code>\n"
                if dev.get('rssi'):
                    reply += f"     Signal: <code>{dev['rssi']} dBm</code>\n"
                if dev.get('uptime'):
                    try:
                        ut = int(dev['uptime'])
                        hours, remainder = divmod(ut, 3600)
                        minutes, _ = divmod(remainder, 60)
                        ut_str = f"{hours}j {minutes}m" if hours > 0 else f"{minutes}m"
                        reply += f"     Uptime: <code>{ut_str}</code>\n"
                    except:
                        pass
                reply += "\n"
                
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
            'chat_id': chat_id,
            'text': reply,
            'parse_mode': 'HTML',
            'reply_markup': {"inline_keyboard": inline_keyboard}
        })
        
    except Exception as e:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
            'chat_id': chat_id,
            'text': f"❌ <b>Error dalam mendapatkan list Wi-Fi:</b> {e}",
            'parse_mode': 'HTML'
        })

def background_telegram_bot():
    """Mendengar pesan Telegram masuk (bot polling) untuk melayani perintah teknisi secara interaktif."""
    print("[telegram-bot] Thread initialized, sleeping 20s for stability...")
    socketio.sleep(20) # Tunggu agar startup stabil
    print("[telegram-bot] Thread starting active polling...")
    offset = 0
    
    while True:
        try:
            token = None
            admin_chat_id = None
            
            # Check various potential env file paths
            env_paths = ['/app/.telegram.env', 'app/.telegram.env', '.telegram.env', '/home/my-project/project-radius/app/.telegram.env']
            for env_path in env_paths:
                if os.path.exists(env_path):
                    try:
                        with open(env_path) as f:
                            for line in f:
                                if '=' in line:
                                    k, v = line.split('=', 1)
                                    k = k.strip()
                                    v = v.strip()
                                    if k == 'TELEGRAM_BOT_TOKEN':
                                        token = v
                                    elif k == 'TELEGRAM_CHAT_ID':
                                        admin_chat_id = v
                        if token and admin_chat_id:
                            break
                    except Exception as e:
                        print(f"[telegram-bot] Error reading {env_path}: {e}")
            
            # Fallback to Config class if not set
            if not token or not admin_chat_id:
                try:
                    from config import Config
                    if not token:
                        token = Config.TELEGRAM_BOT_TOKEN
                    if not admin_chat_id:
                        admin_chat_id = Config.TELEGRAM_CHAT_ID
                except:
                    pass

            if not token:
                print("[telegram-bot] Warning: TELEGRAM_BOT_TOKEN not found!")
                socketio.sleep(10)
                continue
                
            # Long polling updates dari Telegram API
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {'offset': offset, 'timeout': 15}
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                socketio.sleep(5)
                continue
                
            updates = resp.json().get('result', [])
            for update in updates:
                offset = update['update_id'] + 1
                
                # Memproses Interaksi Tombol Callback Query
                if 'callback_query' in update:
                    cb = update['callback_query']
                    cb_id = cb.get('id')
                    cb_data = cb.get('data', '')
                    cb_chat_id = cb.get('message', {}).get('chat', {}).get('id')
                    
                    try:
                        requests.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery", data={
                            'callback_query_id': cb_id
                        }, timeout=3)
                    except:
                        pass
                        
                    if cb_data and cb_chat_id:
                        if cb_data.startswith('ping_nas:'):
                            nas_ip = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ Sedang menguji ping ulang ke IP <code>{nas_ip}</code>...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            status = ping_nas(nas_ip)
                            status_emoji = "🟢 ONLINE" if status == "online" else "🔴 OFFLINE"
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ <b>Hasil Ping Ulang:</b>\nIP: <code>{nas_ip}</code>\nStatus: <b>{status_emoji}</b>",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                        elif cb_data.startswith('list_wifi_clients:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⏳ <b>Menghubungi ONT untuk mengambil daftar perangkat Wi-Fi terbaru...</b>\nMohon tunggu beberapa detik...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            # Jalankan list client di thread terpisah agar tidak memblokir polling loop
                            threading.Thread(target=_wlan_clients_worker, args=(cb_chat_id, username, token), daemon=True).start()
                            
                        elif cb_data.startswith('scan_wifi:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"📡 <b>Memulai Wi-Fi Analyzer untuk pelanggan:</b> <code>{username}</code>...\nProses ini memerlukan komunikasi langsung dengan ONT dan memakan waktu sekitar 10-15 detik. Mohon tunggu...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            # Jalankan scan Wi-Fi di thread terpisah agar tidak memblokir polling loop
                            threading.Thread(target=_wifi_scan_worker, args=(cb_chat_id, username, token), daemon=True).start()
                            
                        elif cb_data.startswith('set_channel:'):
                            parts = cb_data.split(':')
                            username = parts[1]
                            wlan_instance = parts[2]
                            channel = parts[3]
                            
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⏳ Sedang menerapkan Channel <code>{channel}</code> ke ONT WLAN {wlan_instance} via CWMP...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            # Jalankan set channel di thread terpisah agar tidak memblokir
                            def _set_channel_worker(chat_id, username, instance, ch, token):
                                try:
                                    # Cari device info
                                    from services.genieacs import get_genieacs_device_info, set_wifi_channel
                                    acs_data = get_genieacs_device_info(username)
                                    if not acs_data.get('found'):
                                        # Fallback DB
                                        from database import execute_query
                                        mapping_res = execute_query("SELECT serial_number FROM ont_serial_mapping WHERE username = %s LIMIT 1", (username,))
                                        if mapping_res:
                                            db_serial = mapping_res[0]['serial_number'].strip()
                                            acs_data = get_genieacs_device_info(db_serial)
                                            
                                    if not acs_data.get('found'):
                                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                            'chat_id': chat_id,
                                            'text': "❌ <b>Gagal:</b> ONT tidak ditemukan di GenieACS.",
                                            'parse_mode': 'HTML'
                                        })
                                        return
                                        
                                    device_id = acs_data.get('id')
                                    success, msg = set_wifi_channel(device_id, instance, ch)
                                    status_str = "✅ <b>Penerapan Channel Sukses!</b>" if success else "❌ <b>Penerapan Channel Gagal!</b>"
                                    
                                    # Tombol kembali ke cek menu
                                    import json as _json
                                    buttons = [[{"text": "🔙 Kembali", "callback_data": f"cek_back:{username}"}]]
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json={
                                        'chat_id': chat_id,
                                        'text': f"{status_str}\n\n• Detail: {msg}",
                                        'parse_mode': 'HTML',
                                        'reply_markup': {"inline_keyboard": buttons}
                                    })
                                except Exception as err:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': chat_id,
                                        'text': f"❌ <b>Error:</b> {err}",
                                        'parse_mode': 'HTML'
                                    })
                                    
                            threading.Thread(target=_set_channel_worker, args=(cb_chat_id, username, wlan_instance, channel, token), daemon=True).start()
                            
                        elif cb_data.startswith('cek_back:'):
                            username = cb_data.split(':', 1)[1]
                            handle_cek_command(cb_chat_id, username, token)
                            
                        elif cb_data.startswith('kick_session:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"⚡ Sedang memproses pemutusan sesi (CoA/Disconnect-Request) untuk pelanggan <code>{username}</code>...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            try:
                                sessions = execute_query("""
                                    SELECT acctsessionid, nasipaddress, framedipaddress FROM radacct 
                                    WHERE username = %s AND acctstoptime IS NULL
                                """, (username,))
                                
                                if not sessions:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"⚠️ <b>Tidak ada sesi aktif:</b> Pelanggan <code>{username}</code> saat ini sedang offline di RADIUS.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    coa_success = 0
                                    for s in sessions:
                                        nas = execute_query("SELECT secret FROM nas WHERE nasname = %s", (s['nasipaddress'],))
                                        secret = nas[0]['secret'] if nas else 'radiussecret'
                                        if send_coa_disconnect(s['nasipaddress'], s['acctsessionid'], secret, framed_ip=s.get('framedipaddress')):
                                            coa_success += 1
                                        execute_query("""
                                            UPDATE radacct 
                                            SET acctstoptime = NOW(), acctterminatecause = 'Admin-Reset'
                                            WHERE acctsessionid = %s
                                        """, (s['acctsessionid'],), fetch=False)
                                    
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"✅ <b>Sesi Pelanggan Berhasil Diputus!</b>\n• Username: <code>{username}</code>\n• Sesi Aktif: <b>{len(sessions)}</b> (MikroTik CoA: {coa_success} terkirim).\n\nPelanggan akan dial-up ulang secara otomatis.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                            except Exception as ex:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"❌ <b>Gagal Kick Sesi:</b> {ex}",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                                
                        elif cb_data.startswith('reboot_ont:'):
                            username = cb_data.split(':', 1)[1]
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"🔄 Sedang me-reboot ONT untuk user <code>{username}</code> via CWMP (GenieACS)...",
                                'parse_mode': 'HTML'
                            }, timeout=5)
                            
                            try:
                                config = get_genieacs_config()
                                GENIEACS_NBI = config['url']
                                auth = config['auth']
                                import json as _json
                                query = _json.dumps({
                                    "$or": [
                                        {"VirtualParameters.pppoeUsername._value": username},
                                        {"VirtualParameters.pppoeUsername2._value": username},
                                        {"_deviceId._SerialNumber": username},
                                        {"VirtualParameters.getSerialNumber._value": username}
                                    ]
                                })
                                resp_acs = requests.get(
                                    f'{GENIEACS_NBI}/devices',
                                    params={'query': query, 'projection': '_id'},
                                    auth=auth,
                                    timeout=3
                                )
                                devices = resp_acs.json()
                                if not devices:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    d = devices[0]
                                    device_id = d.get('_id')
                                    if not device_id:
                                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                            'chat_id': cb_chat_id,
                                            'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid di GenieACS.",
                                            'parse_mode': 'HTML'
                                        }, timeout=5)
                                    else:
                                        import urllib.parse
                                        reboot_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
                                        try:
                                            resp_reboot = requests.post(reboot_url, json={"name": "reboot"}, auth=auth, timeout=15)
                                            if resp_reboot.status_code in (200, 201, 202):
                                                try:
                                                    from services.genieacs import trigger_connection_request_by_device_id
                                                    trigger_connection_request_by_device_id(device_id)
                                                except:
                                                    pass
                                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                    'chat_id': cb_chat_id,
                                                    'text': f"🚀 <b>Perintah Reboot Terkirim!</b>\nONT untuk user <code>{username}</code> berhasil di-reboot via CWMP.",
                                                    'parse_mode': 'HTML'
                                                }, timeout=5)
                                            else:
                                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                    'chat_id': cb_chat_id,
                                                    'text': f"❌ <b>Gagal:</b> Respon GenieACS API: {resp_reboot.status_code}",
                                                    'parse_mode': 'HTML'
                                                }, timeout=5)
                                        except requests.exceptions.Timeout:
                                            try:
                                                from services.genieacs import trigger_connection_request_by_device_id
                                                trigger_connection_request_by_device_id(device_id)
                                            except:
                                                pass
                                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                                'chat_id': cb_chat_id,
                                                'text': f"⏳ <b>Koneksi Lambat:</b> Hubungan langsung ke ONT <code>{username}</code> melampaui batas waktu, namun <b>perintah reboot telah masuk antrean</b> di GenieACS dan akan dijalankan otomatis saat ONT terhubung.",
                                                'parse_mode': 'HTML'
                                            }, timeout=5)
                            except Exception as ex:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"❌ <b>Error:</b> {ex}",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                                
                        elif cb_data.startswith('prov_menu:'):
                            username = cb_data.split(':', 1)[1]
                            _prov_states[cb_chat_id] = {
                                'state': 'CHOOSE_TYPE',
                                'username': username,
                                'selected_binds': [],
                                'priority': 0
                            }
                            
                            buttons = [
                                [{"text": "🌐 WAN PPPoE Route", "callback_data": "prov_type:ppp_routed"}],
                                [{"text": "🔗 WAN PPPoE Bridge", "callback_data": "prov_type:ppp_bridged"}],
                                [{"text": "🔌 WAN IP Bridge", "callback_data": "prov_type:ip_bridged"}],
                                [{"text": "📡 WAN IPoE Route", "callback_data": "prov_type:ip_routed"}],
                                [{"text": "📡 SSID / Wi-Fi Config", "callback_data": "prov_type:wifi"}],
                                [{"text": "✏️ Edit WAN Terbentuk", "callback_data": "prov_wan_list:edit"}],
                                [{"text": "🗑️ Hapus WAN Terbentuk", "callback_data": "prov_wan_list:delete"}],
                                [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                            ]
                            import json as _json
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': f"🛠️ <b>Menu Provisioning untuk pelanggan:</b> <code>{username}</code>\n\nSilakan pilih tipe konfigurasi:",
                                'parse_mode': 'HTML',
                                'reply_markup': _json.dumps({"inline_keyboard": buttons})
                            }, timeout=5)
                            
                        elif cb_data.startswith('prov_type:'):
                            wtype = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['wan_type'] = wtype
                                if wtype == 'wifi':
                                    state_data['state'] = 'CHOOSE_WIFI_SLOT'
                                    buttons = [
                                        [{"text": "SSID 1 (2.4G)", "callback_data": "prov_wifislot:1"}, {"text": "SSID 2", "callback_data": "prov_wifislot:2"}],
                                        [{"text": "SSID 3", "callback_data": "prov_wifislot:3"}, {"text": "SSID 4", "callback_data": "prov_wifislot:4"}],
                                        [{"text": "SSID 5 (5G)", "callback_data": "prov_wifislot:5"}, {"text": "SSID 6", "callback_data": "prov_wifislot:6"}],
                                        [{"text": "SSID 7", "callback_data": "prov_wifislot:7"}, {"text": "SSID 8", "callback_data": "prov_wifislot:8"}],
                                        [{"text": "🔙 Kembali", "callback_data": f"prov_menu:{state_data['username']}"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "📡 <b>Pilih SSID Slot (1 - 8):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    }, timeout=5)
                                else:
                                    state_data['state'] = 'CHOOSE_SLOT'
                                    buttons = [
                                        [{"text": "Wan 1", "callback_data": "prov_slot:1"}, {"text": "Wan 2", "callback_data": "prov_slot:2"}],
                                        [{"text": "Wan 3", "callback_data": "prov_slot:3"}, {"text": "Wan 4", "callback_data": "prov_slot:4"}],
                                        [{"text": "🔙 Kembali", "callback_data": f"prov_menu:{state_data['username']}"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🌐 <b>Pilih Slot WAN (1 - 4):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    }, timeout=5)
                                    
                        elif cb_data.startswith('prov_slot:'):
                            slot = int(cb_data.split(':', 1)[1])
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['slot'] = slot
                                state_data['state'] = 'CHOOSE_SERVICE_LIST'
                                
                                if state_data['wan_type'] == 'ppp_routed':
                                    buttons = [
                                        [{"text": "INTERNET", "callback_data": "prov_svclist:INTERNET"}],
                                        [{"text": "TR069,INTERNET (FiberHome)", "callback_data": "prov_svclist:TR069,INTERNET"}],
                                        [{"text": "🔙 Kembali", "callback_data": "prov_back_to_type"}]
                                    ]
                                else:
                                    buttons = [
                                        [{"text": "INTERNET", "callback_data": "prov_svclist:INTERNET"}],
                                        [{"text": "OTHER", "callback_data": "prov_svclist:OTHER"}],
                                        [{"text": "🔙 Kembali", "callback_data": "prov_back_to_type"}]
                                    ]
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "🌐 <b>Pilih Service List:</b>",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                }, timeout=5)

                        elif cb_data.startswith('prov_svclist:'):
                            svclist = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['service_list'] = svclist
                                if state_data['wan_type'] == 'ppp_routed':
                                    state_data['state'] = 'ASK_PPPOE_USER'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim Username PPPoE</b> pelanggan:",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                elif state_data['wan_type'] == 'ip_routed':
                                    state_data['state'] = 'CHOOSE_IPOE_ADDR'
                                    buttons = [
                                        [{"text": "DHCP Client", "callback_data": "prov_addr:DHCP"}],
                                        [{"text": "Static IP", "callback_data": "prov_addr:Static"}],
                                        [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🔌 <b>Pilih Mode Pengalamatan IP (Addressing Type):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    }, timeout=5)
                                else:
                                    # Bridge modes bypass IP/NAT, go to VLAN
                                    state_data['state'] = 'CHOOSE_VLAN'
                                    buttons = [
                                        [{"text": "Disable (Tanpa VLAN)", "callback_data": "prov_vlan:disable"}],
                                        [{"text": "Enable (Pakai VLAN)", "callback_data": "prov_vlan:enable"}],
                                        [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🏷️ <b>Apakah ingin menggunakan VLAN Tag?</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    }, timeout=5)

                        elif cb_data.startswith('prov_addr:'):
                            addr = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['ipoe_addr'] = addr
                                if addr == 'Static':
                                    state_data['state'] = 'ASK_STATIC_IP'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim IP Address</b> (contoh: <code>10.51.0.2</code>):",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    state_data['state'] = 'CHOOSE_NAT'
                                    buttons = [
                                        [{"text": "NAT Enabled (Default)", "callback_data": "prov_nat:enable"}],
                                        [{"text": "NAT Disabled", "callback_data": "prov_nat:disable"}],
                                        [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "⚙️ <b>NAT (Network Address Translation):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    }, timeout=5)

                        elif cb_data.startswith('prov_nat:'):
                            nat = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['nat_enabled'] = (nat == 'enable')
                                state_data['state'] = 'CHOOSE_VLAN'
                                buttons = [
                                    [{"text": "Disable (Tanpa VLAN)", "callback_data": "prov_vlan:disable"}],
                                    [{"text": "Enable (Pakai VLAN)", "callback_data": "prov_vlan:enable"}],
                                    [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                ]
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "🏷️ <b>Apakah ingin menggunakan VLAN Tag?</b>",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                }, timeout=5)

                        elif cb_data.startswith('prov_vlan:'):
                            vlan = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                if vlan == 'enable':
                                    state_data['vlan_enabled'] = True
                                    state_data['state'] = 'ASK_VLAN_ID'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim VLAN ID (1 - 4094):</b>",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)
                                else:
                                    state_data['vlan_enabled'] = False
                                    state_data['vlan_id'] = 0
                                    state_data['priority'] = 0
                                    state_data['state'] = 'CHOOSE_BINDING'
                                    state_data['selected_binds'] = []
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🔗 <b>Pilih Binding LAN/WLAN Interface (Bisa multi-select):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": make_binding_keyboard([])})
                                    }, timeout=5)

                        elif cb_data.startswith('prov_priority:'):
                            val = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['priority'] = int(val)
                                state_data['state'] = 'CHOOSE_BINDING'
                                state_data['selected_binds'] = []
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "🔗 <b>Pilih Binding LAN/WLAN Interface (Bisa multi-select):</b>",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": make_binding_keyboard([])})
                                }, timeout=5)

                        elif cb_data.startswith('prov_toggle_bind:'):
                            iface = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                binds = state_data.get('selected_binds', [])
                                if iface in binds:
                                    binds.remove(iface)
                                else:
                                    binds.append(iface)
                                state_data['selected_binds'] = binds
                                
                                import json as _json
                                try:
                                    requests.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", json={
                                        'chat_id': cb_chat_id,
                                        'message_id': cb.get('message', {}).get('message_id'),
                                        'reply_markup': {"inline_keyboard": make_binding_keyboard(binds)}
                                    }, timeout=3)
                                except:
                                    pass

                        elif cb_data == 'prov_bind_done':
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['state'] = 'ASK_F670L'
                                buttons = [
                                    [{"text": "Standard (Bukan F670L)", "callback_data": "prov_f670l:no"}],
                                    [{"text": "ZTE F670L", "callback_data": "prov_f670l:yes"}],
                                    [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                ]
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "⚙️ <b>Apakah tipe perangkat adalah ZTE F670L?</b>\n(ZTE F670L membutuhkan binding Wan Interface juga)",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                }, timeout=5)

                        elif cb_data.startswith('prov_f670l:'):
                            val_f670l = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['is_f670l'] = (val_f670l == 'yes')
                                show_prov_confirm(cb_chat_id, token)

                        elif cb_data.startswith('prov_wifislot:'):
                            wifislot = int(cb_data.split(':', 1)[1])
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['wifi_slot'] = wifislot
                                state_data['state'] = 'ASK_WIFI_SSID'
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "✍️ <b>Ketik/Kirim Nama SSID (Wi-Fi Name)</b>:",
                                    'parse_mode': 'HTML'
                                }, timeout=5)

                        elif cb_data.startswith('prov_wifisec:'):
                            wifisec = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['wifi_sec'] = wifisec
                                if wifisec == 'None':
                                    show_prov_confirm(cb_chat_id, token)
                                else:
                                    state_data['state'] = 'ASK_WIFI_KEY'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🔑 <b>Ketik/Kirim Password Wi-Fi</b> (min 8 karakter):",
                                        'parse_mode': 'HTML'
                                    }, timeout=5)

                        elif cb_data == 'prov_back_to_type':
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                buttons = [
                                    [{"text": "🌐 WAN PPPoE Route", "callback_data": "prov_type:ppp_routed"}],
                                    [{"text": "🔗 WAN PPPoE Bridge", "callback_data": "prov_type:ppp_bridged"}],
                                    [{"text": "🔌 WAN IP Bridge", "callback_data": "prov_type:ip_bridged"}],
                                    [{"text": "📡 WAN IPoE Route", "callback_data": "prov_type:ip_routed"}],
                                    [{"text": "📡 SSID / Wi-Fi Config", "callback_data": "prov_type:wifi"}],
                                    [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                ]
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "Silakan pilih tipe konfigurasi:",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                }, timeout=5)

                        elif cb_data.startswith('prov_wan_list:'):
                            action = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                username = state_data['username']
                                config = get_genieacs_config()
                                GENIEACS_NBI = config['url']
                                auth = config['auth']
                                import json as _json
                                query = _json.dumps({
                                    "$or": [
                                        {"VirtualParameters.pppoeUsername._value": username},
                                        {"VirtualParameters.pppoeUsername2._value": username},
                                        {"_deviceId._SerialNumber": username},
                                        {"VirtualParameters.getSerialNumber._value": username}
                                    ]
                                })
                                resp_acs = requests.get(
                                    f'{GENIEACS_NBI}/devices',
                                    params={'query': query},
                                    auth=auth,
                                    timeout=5
                                )
                                devices = resp_acs.json() if resp_acs.status_code == 200 else []
                                if not devices:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "❌ Perangkat ONT tidak ditemukan di GenieACS.",
                                        'parse_mode': 'HTML'
                                    })
                                    continue
                                
                                device_doc = devices[0]
                                state_data['device_id'] = device_doc.get('_id')
                                
                                wans = []
                                wcd = device_doc.get("InternetGatewayDevice", {}).get("WANDevice", {}).get("1", {}).get("WANConnectionDevice", {})
                                for wcd_idx, wcd_val in wcd.items():
                                    if not wcd_idx.isdigit():
                                        continue
                                    ppp = wcd_val.get("WANPPPConnection", {})
                                    for p_idx, p_val in ppp.items():
                                        if not p_idx.isdigit():
                                            continue
                                        name = p_val.get("Name", {}).get("_value", "")
                                        user = p_val.get("Username", {}).get("_value", "")
                                        desc = f"Wan {wcd_idx if int(wcd_idx) > 1 else p_idx} (PPPoE {user if user else name})"
                                        wans.append({
                                            'desc': desc,
                                            'wcd_idx': wcd_idx,
                                            'conn_type': 'PPP',
                                            'conn_idx': p_idx
                                        })
                                    ip_conn = wcd_val.get("WANIPConnection", {})
                                    for i_idx, i_val in ip_conn.items():
                                        if not i_idx.isdigit():
                                            continue
                                        name = i_val.get("Name", {}).get("_value", "")
                                        addr_type = i_val.get("AddressingType", {}).get("_value", "DHCP")
                                        desc = f"Wan {wcd_idx if int(wcd_idx) > 1 else i_idx} (IPoe {addr_type} {name})"
                                        wans.append({
                                            'desc': desc,
                                            'wcd_idx': wcd_idx,
                                            'conn_type': 'IP',
                                            'conn_idx': i_idx
                                        })
                                        
                                if not wans:
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "⚠️ <b>Tidak ada koneksi WAN yang terbentuk pada ONT ini.</b>",
                                        'parse_mode': 'HTML'
                                    })
                                    continue
                                    
                                buttons = []
                                for wan in wans:
                                    cb_val = f"wan_sel:{action}:{wan['wcd_idx']}:{wan['conn_type']}:{wan['conn_idx']}"
                                    buttons.append([{"text": wan['desc'], "callback_data": cb_val}])
                                buttons.append([{"text": "🔙 Kembali", "callback_data": f"prov_menu:{username}"}])
                                
                                action_text = "mengedit" if action == "edit" else "menghapus"
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"🔍 <b>Pilih koneksi WAN yang ingin di-{action_text}:</b>",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                })

                        elif cb_data.startswith('wan_sel:'):
                            parts = cb_data.split(':')
                            action = parts[1]
                            wcd_idx = parts[2]
                            conn_type = parts[3]
                            conn_idx = parts[4]
                            
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                if action == 'delete':
                                    device_id = state_data.get('device_id')
                                    success, msg = execute_delete_wan(device_id, wcd_idx, conn_type, conn_idx)
                                    status_str = "✅ <b>Penghapusan WAN Sukses!</b>" if success else "❌ <b>Penghapusan WAN Gagal!</b>"
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': f"{status_str}\n\n• Detail: {msg}",
                                        'parse_mode': 'HTML'
                                    })
                                    _prov_states.pop(cb_chat_id, None)
                                else:
                                    username = state_data['username']
                                    config = get_genieacs_config()
                                    GENIEACS_NBI = config['url']
                                    auth = config['auth']
                                    import json as _json
                                    query = _json.dumps({
                                        "$or": [
                                            {"VirtualParameters.pppoeUsername._value": username},
                                            {"VirtualParameters.pppoeUsername2._value": username},
                                            {"_deviceId._SerialNumber": username},
                                            {"VirtualParameters.getSerialNumber._value": username}
                                        ]
                                    })
                                    resp_acs = requests.get(
                                        f'{GENIEACS_NBI}/devices',
                                        params={'query': query},
                                        auth=auth,
                                        timeout=5
                                    )
                                    if resp_acs.status_code == 200 and resp_acs.json():
                                        device_doc = resp_acs.json()[0]
                                        populate_state_from_wan(state_data, device_doc, wcd_idx, conn_type, conn_idx)
                                        show_edit_menu(cb_chat_id, token)
                                    else:
                                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                            'chat_id': cb_chat_id,
                                            'text': "❌ Gagal memuat detail WAN dari GenieACS.",
                                            'parse_mode': 'HTML'
                                        })

                        elif cb_data.startswith('edit_field:'):
                            field = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                if field == 'pppoe':
                                    state_data['state'] = 'EDIT_ASK_PPPOE_USER'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim PPPoE Username baru:</b>",
                                        'parse_mode': 'HTML'
                                    })
                                elif field == 'static_ip':
                                    state_data['state'] = 'EDIT_ASK_STATIC_IP'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim IP Address baru:</b>",
                                        'parse_mode': 'HTML'
                                    })
                                elif field == 'vlan':
                                    state_data['state'] = 'EDIT_CHOOSE_VLAN'
                                    buttons = [
                                        [{"text": "Disable (Tanpa VLAN)", "callback_data": "edit_vlan:disable"}],
                                        [{"text": "Enable (Pakai VLAN)", "callback_data": "edit_vlan:enable"}],
                                        [{"text": "🔙 Kembali", "callback_data": "edit_back_to_menu"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "⚙️ <b>Pilih status VLAN Tagging:</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    })
                                elif field == 'priority':
                                    state_data['state'] = 'EDIT_CHOOSE_PRIORITY'
                                    buttons = [
                                        [{"text": "0", "callback_data": "edit_priority:0"}, {"text": "1", "callback_data": "edit_priority:1"}],
                                        [{"text": "2", "callback_data": "edit_priority:2"}, {"text": "3", "callback_data": "edit_priority:3"}],
                                        [{"text": "4", "callback_data": "edit_priority:4"}, {"text": "5", "callback_data": "edit_priority:5"}],
                                        [{"text": "6", "callback_data": "edit_priority:6"}, {"text": "7", "callback_data": "edit_priority:7"}],
                                        [{"text": "🔙 Kembali", "callback_data": "edit_back_to_menu"}]
                                    ]
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "⚙️ <b>Pilih Priority WAN Baru (0 - 7):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                    })
                                elif field == 'bind':
                                    state_data['state'] = 'EDIT_CHOOSE_BINDING'
                                    import json as _json
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "🔗 <b>Pilih Binding LAN/WLAN Interface baru (Bisa multi-select):</b>",
                                        'parse_mode': 'HTML',
                                        'reply_markup': _json.dumps({"inline_keyboard": make_binding_keyboard(state_data.get('selected_binds', []), prefix='edit')})
                                    })

                        elif cb_data.startswith('edit_vlan:'):
                            val = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                if val == 'disable':
                                    state_data['vlan_enabled'] = False
                                    state_data['vlan_id'] = 0
                                    state_data['priority'] = 0
                                    show_edit_menu(cb_chat_id, token)
                                else:
                                    state_data['state'] = 'EDIT_ASK_VLAN_ID'
                                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                        'chat_id': cb_chat_id,
                                        'text': "✍️ <b>Ketik/Kirim VLAN ID baru (1-4094):</b>",
                                        'parse_mode': 'HTML'
                                    })

                        elif cb_data.startswith('edit_priority:'):
                            val = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                state_data['priority'] = int(val)
                                show_edit_menu(cb_chat_id, token)

                        elif cb_data == 'edit_back_to_menu':
                            show_edit_menu(cb_chat_id, token)

                        elif cb_data.startswith('edit_toggle_bind:'):
                            interface = cb_data.split(':', 1)[1]
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                binds = state_data.get('selected_binds', [])
                                if interface in binds:
                                    binds.remove(interface)
                                else:
                                    binds.append(interface)
                                state_data['selected_binds'] = binds
                                
                                import json as _json
                                try:
                                    requests.post(f"https://api.telegram.org/bot{token}/editMessageReplyMarkup", json={
                                        'chat_id': cb_chat_id,
                                        'message_id': cb.get('message', {}).get('message_id'),
                                        'reply_markup': {"inline_keyboard": make_binding_keyboard(binds, prefix='edit')}
                                    }, timeout=3)
                                except:
                                    pass

                        elif cb_data == 'edit_bind_done':
                            show_edit_menu(cb_chat_id, token)

                        elif cb_data == 'prov_cancel':
                            _prov_states.pop(cb_chat_id, None)
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': cb_chat_id,
                                'text': "❌ <b>Provisioning Dibatalkan!</b>",
                                'parse_mode': 'HTML'
                            }, timeout=5)

                        elif cb_data == 'prov_push':
                            state_data = _prov_states.get(cb_chat_id)
                            if state_data:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': "⏳ <b>Sedang mengirimkan konfigurasi ke GenieACS...</b>",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                                
                                success, msg = execute_prov_push(state_data)
                                _prov_states.pop(cb_chat_id, None)
                                
                                status_str = "✅ <b>Provisioning Sukses!</b>" if success else "❌ <b>Provisioning Gagal!</b>"
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': cb_chat_id,
                                    'text': f"{status_str}\n\n• Detail: {msg}",
                                    'parse_mode': 'HTML'
                                }, timeout=5)
                    continue
                    
                msg_obj = update.get('message', {})
                text = msg_obj.get('text', '').strip()
                chat_id = msg_obj.get('chat', {}).get('id')
                
                if not text or not chat_id:
                    continue

                # Access control: only allow configured admin
                if admin_chat_id and str(chat_id) != str(admin_chat_id):
                    continue

                # If they send a command starting with /, clear any active state
                if text.startswith('/'):
                    _prov_states.pop(chat_id, None)

                # Intercept text message if in a provisioning state or waiting for query username
                state_data = _prov_states.get(chat_id)
                is_override = False
                cmd = None
                arg = ""
                
                if state_data and not text.startswith('/'):
                    state = state_data.get('state')
                    if state == 'ASK_CHECK_USER':
                        _prov_states.pop(chat_id, None)
                        cmd = '/cek'
                        arg = text
                        is_override = True
                    elif state == 'ASK_PROV_USER':
                        _prov_states.pop(chat_id, None)
                        cmd = '/provision'
                        arg = text
                        is_override = True
                    elif state == 'ASK_REBOOT_USER':
                        _prov_states.pop(chat_id, None)
                        cmd = '/reboot_ont'
                        arg = text
                        is_override = True
                        
                if state_data and not text.startswith('/') and not is_override:
                    state = state_data.get('state')
                    if state == 'EDIT_ASK_PPPOE_USER':
                        state_data['pppoe_user'] = text
                        state_data['state'] = 'EDIT_ASK_PPPOE_PASS'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🔑 <b>Ketik/Kirim PPPoE Password baru:</b>",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'EDIT_ASK_PPPOE_PASS':
                        state_data['pppoe_pass'] = text
                        show_edit_menu(chat_id, token)
                    elif state == 'EDIT_ASK_STATIC_IP':
                        state_data['static_ip'] = text
                        state_data['state'] = 'EDIT_ASK_STATIC_MASK'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim Subnet Mask baru:</b>",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'EDIT_ASK_STATIC_MASK':
                        state_data['static_mask'] = text
                        state_data['state'] = 'EDIT_ASK_STATIC_GW'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim Default Gateway baru:</b>",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'EDIT_ASK_STATIC_GW':
                        state_data['static_gw'] = text
                        state_data['state'] = 'EDIT_ASK_STATIC_DNS'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim DNS Server baru:</b>",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'EDIT_ASK_STATIC_DNS':
                        state_data['static_dns'] = text
                        show_edit_menu(chat_id, token)
                    elif state == 'EDIT_ASK_VLAN_ID':
                        try:
                            vlan_id = int(text)
                            if 1 <= vlan_id <= 4094:
                                state_data['vlan_id'] = vlan_id
                                state_data['vlan_enabled'] = True
                                show_edit_menu(chat_id, token)
                            else:
                                raise ValueError()
                        except ValueError:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': "⚠️ VLAN ID tidak valid. Harap masukkan angka antara 1 dan 4094:",
                                'parse_mode': 'HTML'
                            })
                    elif state == 'ASK_PPPOE_USER':
                        state_data['pppoe_user'] = text
                        state_data['state'] = 'ASK_PPPOE_PASS'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🔑 <b>Ketik/Kirim Password PPPoE</b> pelanggan:",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'ASK_PPPOE_PASS':
                        state_data['pppoe_pass'] = text
                        state_data['state'] = 'CHOOSE_NAT'
                        buttons = [
                            [{"text": "NAT Enabled (Default)", "callback_data": "prov_nat:enable"}],
                            [{"text": "NAT Disabled", "callback_data": "prov_nat:disable"}],
                            [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                        ]
                        import json as _json
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "⚙️ <b>NAT (Network Address Translation):</b>",
                            'parse_mode': 'HTML',
                            'reply_markup': _json.dumps({"inline_keyboard": buttons})
                        })
                    elif state == 'ASK_STATIC_IP':
                        state_data['static_ip'] = text
                        state_data['state'] = 'ASK_STATIC_MASK'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim Subnet Mask</b> (contoh: <code>255.255.255.0</code>):",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'ASK_STATIC_MASK':
                        state_data['static_mask'] = text
                        state_data['state'] = 'ASK_STATIC_GW'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim Default Gateway</b> (contoh: <code>10.51.0.1</code>):",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'ASK_STATIC_GW':
                        state_data['static_gw'] = text
                        state_data['state'] = 'ASK_STATIC_DNS'
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "✍️ <b>Ketik/Kirim DNS Server</b> (contoh: <code>8.8.8.8</code> atau <code>8.8.8.8,8.8.4.4</code>):",
                            'parse_mode': 'HTML'
                        })
                    elif state == 'ASK_STATIC_DNS':
                        state_data['static_dns'] = text
                        state_data['state'] = 'CHOOSE_NAT'
                        buttons = [
                            [{"text": "NAT Enabled (Default)", "callback_data": "prov_nat:enable"}],
                            [{"text": "NAT Disabled", "callback_data": "prov_nat:disable"}],
                            [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                        ]
                        import json as _json
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "⚙️ <b>NAT (Network Address Translation):</b>",
                            'parse_mode': 'HTML',
                            'reply_markup': _json.dumps({"inline_keyboard": buttons})
                        })
                    elif state == 'ASK_VLAN_ID':
                        try:
                            vlan_id = int(text)
                            if 1 <= vlan_id <= 4094:
                                state_data['vlan_id'] = vlan_id
                                state_data['state'] = 'CHOOSE_PRIORITY'
                                buttons = [
                                    [{"text": "0", "callback_data": "prov_priority:0"}, {"text": "1", "callback_data": "prov_priority:1"}],
                                    [{"text": "2", "callback_data": "prov_priority:2"}, {"text": "3", "callback_data": "prov_priority:3"}],
                                    [{"text": "4", "callback_data": "prov_priority:4"}, {"text": "5", "callback_data": "prov_priority:5"}],
                                    [{"text": "6", "callback_data": "prov_priority:6"}, {"text": "7", "callback_data": "prov_priority:7"}],
                                    [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                                ]
                                import json as _json
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': chat_id,
                                    'text': "⚙️ <b>Pilih Priority WAN (0 - 7):</b>\n(Default: 0 atau 1)",
                                    'parse_mode': 'HTML',
                                    'reply_markup': _json.dumps({"inline_keyboard": buttons})
                                })
                            else:
                                raise ValueError()
                        except ValueError:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': "⚠️ VLAN ID tidak valid. Harap masukkan angka antara 1 dan 4094:",
                                'parse_mode': 'HTML'
                            })
                    elif state == 'ASK_WIFI_SSID':
                        state_data['wifi_ssid'] = text
                        state_data['state'] = 'CHOOSE_WIFI_SEC'
                        buttons = [
                            [{"text": "None (Tanpa Password)", "callback_data": "prov_wifisec:None"}],
                            [{"text": "11i (WPA2-PSK)", "callback_data": "prov_wifisec:11i"}],
                            [{"text": "WPAand11i (WPA/WPA2-PSK)", "callback_data": "prov_wifisec:WPAand11i"}],
                            [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                        ]
                        import json as _json
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🔑 <b>Pilih Mode Keamanan Wi-Fi</b>:",
                            'parse_mode': 'HTML',
                            'reply_markup': _json.dumps({"inline_keyboard": buttons})
                        })
                    elif state == 'ASK_WIFI_KEY':
                        if len(text) < 8:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': "⚠️ Password Wi-Fi minimal harus 8 karakter. Silakan ketik ulang:",
                                'parse_mode': 'HTML'
                            })
                        else:
                            state_data['wifi_key'] = text
                            show_prov_confirm(chat_id, token)
                    continue

                # Hanya layani perintah yang dimulai dengan / atau cmd yang dioverride
                if not is_override and not text.startswith('/'):
                    continue
                    
                if not is_override:
                    parts = text.split(None, 1)
                    cmd = parts[0].lower()
                    arg = parts[1].strip() if len(parts) > 1 else ""
                
                # Perintah /start atau /help
                if cmd in ('/start', '/help'):
                    help_msg = (
                        "🤖 <b>RADIUS & ACS Telegram Bot</b>\n"
                        "Layanan interaktif untuk Teknisi / Admin:\n\n"
                        "➡️ <b>/cek [username/SN]</b> : Cek status online, IP, MAC, dan Redaman ONT.\n"
                        "➡️ <b>/reboot_ont [username/SN]</b> : Reboot ONT pelanggan secara remote via CWMP.\n"
                        "➡️ <b>/provision [username/SN]</b> : Provisi konfigurasi WAN / Wi-Fi ONT pelanggan.\n"
                        "➡️ <b>/nas</b> : Cek status kesehatan seluruh router NAS MikroTik."
                    )
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                        'chat_id': chat_id,
                        'text': help_msg,
                        'parse_mode': 'HTML'
                    })
                    continue
                
                # Perintah /nas
                if cmd == '/nas':
                    try:
                        nas_list = execute_query("SELECT nasname, shortname FROM nas ORDER BY id")
                        if not nas_list:
                            nas_msg = "🖥️ Tidak ada router NAS yang terdaftar."
                        else:
                            nas_msg = "🖥️ <b>Status Kesehatan NAS Router:</b>\n\n"
                            for nas in nas_list:
                                ip = nas['nasname']
                                name = nas['shortname'] or ip
                                is_online = (ping_nas(ip) == "online")
                                status_emoji = "🟢 ONLINE" if is_online else "🔴 OFFLINE"
                                nas_msg += f"• <b>{name}</b> ({ip}) : {status_emoji}\n"
                        
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': nas_msg,
                            'parse_mode': 'HTML'
                        })
                    except Exception as e:
                        print(f"Error /nas Telegram command: {e}")
                    continue
                
                # Perintah /cek [username]
                if cmd == '/cek':
                    if not arg:
                        _prov_states[chat_id] = {'state': 'ASK_CHECK_USER'}
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🔍 <b>Silakan ketik/kirim Username pelanggan atau Serial Number (SN) ONT yang ingin dicek:</b>",
                            'parse_mode': 'HTML'
                        })
                        continue
                        
                    username = arg.strip()
                    handle_cek_command(chat_id, username, token)
                    continue
                    
                # Perintah /provision [username]
                if cmd == '/provision':
                    if not arg:
                        _prov_states[chat_id] = {'state': 'ASK_PROV_USER'}
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🛠️ <b>Silakan ketik/kirim Username pelanggan atau Serial Number (SN) ONT yang ingin diprovisi:</b>",
                            'parse_mode': 'HTML'
                        })
                        continue
                        
                    username = arg.strip()
                    _prov_states[chat_id] = {
                        'state': 'CHOOSE_TYPE',
                        'username': username,
                        'selected_binds': [],
                        'priority': 0
                    }
                    
                    buttons = [
                        [{"text": "🌐 WAN PPPoE Route", "callback_data": "prov_type:ppp_routed"}],
                        [{"text": "🔗 WAN PPPoE Bridge", "callback_data": "prov_type:ppp_bridged"}],
                        [{"text": "🔌 WAN IP Bridge", "callback_data": "prov_type:ip_bridged"}],
                        [{"text": "📡 WAN IPoE Route", "callback_data": "prov_type:ip_routed"}],
                        [{"text": "📡 SSID / Wi-Fi Config", "callback_data": "prov_type:wifi"}],
                        [{"text": "✏️ Edit WAN Terbentuk", "callback_data": "prov_wan_list:edit"}],
                        [{"text": "🗑️ Hapus WAN Terbentuk", "callback_data": "prov_wan_list:delete"}],
                        [{"text": "❌ Batal", "callback_data": "prov_cancel"}]
                    ]
                    import json as _json
                    requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                        'chat_id': chat_id,
                        'text': f"🛠️ <b>Menu Provisioning untuk pelanggan:</b> <code>{username}</code>\n\nSilakan pilih tipe konfigurasi:",
                        'parse_mode': 'HTML',
                        'reply_markup': _json.dumps({"inline_keyboard": buttons})
                    })
                    continue

                # Perintah /reboot_ont [username]
                if cmd == '/reboot_ont':
                    if not arg:
                        _prov_states[chat_id] = {'state': 'ASK_REBOOT_USER'}
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': "🔄 <b>Silakan ketik/kirim Username pelanggan atau Serial Number (SN) ONT yang ingin direboot:</b>",
                            'parse_mode': 'HTML'
                        })
                        continue
                        
                    username = arg.strip()
                    
                    # 1. Cari device_id di GenieACS
                    try:
                        config = get_genieacs_config()
                        GENIEACS_NBI = config['url']
                        auth = config['auth']
                        import json as _json
                        query = _json.dumps({
                            "$or": [
                                {"VirtualParameters.pppoeUsername._value": username},
                                {"VirtualParameters.pppoeUsername2._value": username},
                                {"_deviceId._SerialNumber": username},
                                {"VirtualParameters.getSerialNumber._value": username}
                            ]
                        })
                        resp_acs = requests.get(
                            f'{GENIEACS_NBI}/devices',
                            params={'query': query, 'projection': '_id'},
                            auth=auth,
                            timeout=3
                        )
                        devices = resp_acs.json()
                        if not devices:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': f"❌ <b>Gagal:</b> ONT untuk user <code>{username}</code> tidak ditemukan di GenieACS.",
                                'parse_mode': 'HTML'
                            })
                            continue
                            
                        d = devices[0]
                        device_id = d.get('_id')
                        if not device_id:
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                
                                'chat_id': chat_id,
                                'text': f"❌ <b>Gagal:</b> ID Perangkat tidak valid di GenieACS.",
                                'parse_mode': 'HTML'
                            })
                            continue
                            
                        # 2. Kirim task reboot ke GenieACS NBI
                        import urllib.parse
                        reboot_url = f"{GENIEACS_NBI}/devices/{urllib.parse.quote(device_id)}/tasks?connection_request"
                        try:
                            resp_reboot = requests.post(reboot_url, json={"name": "reboot"}, auth=auth, timeout=15)
                            if resp_reboot.status_code in (200, 201, 202):
                                try:
                                    from services.genieacs import trigger_connection_request_by_device_id
                                    trigger_connection_request_by_device_id(device_id)
                                except:
                                    pass
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': chat_id,
                                    'text': f"🚀 <b>Perintah Reboot Terkirim!</b>\nONT untuk user <code>{username}</code> berhasil di-reboot via CWMP (GenieACS).",
                                    'parse_mode': 'HTML'
                                })
                            else:
                                requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                    'chat_id': chat_id,
                                    'text': f"❌ <b>Gagal:</b> Respon GenieACS API: {resp_reboot.status_code}",
                                    'parse_mode': 'HTML'
                                })
                        except requests.exceptions.Timeout:
                            try:
                                from services.genieacs import trigger_connection_request_by_device_id
                                trigger_connection_request_by_device_id(device_id)
                            except:
                                pass
                            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                                'chat_id': chat_id,
                                'text': f"⏳ <b>Koneksi Lambat:</b> Hubungan langsung ke ONT <code>{username}</code> melampaui batas waktu, namun <b>perintah reboot telah masuk antrean</b> di GenieACS dan akan dijalankan otomatis saat ONT terhubung.",
                                'parse_mode': 'HTML'
                            })
                    except Exception as e:
                        requests.post(f"https://api.telegram.org/bot{token}/sendMessage", data={
                            'chat_id': chat_id,
                            'text': f"❌ <b>Error:</b> {e}",
                            'parse_mode': 'HTML'
                        })
                    continue
                    
        except Exception as e:
            print(f"[telegram-bot] Error in polling loop: {e}")
            
        socketio.sleep(2)
