"""
mikrotik_queue.py
-----------------
Service untuk mengelola Queue Tree dan Firewall Mangle di MikroTik
khusus untuk IPoE users. Tidak mempengaruhi PPPoE, hotspot, VPN, dll.

Pola mangle yang dibuat (4 rules per user):
  ;;; <display_name>
  chain=prerouting  mark-connection <username>_conn_ul  src-address=<ip>
  chain=prerouting  mark-packet     <username>_ul        connection-mark=<username>_conn_ul
  chain=postrouting mark-connection <username>_conn_dl  dst-address=<ip>
  chain=postrouting mark-packet     <username>_dl        connection-mark=<username>_conn_dl

Pola queue tree yang dibuat (2 entries per user):
  name=<username>_dl  parent=<parent_dl>  packet-mark=<username>_dl  max-limit=<X>M
  name=<username>_ul  parent=<parent_ul>  packet-mark=<username>_ul  max-limit=<X>M
"""

import re


# ---------------------------------------------------------------------------
# Helper: normalisasi username → safe key untuk mangle/queue
# Sesuai pola existing: spasi → underscore, karakter non-alnum → hilang
# Contoh: "andi kuli" → "andi_kuli", "kost kartini" → "kost_kartini"
# ---------------------------------------------------------------------------
def _safe_key(username: str) -> str:
    """Ubah username/MAC menjadi safe key untuk nama mangle & queue."""
    key = username.strip().lower()
    key = re.sub(r'[\s\-:]+', '_', key)       # spasi, strip, titik dua → underscore
    key = re.sub(r'[^a-z0-9_]', '', key)       # buang karakter aneh
    key = re.sub(r'_+', '_', key).strip('_')   # collapse underscore ganda
    return key


# ---------------------------------------------------------------------------
# Helper: parse max-limit dari Mikrotik-Rate-Limit
# Format yang didukung: "10M", "10M/10M", "10240k", "10240k/10240k"
# Return: string max-limit misal "10M" atau "10240k"
# ---------------------------------------------------------------------------
def _parse_max_limit(rate_limit: str) -> str:
    """Ambil nilai download (bagian pertama) dari Mikrotik-Rate-Limit."""
    if not rate_limit:
        return '10M'
    # Format "10M/10M" atau "10240k/10240k" → ambil bagian pertama
    parts = str(rate_limit).strip().split('/')
    val = parts[0].strip()
    # Normalisasi: "10m" → "10M", "10240k" tetap "10240k"
    val = re.sub(r'(?i)m$', 'M', val)
    val = re.sub(r'(?i)k$', 'k', val)
    val = re.sub(r'(?i)g$', 'G', val)
    return val if val else '10M'


# ---------------------------------------------------------------------------
# MANGLE OPERATIONS
# ---------------------------------------------------------------------------

def _find_mangle_comment(api, comment: str):
    """Cari semua mangle rule dengan comment tertentu, return list of ids."""
    try:
        rules = list(api('/ip/firewall/mangle/print'))
        ids = [r['.id'] for r in rules if r.get('comment', '').strip() == comment]
        return ids
    except Exception:
        return []


def _find_mangle_by_conn_mark(api, conn_mark: str):
    """Cari mangle rule berdasarkan connection-mark (untuk packet mark rules)."""
    try:
        rules = list(api('/ip/firewall/mangle/print'))
        ids = [r['.id'] for r in rules if r.get('new-connection-mark') == conn_mark
               or r.get('connection-mark') == conn_mark]
        return ids
    except Exception:
        return []


def remove_mangle_rules(api, username: str) -> dict:
    """
    Hapus semua mangle rules milik user berdasarkan username.
    Aman: hanya menghapus rules yang packet-mark / connection-mark mengandung
    prefix username yang tepat — tidak akan menghapus rules user lain.
    Return: {'removed': N, 'errors': []}
    """
    key = _safe_key(username)
    removed = 0
    errors = []

    try:
        all_rules = list(api('/ip/firewall/mangle/print'))
    except Exception as e:
        return {'removed': 0, 'errors': [str(e)]}

    # Identifikasi rule milik user ini berdasarkan mark names
    target_marks = {
        f'{key}_conn_ul',
        f'{key}_conn_dl',
        f'{key}_ul',
        f'{key}_dl',
    }

    for rule in all_rules:
        rule_id = rule.get('.id')
        new_conn  = rule.get('new-connection-mark', '')
        conn_mark = rule.get('connection-mark', '')
        new_pkt   = rule.get('new-packet-mark', '')

        if any(m in target_marks for m in [new_conn, conn_mark, new_pkt]):
            try:
                api('/ip/firewall/mangle/remove', **{'.id': rule_id})
                removed += 1
            except Exception as e:
                errors.append(f'mangle {rule_id}: {e}')

    return {'removed': removed, 'errors': errors}


