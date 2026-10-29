# 🚀 RADIUS Manager & FreeRADIUS Dashboard

Sistem manajemen billing, otentikasi, dan pemantauan terpusat untuk ISP skala kecil hingga menengah. Proyek ini menggabungkan **FreeRADIUS 3.0** (dengan backend database **PostgreSQL**), caching **Redis**, web panel **Flask Dashboard**, dan load balancer **Traefik** dengan SSL otomatis dari Let's Encrypt (Cloudflare).

Layanan ini mendukung multi-protokol otentikasi:
* **PPPoE** (Otentikasi PPPoE standard)
* **IPoE** (Otentikasi transparan berbasis MAC Address untuk DHCP Server)
* **Hotspot** (Manajemen voucher & lock MAC)
* **VPN** (Otentikasi user VPN seperti L2TP/SSTP/Wireguard)

### 🌐 Fitur Peta Jaringan FTTH (Network Map)
Sistem dilengkapi dengan **visualisasi peta jaringan FTTH** yang mendukung:
* **Topologi Estafet Splitter Bertingkat** - Sistem splitter dengan ratio fleksibel (30:70, 20:80, dll)
* **OLT** (Optical Line Terminal) - Pusat transmisi fiber optik
* **ODC** (Optical Distribution Cabinet) - Cabinet distribusi ke area
* **OIT** (Optical Inline Tap) - Splitter inline untuk sistem estafet dengan ratio custom
* **ODP** (Optical Distribution Point) - Splitter box terakhir sebelum ke pelanggan
* **Visualisasi 3 Jenis Kabel:**
  - Kabel Feeder (Ungu) - OLT → OIT atau OIT → OIT
  - Kabel Tap (Kuning) - OIT → ODP
  - Kabel Distribusi (Cyan) - ODC/ODP → Pelanggan atau koneksi umum
* **Real-time ONT Health Monitoring** - RX Power, temperatur, status online/offline
* **Drag & Drop** untuk reposisi marker di peta
* **Manual Cable Routing** - Gambar rute kabel manual mengikuti jalan

---

