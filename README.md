# 🚀 RADIUS Manager & FreeRADIUS Dashboard

Sistem manajemen billing, otentikasi, dan pemantauan terpusat untuk ISP skala kecil hingga menengah. Proyek ini menggabungkan **FreeRADIUS 3.0** (dengan backend database **PostgreSQL**), caching **Redis**, web panel **Flask Dashboard**, dan load balancer **Traefik** dengan SSL otomatis dari Let's Encrypt (Cloudflare).

Layanan ini mendukung multi-protokol otentikasi:
* **PPPoE** (Otentikasi PPPoE standard)
* **IPoE** (Otentikasi transparan berbasis MAC Address untuk DHCP Server)
* **Hotspot** (Manajemen voucher & lock MAC)
* **VPN** (Otentikasi user VPN seperti L2TP/SSTP/Wireguard)

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

## ⚙️ Integrasi MikroTik IPoE (MAC Authentication)

Untuk client IPoE (DHCP MAC Authentication), agar nama client di **Simple Queue** RouterOS bisa disesuaikan secara manual (kustom), modul FreeRADIUS dikonfigurasi untuk **tidak** mengirimkan attribute limitasi kecepatan `Mikrotik-Rate-Limit` secara langsung. 

Sebagai gantinya, MikroTik RouterOS mengambil data limitasi (`rate_limit`) dan nama pelanggan (`name`) melalui API endpoint `/api/ipoe/get-name/<MAC>` dan memproses konversinya ke **Static Simple Queue** secara dinamis saat client mendapatkan lease DHCP.

### 1. Pengaturan di MikroTik (WinBox)
1. Masuk ke **IP > DHCP Server**.
2. Klik dua kali pada server DHCP yang ingin digunakan untuk IPoE (contoh: `dhcp2`).
3. Pada tab **DHCP**, centang opsi **Use Radius** menjadi aktif (`yes`).
4. Klik kolom **Lease Script** di bagian paling bawah tab DHCP.
5. Tempelkan script di bawah ini ke dalam kolom **Lease Script**:

### 📋 DHCP Lease Script (RouterOS v7 - VPN IP Direct Edition)
```routeros
:if ($leaseBound = 1) do={
    :delay 2s; # Tunggu lease stabil
    
    :local success false
    :local attempts 3
    :local delayTime 3s
    :local res
    :local data ""
    
    :for i from=1 to=$attempts do={
        :if (!$success) do={
            :do {
                # Mengambil data langsung via IP VPN VPS (10.20.0.1) untuk menghindari resolving error
                :set res [/tool fetch url="https://10.20.0.1/api/ipoe/get-name/$leaseActMAC" http-header-field="Host: radius.access.daragroup.cloud" check-certificate=no as-value output=user]
                :set data ($res->"data")
                :set success true
            } on-error={
                :log warn "IPoE: Percobaan ke-$i gagal menghubungi IP NAS (10.20.0.1) untuk MAC $leaseActMAC. Mencoba lagi dalam $delayTime..."
                :delay $delayTime
            }
        }
    }
    
    :if ($success) do={
        # Cari posisi titik koma (;) untuk memisah data
        :local pos [:find $data ";"]
        :if ($pos > 0) do={
            :local name [:pick $data 0 $pos]
            :local rate [:pick $data ($pos+1) [:len $data]]
            
            # Hapus queue bawaan (jika ada) agar tidak bentrok
            /queue simple remove [find target="$leaseActIP/32"]
            
            # Jika user memiliki rate limit di dashboard, buat static queue kustom
            :if ([:len $rate] > 0) do={
                /queue simple add name="IPoE-$name" target="$leaseActIP/32" max-limit=$rate parent=GLOBAL
            }
        }
    } else={
        :log error "Gagal memproses limitasi IPoE dari RADIUS Dashboard untuk MAC: $leaseActMAC (Koneksi ke IP NAS gagal setelah $attempts percobaan)"
    }
} else={
    # Saat client terputus (leaseBound = 0), hapus static queue secara otomatis
    /queue simple remove [find target="$leaseActIP/32"]
}
```

---

## ☕ Dukungan & Donasi
Jika proyek ini membantu kelancaran bisnis dan usaha Anda, dukung pengembang melalui donasi:
* **DANA:** `081211081663`
