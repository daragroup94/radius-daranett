import subprocess
import threading

def _send_coa_packet(nas_ip, secret, packet_type, attributes):
    """Kirim CoA/Disconnect packet via radclient di freeradius container"""
    try:
        # Gunakan newline-separated attributes
        attr_lines = '\n'.join(f'{k} = "{v}"' for k, v in attributes.items())
        # Jalankan docker exec secara langsung tanpa sh -c / pipeline untuk menghindari RCE
        cmd = [
            'docker', 'exec', '-i', 'freeradius-server',
            'radclient', '-t', '3', '-r', '1', f'{nas_ip}:3799', packet_type, secret
        ]
        result = subprocess.run(
            cmd, input=f"{attr_lines}\n", capture_output=True, text=True, timeout=6
        )
        return result.stdout
    except Exception as e:
        print(f"CoA error ({nas_ip}): {e}")
        return ''

def send_coa_disconnect(nas_ip, session_id, nas_secret, framed_ip=None):
    if framed_ip:
        out = _send_coa_packet(nas_ip, nas_secret, 'disconnect', {
            'Framed-IP-Address': framed_ip, 'NAS-IP-Address': nas_ip
        })
        if 'Disconnect-ACK' in out:
            return True
    # Fallback: Acct-Session-Id (PPPoE)
    out = _send_coa_packet(nas_ip, nas_secret, 'disconnect', {
        'Acct-Session-Id': session_id, 'NAS-IP-Address': nas_ip
    })
    return 'Disconnect-ACK' in out

def send_coa_update(nas_ip, session_id, nas_secret, rate_limit):
    out = _send_coa_packet(nas_ip, nas_secret, 'coa', {
        'Acct-Session-Id': session_id, 'NAS-IP-Address': nas_ip,
        'Mikrotik-Rate-Limit': rate_limit
    })
    return 'CoA-ACK' in out

def send_coa_update_async(nas_ip, session_id, nas_secret, rate_limit):
    """Kirim CoA di background thread agar tidak block response"""
    t = threading.Thread(target=send_coa_update, args=(nas_ip, session_id, nas_secret, rate_limit), daemon=True)
    t.start()