def create_mangle_rules(api, username: str, ip_address: str, display_name: str = None) -> dict:
    """
    Buat 4 mangle rules untuk user IPoE (sesuai pola eksisting).
    Jika rules sudah ada, hapus dulu lalu buat ulang.
    Return: {'success': bool, 'errors': []}
    """
    key = _safe_key(username)
    comment = display_name.strip() if display_name else username.strip()
    errors = []

    # Hapus rules lama terlebih dahulu (jika ada)
    remove_mangle_rules(api, username)

    rules = [
        # 1. prerouting: mark-connection upload
        {
            'chain': 'prerouting',
            'action': 'mark-connection',
            'new-connection-mark': f'{key}_conn_ul',
            'passthrough': 'yes',
            'src-address': ip_address,
            'comment': comment,
        },
        # 2. prerouting: mark-packet upload
        {
            'chain': 'prerouting',
            'action': 'mark-packet',
            'new-packet-mark': f'{key}_ul',
            'passthrough': 'no',
            'connection-mark': f'{key}_conn_ul',
        },
        # 3. postrouting: mark-connection download
        {
            'chain': 'postrouting',
            'action': 'mark-connection',
            'new-connection-mark': f'{key}_conn_dl',
            'passthrough': 'yes',
            'dst-address': ip_address,
        },
        # 4. postrouting: mark-packet download
        {
            'chain': 'postrouting',
            'action': 'mark-packet',
            'new-packet-mark': f'{key}_dl',
            'passthrough': 'no',
            'connection-mark': f'{key}_conn_dl',
        },
    ]

    for rule in rules:
        try:
            api('/ip/firewall/mangle/add', **rule)
        except Exception as e:
            errors.append(f'mangle add error: {e}')

    return {'success': len(errors) == 0, 'errors': errors}


# ---------------------------------------------------------------------------
# QUEUE TREE OPERATIONS
# ---------------------------------------------------------------------------

def _find_queue_by_name(api, name: str):
    """Cari queue tree entry berdasarkan nama. Return .id atau None."""
    try:
        queues = list(api('/queue/tree/print'))
        for q in queues:
            if q.get('name') == name:
                return q.get('.id')
    except Exception:
        pass
    return None


def remove_queue_tree(api, username: str) -> dict:
    """
    Hapus 2 queue tree entries milik user (DL dan UL).
    Aman: hanya hapus berdasarkan nama yang tepat.
    Return: {'removed': N, 'errors': []}
    """
    key = _safe_key(username)
    removed = 0
    errors = []

    for name in [f'{key}_dl', f'{key}_ul']:
        qid = _find_queue_by_name(api, name)
        if qid:
            try:
                api('/queue/tree/remove', **{'.id': qid})
                removed += 1
            except Exception as e:
                errors.append(f'queue {name}: {e}')

    return {'removed': removed, 'errors': errors}


def create_queue_tree(api, username: str, rate_limit: str,
                      parent_dl: str, parent_ul: str,
                      limit_at: str = '0', priority: int = 8) -> dict:
    """
    Buat/update 2 queue tree entries untuk user IPoE (DL dan UL).
    Jika sudah ada, hapus dulu lalu buat ulang.
    Return: {'success': bool, 'errors': []}
    """
    key = _safe_key(username)
    max_limit = _parse_max_limit(rate_limit)
    errors = []

    # Hapus queue lama terlebih dahulu (jika ada)
    remove_queue_tree(api, username)

    # Clamp priority ke range 1-8
    prio = max(1, min(8, int(priority)))

    # Limit-at: jika tidak diisi atau '0', set ke 0
    lat = limit_at.strip() if limit_at else '0'

    queues = [
        # Download queue
        {
            'name': f'{key}_dl',
            'parent': parent_dl,
            'packet-mark': f'{key}_dl',
            'limit-at': lat,
            'max-limit': max_limit,
            'priority': str(prio),
            'queue': 'cake-pppoe-rx',
            'bucket-size': '0.1',
        },
        # Upload queue
        {
            'name': f'{key}_ul',
            'parent': parent_ul,
            'packet-mark': f'{key}_ul',
            'limit-at': lat,
            'max-limit': max_limit,
            'priority': str(prio),
            'queue': 'cake-pppoe-tx',
            'bucket-size': '0.1',
        },
    ]

    for q in queues:
        try:
            api('/queue/tree/add', **q)
        except Exception as e:
            errors.append(f'queue add error ({q["name"]}): {e}')

    return {'success': len(errors) == 0, 'errors': errors}


