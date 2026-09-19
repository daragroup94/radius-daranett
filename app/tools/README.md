# 🛠️ Diagnostic, Testing & Ad-hoc Tools

Direktori ini berisi kumpulan skrip diagnostik, pengujian CWMP/TR-069 (GenieACS), otomasi ONT ZTE, serta inspeksi router MikroTik dan OLT. Skrip-skrip ini dipisahkan dari direktori aplikasi utama (`app/`) agar struktur kode inti tetap bersih dan rapi.

---

## 📂 Kategori Skrip

### 1. 🔧 ZTE ONT (F670L) WAN & Wi-Fi Provisioning
Skrip untuk otomasi penambahan WAN Bridge/Route, binding SSID, dan konfigurasi VLAN langsung melalui TR-069:
- `add_wan_bridge_f670l_standalone.py`: Menambahkan WAN Bridge Hotspot VLAN 55 bind ke SSID 6 secara mandiri.
- `correct_add_wan_f670l.py` & `final_add_wan_f670l.py`: Variasi pembuatan WAN connection device.
- `bind_f670l_correct.py` & `bind_to_ssid6.py`: Pengikatan interface LAN/WLAN ke WAN bridge.
- `fix_vlan_f670l.py` & `fix_wan_f670l.py`: Penyesuaian tag VLAN dan parameter WAN pada ZTE ONT.
- `enable_zte_wan_mgmt.py`: Mengaktifkan web management WAN pada ZTE.
- `test_add_wan_hotspot_f670l.py`: Pengujian alur pembuatan WAN hotspot bertingkat.

### 2. 🔍 GenieACS / TR-069 CWMP Inspection & Debugging
Skrip untuk membaca, memicu connection request, dan menguji endpoint NBI GenieACS:
- `debug_ont.py`: Mengambil seluruh Virtual Parameters dan status koneksi ONT berdasarkan Serial Number atau username.
- `deep_analysis.py`: Analisis mendalam parameter device dari GenieACS.
- `test_acs.py`, `test_acs_device.py`, `test_acs_ip.py`, `test_acs_raw.py`, `test_acs_values.py`, `test_acs_vendor.py`: Rangkaian pengujian konektivitas dan payload API NBI GenieACS.
- `fetch_all_params.py` & `get_parameter_names.py`: Dump daftar nama parameter data model TR-098/TR-181.
- `fetch_wan_correctly.py` & `print_wan_details.py`: Ekstraksi detail konfigurasi WAN IP/PPP secara presisi.
- `trigger_cr.py`: Memicu HTTP Digest Connection Request langsung ke ONT.
- `refresh_ont.py`: Meminta ONT memperbarui seluruh parameter (SetParameterValues/GetParameterValues).
- `run_cwmp_task.py`: Menjalankan task custom ke antrean GenieACS.
- `wait_for_task.py` & `wait_for_task2.py`: Polling status task antrean hingga selesai (*completed/fault*).
- `dump_all_acs.py`, `dump_device.py`, `dump_full_device.py`, `dump_full_wan.py`, `dump_provisions_full.py`: Dump data dokumen JSON GenieACS.

### 3. 🚨 Fault & Task Queue Management
- `check_all_faults.py`, `check_faults.py`, `analyze_all_faults.py`: Memeriksa log error/fault task di GenieACS.
- `clear_all_faults.py`: Menghapus antrean task yang berstatus fault/gagal.
- `check_task_queue.py` & `check_tasks.py`: Memeriksa antrean task yang sedang pending.

### 4. 🌐 MikroTik & OLT Inspection
- `check_mikrotik_dupes.py`: Memeriksa dan melaporkan sesi duplikat pada active PPP di seluruh router MikroTik.
- `probe_olt.py`: Uji probing konektivitas dan respon OLT.
- `find_all_modules.py`, `find_getmodule_data.py`, `show_getmodules.py`: Inspeksi modul OLT Tenda/SNMP.

---

## 🚀 Cara Menjalankan Skrip

Skrip dapat dijalankan dari dalam container `radius-dashboard` atau langsung dari host jika dependensi Python terpenuhi:

### Dari Dalam Container Docker:
```bash
# Contoh menjalankan debug_ont.py
docker compose exec -it radius-dashboard python tools/debug_ont.py

# Contoh menjalankan check_mikrotik_dupes.py
docker compose exec -it radius-dashboard python tools/check_mikrotik_dupes.py
```

### Dari Host (dengan PYTHONPATH mengarah ke /app):
```bash
cd /root/radius-daranett/app
PYTHONPATH=. python tools/debug_ont.py
```