## 📋 DAFTAR ISI
1. [Persyaratan Awal (VPS Fresh)](#-persyaratan-awal-vps-fresh)
2. [Langkah Instalasi Baru](#-langkah-instalasi-baru)
3. [Panduan Migrasi VPS (Pindah Server)](#-panduan-migrasi-vps-pindah-server)
4. [Akses Web & pgAdmin](#-akses-web--pgadmin)
5. [Integrasi MikroTik IPoE (DHCP Lease Script)](#-integrasi-mikrotik-ipoe-mac-authentication)
6. [Dukungan & Donasi](#-dukungan--donasi)

---

## 🛠️ Persyaratan Awal (VPS Fresh)

Sebelum melakukan instalasi pada VPS baru, pastikan OS yang digunakan adalah **Ubuntu 22.04 LTS** atau **Ubuntu 24.04 LTS** (rekomendasi). 

### 1. Install Docker & Docker Compose
Jalankan perintah berikut di VPS fresh Anda untuk menginstal Docker Engine dan Docker Compose:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y curl git nano

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose Plugin
sudo apt install -y docker-compose-plugin
```

### 2. Buat Network Eksternal Docker
Proyek ini menggunakan jaringan eksternal bernama `my-net` untuk interkoneksi container. Anda **wajib** membuatnya secara manual sebelum menjalankan container:
```bash
docker network create my-net
```

---

## 🚀 Langkah Instalasi Baru

Jika Anda ingin men-deploy proyek ini dari awal tanpa database lama:

1. **Clone repositori ini:**
   ```bash
   git clone https://github.com/daraesa/project-radius.git
   cd project-radius
   ```

2. **Setup Environment Variables:**
   Salin file template `.env.example` menjadi `.env` lalu sesuaikan isinya:
   ```bash
   cp .env.example .env
   nano .env
   ```
   *Pastikan mengubah password database, token bot Telegram, email pgAdmin, dan domain Traefik Anda.*

3. **Jalankan Project:**
   Build dan jalankan seluruh container menggunakan Docker Compose:
   ```bash
   docker compose up -d --build
   ```

4. **Verifikasi Status:**
   Pastikan seluruh layanan (Database, Redis, RADIUS, Dashboard, pgAdmin) berjalan normal (status `Up` atau `healthy`):
   ```bash
   docker compose ps
   ```

---

## 📦 Panduan Migrasi VPS (Pindah Server)

Jika Anda ingin memindahkan sistem dari VPS lama ke VPS baru, ikuti langkah-langkah backup & restore berikut secara berurutan:

### Langkah 1: Backup Data dari VPS Lama
Jalankan perintah ini di VPS **Lama** Anda untuk mengambil database backup dan mengamankan file konfigurasi penting:

1. **Dump Database PostgreSQL ke file SQL:**
   ```bash
   cd ~/project-radius
   docker compose exec -t postgres pg_dump -U radius -d radius > backup_database.sql
   ```
2. **Amankan berkas konfigurasi penting:**
   * Ambil file `.env` (berisi password & token rahasia).
   * Ambil file `clients.conf` (berisi daftar secret key client NAS).
   * Ambil file `backup_database.sql` yang baru dibuat.
   
   *(Anda dapat mendownload file-file tersebut menggunakan SCP/SFTP ke komputer lokal Anda).*

### Langkah 2: Setup VPS Baru & Clone Repositori
Lakukan persiapan awal di VPS **Baru** Anda:

1. Lakukan instalasi Docker dan pembuatan docker network seperti yang dijelaskan pada bagian [Persyaratan Awal](#-persyaratan-awal-vps-fresh).
2. Clone proyek ke VPS baru:
   ```bash
   git clone https://github.com/daraesa/project-radius.git
   cd project-radius
   ```
3. Upload/salin file backup dari komputer Anda ke dalam direktori proyek `/home/dg/radius-dashboard` (atau direktori klon Anda) di VPS baru:
   * Upload `.env` ke direktori root proyek.
   * Upload `clients.conf` ke direktori root proyek.
   * Upload `backup_database.sql` ke direktori root proyek.

### Langkah 3: Restore Database di VPS Baru
Ada 2 cara untuk mengimpor data database ke VPS baru:

#### Cara A: Sebelum Menjalankan Container (Otomatis)
PostgreSQL Docker container otomatis menjalankan script `.sql` yang ditaruh di folder `init.db/` saat database pertama kali di-inisialisasi:
1. Pindahkan file backup Anda ke folder `init.db`:
   ```bash
   mv backup_database.sql init.db/03-restore-backup.sql
   ```
2. Jalankan docker compose:
   ```bash
   docker compose up -d --build
   ```
3. Docker database akan otomatis membuat struktur dan memasukkan seluruh data backup Anda.

#### Cara B: Setelah Container Berjalan (Manual)
Jika container sudah terlanjur dijalankan dan dalam keadaan kosong:
1. Pastikan container database dalam status running:
   ```bash
   docker compose up -d postgres
   ```
2. Impor file backup langsung ke dalam container database:
   ```bash
   cat backup_database.sql | docker compose exec -T postgres psql -U radius -d radius
   ```
3. Restart container dashboard & freeradius agar terhubung ulang ke database yang terisi data:
   ```bash
   docker compose restart radius-dashboard freeradius
   ```

---

## 🧭 Akses Web & pgAdmin

* **Web Dashboard:** `https://radius.access.daragroup.cloud` (atau port `5000` internal `http://your-vps-ip:5000`)
* **pgAdmin (Manajemen DB):** `https://pgadmin.access.daragroup.cloud`

*(Domain di atas dapat disesuaikan di file `docker-compose.yml` pada konfigurasi Traefik labels)*

---

---

## 📡 Panduan Sistem Estafet Splitter dengan OIT (Optical Inline Tap)

Sistem ini mendukung topologi FTTH estafet bertingkat menggunakan **OIT (Optical Inline Tap)** untuk pembagian sinyal dengan ratio fleksibel.

### Contoh Implementasi Estafet:

```
OLT (PON 1 +9dBm)
    │
    ├─ [OIT-1 @ 30:70]
    │   ├─ 30% Tap → +3dBm → [Splitter 1:4] → ODC-Area-A
    │   │
    │   └─ 70% Pass → +7dBm ─────────────────┐
    │                                         │
    └─ [OIT-2 @ 20:80] <──────────────────────┘
        ├─ 20% Tap → +5.6dBm → [Splitter 1:4] → ODC-Area-B
        │
        └─ 80% Pass → +6.8dBm → ODP (End of Line)
```

### Cara Menambahkan OIT di Peta:

1. **Klik Tombol "Tambah OLT/ODC/ODP"** di header peta
2. **Pilih Tipe**: `OIT (Inline Tap Splitter - Estafet)`
3. **Isi Form OIT**:
   - **Nama Alat**: Contoh `OIT-PATI-01`
   - **Sumber Feeder (Upstream)**: Pilih OLT atau OIT sebelumnya
   - **Panjang Kabel Masuk**: Dalam meter (contoh: 500)
   - **Redaman Input**: dBm dari upstream (contoh: +5.2)
   - **Rasio Tap Splitter**: Pilih ratio (10:90, 15:85, 20:80, 25:75, 30:70, 40:60, 50:50)
   - **Merk Splitter**: Brand splitter yang digunakan (contoh: Fiberhome)
   - **Redaman Tap Output**: Sinyal ke ODP (contoh: +3.5 dBm)
   - **Redaman Pass Output**: Sinyal lanjut ke OIT berikutnya (contoh: +7.0 dBm)
   
4. **Port TAP (menuju ODP)**:
   - Pilih ODP tujuan dari port Tap
   - Pilih ratio splitter di ODP (1:2, 1:4, 1:8, 1:16)
   - Isi panjang kabel ke ODP
   
5. **Port PASS (lanjut ke OIT berikutnya)**:
   - Pilih OIT berikutnya (jika masih ada estafet)
   - Isi panjang kabel
   - Centang "Ujung Jalur" jika ini OIT terakhir
   
6. **Status & Catatan**:
   - Status Operasional (Aktif / Instalasi / Gangguan)
   - Tanggal Pemasangan
   - Catatan Teknisi

7. **Klik pada peta** atau **paste koordinat** untuk menempatkan marker OIT
8. **Klik "Simpan Alat"**

### Menghubungkan Kabel Infrastruktur:

Setelah OIT dibuat, hubungkan kabel secara manual:

1. **Klik pada marker OLT atau OIT upstream**
2. **Klik tombol "Tarik Kabel"** dalam popup
3. **Pilih OIT tujuan** dari dropdown
4. **Klik beberapa titik di peta** untuk menggambar jalur kabel mengikuti jalan
5. **Klik marker tujuan** untuk menyelesaikan koneksi
6. **Klik "Simpan Rute"**

Sistem akan otomatis menentukan jenis kabel:
- **Kabel Feeder (Ungu tebal)**: Jika salah satu ujung adalah OLT atau OIT ke OIT
- **Kabel Tap (Kuning)**: OIT ke ODP
- **Kabel Distribusi (Cyan)**: Koneksi lainnya

### Tips Penggunaan:

- Gunakan nama yang deskriptif untuk OIT (contoh: `OIT-PATI-JL-RAYA-01`)
- Catat redaman aktual di lapangan untuk monitoring performa jaringan
- Manfaatkan field "Catatan Teknisi" untuk informasi lokasi spesifik (contoh: "Dekat Rumah Pak RT")
- Centang "Ujung Jalur" pada OIT terakhir untuk menandai terminal point
- Gunakan status "Instalasi" saat pemasangan baru, ubah ke "Aktif" setelah selesai
- Tandai "Gangguan" jika ada masalah untuk memudahkan tracking troubleshooting

---

## ⚙️ Integrasi MikroTik IPoE (MAC Authentication)

Untuk client IPoE (DHCP MAC Authentication), rate limit dan queue **tidak dibatasi secara dinamis oleh RADIUS/Dashboard**. FreeRADIUS dikonfigurasi untuk **tidak mengirimkan** attribute `Mikrotik-Rate-Limit`.

Dengan konfigurasi ini:
1. **Tidak ada dynamic queue (flag D)** yang dikirim oleh RADIUS.
2. Anda memiliki **kebebasan penuh** mengatur Simple Queue, Queue Tree, Parent Queue, PCQ, burst, maupun priority secara **manual dan fleksibel langsung di MikroTik (Winbox > Queues)**.
3. Antrean manual yang Anda buat di MikroTik tidak akan tertimpa atau terhapus oleh sistem RADIUS.

### Pengaturan di MikroTik (WinBox)
1. Masuk ke **IP > DHCP Server**.
2. Klik dua kali pada DHCP Server untuk IPoE (contoh: `dhcp1`).
3. Pada tab **DHCP**, centang opsi **Use Radius** (`yes`).
4. Pada menu **Queues > Simple Queues** di Winbox:
   - Tambahkan Simple Queue baru secara manual.
   - **Name**: Nama pelanggan / deskripsi sesuai keinginan Anda.
   - **Target**: IP Address pelanggan (contoh: `172.16.200.13/32`) atau subnet/interface.
   - **Max Limit**: Upload dan Download sesuai paket yang Anda tentukan secara fleksibel.
   - Anda bebas menentukan **Parent Queue**, **Queue Type**, **Priority**, maupun **Burst**.

---

## ☕ Dukungan & Donasi
Jika proyek ini membantu kelancaran bisnis dan usaha Anda, dukung pengembang melalui donasi:
* **DANA:** `081211081663`