# ---------------------------------------------------------------------------
# HIGH-LEVEL: sync satu user (mangle + queue sekaligus)
# ---------------------------------------------------------------------------

def sync_user_queue(api, username: str, ip_address: str, rate_limit: str,
                    parent_dl: str, parent_ul: str,
                    limit_at: str = '0', priority: int = 8,
                    display_name: str = None) -> dict:
    """
    Sinkronisasi lengkap mangle + queue tree untuk satu user IPoE.
    Aman untuk dipanggil saat add maupun update — akan hapus rules lama dulu.

    Args:
        api         : koneksi librouteros aktif
        username    : MAC address / username IPoE
        ip_address  : IP statis user (Framed-IP-Address)
        rate_limit  : nilai dari Mikrotik-Rate-Limit, contoh "10M" atau "10M/10M"
        parent_dl   : nama parent queue DL di MikroTik, contoh "GLOBAL_DL (isp3 bridge)"
        parent_ul   : nama parent queue UL di MikroTik, contoh "GLOBAL-UL (isp2)"
        limit_at    : guaranteed bandwidth (opsional, default "0")
        priority    : 1-8 (default 8)
        display_name: nama tampilan untuk comment mangle (default: username)

    Return:
        {
            'success': bool,
            'mangle': {'success': bool, 'errors': []},
            'queue':  {'success': bool, 'errors': []},
        }
    """
    if not ip_address:
        return {
            'success': False,
            'mangle': {'success': False, 'errors': ['ip_address kosong, mangle tidak dibuat']},
            'queue':  {'success': False, 'errors': ['ip_address kosong, queue tidak dibuat']},
        }

    mangle_result = create_mangle_rules(api, username, ip_address, display_name)
    queue_result  = create_queue_tree(api, username, rate_limit, parent_dl, parent_ul,
                                      limit_at, priority)

    return {
        'success': mangle_result['success'] and queue_result['success'],
        'mangle': mangle_result,
        'queue':  queue_result,
    }


def remove_user_queue(api, username: str) -> dict:
    """
    Hapus mangle + queue tree milik user secara lengkap.
    Dipanggil saat user IPoE dihapus.

    Return:
        {
            'success': bool,
            'mangle_removed': N,
            'queue_removed': N,
            'errors': [],
        }
    """
    mangle_result = remove_mangle_rules(api, username)
    queue_result  = remove_queue_tree(api, username)

    all_errors = mangle_result['errors'] + queue_result['errors']
    return {
        'success': len(all_errors) == 0,
        'mangle_removed': mangle_result['removed'],
        'queue_removed':  queue_result['removed'],
        'errors': all_errors,
    }


# ---------------------------------------------------------------------------
# UTILITY: ambil daftar parent queue dari MikroTik untuk dropdown UI
# ---------------------------------------------------------------------------

def get_queue_parents(api) -> list:
    """
    Ambil semua queue tree yang bisa menjadi parent (tidak punya packet-mark).
    Biasanya ini adalah queue parent/global seperti HSGQ-DL, GLOBAL-UL, dsb.

    Return: list of {'name': str, 'parent': str, 'max_limit': str}
    """
    try:
        queues = list(api('/queue/tree/print'))
        parents = []
        for q in queues:
            # Parent queue biasanya tidak punya packet-mark
            if not q.get('packet-mark'):
                parents.append({
                    'name': q.get('name', ''),
                    'parent': q.get('parent', ''),
                    'max_limit': q.get('max-limit', '0'),
                })
        return parents
    except Exception as e:
        return []


def get_user_queue_status(api, username: str) -> dict:
    """
    Cek status queue tree user di MikroTik.
    Return: {'found': bool, 'dl': {...}, 'ul': {...}}
    """
    key = _safe_key(username)
    try:
        queues = list(api('/queue/tree/print'))
        dl = next((q for q in queues if q.get('name') == f'{key}_dl'), None)
        ul = next((q for q in queues if q.get('name') == f'{key}_ul'), None)
        return {
            'found': dl is not None or ul is not None,
            'dl': dl,
            'ul': ul,
        }
    except Exception as e:
        return {'found': False, 'dl': None, 'ul': None, 'error': str(e)}
